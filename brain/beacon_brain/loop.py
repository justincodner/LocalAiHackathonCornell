"""Drive one voice task at a time from the Helper's event stream."""

import asyncio
import json
import logging
import re
import time
from dataclasses import replace

import httpx

from . import (browser, commands, compose, fastpath, followup, grounding, locate, narrate, page_summary, questions,
               risk, route, screen_words, tools)
from .config import Config
from .helper_client import HelperError
from .llm import BonsaiError, ToolCall
from .log import append_step
from .memory import Conversation, TaskMemory
from . import drafts  # Mail replies and "send it" on a draft, run in code
from . import question_switch


LOGGER = logging.getLogger(__name__)
TERMINALS = {"say", "done", "read_element"}
QUESTION_PAUSE_S = 60
CONNECTION_ERRORS = (HelperError, httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError)
DECIDE_TOOLS = [tool for tool in tools.TOOL_DEFINITIONS if tool["function"]["name"] in {"click", "set_value"}]
# Focus on one of these means Return would press it, not send the typed message.
PRESSABLE = {"button", "link", "checkbox", "radio", "menuitem", "tab", "row", "cell", "popup", "slider", "menu", "list"}
SCREEN_SUMMARY_TOOLS = [tool for tool in tools.TOOL_DEFINITIONS if tool["function"]["name"] == "say"]


TALK_KEY_NAMES = {"right_command": "Command", "right_cmd": "Command", "right_control": "Control",
                  "right_ctrl": "Control", "right_shift": "Shift", "fn": "Fn", "function": "Fn"}


def answer_hint(ptt_key: str) -> str:
    """How to answer a question. The talk key is push-to-talk: held while speaking (HotkeyMonitor)."""
    return f"Hold the {TALK_KEY_NAMES.get(ptt_key.casefold(), 'Option')} key and say your answer."


HOW_DO_I = re.compile(r"^(?:how|where) (?:do|can|should|would) (?:i|we)\b|^how to\b")


def snapshot_summary(snapshot: dict) -> ToolCall:
    app = snapshot["app"]
    title = snapshot["window"].strip()
    answer = f'The active window in {app} is titled "{title}".' if title else f"{app} is open."
    return ToolCall("screen-summary", "say", {"text": answer})


