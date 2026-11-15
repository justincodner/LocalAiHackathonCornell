"""Drive one voice task at a time from the Helper's event stream."""

import asyncio
import json
import logging
import re
import time
from dataclasses import replace

import httpx

from . import (browser, chats, color_filter, commands, compose, document, fastpath, followup, grounding, locate,
               messaging, narrate, page_summary, questions, risk, route, screen_words, steps, tools)
from .config import Config
from .helper_client import HelperError
from .llm import BonsaiError, ToolCall, has_tool_markup, strip_tool_markup
from .log import append_step
from .memory import Conversation, TaskMemory
from . import drafts  # Mail replies and "send it" on a draft, run in code
from . import question_switch


LOGGER = logging.getLogger(__name__)
TERMINALS = {"say", "done", "read_element"}
QUESTION_PAUSE_S = 60
GLIDE_TICK_S = 0.05  # run_glide: one small scroll step per tick (20 a second)
CONNECTION_ERRORS = (HelperError, httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError)
DECIDE_TOOLS = [tool for tool in tools.TOOL_DEFINITIONS if tool["function"]["name"] in {"click", "set_value"}]
FIND_FIELD_TOOLS = [tool for tool in tools.TOOL_DEFINITIONS if tool["function"]["name"] == "click"]  # steps.py
# Focus on one of these means Return would press it, not send the typed message.
PRESSABLE = {"button", "link", "checkbox", "radio", "menuitem", "tab", "row", "cell", "popup", "slider", "menu", "list"}
SCREEN_SUMMARY_TOOLS = [tool for tool in tools.TOOL_DEFINITIONS if tool["function"]["name"] == "say"]
DOCUMENT_TOOLS = SCREEN_SUMMARY_TOOLS + document.TOOLS
MESSAGE_TOOLS = [tool for tool in tools.TOOL_DEFINITIONS if tool["function"]["name"] == "type_text"]  # messaging.py


def prompt_families(prompt: str) -> list[tuple[str, list[dict]]]:
    """The fixed system prompt + tool list prefixes the Brain sends, most used first.

    Bonsai 2 is a hybrid model: llama-server can reuse a cached prefix only up to a saved
    checkpoint, and a prefix gets one where it ends only if it was prewarmed on its own.
    Any family left out re-reads its whole prompt on every request (a "What's on my screen?"
    step took 19 s that way on the Windows laptop).
    """
    return [
        (prompt, tools.TOOL_DEFINITIONS),  # actions
        (prompt, SCREEN_SUMMARY_TOOLS),  # "What's on my screen?" outside a browser
        (questions.ROUTING_PROMPT, SCREEN_SUMMARY_TOOLS + questions.ROUTE_TOOLS),  # questions
        (questions.SCREEN_PROMPT, SCREEN_SUMMARY_TOOLS),  # questions about the screen
        (questions.ADVICE_PROMPT, SCREEN_SUMMARY_TOOLS),  # "What should I do?" about the screen
        (page_summary.PROMPT, SCREEN_SUMMARY_TOOLS),  # "What's on my screen?" in a browser
        (document.PROMPT, DOCUMENT_TOOLS),
        (followup.PROMPT, followup.REWRITE_TOOLS),  # follow-up rewrites
        (prompt, FIND_FIELD_TOOLS),  # "which field is the search bar?" (steps.py)
    ]


TALK_KEY_NAMES = {"right_command": "Command", "right_cmd": "Command", "right_control": "Control",
                  "right_ctrl": "Control", "right_shift": "Shift", "fn": "Fn", "function": "Fn"}


def answer_hint(ptt_key: str) -> str:
    """How to answer a question. The talk key is push-to-talk: held while speaking (HotkeyMonitor)."""
    return f"Hold the {TALK_KEY_NAMES.get(ptt_key.casefold(), 'Option')} key and say your answer."


HOW_DO_I = re.compile(r"^(?:how|where) (?:do|can|should|would) (?:i|we)\b|^how to\b")


STOP_WORDS = frozenset("the a an to and or of in on for my me this that it is please can you could would i".split())


def unrelated_click(call, snapshot: dict, said: str) -> bool:
    element = tools.element_for(call, snapshot) or {}
    label = re.findall(r"[a-z0-9]+", (element.get("label") or "").casefold())
    if len(label) < 5:
        return False  # short labels ("Raise", "Pay now") are judged by grounding
    wanted = set(re.findall(r"[a-z0-9]+", said.casefold())) - STOP_WORDS
    return bool(wanted) and not any(word in wanted or any(w.startswith(word[:4]) for w in wanted if len(w) >= 4)
                                    for word in label if word not in STOP_WORDS and len(word) >= 3)


def snapshot_summary(snapshot: dict) -> ToolCall:
    app = snapshot["app"]
    title = snapshot["window"].strip()
    answer = f'The active window in {app} is titled "{title}".' if title else f"{app} is open."
    return ToolCall("screen-summary", "say", {"text": answer})


