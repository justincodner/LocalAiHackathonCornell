"""Drive one voice task at a time from the Helper's event stream."""

import asyncio
import json
import logging
import time
from dataclasses import replace

import httpx

from . import fastpath, grounding, narrate, risk, tools
from .config import Config
from .helper_client import HelperError
from .llm import BonsaiError
from .log import append_step
from .memory import TaskMemory


LOGGER = logging.getLogger(__name__)
TERMINALS = {"say", "done", "read_element"}
CONNECTION_ERRORS = (HelperError, httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError)


class Brain:
    def __init__(self, helper, model, config: Config, prompt: str):
        self.helper = helper
        self.model = model
        self.config = config
        self.prompt = prompt
        self.state = "idle"
        self.active: asyncio.Task | None = None
        self.reply: asyncio.Future | None = None
        self.speech_events: dict[str, asyncio.Future] = {}
        self.finished_speech: dict[str, bool] = {}
        self.screen_generation = 0
        self.task_number = 0
        self.previous_result = ""
        self.last_speech = ""
        self.rate = 1.0

    async def set_state(self, state: str, **fields) -> None:
        self.state = state
        try:
            await self.helper.hud(state, **fields)
        except (HelperError, httpx.HTTPError):
            LOGGER.debug("HUD update failed", exc_info=True)

    async def cancel_active(self) -> None:
        if self.active is None or self.active.done():
            return
        self.active.cancel()
        await asyncio.gather(self.active, return_exceptions=True)

    async def on_event(self, event: dict) -> None:
        kind = event["type"]
        if kind == "app.changed":
            self.screen_generation += 1
        elif kind == "tts.finished" and isinstance(event.get("speech_id"), str):
            speech_id = event["speech_id"]
            interrupted = bool(event.get("interrupted"))
            waiting = self.speech_events.get(speech_id)
            if waiting is not None and not waiting.done():
                waiting.set_result(interrupted)
            else:
                if len(self.finished_speech) >= 32:
                    self.finished_speech.pop(next(iter(self.finished_speech)))
                self.finished_speech[speech_id] = interrupted
        elif kind == "barge_in":
            await self.cancel_active()
        elif kind == "listen.started" and self.state == "idle":
            await self.set_state("listening")
        elif kind == "listen.cancelled" and self.state == "listening":
            await self.set_state("idle")
        elif kind == "stt.final" and isinstance(event.get("text"), str) and event["text"].strip():
            text = event["text"].strip()
            if self.reply is not None and not self.reply.done():
                self.reply.set_result(text)
                return
            await self.cancel_active()
            self.task_number += 1
            self.active = asyncio.create_task(self.handle_utterance(text, event.get("stt_ms", 0)))

    async def speak(self, text: str, *, wait: bool = True) -> bool:
        self.last_speech = text
        speech_id = await self.helper.speak(text, self.rate)
        if not wait:
            return True
        if speech_id in self.finished_speech:
            return not self.finished_speech.pop(speech_id)
        finished = asyncio.get_running_loop().create_future()
        self.speech_events[speech_id] = finished
        try:
            return not await finished
        finally:
            self.speech_events.pop(speech_id, None)

    async def say(self, text: str) -> None:
        await self.set_state("speaking", caption=text)
        await self.speak(text)

    async def ask(self, question: str, *, confirm: bool = False) -> str | None:
        await self.set_state("confirm" if confirm else "speaking", caption=question)
        if not await self.speak(question):
            return None
        reply = asyncio.get_running_loop().create_future()
        self.reply = reply
        try:
            await self.helper.listen("start")
            try:
                return await asyncio.wait_for(reply, self.config.reply_timeout_s)
            except TimeoutError:
                await self.helper.listen("stop")
                await self.say("I didn't hear a reply, so I stopped.")
                return None
        finally:
            self.reply = None

    async def handle_utterance(self, text: str, stt_ms: int) -> None:
        command = fastpath.classify(text)
        try:
            async with asyncio.timeout(self.config.task_timeout_s):
                if command == "stop":
                    await self.helper.stop_speech()
                elif command == "repeat":
                    await self.say(self.last_speech or "I haven't said anything yet.")
                elif command in {"slower", "faster"}:
                    delta = -0.1 if command == "slower" else 0.1
                    self.rate = max(0.5, min(1.5, round(self.rate + delta, 1)))
                    await self.say(f"Speaking {command}.")
                else:
                    await self.run_task(text, stt_ms)
        except (TimeoutError, BonsaiError) as error:
            LOGGER.warning("Task ended: %s", error)
            apology = (
                "I'm having trouble thinking. Please try again." if isinstance(error, BonsaiError)
                else "That took too long, so I stopped. Please try again."
            )
            try:
                async with asyncio.timeout(4):
                    await self.say(apology)
            except CONNECTION_ERRORS:
                LOGGER.debug("Could not tell the user the task ended", exc_info=True)
        except CONNECTION_ERRORS:
            LOGGER.warning("Helper interaction failed", exc_info=True)
        finally:
            await self.set_state("idle")

    async def run_task(self, text: str, stt_ms: int) -> None:
        memory = TaskMemory(self.prompt, text, self.previous_result)
        task_id = f"t_{self.task_number}"
        generation = self.screen_generation
        snapshot = await self.helper.snapshot()
        previous_snapshot = None
        task_summary = ""
        consecutive_invalid = 0
        failures = []
        confirmed_screen = None
        actions_taken: list[str] = []  # spoken past tense, for honest done summaries
        committed = False  # a confirm-level action (send, delete, ...) actually ran
        fresh_request = True  # no action has run since the user last spoke (grounding.check)
        for step in range(1, self.config.max_steps + 1):
            if generation != self.screen_generation:
                generation = self.screen_generation
                previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
            await self.set_state("thinking", transcript=memory.latest_words)
            started = time.perf_counter()
            call = await self.think(memory.messages(snapshot), filler=step == 1)
            llm_ms = round((time.perf_counter() - started) * 1000)
            if call.stray_text:
                LOGGER.warning("Model wrote %d chars outside its tool call", len(call.stray_text))
            acted_at = time.perf_counter()
            screen = snapshot
            call = tools.normalize(call, snapshot)
            if call.name == "done" and isinstance(call.arguments.get("summary"), str):
                honest = narrate.honest_summary(call.arguments["summary"], actions_taken, committed)
                if honest != call.arguments["summary"]:
                    LOGGER.warning("Replaced a done summary that claimed an action Beacon did not take")
                    call = replace(call, arguments={**call.arguments, "summary": honest})
            error = tools.validate(call, snapshot)
            grounded = ""
            if error is None:
                checked = grounding.check(call, snapshot, memory.latest_words,
                                          fresh_request=fresh_request, said=memory.all_words)
                if checked != call:
                    grounded = "asked" if checked.name == "ask_user" else "retargeted"
                    LOGGER.info("Grounding %s %s %s", grounded, call.name, call.arguments)
                    call = checked
            if len(failures) == 2 and failures[-1] == failures[-2] == (call.name, call.arguments):
                error = "That tool call already failed twice"
            if generation != self.screen_generation:
                error = "The frontmost app changed. Use the refreshed SCREEN"
            verdict = "allow" if error else risk.check(call, snapshot)
            proposed_summary = narrate.confirm_text(call, snapshot) if verdict == "confirm" else ""
            # The model's own confirm, approved on the previous step on this same screen, covers
            # this action if it named the same commitment ("Send to Sarah…" covers the Send button).
            already_confirmed = (
                verdict == "confirm" and confirmed_screen is not None
                and confirmed_screen[:2] == (snapshot["app"], snapshot["text"])
                and risk.covered_by(confirmed_screen[2], call, snapshot)
            )
            request_for_model = memory.request
            confirmed_screen = None
            try:
                if error:
                    consecutive_invalid += 1
                    result = {"ok": False, "error": error}
                elif verdict == "refuse":
                    task_summary = "I can't do that one for you."
                    await self.say(task_summary)
                    result = {"ok": False, "error": "Action refused"}
                elif verdict == "confirm":
                    consecutive_invalid = 0
                    reply = "yes" if already_confirmed else await self.ask(
                        proposed_summary.rstrip(".") + ". Say yes to continue.", confirm=True,
                    )
                    reply_command = fastpath.classify(reply or "")
                    if reply_command == "yes":
                        current = await self.helper.snapshot()
                        if generation != self.screen_generation or (
                            snapshot["app"], snapshot["text"], tools.element_for(call, snapshot)
                        ) != (current["app"], current["text"], tools.element_for(call, current)):
                            result = {"ok": False, "error": "SCREEN changed after confirmation"}
                        else:
                            result = await self.perform(call, current, previous_snapshot)
                        snapshot = current
                    elif reply and reply_command not in {"stop", "no"}:
                        result = {"ok": False, "reply": reply}
                        memory.answer(proposed_summary.rstrip(".") + ". Say yes to continue.", reply)
                        fresh_request = True
                    else:
                        result = {"ok": False, "end_task": True, "error": "Action cancelled"}
                else:
                    consecutive_invalid = 0
                    result = await self.perform(call, snapshot, previous_snapshot)
                    if call.name in TERMINALS:
                        task_summary = self.last_speech
                    if call.name == "confirm" and result.get("approved"):
                        summary = call.arguments["summary"].strip().rstrip(".").casefold()
                        confirmed_screen = (snapshot["app"], snapshot["text"], summary)
                    if call.name in {"ask_user", "confirm"} and result.get("reply") and not result.get("approved"):
                        memory.answer(call.arguments.get("question") or call.arguments.get("summary", ""),
                                      result["reply"])
                        fresh_request = True
            except HelperError as failure:
                result = {"ok": False, "error": {"code": failure.code, "message": str(failure)}}
                if failure.code in {"element_not_found", "action_failed", "stale_snapshot"}:
                    generation = self.screen_generation
                    previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
                if failure.code == "stale_snapshot" and call.name in tools.SCREEN_ACTIONS | {"read_element"}:
                    result = await self.retry_on_fresh_screen(call, screen, snapshot, previous_snapshot) or result

            act_ms = round((time.perf_counter() - acted_at) * 1000)
            if result["ok"] and call.name in tools.SCREEN_ACTIONS:
                actions_taken.append(narrate.past_action(call, screen))
                committed = committed or verdict == "confirm" or (
                    call.name == "press_key" and risk.normal_keys(call.arguments["keys"]).split("+")[-1] in {"return", "enter"}
                )  # Return sends in many chat apps, so don't claim "nothing has been sent"
                fresh_request = False
                await asyncio.sleep(self.config.settle_ms / 1000)
                generation = self.screen_generation
                previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
                if call.name == "type_text":
                    field = tools.element_for(call, snapshot)
                    if field is not None:
                        result["value"] = field.get("value")
            append_step(
                self.config.log_path,
                task_id=task_id, step=step, utterance=text, snapshot_id=screen["snapshot_id"],
                snapshot_tokens=(len(screen["text"]) + 3) // 4, tool=call.name, args=call.arguments,
                valid=error is None, risk=verdict, ok=result["ok"], stray_chars=len(call.stray_text),
                grounded=grounded or None,
                ms={"stt": stt_ms, "snapshot": screen.get("build_ms", 0), "llm": llm_ms, "act": act_ms},
            )
            target = tools.element_for(call, screen) if "id" in call.arguments else None
            memory.add(call, result, screen["snapshot_id"], request_for_model,
                       json.dumps(target["label"]) if target else "")
            if consecutive_invalid >= 2:
                task_summary = "I couldn't find a valid action for that request."
                await self.say(task_summary)
                break
            if verdict == "refuse" or result.get("end_task") or (call.name in TERMINALS and result["ok"]):
                break
            if not result["ok"]:
                failures = [*failures[-1:], (call.name, call.arguments)]
            else:
                failures = []
        else:
            task_summary = "I couldn't finish that. Here is where I stopped."
            await self.say(task_summary)
        self.previous_result = memory.summarize(task_summary or "The previous task stopped.")

    async def think(self, messages: list[dict], *, filler: bool):
        """Ask the model, saying "One moment." if it takes longer than the filler delay (spec 10.2)."""
        if not filler or self.config.thinking_filler_ms <= 0:
            return await self.model.next_tool_call(messages, tools.TOOL_DEFINITIONS)

        async def say_filler():
            await asyncio.sleep(self.config.thinking_filler_ms / 1000)
            try:
                await self.helper.speak("One moment.", self.rate)
            except CONNECTION_ERRORS:
                LOGGER.debug("Filler speech failed", exc_info=True)

        timer = asyncio.create_task(say_filler())
        try:
            return await self.model.next_tool_call(messages, tools.TOOL_DEFINITIONS)
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    async def retry_on_fresh_screen(self, call, old: dict, fresh: dict, previous: dict | None) -> dict | None:
        """Spec 8.5: on stale_snapshot, retry the same call once if its target is unchanged."""
        if old["app"] != fresh["app"]:
            return None
        if "id" in call.arguments:
            before, after = tools.element_for(call, old), tools.element_for(call, fresh)
            if before is None or after is None or (before["role"], before["label"]) != (after["role"], after["label"]):
                return None
        try:
            return await self.perform(call, fresh, previous)
        except HelperError as failure:
            return {"ok": False, "error": {"code": failure.code, "message": str(failure)}}

    async def perform(self, call, snapshot: dict, previous_snapshot: dict | None) -> dict:
        args = call.arguments
        if call.name == "say" or call.name == "done":
            await self.say(narrate.clean_speech(args.get("text") or args["summary"]))
            return {"ok": True, "spoken": True}
        if call.name == "read_element":
            text = await self.helper.read(snapshot["snapshot_id"], args["id"])
            await self.say(text)
            return {"ok": True, "text": text[:1500]}
        if call.name == "ask_user":
            reply = await self.ask(narrate.clean_speech(args["question"]))
            return {"ok": bool(reply), "reply": reply, "end_task": not reply or fastpath.classify(reply) == "stop"}
        if call.name == "confirm":
            reply = await self.ask(
                narrate.clean_speech(args["summary"]).rstrip(".") + ". Say yes to continue.", confirm=True,
            )
            command = fastpath.classify(reply or "")
            return {"ok": bool(reply), "reply": reply, "approved": command == "yes",
                    "end_task": not reply or command in {"stop", "no"}}
        if call.name in tools.SCREEN_ACTIONS:
            caption = narrate.action_text(call, snapshot)
            highlight = {"highlight": tools.element_ref(snapshot, args["id"])} if "id" in args else {}
            await self.set_state("acting", caption=caption, **highlight)
            await self.speak(caption, wait=False)
        return await tools.execute(self.helper, call, snapshot, previous_snapshot)
