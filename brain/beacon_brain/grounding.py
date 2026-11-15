"""Check that an action is grounded in what the user actually said.

The 2B model rarely asks when a request is ambiguous: given "click the button" it clicks
the first button, and given "type my message" it invents a message. Like the risk guard,
this runs in code after validation and either lets the call through, retargets an obvious
mis-pick, or turns it into an ask_user with a question built from the screen.

It is deliberately lenient about letting a call through and strict about changing one.
"""

import re
from dataclasses import replace

from .llm import ToolCall
from .risk import CONFIRM_LABEL
from .screen_words import matches as sounds_like

PRESSABLE = {"button", "link", "menuitem", "row", "cell", "tab", "checkbox", "radio", "popup"}
CLICK_COMMAND = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+)?(?:click|press|tap|select|choose|hit|push|open)\s+(?:on\s+)?(?P<target>.+)$"
)
ARTICLES = {"the", "a", "an", "on", "please", "my", "me", "of", "to"}
# Words that name a kind of thing rather than a particular one.
GENERIC = ARTICLES | {
    "in", "this", "these", "those", "for", "with", "at", "from", "and", "or", "button", "buttons", "link",
    "links", "tab", "tabs", "row", "rows", "item", "items", "one", "ones", "field", "box", "list",
    "message", "messages", "email", "emails", "note", "notes", "thing", "something", "s", "icon",
    "option", "options", "entry", "control", "now", "any", "some", "screen", "window", "page", "here",
    "there", "again",
}
CONTEXT_WORDS = {
    "it", "that", "this", "them", "him", "her",  # pronouns
    "first", "second", "third", "fourth", "last", "top", "bottom", "next", "previous", "newest", "latest",
}
ROLE_WORDS = {"button": "button", "buttons": "button", "link": "link", "links": "link", "tab": "tab",
              "tabs": "tab", "row": "row", "rows": "row", "checkbox": "checkbox", "box": "checkbox"}
NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20",
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "hundred": "100",
}
FILLER = {"a", "an", "the", "to", "and", "of", "for", "in", "on", "at", "is", "am", "are", "be", "m", "s",
          "ll", "re", "ve", "d", "t", "i", "you", "it", "will", "im", "hi", "hey", "hello", "so"}
EMAIL_OR_URL = re.compile(r"\S+@\S+\.\S+|https?://\S+|\b[\w-]+\.(?:com|org|net|edu|io|gov|co|ai|dev)\b", re.I)
MIN_TYPED_OVERLAP = 0.5


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def word_matches(word: str, label_words: list[str]) -> bool:
    """Exact, the label word extends the user's word ("message" -> "messages"; never the reverse),
    or it is a name that sounds alike (speech-to-text spelling)."""
    word = NUMBERS.get(word, word)
    label_words = [NUMBERS.get(token, token) for token in label_words]
    if len(word) < 3:
        return word in label_words
    # Or dictation misspelled a name that is on screen ("crillion" -> "Krillion").
    return any(token == word or token.startswith(word) or sounds_like(word, token) for token in label_words)


def own_label_words(item: dict) -> list[str]:
    # "unlabeled, near 'Reply'" names its neighbour, not itself: match only "unlabeled".
    return words(re.sub(r",\s*near\s+'.*'$", "", item.get("label") or ""))


def spoken_label(element: dict) -> str:
    label = element.get("label") or ""
    match = re.fullmatch(r"unlabeled, near '(.+)'", label)
    return f"the unlabeled {element['role']} near {match.group(1)}" if match else label


def ask(call: ToolCall, question: str) -> ToolCall:
    return ToolCall(call.id, "ask_user", {"question": question}, call.stray_text)


def choice_question(noun: str, options: list[dict]) -> str:
    names = [spoken_label(item) for item in options[:4]]
    if len(options) > 4:
        extra = len(options) - 4
        names[-1] += f", or {extra} more" if extra > 1 else ", or one more"
        return f"Which {noun}: {', '.join(names)}?"
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return f"Which {noun}: {listed}?"