def screen_evidence(snapshot: dict) -> list[str]:
    """Every label and value in a snapshot, with its window and app: what an answer may name."""
    return [snapshot.get("window") or "", snapshot.get("app") or "", *(
        f"{item.get('label') or ''} {item.get('value') or ''}" for item in snapshot.get("elements", []))]


def spoken_form(text: str) -> str:
    """Speech and captions never carry tool-call syntax the model wrote as text."""
    if not has_tool_markup(text):
        return text
    LOGGER.warning("Removed tool-call syntax from speech")
    return strip_tool_markup(text) or "Sorry, I didn't get that. Try asking again."


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
        self.task_deadline: asyncio.Timeout | None = None  # the running task's timeout (handle_utterance)
        self.task_started = 0.0
        self.prompt = prompt
        self.state = "idle"
        self.active: asyncio.Task | None = None
        self.scrolling = False  # a continuous scroll (run_glide) is running: the talk key stops it
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
        self.reading: tuple[document.Document, int] | None = None
        self.color_filter = color_filter.Controller(helper)  # macOS Color Filters, run in code

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
        message = question_switch.enabled_message()
        await self.set_state("idle", caption=message, question_model=True)
        await self.say(message)

    async def cancel_active(self) -> None:
        if self.active is None or self.active.done():
            return
        self.active.cancel()
        await asyncio.gather(self.active, return_exceptions=True)

    async def on_event(self, event: dict) -> None:
        kind = event["type"]
        if kind == "app.changed":
            self.screen_generation += 1
            self.reading = None
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
            self.reading = None
            await self.set_state("idle")
        elif kind == "listen.started":
            if self.scrolling:
                await self.cancel_active()  # the talk key stops "scroll through the results"
            if self.state == "idle":
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
                            and (fastpath.is_question(text) or document.intent(text) is not None))
            # "Open a new tab and search for amazon.com" while Beacon waits on its own question is a
            # new request, not the answer: it would steer the old task somewhere unrelated.
            new_command = (self.state != "confirm" and not self.awaiting_message and self.new_command(text))
            if self.reply is not None and not self.reply.done() and not (new_question or new_command):
                self.reply.set_result(text)
                return
            await self.cancel_active()
            self.task_number += 1
            self.active = asyncio.create_task(self.handle_utterance(text, event.get("stt_ms", 0)))

    def new_command(self, text: str) -> bool:
        """A complete request of its own (one the fast paths know), rather than a short answer."""
        if len(text.split()) < 3:
            return False  # "Fold", "the blue one", "yes please"
        return bool(commands.plan(text, None) or commands.continuous_scroll(text) or browser.plan(text, "google chrome") or chats.parse(text)
                    or color_filter.parse(text) is not None
                    or commands.app_request(text) and len(text.split()) <= 6 and not fastpath.classify(text))

    async def speak(self, text: str, *, wait: bool = True) -> bool:
        text = spoken_form(text)
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
        text = spoken_form(text)
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
        reading_intent = document.intent(text) if focus is None else None
        timeout = self.config.task_timeout_s
        if reading_intent in {"read", "continue"}:
            timeout = max(timeout, 150)  # a full passage can take longer to speak than an action task
        try:
            self.task_started = asyncio.get_running_loop().time()
            async with asyncio.timeout(timeout) as self.task_deadline:
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
                    self.reading = None
                    await self.say("Okay. I've forgotten our conversation.")
                elif focus is None and commands.clock_answer(text):
                    # "What time is it?": the menu-bar clock is outside the window snapshot.
                    answer = commands.clock_answer(text)
                    await self.say(answer)
                    self.remember(text, answer)
                elif focus is None and (color := color_filter.parse(
                        text, recent=self.color_filter.recent(self.task_number))) is not None:
                    await self.run_color_filter(color, text, stt_ms)
                elif focus is not None:
                    await self.answer_about_selection(text, focus, stt_ms)
                elif reading_intent in {"read", "continue"}:
                    await self.read_document(text, stt_ms, resume=reading_intent == "continue")
                else:
                    self.heard = text
                    request, context = await self.resolve(text)
                    await self.route_request(request, stt_ms, context=context)
        except (TimeoutError, BonsaiError) as error:
            LOGGER.warning("Task ended: %s", error or type(error).__name__)
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
            self.task_deadline = None
            await self.set_state("idle")

    def keep_going(self) -> None:
        """A task step finished: give the next one a fresh task_timeout_s, up to task_max_s in all."""
        if self.task_deadline is None:
            return
        now = asyncio.get_running_loop().time()
        deadline = min(now + self.config.task_timeout_s,
                       self.task_started + max(self.config.task_max_s, self.config.task_timeout_s))
        current = self.task_deadline.when()
        if current is None or deadline > current:  # never cut short a longer window, such as reading a page
            self.task_deadline.reschedule(deadline)

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

    async def mentions_screen(self, text: str) -> bool:
        """Whether a word of the question is in the front window (one quick window read)."""
        try:
            snapshot = await self.helper.snapshot()
        except (HelperError, *CONNECTION_ERRORS):
            return False
        shown = [snapshot.get("window") or ""] + [
            f"{item.get('label') or ''} {item.get('value') or ''}" for item in snapshot.get("elements", [])]
        return questions.overlaps_screen(text, shown)

    async def friend_question_in_chat(self, text: str) -> bool:
        intent = messaging.parse(text)
        if intent is None or not intent.bare or messaging.bare_kind(intent) != "ask":
            return False
        try:
            front = await self.helper.frontmost()
        except (HelperError, *CONNECTION_ERRORS):
            return False
        return chats.in_chat_app(front)

    def remember(self, request: str, reply: str, app: str = "") -> None:
        self.conversation.remember(request, reply, app)

    async def route_request(self, text: str, stt_ms: int, *, context: str = "") -> None:
        if document.intent(text) == "lookup":
            await self.run_task(text, stt_ms, screen_question=True, context=context)
            return
        # "And his wife?" opens with no question word, but as a follow-up it is still a question.
        question = fastpath.is_question(text) or bool(context and followup.asks(text))
        # "Send to this chat, did you find it?" ends in a question mark but asks for a send.
        sends_message = ((fastpath.wants_send(text) and bool(compose.command_and_content(text)[1])) or messaging.explicit(text)
                         or messaging.wants_message_extraction(text))
        if question and not sends_message and await self.friend_question_in_chat(text):
            # "Are you coming tonight?" with a chat open is likely words for the chat: ask before sending.
            sends_message = True
        if (fastpath.wants_screen_summary(text) or fastpath.asks_for_action(text)
                or fastpath.wants_follow_through(text) or sends_message or not question):
            await self.run_task(text, stt_ms, context=context)
            return
        last = self.conversation.last
        if fastpath.needs_screen(text) or (context and last is not None and last.app):
            # A follow-up to an exchange about the screen ("And the tax?") is about it too.
            await self.run_task(text, stt_ms, screen_question=True, context=context)
            return
        if questions.looks_up_screen(text) or (questions.about_user_or_now(text) and await self.mentions_screen(text)):
            # "Is there anything about space?", "Do I need to buy coffee?" (coffee is in the open
            # note): the answer is on screen. The router model tends to answer these from general
            # knowledge instead.
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
        # Advice offers the same tools as any question so it reuses the prewarmed routing family;
        # act_on_screen on advice falls through to a screen answer below, never an action.
        decision, answered_by = await self.think(messages, SCREEN_SUMMARY_TOOLS + questions.ROUTE_TOOLS,
                                                filler=True, task_route=route.QUESTION)
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
        glide = None if screen_question or screen_summary else commands.continuous_scroll(text)
        if glide is not None:
            # "Scroll through the results", "keep scrolling": steady small scrolls until stopped.
            await self.run_glide(glide, text, stt_ms)
            return
        if not (screen_question or screen_summary) and commands.plan(text, None) is not None:
            # "Press enter", "next tab": one key or scroll, no model call and no window read.
            if await self.run_command(text, stt_ms):
                return
        chat = None if screen_question or screen_summary else chats.parse(text)
        if chat is not None:
            # "Switch to the poker chat", "send hello to the poker chat": from any app, in code.
            await self.run_chat(chat, text, stt_ms)
            return
        if not (screen_question or screen_summary) and commands.app_request(text) is not None:
            # "Open messages", "switch to chrome": open or bring the app to the front.
            if await self.run_open_app(text, stt_ms):
                return
        bare = None if screen_question or screen_summary or chat else chats.bare_switch(text)
        if bare is not None:
            # "Switch to Clav" with Messages in front: the chat, through the same search path.
            try:
                front = await self.helper.frontmost()
            except HelperError:
                front = {}
            if chats.in_chat_app(front):
                await self.run_chat(bare, text, stt_ms)
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
        if not (screen_question or screen_summary) and await self.run_message(heard, text, snapshot, stt_ms):
            # "Tell him I'm running late", "reply sounds good", or a dictated sentence with a chat
            # open: who and what are read in code, then focus, type, Return and check in code.
            return
        button = None if (screen_question or screen_summary or fastpath.wants_send(text)
                          or fastpath.is_question(text)) else commands.named_button(text, snapshot, bare=True)
        if button is not None:
            click = ToolCall("command-1", "click", {"id": button["id"]})
            if risk.check(click, snapshot) == "allow":
                # "Fold", "press call": the one control the words name, with no model call.
                label = (button.get("label") or "").strip()
                await self.run_browser_plan(browser.Plan([click], f"Pressing {label}", f"I pressed {label}."), text,
                                            snapshot, stt_ms, kind="command")
                return
        if not (screen_question or screen_summary) and await steps.run(
                self, text, snapshot, stt_ms, task_route=route.classify(text) if self.question_model else "single"):
            # "In the Amazon search bar search for X": find the field (code, else the model), then
            # type, Return and check the page in code. The model never has to press Return.
            return
        quick = None if screen_question or screen_summary else (
            browser.named_tab(text, snapshot) or browser.plan(text, snapshot["app"]))
        if quick is not None:
            await self.run_browser_plan(quick, text, snapshot, stt_ms)
            return
        elsewhere = None if screen_question or screen_summary else chats.person_send(text, snapshot)
        if elsewhere is not None:
            await self.run_chat(elsewhere, text, stt_ms)
            return
        here = None if screen_question or screen_summary else chats.here_send(text, snapshot)
        if here is not None:
            # "Send to this chat, did you find it?": the open chat's box, typed and sent in code.
            await self.send_in_chat(snapshot, here, text, f"t_{self.task_number}", time.perf_counter(), stt_ms)
            return
        if not (screen_question or screen_summary) and await drafts.run(self, text, snapshot, stt_ms):
            return
        previous_snapshot = None
        task_summary = ""
        consecutive_invalid = 0
        answers_used = 0  # answers to ask_user already checked for a button they name ("Fold")
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
            if step > 1:
                self.keep_going()
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
            if len(memory.answers) > answers_used:
                answers_used = len(memory.answers)
                named = commands.named_button(memory.latest_words, snapshot)
                if named is not None and (call.name, call.arguments.get("id")) != ("click", named["id"]):
                    # "Fold" answering "Fold or raise?": press exactly that, whatever the model picked.
                    LOGGER.info("The answer names %r: clicking it instead of %s", named.get("label"), call.name)
                    call = ToolCall(call.id, "click", {"id": named["id"]}, call.stray_text)
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
            if (last_action == "type_text" and not sending and call.name in {"confirm", "done", "ask_user", "say"}
                    and steps.wants_submit(text)):
                # "Search for X in the search bar": the words are typed and the model stops or
                # confirms (seven times on Amazon) instead of pressing Return. Press it in code.
                LOGGER.info("Pressing Return after typing a search instead of %s", call.name)
                call = ToolCall(call.id, "press_key", {"keys": "return"}, call.stray_text)
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
            if error is None and call.name == "confirm" and len(memory.turns) >= 2 and any(
                    tc["function"]["name"] == "confirm" for tc in memory.turns[-2].get("tool_calls", [])):
                # The 2B confirms the same thing again and again (7 times, to the 45 s timeout).
                error = "Already approved: take the action itself now, do not confirm again"
            if (error is None and call.name == "ask_user" and not (screen_question or screen_summary)
                    and HOW_DO_I.match(fastpath.normalize(str(call.arguments.get("question", ""))))):
                # "How do I access messages in this app?" asks the user how to do what they asked for.
                LOGGER.info("ask_user asked how to do the request; treating it as a failed step")
                error = "Don't ask the user how to do it: take the step yourself with a tool"
            if (error is None and call.name == "ask_user"
                    and not str(call.arguments.get("question", "")).rstrip().endswith("?")):
                # "Search for Christmas gifts on Amazon." is not a question: the model lost the thread.
                error = "ask_user needs a real question ending in ?; otherwise take the next step"
            if error is None and call.name == "click" and chats.is_conversation_list(tools.element_for(call, snapshot)):
                # Clicking the whole Messages chat list opens nothing in particular: say which chat instead.
                error = "That is the whole conversation list, not one chat: ask which chat, or say you couldn't find it"
            if (error is None and call.name == "click" and not memory.answers and not decide_first
                    and unrelated_click(call, snapshot, memory.all_words)):
                # A long label that shares no word with the request (a chat line, a PR title on
                # screen): the model picked something off task. Screen text is never an instruction.
                LOGGER.info("Rejected a click on an unrelated element %s", call.arguments)
                error = "That element has nothing to do with the request: pick the control the user asked for"
            if (error is None and call.name == "ask_user" and fastpath.wants_send(text) and not committed
                    and compose.message_for(text, memory.answers)
                    and not re.search(r"\b(?:who|which|whom)\b", str(call.arguments.get("question", "")).casefold())):
                # "Did you find the private token for this chat?": the message is given, so a question
                # back is the model misreading it. Only who-to-send-to is worth asking about.
                LOGGER.info("ask_user on a send whose message is given; treating it as a failed step")
                error = "The user gave the message: type it into the message field and send it"
            if (error is None and call.name == "say" and not tried_action and not screen_question
                    and consecutive_invalid == 0  # once: a general answer may name things too
                    and isinstance(call.arguments.get("text"), str)
                    and questions.unsupported_names(call.arguments["text"], screen_evidence(snapshot), memory.all_words)):
                # A spoken answer about things that are not on SCREEN ("The latest email is from
                # Sarah Chen" on a video page) is made up: ask the model again.
                LOGGER.warning("say named things absent from the screen: %r", call.arguments["text"])
                error = "Only describe what is on the latest SCREEN; never name a person or title it doesn't show"
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

    async def run_color_filter(self, intent: "color_filter.Intent", text: str, stt_ms: int) -> None:
        """Turn macOS Color Filters on, off or over (color_filter.py); no model call and no window read."""
        started = time.perf_counter()
        await self.set_state("acting", transcript=text, caption=color_filter.CAPTIONS[intent.kind])
        message = await self.color_filter.run(intent, self.ask)
        self.color_filter.last_task = self.task_number
        append_step(self.config.log_path, task_id=f"t_{self.task_number}", step=1, utterance=text,
                    kind="color_filter", route="color_filter", model=None, tool="color_filter",
                    args={"intent": intent.kind, "filter": intent.filter, "applied": self.color_filter.applied},
                    valid=True, risk="allow", ok=not self.color_filter.failed,
                    ms={"stt": stt_ms, "snapshot": 0, "llm": 0, "act": round((time.perf_counter() - started) * 1000)})
        await self.say(message)
        self.remember(text, message)

    async def run_message(self, heard: str, text: str, snapshot: dict, stt_ms: int) -> bool:
        """Send a chat message read in code (messaging.py). False leaves the request to the other paths."""
        intent = messaging.parse(text)
        spoken = messaging.parse(heard) if heard != text else intent
        if intent is not None and spoken is not None and spoken.body:
            intent = replace(intent, body=spoken.body)  # the message as heard, never respelled to screen words
        field = compose.message_field(snapshot)
        chat_here = field is not None and compose.return_sends(snapshot)
        if intent is None and messaging.wants_message_extraction(text) and (chat_here or messaging.named(text)):
            # "Could you text David about being late": a send, but the message needs the model.
            await self.set_state("thinking", transcript=text)
            try:
                call, _ = await self.think(messaging.extract_messages(heard), MESSAGE_TOOLS, filler=True,
                                           task_route="single" if self.question_model is None else route.ACTION)
            except BonsaiError:
                call = None
            body = messaging.checked_body(call, heard)
            LOGGER.info("Message extracted by the model: %r", body)
            # Not usable: ask "What should the message say?" (send_in_chat does) rather than guess.
            intent = messaging.Intent(body, messaging.named(text))
            if intent.recipient is None and not chat_here:
                return False
        elif intent is None or not intent.body:
            return False
        draft = (field.get("value") or "").strip() if field else ""
        title = messaging.chat_title(snapshot)
        if intent.body is None and not intent.recipient and chat_here and not draft:
            await self.send_in_chat(snapshot, chats.Request(title or "this chat", snapshot.get("app"), None, True),
                                    text, f"t_{self.task_number}", time.perf_counter(), stt_ms)
            return True
        if intent.misheard and chat_here and not messaging.is_open_chat(intent.recipient, title):
            # "Send the collab, I'm going ...": probably "send to Clav" misheard. Ask once rather than
            # search for a chat called "collab".
            intent = replace(intent, recipient=None, bare=True, question=False)
            kind = "ask"
        else:
            kind = messaging.bare_kind(intent) if intent.bare else "send"
        if intent.bare:
            if not (chat_here and chats.in_chat_app(snapshot)) or draft:
                return False
            if kind == "skip":
                return False
            if kind == "ask":
                reply = await self.ask(f"Should I send {intent.body.rstrip('.')} to {title or 'this chat'}? "
                                       "Say yes to continue.", confirm=True)
                if fastpath.classify(reply or "") != "yes":
                    await self.say("Okay, I didn't send it.")
                    return True
            await self.send_in_chat(snapshot, chats.Request(title or "this chat", snapshot.get("app"), intent.body, True),
                                    text, f"t_{self.task_number}", time.perf_counter(), stt_ms, confirmed=kind == "ask")
            return True
        if intent.recipient is None or (chat_here and messaging.is_open_chat(intent.recipient, title)):
            if not chat_here or draft:
                return False  # no chat box to type into, or a draft is already there ("send it" sends that)
            await self.send_in_chat(snapshot, chats.Request(title or "this chat", snapshot.get("app"), intent.body, True),
                                    text, f"t_{self.task_number}", time.perf_counter(), stt_ms)
            return True
        if not compose.return_sends(snapshot) or (chat_here and (snapshot.get("app") or "").casefold() != "messages"):
            return False  # Mail (drafts.py), or a Discord/Slack DM: the model clicks the person in the sidebar
        # Someone else's chat: open it (chats.Switcher), then type and send there.
        await self.run_chat(chats.Request(intent.recipient, None, intent.body, True), text, stt_ms)
        return True

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

    async def run_glide(self, glide: "commands.Glide", text: str, stt_ms: int) -> None:
        """Scroll the front window steadily until "stop", the talk key, or config.scroll_max_s.

        Small pixel steps every GLIDE_TICK_S, paced by the clock so a slow Helper call never
        slows the scroll down. Any new request or the talk key cancels this task (on_event).
        """
        task_id = f"t_{self.task_number}"
        started = time.perf_counter()
        sent, ok, stopped = 0, True, False
        await self.set_state("acting", caption=f"Scrolling {glide.direction}. Press the talk key to stop.")
        self.scrolling = True
        try:
            while (elapsed := time.perf_counter() - started) < self.config.scroll_max_s:
                due = int(glide.pixels_per_second * elapsed) - sent
                if due >= 1:
                    try:
                        await self.helper.request("/input/scroll", {"direction": glide.direction, "amount": due})
                    except HelperError as error:
                        LOGGER.warning("Continuous scroll failed: %s", error.code)
                        ok = False
                        break
                    sent += due
                self.keep_going()
                await asyncio.sleep(GLIDE_TICK_S)
        except asyncio.CancelledError:
            stopped = True
            raise
        finally:
            self.scrolling = False
            append_step(self.config.log_path, task_id=task_id, step=1, utterance=text, kind="command",
                        route="command", model=None, tool="scroll", valid=True, risk="allow", ok=ok,
                        args={"direction": glide.direction, "speed": glide.speed, "pixels": sent,
                              "stopped": stopped},
                        ms={"stt": stt_ms, "snapshot": 0, "llm": 0,
                            "act": round((time.perf_counter() - started) * 1000)})
        if not ok:
            await self.say("I couldn't scroll this window.")
            return
        await self.say("Done scrolling.")
        self.remember(text, f"Scrolled {glide.direction} continuously.")

    async def run_chat(self, chat: "chats.Request", text: str, stt_ms: int) -> None:
        """Open the named chat (chats.py), then type and send the message when there is one."""
        task_id = f"t_{self.task_number}"
        started = time.perf_counter()
        try:
            front = await self.helper.frontmost()
        except HelperError:
            front = {"app": "", "window": ""}
        app = chats.chat_app(chat, front)
        await self.set_state("acting", caption=f"Opening the {chat.name} chat")
        switcher = chats.Switcher(self.helper, min(self.config.settle_ms, 300) / 1000, ask=self.ask)
        try:
            snapshot, shown = await switcher.open(app, chat.name, front)
        except HelperError as failure:
            LOGGER.warning("Chat switch failed: %s", failure.code)
            snapshot, shown = None, f"I couldn't open the {chat.name} chat."
        append_step(self.config.log_path, task_id=task_id, step=1, utterance=text, kind="chat", route="chat",
                    model=None, tool="open_chat", args={"app": app, "name": chat.name}, valid=True, risk="allow",
                    ok=snapshot is not None, ms={"stt": stt_ms, "snapshot": 0, "llm": 0,
                                                 "act": round((time.perf_counter() - started) * 1000)})
        if snapshot is None:
            await self.say(shown)
            self.remember(text, shown, app)
            return
        if not chat.send:
            summary = f"Opened the {shown} chat."
            await self.say(summary)
            self.remember(text, summary, app)
            return
        await self.send_in_chat(snapshot, chats.Request(shown, app, chat.message, True), text, task_id, started,
                                stt_ms)


    async def send_in_chat(self, snapshot: dict, chat: "chats.Request", text: str, task_id: str, started: float,
                           stt_ms: int, *, confirmed: bool = False) -> None:
        """Type the message into the open chat's box and press Return (asks for it when missing)."""
        shown, app = chat.name, chat.app
        message = chat.message
        if message is None:
            message = await self.ask(compose.ASK_MESSAGE)
            if not message or fastpath.classify(message) in {"stop", "no"}:
                return
        if self.config.confirm_sends and not confirmed:
            reply = await self.ask(f"Send {message} to {shown}?", confirm=True)
            if fastpath.classify(reply or "") != "yes":
                await self.say("Okay, I didn't send it.")
                return
        await self.set_state("acting", caption=f"Sending to {shown}" if shown != "this chat" else "Sending")
        sent = await self.type_and_send(snapshot, message)
        append_step(self.config.log_path, task_id=task_id, step=2, utterance=text, kind="chat", route="chat",
                    model=None, tool="send", args={"app": app, "name": shown, "text": message}, valid=True, risk="allow",
                    ok=sent, ms={"stt": stt_ms, "snapshot": 0, "llm": 0,
                                 "act": round((time.perf_counter() - started) * 1000)})
        if not sent:
            summary = f"I typed your message to {shown} but it didn't send. Press Return to send it."
            await self.say(summary)
            self.remember(text, summary, app)
            return
        summary = f"Sent: {message}." if shown == "this chat" else f"Sent to {shown}: {message}."
        await self.say(summary)
        self.remember(text, summary, app)

    async def type_and_send(self, snapshot: dict, message: str) -> bool:
        """Focus the message box, type, check it landed, press Return, check the box emptied.

        False only when the message is still sitting in the box after two Returns.
        """
        settle = min(self.config.settle_ms, 300) / 1000
        field = compose.message_field(snapshot)
        if field is not None and field["id"] != snapshot.get("focused_id"):
            await self.helper.request("/ax/act", {**tools.element_ref(snapshot, field["id"]), "action": "focus"})
        await self.helper.request("/input/type", {"text": message})
        for attempt in range(2):
            await self.helper.request("/input/key", {"keys": "return"})
            after = await self.box(settle)
            if after is None or message.strip() not in (after.get("value") or ""):
                return True
            LOGGER.warning("The message is still in the box after Return (attempt %d)", attempt + 1)
        return False

    async def box(self, settle: float) -> dict | None:
        """The message box on a fresh read of the window (None if it can't be read)."""
        if settle > 0:
            await asyncio.sleep(settle)
        try:
            self.last_box = await self.helper.snapshot()
        except HelperError:
            return None
        return compose.message_field(self.last_box)

    async def run_open_app(self, text: str, stt_ms: int) -> bool:
        """Open or bring forward the app the user named; False when it is not clearly an installed app."""
        spoken, go_to = commands.app_request(text)
        try:
            front = await self.helper.frontmost()
        except HelperError as error:
            if go_to or error.code != "element_not_found":
                LOGGER.info("Could not identify the front app for an open request: %s", error.code)
                return False
            # An explicit app launch can proceed when there is no focused window.
            front = {"app": "", "elements": [], "snapshot_id": None}
        try:
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
        if (call.name == "press_key" and call.arguments.get("keys") == "return" and typed.strip()
                and not fastpath.wants_send(text) and steps.wants_submit(text)):
            return f"I searched for {typed.strip()}."  # no extra model call just to hear "done"
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
        is_browser = snapshot["app"].casefold() in page_summary.BROWSERS
        refers_to_document = (document.DOCUMENT_REFERENCE.search(fastpath.normalize(request))
                              or document.intent(request) == "lookup")
        if is_browser or refers_to_document:
            page = await self.load_document(snapshot)
            if page is not None and page.chunks:
                return await self.investigate_page(snapshot, page, request, advisory=advisory)
        if is_browser:
            content = page_summary.visible_content(snapshot, include_display_values=True)
            messages = page_summary.messages(snapshot, content, request, prompt=prompt)
            evidence = list(content)
        else:
            messages = TaskMemory(prompt, request).messages(snapshot)
            evidence = [f"{item.get('label') or ''} {item.get('value') or ''}" for item in snapshot["elements"]]
        hint = "" if advisory else questions.answer_hints(request, snapshot)
        if hint:
            messages[-1] = {**messages[-1], "content": messages[-1]["content"] + hint}
        shown = [*evidence, snapshot.get("window") or "", snapshot.get("app") or ""]
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
        # The retry instruction goes after the request, not into the system prompt: the system
        # prompt + tools must stay a prewarmed family, or the retry re-reads its whole prefix.
        retry_messages = [*messages[:-1], {**messages[-1],
                                           "content": f"{messages[-1]['content']}\n\n{questions.SCREEN_RETRY}"}]
        try:
            for attempt_messages in (messages, retry_messages):
                answer, _ = await self.think(attempt_messages, SCREEN_SUMMARY_TOOLS,
                                             filler=attempt_messages is messages, task_route=route.QUESTION)
                if answer.name == "say" and tools.validate(answer, {}) is None:
                    text = answer.arguments["text"].strip()
                    if (text and not questions.is_question_only(text)
                            and fastpath.normalize(text) != fastpath.normalize(request)):
                        if allowed_numbers is not None and not questions.numbers(text) <= allowed_numbers:
                            break
                        invented = [] if advisory else questions.unsupported_names(text, shown, request)
                        if invented:
                            # "The latest email is from Sarah Chen" on a YouTube page: not on screen.
                            LOGGER.warning("Screen answer named %s, which is not on screen", invented)
                            continue
                        if questions.contradicts_itself(request, text):
                            # "Yes, there are 0 open pull requests": ask again; a second try that
                            # still contradicts keeps only its facts.
                            LOGGER.info("Screen answer contradicted itself: %r", text)
                            if attempt_messages is messages:
                                continue
                            facts = re.sub(r"^yes\b[\s,.!]*", "", text, flags=re.I)
                            return replace(answer, arguments={"text": facts[:1].upper() + facts[1:]})
                        return answer
        except BonsaiError:
            LOGGER.warning("Could not answer from the active screen", exc_info=True)
        return unavailable

    async def load_document(self, snapshot: dict | None = None, **scroll) -> document.Document | None:
        reader = getattr(self.helper, "page", None)
        if reader is not None:
            try:
                return document.Document.from_payload(await reader(snapshot, **scroll))
            except CONNECTION_ERRORS:
                LOGGER.debug("Page content unavailable", exc_info=True)
        return None

    async def read_document(self, request: str, stt_ms: int, *, resume: bool) -> None:
        await self.set_state("thinking", transcript=request)
        generation = self.screen_generation
        page = await self.load_document()
        previous = self.reading
        if not resume:
            self.reading = None
        index = 0
        if not page or not page.chunks:
            await self.say("I couldn't read document text in the active window.")
            return
        if resume:
            if (previous is None or previous[0].page_id != page.page_id
                    or previous[0].chunks[:previous[1]] != page.chunks[:previous[1]]):
                await self.say("Say read this page to start reading the active page.")
                return
            index = previous[1]
        if index >= len(page.chunks):
            if page.partial:
                more = await self.load_document(direction="down", page_id=page.page_id)
                if more and more.page_id == page.page_id and more.chunks and more != page:
                    # Lazy-loaded sections can extend the entire tree. Skip any unchanged leading passages.
                    index = next((i for i, (old, new) in enumerate(zip(page.chunks, more.chunks))
                                  if old != new), len(page.chunks))
                    page = more
            else:
                await self.say("That's the end of the page.")
                return
            if index >= len(page.chunks):
                await self.say("I couldn't find any more text after scrolling.")
                return
        if generation != self.screen_generation:
            return
        passage = page.chunks[index].text
        spoken = passage
        if index + 1 < len(page.chunks):
            spoken += "\nSay continue reading for the next passage."
        elif page.partial:
            spoken += "\nSay continue reading to look for more text below."
        await self.set_state("speaking", caption=spoken)
        if await self.speak(spoken) and generation == self.screen_generation:
            self.reading = (page, index + 1)
        append_step(self.config.log_path, task_id=f"t_{self.task_number}", step=1, kind="document",
                    utterance=request, tool="read_page", args={"chunk": index + 1}, ok=True,
                    ms={"stt": stt_ms})

    async def investigate_page(self, snapshot: dict, page: document.Document, request: str,
                               *, advisory: bool) -> ToolCall:
        """Read-only model tools over the capture, with at most two viewport advances."""
        seen = page.start(request)
        recent = seen
        status = ""
        scrolls = 0
        failed_answer = False
        generation = self.screen_generation
        unavailable = ToolCall("page-question", "say", {
            "text": "I couldn't read enough of the page to answer that."
        })
        for step in range(max(1, min(self.config.max_steps, 6))):
            if generation != self.screen_generation:
                return unavailable
            available = DOCUMENT_TOOLS if scrolls < 2 else DOCUMENT_TOOLS[:-1]
            messages = [
                {"role": "system", "content": document.PROMPT},
                {"role": "user", "content": f"ACTIVE PAGE: {json.dumps(snapshot['window'], ensure_ascii=False)}\n"
                 f"{page.describe()} {status}\n{page.quote(recent)}\n\nREQUEST: {request}"},
            ]
            try:
                call, _ = await self.think(messages, available, filler=step == 0, task_route=route.QUESTION)
            except BonsaiError:
                LOGGER.warning("Could not answer from the active page", exc_info=True)
                break
            if generation != self.screen_generation:
                return unavailable
            args = call.arguments
            if call.name == "say" and tools.validate(call, {}) is None:
                answer = args["text"].strip()
                evidence = [line for i in seen for line in page.chunks[i - 1].evidence().splitlines()]
                allowed = None if advisory else questions.reference_numbers(request, evidence)
                missing = questions.missing_betting_context(request, evidence) if advisory else None
                if missing:
                    return ToolCall("page-question", "say", {"text": missing})
                if (answer and not questions.is_question_only(answer)
                        and fastpath.normalize(answer) != fastpath.normalize(request)
                        and (allowed is None or questions.numbers(answer) <= allowed)):
                    return call
                if failed_answer:
                    break
                failed_answer = True
                status = questions.SCREEN_RETRY
            elif call.name == "search_page" and set(args) == {"query"} and 0 < len(
                    query := document.clean(args["query"])) <= 120:
                matches = page.search(query)
                recent = matches[:document.MAX_CHUNKS_PER_SEARCH] or recent
                status = (f"Search found {len(matches)} matching passages. "
                          f"Passage numbers: {', '.join(map(str, matches[:12]))}." if matches
                          else "No passages matched. Try another term or read another passage.")
            elif (call.name == "read_page" and set(args) == {"chunk"}
                  and type(args["chunk"]) is int and 1 <= args["chunk"] <= len(page.chunks)):
                recent = [args["chunk"], *[i for i in recent if i != args["chunk"]][:1]]
                status = ""
            elif (call.name == "scroll_page" and set(args) == {"direction"}
                  and args["direction"] in ("up", "down") and scrolls < 2):
                scrolls += 1
                next_page = await self.load_document(snapshot, direction=args["direction"], page_id=page.page_id)
                if next_page and next_page.page_id == page.page_id and next_page.chunks and next_page != page:
                    page = next_page
                    recent = page.start(request)
                    seen = []
                    status = "Scrolled. Passage numbers refer to this new capture."
                else:
                    status = "Scrolling did not reveal new text in the active page."
            else:
                status = "Use one of the offered page tools with valid arguments."
            seen = list(dict.fromkeys([*seen, *recent]))
        return unavailable

    async def describe_page(self, snapshot: dict, request: str) -> ToolCall:
        page = await self.load_document(snapshot)
        if page is not None and page.chunks:
            return await self.investigate_page(snapshot, page, f"{request}\nGive a brief overview with concrete details.",
                                               advisory=False)
        content = page_summary.visible_content(snapshot)
        if content:
            try:
                call, _ = await self.think(page_summary.messages(snapshot, content, request), SCREEN_SUMMARY_TOOLS,
                                           filler=True, task_route=route.QUESTION)
            except BonsaiError:
                LOGGER.warning("Using the available page content after Bonsai failed", exc_info=True)
            else:
                if (call.name == "say" and not tools.validate(call, snapshot)
                        and page_summary.mentions_content(call.arguments["text"], snapshot, content)
                        and not questions.unsupported_names(call.arguments["text"],
                                                            [*content, snapshot["window"], snapshot["app"]], request)):
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