class Brain:
    def __init__(self, helper, model, config: Config, prompt: str, question_model=None):
        self.helper = helper
        self.model = model  # fast model: actions and multi-step tasks
        self.question_model = question_model  # optional accurate model for questions (route.py)
        self.question_paused_until = 0.0  # after a question-model failure, skip it for a while
        self.question_switch = None  # QuestionModelSwitch, set by main: the HUD's 4B toggle
        self.switching: asyncio.Task | None = None
        self.last_answered_by: str | None = None  # which model answered the latest call, for the log
        self.config = config
        self.prompt = prompt
        self.state = "idle"
        self.active: asyncio.Task | None = None
        self.reply: asyncio.Future | None = None
        self.awaiting_message = False  # asked "What should the message say?"
        self.speech_events: dict[str, asyncio.Future] = {}
        self.finished_speech: dict[str, bool] = {}
        self.screen_generation = 0
        self.task_number = 0
        self.conversation = Conversation(config.memory_tokens, config.memory_turns, config.memory_idle_s)
        self.heard = ""  # the user's own words for the current request, before resolve()
        self.last_speech = ""
        self.rate = 1.0

    async def set_state(self, state: str, **fields) -> None:
        self.state = state
        try:
            await self.helper.hud(state, **fields)
        except (HelperError, httpx.HTTPError):
            LOGGER.debug("HUD update failed", exc_info=True)

    async def switch_question_model(self, enable: bool) -> None:
        """Load or unload the Bonsai 4B for questions without restarting Beacon (the HUD's ⋯ menu)."""
        on = self.question_model is not None
        if enable == on or self.question_switch is None:
            if self.question_switch is None and enable:
                await self.set_state("error", caption="The 4B model can't be switched in this setup.", question_model=False)
            else:
                await self.set_state("idle", question_model=on)
            return
        if not enable:
            client, self.question_model = self.question_model, None
            await self.question_switch.stop(client)
            await self.set_state("idle", caption=question_switch.DISABLED, question_model=False)
            await self.say(question_switch.DISABLED)
            return
        await self.set_state("thinking", caption=question_switch.LOADING, question_model="loading")
        try:
            self.question_model = await self.question_switch.start()
        except (BonsaiError, OSError) as error:
            LOGGER.warning("Could not load the 4B question model: %s", error)
            await self.set_state("error", caption="I couldn't load the 4B model. Staying on the 2B.", question_model=False)
            return
        self.question_paused_until = 0.0
        await self.set_state("idle", caption=question_switch.ENABLED, question_model=True)
        await self.say(question_switch.ENABLED)

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
        elif kind == "model.question" and isinstance(event.get("enabled"), bool):
            if self.switching is None or self.switching.done():
                self.switching = asyncio.create_task(self.switch_question_model(event["enabled"]))
        elif kind == "conversation.reset":
            # The HUD's X on a screenshot: stop whatever is running and start a fresh conversation.
            await self.cancel_active()
            self.conversation.forget()
            await self.set_state("idle")
        elif kind == "listen.started" and self.state == "idle":
            await self.set_state("listening")
        elif kind == "listen.cancelled" and self.state == "listening":
            await self.set_state("idle")
        elif kind == "stt.final" and isinstance(event.get("text"), str) and event["text"].strip():
            text = event["text"].strip()
            focus = questions.selection_focus(event)
            if focus is not None:
                # A question about a screen region the user just selected is always a new request,
                # even while Beacon waits for an answer to something else.
                await self.cancel_active()
                self.task_number += 1
                self.active = asyncio.create_task(self.handle_utterance(text, event.get("stt_ms", 0), focus=focus))
                return
            # A dictated message ("Are you free tonight?") is the answer, not a new question.
            new_question = (self.state != "confirm" and not self.awaiting_message
                            and fastpath.is_question(text))
            if self.reply is not None and not self.reply.done() and not new_question:
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
        if confirm:
            await self.set_state("confirm", caption=question)
            spoken = question
        else:
            # The HUD shows the question in its "asking" look, with its own answer prompt.
            await self.set_state("asking", caption=question)
            spoken = f"{question} {answer_hint(self.config.ptt_key)}"
        if not await self.speak(spoken):
            return None
        reply = asyncio.get_running_loop().create_future()
        self.reply = reply
        self.awaiting_message = question.startswith(compose.ASK_MESSAGE)
        try:
            if confirm:
                await self.helper.listen("start")
            try:
                return await asyncio.wait_for(reply, self.config.reply_timeout_s)
            except TimeoutError:
                if confirm:
                    await self.helper.listen("stop")
                await self.say("I didn't hear a reply, so I stopped.")
                return None
        finally:
            self.reply = None
            self.awaiting_message = False

    async def handle_utterance(self, text: str, stt_ms: int, focus: dict | None = None) -> None:
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
                elif command == "forget":
                    self.conversation.forget()
                    await self.say("Okay. I've forgotten our conversation.")
                elif focus is not None:
                    await self.answer_about_selection(text, focus, stt_ms)
                else:
                    self.heard = text
                    request, context = await self.resolve(text)
                    await self.route_request(request, stt_ms, context=context)
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

    async def resolve(self, text: str) -> tuple[str, str]:
        """Return (the request to carry out, the earlier conversation it still depends on).

        Only follow-ups pay for memory. A follow-up question is rewritten once to stand alone
        ("How old is he?" -> "How old is Emmanuel Macron?"), so routing and the question
        prompts never need the history. A follow-up action keeps its words and gets the
        history in its task prompt instead, where it is cached for every later step.
        """
        if not self.conversation.exchanges or not followup.is_follow_up(text):
            return text, ""
        context = self.conversation.text()
        if not followup.asks(text):
            return text, context
        await self.set_state("thinking", transcript=text)
        try:
            call, _ = await self.think(followup.messages(context, text), followup.REWRITE_TOOLS,
                                       filler=True, task_route=route.ACTION)
        except BonsaiError:
            LOGGER.warning("Could not rewrite a follow-up; answering it with the conversation", exc_info=True)
            return text, context
        rewritten = followup.checked(call, text, context)
        if rewritten is None:
            LOGGER.info("Rejected follow-up rewrite %s", call.arguments)
            return text, context
        LOGGER.info("Follow-up %r -> %r", text, rewritten)
        return rewritten, context

    def remember(self, request: str, reply: str, app: str = "") -> None:
        self.conversation.remember(request, reply, app)

    async def route_request(self, text: str, stt_ms: int, *, context: str = "") -> None:
        # "And his wife?" opens with no question word, but as a follow-up it is still a question.
        question = fastpath.is_question(text) or bool(context and followup.asks(text))
        if (fastpath.wants_screen_summary(text) or fastpath.asks_for_action(text)
                or fastpath.wants_follow_through(text) or not question):
            await self.run_task(text, stt_ms, context=context)
            return
        last = self.conversation.last
        if fastpath.needs_screen(text) or (context and last is not None and last.app):
            # A follow-up to an exchange about the screen ("And the tax?") is about it too.
            await self.run_task(text, stt_ms, screen_question=True, context=context)
            return

        await self.set_state("thinking", transcript=text)
        started = time.perf_counter()
        unresolved = bool(context) and text == self.heard
        messages = [
            {"role": "system", "content": questions.ROUTING_PROMPT},
            # A rewritten follow-up already carries what it needs; an unresolved one gets the history.
            {"role": "user", "content": f"EARLIER CONVERSATION:\n{context}\n\nQUESTION: {text}" if unresolved else text},
        ]
        advisory = fastpath.wants_advice(text)
        route_tools = questions.ROUTE_TOOLS[:1] if advisory else questions.ROUTE_TOOLS
        decision, answered_by = await self.think(messages, SCREEN_SUMMARY_TOOLS + route_tools, filler=True,
                                                task_route=route.QUESTION)
        if decision.name == "act_on_screen" and not advisory and not decision.arguments:
            await self.run_task(text, stt_ms)
            return
        if decision.name == "use_screen" and not decision.arguments:
            await self.run_task(text, stt_ms, screen_question=True)
            return

        def clean_answer(call: ToolCall) -> str:
            if call.name != "say" or tools.validate(call, {}):
                return ""
            answer = narrate.clean_speech(call.arguments["text"])
            return answer if not questions.is_question_only(answer) and (
                fastpath.normalize(answer) != fastpath.normalize(text)) else ""

        answer = clean_answer(decision)
        if advisory and not answer:
            await self.run_task(text, stt_ms, screen_question=True)
            return
        if decision.name == "say" and not answer:
            messages[0]["content"] = questions.GENERAL_PROMPT
            retry, answered_by = await self.think(messages, SCREEN_SUMMARY_TOOLS, filler=False, task_route=route.QUESTION)
            answer = clean_answer(retry)
        model_ms = round((time.perf_counter() - started) * 1000)
        started_speaking = time.perf_counter()
        reply = answer or "I couldn't answer that. Try asking another way."
        await self.say(reply)
        if answer:
            self.remember(text, answer)
        append_step(self.config.log_path, task_id=f"t_{self.task_number}", step=1,
                    utterance=text, heard=self.heard if self.heard != text else None, kind="general", route=route.QUESTION, model=answered_by, snapshot_id=None, snapshot_tokens=0,
                    tool="say", args={"text": reply}, valid=bool(answer), risk="allow", ok=bool(answer),
                    ms={"stt": stt_ms, "snapshot": 0, "llm": model_ms,
                        "act": round((time.perf_counter() - started_speaking) * 1000)})

    async def answer_about_selection(self, text: str, focus: dict, stt_ms: int) -> None:
        """Answer a question about the screen region the user selected. Its text is the primary context."""
        await self.set_state("thinking", transcript=text)
        started = time.perf_counter()
        answered_by = None
        if questions.wants_selection_read(text):
            answer = questions.selection_text(focus)
        else:
            messages = questions.selection_messages(text, focus)
            answer = ""
            for attempt in range(2):
                call, answered_by = await self.think(messages, SCREEN_SUMMARY_TOOLS, filler=attempt == 0,
                                                     task_route=route.QUESTION)
                if call.name == "say" and tools.validate(call, {}) is None:
                    candidate = narrate.clean_speech(call.arguments["text"])
                    if (candidate and not questions.is_question_only(candidate)
                            and fastpath.normalize(candidate) != fastpath.normalize(text)):
                        answer = candidate
                        break
                messages[0]["content"] = f"{questions.SELECTION_PROMPT}\n{questions.SELECTION_RETRY}"
        model_ms = round((time.perf_counter() - started) * 1000)
        started_speaking = time.perf_counter()
        reply = answer or questions.SELECTION_FALLBACK
        await self.say(reply)
        if answer:
            self.remember(text, answer)
        append_step(self.config.log_path, task_id=f"t_{self.task_number}", step=1,
                    utterance=text, kind="selection", route=route.QUESTION, model=answered_by, snapshot_id=None,
                    snapshot_tokens=0, focus_chars=len(questions.selection_text(focus)),
                    tool="say", args={"text": reply}, valid=bool(answer), risk="allow", ok=bool(answer),
                    ms={"stt": stt_ms, "snapshot": 0, "llm": model_ms,
                        "act": round((time.perf_counter() - started_speaking) * 1000)})

    async def run_task(self, text: str, stt_ms: int, *, screen_question: bool = False, context: str = "") -> None:
        screen_summary = fastpath.wants_screen_summary(text)
        memory = TaskMemory(self.prompt, text, "" if screen_summary or screen_question else context)
        available_tools = SCREEN_SUMMARY_TOOLS if screen_summary else tools.TOOL_DEFINITIONS
        task_id = f"t_{self.task_number}"
        generation = self.screen_generation
        if not (screen_question or screen_summary) and commands.plan(text, None) is not None:
            # "Press enter", "next tab": one key or scroll, no model call and no window read.
            if await self.run_command(text, stt_ms):
                return
        if not (screen_question or screen_summary) and commands.app_request(text) is not None:
            # "Open messages", "switch to chrome": open or bring the app to the front.
            if await self.run_open_app(text, stt_ms):
                return
        try:
            snapshot = await self.helper.snapshot()
        except HelperError as error:
            LOGGER.warning("Could not read the active window: %s", error.code)
            await self.say("I couldn't read the current window. Bring the app you want to use to the front and try again.")
            return
        heard, text = text, screen_words.correct(text, snapshot)  # misheard on-screen names
        if text != heard:
            LOGGER.info("Heard %r; on screen that is %r", heard, text)
            append_step(self.config.log_path, task_id=task_id, step=0, kind="heard", utterance=heard, corrected=text)
            memory.utterance = text
        quick = None if screen_question or screen_summary else browser.plan(text, snapshot["app"])
        if quick is not None:
            await self.run_browser_plan(quick, text, snapshot, stt_ms)
            return
        if not (screen_question or screen_summary) and await drafts.run(self, text, snapshot, stt_ms):
            return
        previous_snapshot = None
        task_summary = ""
        consecutive_invalid = 0
        failures = []
        confirmed_screen = None
        actions_taken: list[str] = []  # spoken past tense, for honest done summaries
        committed = False  # a confirm-level action (send, delete, ...) actually ran
        fresh_request = True  # no action has run since the user last spoke (grounding.check)
        last_action = ""  # name of the latest screen action that ran
        last_typed = ""  # text of the latest type_text that ran
        typed_field = None  # id of the field it went into
        sending = fastpath.wants_send(text) and not (screen_question or screen_summary)
        # "Send hello to Emmett", "decide and do it": a say/done before any action is not the end.
        # "Make the best move for me" hands Beacon the choice and the action in one go.
        must_act = not (screen_question or screen_summary) and (
            fastpath.wants_send(text) or fastpath.wants_follow_through(text)
            or (fastpath.wants_decision(text) and not fastpath.is_question(text)))
        early_stops = 0
        decide_first = must_act and fastpath.wants_decision(text)
        said_decision = False
        tried_action = False  # a screen action was attempted, even if it failed
        last_success: tuple | None = None  # (name, arguments) of the latest screen action that worked
        # Route once per task from the user's own words: questions to the accurate model,
        # actions to the fast one. Without a question model everything uses the one model.
        if self.question_model is None:
            task_route = "single"
        else:
            task_route = route.QUESTION if screen_question or screen_summary else route.classify(text)
        for step in range(1, self.config.max_steps + 1):
            if generation != self.screen_generation:
                generation = self.screen_generation
                previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
            await self.set_state("thinking", transcript=memory.latest_words)
            started = time.perf_counter()
            self.last_answered_by = None  # stays None when code, not a model, wrote the answer
            auto_send = None
            if sending and not committed and last_action == "type_text" and last_typed.strip() and (
                    last_typed.strip() == (compose.message_for(text, memory.answers) or "").strip()):
                # The user's own message is typed: send it now instead of asking the model (1 to 15 s)
                # only to hear "done" or, worse, have it click something else first.
                auto_send = self.send_typed(ToolCall(f"send-{step}", "done", {"summary": ""}), last_action,
                                            committed, memory.all_words, snapshot, typed_field)
            elif (sending and not committed and not last_typed and compose.recipient(text)
                  and compose.message_for(text, memory.answers)
                  and compose.chat_open(compose.recipient(text), snapshot)):
                # The named person's conversation is open and the user said the message: type it
                # in its message field now. The model would only be overridden to do the same.
                auto_send = compose.plan(ToolCall(f"compose-{step}", "done", {"summary": ""}), text,
                                         memory.answers, snapshot, strict=True)
            elif sending and not tried_action and not last_typed and (tab := compose.place_tab(text, snapshot)):
                # "Go to Discord and send ...": the Discord tab is right there. Click it without a
                # model call; the 2B model sometimes clicks the wrong tab first.
                auto_send = ToolCall(f"place-{step}", "click", {"id": tab["id"]})
            if auto_send is not None:
                call = auto_send
            elif screen_question:
                call = await self.answer_from_screen(snapshot, memory.request)
            elif decide_first and not tried_action and step <= 2:
                # "Decide and do it": offer only action tools so the model must commit to one option
                # instead of weighing them aloud or asking the user to choose.
                messages = memory.messages(snapshot)
                messages[-1]["content"] += "\nDecide yourself: pick the best option on SCREEN and act on it now."
                call, _ = await self.think(messages, DECIDE_TOOLS, filler=step == 1, task_route=task_route)
            elif screen_summary and snapshot["app"].casefold() in page_summary.BROWSERS:
                call = await self.describe_page(snapshot, memory.request)
            else:
                try:
                    call, _ = await self.think(memory.messages(snapshot), available_tools, filler=step == 1,
                                               task_route=task_route)
                except BonsaiError:
                    if not screen_summary:
                        raise
                    call = snapshot_summary(snapshot)
            if screen_summary and (call.name != "say" or tools.validate(call, snapshot)):
                call = snapshot_summary(snapshot)
            llm_ms = round((time.perf_counter() - started) * 1000)
            if call.stray_text:
                LOGGER.warning("Model wrote %d chars outside its tool call", len(call.stray_text))
            acted_at = time.perf_counter()
            screen = snapshot
            call = tools.normalize(call, snapshot)
            if (call.name in tools.SCREEN_ACTIONS and last_success == (call.name, call.arguments)
                    and not fastpath.wants_repeat(memory.all_words)):
                # The 2B model often repeats the step it just took (types the number again, scrolls
                # or clicks Fold again) instead of calling done: the request is already done.
                LOGGER.info("Repeated %s %s: finishing instead", call.name, call.arguments)
                call = ToolCall(call.id, "done", {"summary": narrate.did(actions_taken)}, call.stray_text)
            if fastpath.wants_send(text) and committed and call.name == "type_text":
                # Already sent: typing again would start a duplicate message.
                call = ToolCall(call.id, "done", {"summary": f"Sent: {last_typed}"}, call.stray_text)
            if sending and not committed:
                if last_action == "type_text":
                    if call.name == "type_text":  # typing the message again: send it instead
                        call = ToolCall(call.id, "done", {"summary": ""}, call.stray_text)
                elif not last_typed and call.name in {"type_text", "ask_user", "done", "say", "confirm", "click"}:
                    # "Go to Discord and send a message to Sam hello": once Sam's conversation is
                    # open (after any steps that got there), type the message into its message
                    # field instead of clicking around or stopping.
                    planned = compose.plan(call, text, memory.answers, snapshot,
                                           strict=tried_action or call.name == "click")
                    if planned is not None:
                        LOGGER.info("Send request: %s %s instead of %s", planned.name, planned.arguments, call.name)
                        call = planned
            send_call = self.send_typed(call, last_action, committed, memory.all_words, snapshot, typed_field)
            if send_call is not None:
                # "Send hello to Emmett": the 2B model types, then stops or asks. Finish the
                # request it was given: click the Send button, or press Return in the field.
                LOGGER.info("Sending the typed message (%s) instead of %s", send_call.name, call.name)
                call = send_call
            if (call.name == "ask_user" and isinstance(call.arguments.get("question"), str)
                    and questions.echoes_request(call.arguments["question"], text)):
                # The small model sometimes "asks" the user's own question back: answer it instead.
                LOGGER.info("ask_user repeated the request; answering it from the screen instead")
                call = await self.answer_from_screen(snapshot, memory.request)
            if call.name == "done" and isinstance(call.arguments.get("summary"), str):
                honest = narrate.honest_summary(call.arguments["summary"], actions_taken, committed)
                if honest != call.arguments["summary"]:
                    LOGGER.warning("Replaced a done summary that claimed an action Beacon did not take")
                    call = replace(call, arguments={**call.arguments, "summary": honest})
            error = tools.validate(call, snapshot)
            if (error is None and call.name == "ask_user" and not (screen_question or screen_summary)
                    and HOW_DO_I.match(fastpath.normalize(str(call.arguments.get("question", ""))))):
                # "How do I access messages in this app?" asks the user how to do what they asked for.
                LOGGER.info("ask_user asked how to do the request; treating it as a failed step")
                error = "Don't ask the user how to do it: take the step yourself with a tool"
            if (error is None and must_act and call.name == "done" and early_stops < 2
                    and (not tried_action or (sending and not last_typed and not committed
                                             and compose.message_for(text, memory.answers)))):
                early_stops += 1
                error = "The request is not finished: take the action the user asked for"
            if (error is None and must_act and not tried_action and call.name == "ask_user"
                    and decide_first and early_stops < 2):
                early_stops += 1
                error = "The user asked you to decide: pick the best option on SCREEN yourself and do it"
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
            was_fresh = fresh_request
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
                    reply = "yes" if already_confirmed or not self.config.confirm_sends else await self.ask(
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
                        generation = self.screen_generation
                        previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
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
                        generation = self.screen_generation
                        previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
            except HelperError as failure:
                result = {"ok": False, "error": {"code": failure.code, "message": str(failure)}}
                if failure.code in {"element_not_found", "action_failed", "stale_snapshot"}:
                    generation = self.screen_generation
                    previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
                if failure.code == "stale_snapshot" and call.name in tools.SCREEN_ACTIONS | {"read_element"}:
                    result = await self.retry_on_fresh_screen(call, screen, snapshot, previous_snapshot) or result

            act_ms = round((time.perf_counter() - acted_at) * 1000)
            tried_action = tried_action or (error is None and call.name in tools.SCREEN_ACTIONS)
            if call.name in tools.SCREEN_ACTIONS or call.name in TERMINALS:
                last_success = (call.name, call.arguments) if result["ok"] else None
            if result["ok"] and call.name in tools.SCREEN_ACTIONS:
                actions_taken.append(narrate.past_action(call, screen))
                last_action = call.name
                if call.name == "type_text":
                    last_typed = call.arguments.get("text", "")
                    typed_field = call.arguments.get("id", screen.get("focused_id"))
                committed = committed or verdict == "confirm" or (
                    call.name == "press_key" and risk.normal_keys(call.arguments["keys"]).split("+")[-1] in {"return", "enter"}
                    and compose.return_sends(screen)
                )  # Return sends in many chat apps, so don't claim "nothing has been sent"
                fresh_request = False
            target = tools.element_for(call, screen) if "id" in call.arguments else None
            finished = self.finished_summary(call, result, text, target, was_fresh=was_fresh,
                                             decide_first=decide_first, committed=committed, typed=last_typed,
                                             screen=screen)
            if result["ok"] and call.name in tools.SCREEN_ACTIONS and not finished:
                # Only a step the model continues from needs the settled, fresh screen.
                await asyncio.sleep(self.config.settle_ms / 1000)
                generation = self.screen_generation
                previous_snapshot, snapshot = snapshot, await self.helper.snapshot()
                if call.name == "type_text":
                    field = tools.element_for(call, snapshot)
                    if field is not None:
                        result["value"] = field.get("value")
            append_step(
                self.config.log_path,
                task_id=task_id, step=step, utterance=text, heard=self.heard if self.heard != text else None,
                kind="screen", snapshot_id=screen["snapshot_id"],
                snapshot_tokens=(len(screen["text"]) + 3) // 4, tool=call.name, args=call.arguments,
                valid=error is None, risk=verdict, ok=result["ok"], stray_chars=len(call.stray_text),
                grounded=grounded or None, route=task_route, model=self.last_answered_by,
                ms={"stt": stt_ms, "snapshot": screen.get("build_ms", 0), "llm": llm_ms, "act": act_ms},
            )
            memory.add(call, result, screen["snapshot_id"], request_for_model,
                       json.dumps(target["label"]) if target else "")
            if finished:
                # The request was this one step: say so instead of asking the model again just
                # to hear "done" (1 to 4 s on the M1, and small models misreport the choice).
                task_summary = finished
                await self.say(task_summary)
                break
            if consecutive_invalid >= 2:
                task_summary = "I couldn't find a valid action for that request."
                await self.say(task_summary)
                break
            if (call.name == "say" and result["ok"] and must_act and not tried_action
                    and not said_decision):
                said_decision = True  # the decision was spoken; now carry it out
                memory.done_so_far.append(f"said {call.arguments['text']!r}")
                continue
            if verdict == "refuse" or result.get("end_task") or (call.name in TERMINALS and result["ok"]):
                break
            if not result["ok"]:
                failures = [*failures[-1:], (call.name, call.arguments)]
            else:
                failures = []
        else:
            task_summary = "I couldn't finish that. Here is where I stopped."
            await self.say(task_summary)
        self.remember(memory.one_line,
                      task_summary or (narrate.did(actions_taken) if actions_taken else "The task stopped."),
                      snapshot["app"])

    async def run_command(self, text: str, stt_ms: int) -> bool:
        """Run a one-step command (commands.py) on the frontmost app; False leaves it to the full loop."""
        try:
            front = await self.helper.frontmost()
        except HelperError as error:
            LOGGER.info("Could not read the frontmost app for a quick command: %s", error.code)
            return False
        plan = commands.plan(text, front["app"])
        if plan is None or any(risk.check(call, front) != "allow" for call in plan.steps):
            return False  # "go back" in Mail, or a key the risk check wants to confirm or refuse
        await self.run_browser_plan(plan, text, front, stt_ms, kind="command")
        return True

    async def run_open_app(self, text: str, stt_ms: int) -> bool:
        """Open or bring forward the app the user named; False when it is not clearly an installed app."""
        spoken, go_to = commands.app_request(text)
        try:
            front = await self.helper.frontmost()
            apps = await self.helper.apps()
        except HelperError as error:
            LOGGER.info("Could not list apps for an open request: %s", error.code)
            return False
        in_browser = front["app"].casefold() in page_summary.BROWSERS
        if go_to and (in_browser or spoken in browser.NOT_SITES):
            return False  # "go to youtube" in Chrome is a website; "go to settings" may mean the page's
        name = commands.match_app(spoken, apps.get("running", []), apps.get("installed", []))
        if name is None:
            return False  # "open youtube", "open the reply": a website or something on the screen
        call = ToolCall("command-1", "open_app", {"name": name})
        if risk.check(call, front) != "allow":
            return False  # the full loop explains why it won't open Terminal or Settings
        if name.casefold() == front["app"].casefold():
            await self.say(f"{name} is already open.")
            self.remember(text, f"{name} is already open.", name)
            return True
        await self.run_browser_plan(browser.Plan([call], f"Opening {name}", f"Opened {name}."), text, front,
                                    stt_ms, kind="command")
        return True

    async def run_browser_plan(self, plan: "browser.Plan", text: str, snapshot: dict, stt_ms: int,
                               *, kind: str = "browser") -> None:
        """Run planned keyboard steps (browser.py, commands.py), no model call."""
        task_id = f"t_{self.task_number}"
        await self.set_state("acting", caption=plan.caption)
        summary = plan.summary
        for step, call in enumerate(plan.steps, 1):
            started = time.perf_counter()
            try:
                result = await tools.execute(self.helper, call, snapshot, None)
            except HelperError as failure:
                result = {"ok": False, "error": {"code": failure.code, "message": str(failure)}}
            append_step(self.config.log_path, task_id=task_id, step=step, utterance=text, kind=kind,
                        route=kind, model=None, snapshot_id=snapshot["snapshot_id"], snapshot_tokens=0,
                        tool=call.name, args=call.arguments, valid=True, risk="allow", ok=bool(result.get("ok")),
                        ms={"stt": stt_ms, "snapshot": snapshot.get("build_ms", 0), "llm": 0,
                            "act": round((time.perf_counter() - started) * 1000)})
            if not result.get("ok"):
                summary = ("I couldn't do that in the browser." if kind == "browser"
                           else "I couldn't do that.") + " Please try again."
                break
            if step < len(plan.steps) and (kind == "browser" or call.name == "scroll"):
                # A new tab or address bar takes a moment to take keyboard focus; a scroll to finish.
                await asyncio.sleep(min(self.config.settle_ms, 300 if kind == "browser" else 150) / 1000)
        await self.say(summary)
        self.remember(text, summary, snapshot.get("app") or "")

    @staticmethod
    def finished_summary(call, result: dict, text: str, target: dict | None, *, was_fresh: bool,
                         decide_first: bool, committed: bool, typed: str, screen: dict | None = None) -> str:
        """What to say when this successful step completes the whole request, else ""."""
        if not result.get("ok") or call.name not in tools.SCREEN_ACTIONS:
            return ""
        label = grounding.spoken_label(target) if target else ""
        if call.name == "click" and (decide_first or (was_fresh and fastpath.single_click(text))):
            return f"I pressed {label or 'that option'}."
        if call.name == "scroll" and was_fresh and fastpath.single_scroll(text):
            return f"I scrolled {call.arguments['direction']}."
        if fastpath.wants_send(text) and committed and typed and call.name in {"click", "press_key"}:
            name = compose.recipient(text)
            name = compose.shown_name(name, screen) if name else name
            body = typed.strip() if typed.strip()[-1:] in {".", "!", "?"} else typed.strip() + "."
            return f"Sent to {name}: {body}" if name else f"Sent: {body}"
        return ""

    @staticmethod
    def send_typed(call, last_action: str, committed: bool, said: str, snapshot: dict,
                   field_id: int | None = None) -> ToolCall | None:
        """After typing for a send request, the call that sends it, or None to keep the model's call."""
        if last_action != "type_text" or committed or not fastpath.wants_send(said):
            return None
        buttons = Brain.send_buttons(snapshot, field_id)
        if call.name == "click" and (tools.element_for(call, snapshot) in buttons
                                     or fastpath.normalize(Brain.label_of(call, snapshot)) in {"send", "send message"}):
            return None  # the model is pressing Send itself
        if call.name not in {"done", "ask_user", "confirm", "say", "click", "scroll"}:
            return None
        # Clicking anything else now ("More" on a message, "Wave") would leave the message unsent.
        if len(buttons) == 1:  # Mail and web composers: Return there only adds a new line
            return ToolCall(call.id, "click", {"id": buttons[0]["id"]}, call.stray_text)
        # Keyboard focus stays in the field we just typed into. Messages often reports no focus
        # after typing, and Chrome may report the page itself, so only hold back when focus is
        # visibly on a control that Return would press (a button, link, row...).
        focused = next((item for item in snapshot["elements"] if item["id"] == snapshot.get("focused_id")), None)
        if focused is None or focused["role"] not in PRESSABLE:
            return ToolCall(call.id, "press_key", {"keys": "return"}, call.stray_text)
        return None

    @staticmethod
    def label_of(call, snapshot: dict) -> str:
        return ((tools.element_for(call, snapshot) or {}).get("label") or "") if "id" in call.arguments else ""

    @staticmethod
    def send_buttons(snapshot: dict, field_id: int | None) -> list[dict]:
        """The composer's send button: one labelled Send, else a Reply or Post after the field.

        A Reply above the field belongs to a message (Discord's hover bar), not the composer.
        """
        enabled = [item for item in snapshot["elements"]
                   if item["role"] == "button" and "disabled" not in item["states"]]
        send = [item for item in enabled if fastpath.normalize(item.get("label") or "") in {"send", "send message"}]
        if send:
            return send
        ids = [item["id"] for item in snapshot["elements"]]
        start = ids.index(field_id) if field_id in ids else 0
        after = {item["id"] for item in snapshot["elements"][start:]}
        return [item for item in enabled if item["id"] in after
                and fastpath.normalize(item.get("label") or "") in {"reply", "post"}]

    async def answer_from_screen(self, snapshot: dict, request: str, *, advisory: bool | None = None) -> ToolCall:
        located = locate.where(snapshot, request)
        if located:  # "Where do I pay?": ring the control and say where it is, no model call
            await self.set_state("speaking", caption=located[0], highlight=tools.element_ref(snapshot, located[1]))
            return ToolCall("screen-locate", "say", {"text": located[0]})
        advisory = fastpath.wants_advice(request) if advisory is None else advisory
        prompt = questions.ADVICE_PROMPT if advisory else questions.SCREEN_PROMPT
        if snapshot["app"].casefold() in page_summary.BROWSERS:
            content = page_summary.visible_content(snapshot, include_display_values=True)
            messages = page_summary.messages(snapshot, content, request, prompt=prompt)
            evidence = list(content)
        else:
            messages = TaskMemory(prompt, request).messages(snapshot)
            evidence = [f"{item.get('label') or ''} {item.get('value') or ''}" for item in snapshot["elements"]]
        allowed_numbers = None if advisory else questions.reference_numbers(request, evidence)
        missing_context = questions.missing_betting_context(request, evidence) if advisory else None
        if missing_context:
            return ToolCall("screen-question", "say", {"text": missing_context})
        fallback = ("I couldn't read enough from the active window to recommend an option. "
                    "Tell me any detail the page doesn't show."
                    if advisory else "I couldn't find that on the current screen.")
        unavailable = ToolCall("screen-question", "say", {"text": fallback})
        if (allowed_numbers is not None and not allowed_numbers) or not any(label.strip() for label in evidence):
            return unavailable
        try:
            for attempt in range(2):
                answer, _ = await self.think(messages, SCREEN_SUMMARY_TOOLS, filler=attempt == 0,
                                             task_route=route.QUESTION)
                if answer.name == "say" and tools.validate(answer, {}) is None:
                    text = answer.arguments["text"].strip()
                    if (text and not questions.is_question_only(text)
                            and fastpath.normalize(text) != fastpath.normalize(request)):
                        if allowed_numbers is None or questions.numbers(text) <= allowed_numbers:
                            return answer
                        break
                messages[0]["content"] = f"{prompt}\n{questions.SCREEN_RETRY}"
        except BonsaiError:
            LOGGER.warning("Could not answer from the active screen", exc_info=True)
        return unavailable

    async def describe_page(self, snapshot: dict, request: str) -> ToolCall:
        content = page_summary.visible_content(snapshot)
        if content:
            try:
                call, _ = await self.think(page_summary.messages(snapshot, content, request), SCREEN_SUMMARY_TOOLS,
                                           filler=True, task_route=route.QUESTION)
            except BonsaiError:
                LOGGER.warning("Using the available page content after Bonsai failed", exc_info=True)
            else:
                if (call.name == "say" and not tools.validate(call, snapshot)
                        and page_summary.mentions_content(call.arguments["text"], snapshot, content)):
                    return call
        return ToolCall("page-summary", "say", {"text": page_summary.fallback(snapshot, content)})

    async def ask_model(self, messages: list[dict], available_tools: list[dict], task_route: str):
        """Return (tool call, which model answered). The question model falls back to the fast one."""
        if (task_route == route.QUESTION and self.question_model is not None
                and time.monotonic() >= self.question_paused_until):
            try:
                call = await self.question_model.next_tool_call(messages, available_tools)
                self.last_answered_by = "question"
                return call, "question"
            except BonsaiError as error:
                # A busy or hung 4B must not cost every later step its timeout again: pause it for
                # longer than one task can run (45 s), then try it again.
                self.question_paused_until = time.monotonic() + QUESTION_PAUSE_S
                LOGGER.warning("Question model failed (%s); using the fast model for %d s", error, QUESTION_PAUSE_S)
        call = await self.model.next_tool_call(messages, available_tools)
        self.last_answered_by = "fast"
        return call, "fast"

    async def think(self, messages: list[dict], available_tools: list[dict], *, filler: bool,
                    task_route: str = "single"):
        """Ask the model, saying "One moment." if it takes longer than the filler delay (spec 10.2)."""
        if not filler or self.config.thinking_filler_ms <= 0:
            return await self.ask_model(messages, available_tools, task_route)

        async def say_filler():
            await asyncio.sleep(self.config.thinking_filler_ms / 1000)
            try:
                await self.helper.speak("One moment.", self.rate)
            except CONNECTION_ERRORS:
                LOGGER.debug("Filler speech failed", exc_info=True)

        timer = asyncio.create_task(say_filler())
        try:
            return await self.ask_model(messages, available_tools, task_route)
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
            text = await locate.read_aloud(self, snapshot, args["id"])
            return {"ok": True, "text": text[:1500]}
        if call.name == "ask_user":
            reply = await self.ask(narrate.clean_speech(args["question"]))
            return {"ok": bool(reply), "reply": reply, "end_task": not reply or fastpath.classify(reply) == "stop"}
        if call.name == "confirm" and not self.config.confirm_sends:
            return {"ok": True, "approved": True}  # the user's request is the approval
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