def check_click(call: ToolCall, snapshot: dict, utterance: str) -> ToolCall:
    command = CLICK_COMMAND.match(" ".join(words(utterance)))
    if command is None:
        return call  # not a direct "click X" request; multi-step plans pick their own targets
    target_words = words(command.group("target"))
    elements = snapshot["elements"]
    chosen = next((item for item in elements if item["id"] == call.arguments.get("id")), None)
    if chosen is None:
        return call
    chosen_words = own_label_words(chosen)
    named = [word for word in target_words if word not in ARTICLES]
    if any(word_matches(word, chosen_words) for word in named):
        return call  # the chosen control carries a word the user said ("open my notes" -> Notes)

    content = [word for word in target_words if word not in GENERIC and word not in CONTEXT_WORDS]
    if not content:
        if any(word in CONTEXT_WORDS for word in target_words):
            return call  # "click it", "the first one": depends on context or order we don't check
        role = next((ROLE_WORDS[word] for word in target_words if word in ROLE_WORDS), chosen["role"])
        candidates = [item for item in elements if item["role"] == role and "disabled" not in item["states"]]
        if len(candidates) > 1:
            return ask(call, choice_question({"row": "one"}.get(role, role), candidates))
        return call

    def strong_match(item: dict) -> bool:
        label_words = own_label_words(item)
        hits = sum(word_matches(word, label_words) for word in content)
        return hits * 2 >= len(content) and hits > 0

    matches = [item for item in elements if item["role"] in PRESSABLE and strong_match(item)
               and "disabled" not in item["states"]]
    if len(matches) == 1:
        target = matches[0]
        target_label = own_label_words(target)
        risky = CONFIRM_LABEL.search(target.get("label") or "")
        # Onto a Pay/Send/Delete control only when the user said that verb AND every word they
        # named is in its label ("click pay invoice"). "click invoice" and "open the delete
        # folder" ask instead.
        said_verb = bool(risky) and risky.group(0).split()[0].casefold() in target_words
        if not risky or (said_verb and all(word_matches(word, target_label) for word in content)):
            return replace(call, arguments={**call.arguments, "id": target["id"]})
        return ask(call, f"Did you mean {spoken_label(target)}?")
    if matches:
        return ask(call, choice_question("one", matches))
    spoken_target = " ".join(word for word in target_words if word not in {"the", "a", "an", "on"})
    unlabeled = [item for item in elements if item["role"] in PRESSABLE and "disabled" not in item["states"]
                 and (item.get("label") or "").startswith("unlabeled")]
    if len(unlabeled) == 1:
        # An icon-only button (the checkout's pay button) has no name to match: offer it.
        return ask(call, f"I don't see {spoken_target} by name. Do you mean {spoken_label(unlabeled[0])}?")
    return ask(call, f"I don't see {spoken_target} on this screen. What should I click instead?")


KEY_WORDS = {
    "enter", "return", "tab", "escape", "esc", "space", "spacebar", "delete", "backspace", "up", "down", "left",
    "right", "arrow", "command", "cmd", "control", "ctrl", "shift", "option", "alt", "fn", "home", "end",
    "page", "key", "keys", "plus",
}


def check_key_press(call: ToolCall, snapshot: dict, utterance: str) -> ToolCall:
    """ "Press call" names a button, but the model may send a shortcut (cmd+shift+c) instead.

    When the user named no key and exactly one control on screen carries the words they
    said, press that control.
    """
    command = CLICK_COMMAND.match(" ".join(words(utterance)))
    if command is None:
        return call
    target_words = words(command.group("target"))
    if any(word in KEY_WORDS or re.fullmatch(r"[a-z]|f\d{1,2}", word) for word in target_words):
        return call  # "press enter", "press command c", "press f5": a real key
    content = [word for word in target_words if word not in GENERIC and word not in CONTEXT_WORDS]
    if not content:
        return call
    matches = [item for item in snapshot["elements"] if item["role"] in PRESSABLE
               and "disabled" not in item["states"]
               and all(word_matches(word, own_label_words(item)) for word in content)]
    if len(matches) != 1 or CONFIRM_LABEL.search(matches[0].get("label") or ""):
        return call
    return ToolCall(call.id, "click", {"id": matches[0]["id"]}, call.stray_text)


def check_typing(call: ToolCall, said: str) -> ToolCall:
    # Addresses and links are spelled differently from how they are spoken, so leave them
    # out of the comparison, but still check every other word typed around them.
    text = EMAIL_OR_URL.sub(" ", call.arguments.get("text", ""))
    typed = [word for word in words(text) if word not in FILLER]
    if not typed:
        return call
    heard = words(said)
    overlap = sum(word_matches(word, heard) for word in typed) / len(typed)
    if overlap < MIN_TYPED_OVERLAP:
        return ask(call, "What would you like me to type?")
    return call


def check(call: ToolCall, snapshot: dict, utterance: str, *, fresh_request: bool, said: str | None = None) -> ToolCall:
    """Return the call, a retargeted click, or an ask_user that resolves the ambiguity.

    utterance: what the user said most recently (the request, or their answer to a question).
    said: everything the user said in this task; typed text may come from any of it.
    fresh_request: no action has run since the user last spoke, so a click must be the
    thing they named. Later clicks in a multi-step plan are the model's own steps.
    """
    if call.name == "type_text":
        return check_typing(call, said or utterance)
    if call.name == "press_key" and fresh_request:
        call = check_key_press(call, snapshot, utterance)
    if call.name == "click" and fresh_request:
        return check_click(call, snapshot, utterance)
    return call
