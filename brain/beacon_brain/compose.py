"""Work out what message the user wants sent, so the request itself is never typed.

The 2B model often types the command ("Send a message to David") instead of the message,
or types the same text again and again. These checks run in code on every type_text for a
send request.
"""

import re

SEND_VERB = re.compile(r"\b(?:send|text|message|tell|reply|respond|dm)\b", re.IGNORECASE)
CONTENT_MARKER = re.compile(
    r"\b(?:saying|that says|which says|to say|says|the following|with the message|with|that)\b\s*[:,]?\s*",
    re.IGNORECASE,
)
SEND_OBJECT = re.compile(r"\bsend\s+(?P<body>.+?)\s+to\s+[\w'-]+(?:\s+[\w'-]+)?\s*$", re.IGNORECASE)
# "Text Emmett I'm running late", "tell Sam that I'm here": the verb, a name, then the message.
DIRECT = re.compile(r"(?:^|\b(?:and|then)\s+(?:then\s+)?)(?:(?:please|can you|could you|hey)\W+)*(?:text|message|tell|dm)\s+(?P<name>[A-Z][\w'-]+)\s+"
                    r"(?:that\s+|saying\s+)?(?P<body>.+)$", re.IGNORECASE)
# "Send a message to KingSnipeshot hello", "write a DM to sam.hello": the message comes right after the
# name. Speech-to-text often glues a sentence break to it ("KingSneapeshot.hello").
MESSAGE_TO = re.compile(r"\b(?:send|write|drop)\s+(?:(?:him|her|them)\s+)?(?:a|an)\s+(?:(?:quick|short|new)\s+)?"
                        r"(?:message|text|dm|note|imessage|reply)\s+to\s+@?(?P<name>[\w'-]+)(?P<sep>[\s.,:;!-]+)"
                        r"(?P<body>.+)$", re.IGNORECASE)
# A lone capitalised word after the name is more likely a surname ("to Sarah Chen") than the message.
SHORT_MESSAGES = {"hello", "hi", "hey", "yo", "yes", "no", "ok", "okay", "thanks", "lol", "sure", "sup", "gm", "gn"}
NOT_CONTENT = {"a message", "message", "a text", "text", "the message", "a note", "him", "her", "them", "it",
               "this", "that", "something", "a reply", "the following", "an imessage", "a quick message"}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def sentences(text: str) -> list[str]:
    return [part.strip(" -") for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip(" -")]


def _clean(text: str) -> str | None:
    text = text.strip().strip("\"'").strip()
    return text if text and text.casefold().rstrip(".!?") not in NOT_CONTENT else None


def command_and_content(utterance: str) -> tuple[str, str | None]:
    """The sentence that asks to send, and the message text the user gave (None if none)."""
    parts = sentences(utterance)
    index = next((i for i, part in enumerate(parts) if SEND_VERB.search(part)), None)
    if index is None:
        return "", None
    command = parts[index]
    after = " ".join(parts[index + 1:]).strip()
    marker = CONTENT_MARKER.search(command)
    if marker and command[marker.end():].strip(" .") and not marker.group(0).strip().casefold().startswith("with"):
        return command, _clean(command[marker.end():]) or _clean(after)
    if ":" in command and command.split(":", 1)[1].strip():
        return command, _clean(command.split(":", 1)[1])
    to_name = MESSAGE_TO.search(command)
    if to_name and _message_after_name(to_name):
        return command, _message_after_name(to_name)
    direct = DIRECT.search(command)
    if direct and direct.group("name") not in {"a", "an", "the"} and _clean(direct.group("body")):
        return command, _clean(direct.group("body"))
    obj = SEND_OBJECT.search(command.rstrip(".!?"))
    if obj and _clean(obj.group("body")):
        return command, _clean(obj.group("body"))
    return command, _clean(after)


def _message_after_name(match: re.Match) -> str | None:
    body = match.group("body").strip()
    glued = bool(set(match.group("sep")) - set(" \t"))  # "KingSneapeshot.hello", "Sam, hi"
    word = body.rstrip(".!?")
    if not glued and " " not in word and word[:1].isupper() and word.casefold() not in SHORT_MESSAGES:
        return None
    return _clean(word if " " not in word else body)  # "hello." from speech-to-text: type "hello"


def message_for(utterance: str, answers: list[tuple[str, str]]) -> str | None:
    """What to type: the user's latest answer to a question, else the text in the request."""
    if answers:
        return answers[-1][1].strip() or None
    return command_and_content(utterance)[1]


def is_command_text(typed: str, utterance: str, content: str | None) -> bool:
    """The model typed (part of) the request instead of the message."""
    typed_words = words(typed)
    if not typed_words:
        return False
    if content and " ".join(typed_words) in " ".join(words(content)):
        return False
    command, _ = command_and_content(utterance)
    command_words = set(words(command))
    return bool(SEND_VERB.search(typed)) and all(word in command_words for word in typed_words)


ASK_MESSAGE = "What should the message say?"
RECIPIENT = re.compile(r"\b(?i:to|send|text|message|tell|dm)\s+(?P<name>[A-Z][\w'-]+)")
NOT_NAMES = {"A", "An", "The", "My", "Me", "Him", "Her", "Them", "It", "This", "That", "Hello", "Hi", "Hey", "I"}
FIELD_LABEL = re.compile(r"^(?:i?message|text message|send a message|write a message|reply|chat|type a message)\b",
                         re.IGNORECASE)


NAVIGATION = re.compile(r"\b(?:go|switch|head|navigate|get|jump|move|come)(?:\s+(?:back|over))?\s*$", re.IGNORECASE)


def recipient(utterance: str) -> str | None:
    command, _ = command_and_content(utterance)
    to_name = MESSAGE_TO.search(command)
    if to_name and to_name.group("name").casefold() not in {n.casefold() for n in NOT_NAMES}:
        return to_name.group("name")
    # "Go to Discord and message Sam": the place to go to is not the person.
    match = next((m for m in RECIPIENT.finditer(command) if m.group("name") not in NOT_NAMES
                  and not NAVIGATION.search(command[:m.start()])), None)
    return match.group("name") if match else None


def message_field(snapshot: dict) -> dict | None:
    fields = [item for item in snapshot["elements"] if item["role"] in {"textfield", "textarea"}
              and not {"disabled", "inferred"} & set(item.get("states", []))]
    labeled = [item for item in fields if FIELD_LABEL.search((item.get("label") or "").strip())]
    focused = next((item for item in labeled if item["id"] == snapshot.get("focused_id")), None)
    return focused or (labeled[-1] if labeled else None)


def on_screen(name: str, snapshot: dict) -> bool:
    """The named person appears on screen, allowing for speech-to-text spelling ("Emmit")."""
    return _matches(name, words(f"{snapshot.get('window', '')}\n{snapshot['text']}"))


def _matches(name: str, shown: list[str]) -> bool:
    import difflib

    return name.casefold() in shown or bool(difflib.get_close_matches(name.casefold(), shown, n=1, cutoff=0.7))


def chat_open(name: str | None, snapshot: dict, field: dict | None = None) -> bool:
    """The open conversation is the named person's: their name is on the message field
    ("Message @Kingsnipeshot"), the window title, a heading, or the selected row or tab.

    Stricter than on_screen, which a name in a sidebar list also satisfies.
    """
    field = field or message_field(snapshot)
    if field is None:
        return False
    if not name:
        return True
    places = [field.get("label") or "", snapshot.get("window") or ""]
    places += [item.get("label") or "" for item in snapshot["elements"]
               if item["role"] == "heading" or "selected" in item.get("states", [])]
    return _matches(name, words(" ".join(places)))


def shown_name(name: str, snapshot: dict | None) -> str:
    """The open conversation's spelling of a misheard name ("KingSneapeshot" -> "Kingsnipeshot")."""
    import difflib

    field = message_field(snapshot) if snapshot else None
    if field is None:
        return name
    places = [field.get("label") or "", snapshot.get("window") or ""]
    places += [item.get("label") or "" for item in snapshot["elements"] if item["role"] == "heading"]
    shown = {word.casefold(): word for word in re.findall(r"[\w'-]+", " ".join(places))}
    close = difflib.get_close_matches(name.casefold(), list(shown), n=1, cutoff=0.7)
    return shown[close[0]] if close else name


def plan(call, utterance: str, answers: list[tuple[str, str]], snapshot: dict, *, strict: bool = False):
    """Before anything is typed for a send request, the type_text or question that should run.

    Returns None to keep the model's call: no message field, or the named person's chat
    isn't the one open (then the model's own question about the recipient is right).
    strict: the model is navigating (clicking, or past its first step), so only take over once
    the named person's conversation is clearly the one open (chat_open).
    """
    from .llm import ToolCall

    field = message_field(snapshot)
    if field is None:
        return None
    name = recipient(utterance)
    if name and not on_screen(name, snapshot):
        return None
    if strict and not chat_open(name, snapshot, field):
        return None
    content = message_for(utterance, answers)
    if not name and not content:
        return None  # "send it": about a draft that already exists
    if content:
        target = call.arguments.get("id", snapshot.get("focused_id"))
        if call.name == "type_text" and call.arguments.get("text", "").strip() == content and target == field["id"]:
            return None
        return ToolCall(call.id, "type_text", {"id": field["id"], "text": content}, call.stray_text)
    if (field.get("value") or "").strip():
        return None  # "send it": the draft is already written
    typed = call.arguments.get("text", "") if call.name == "type_text" else ""
    heard = set(words(" ".join([utterance, *(reply for _, reply in answers)])))
    if typed and not is_command_text(typed, utterance, None) and set(words(typed)) & heard:
        return None  # "Text Emmett I'm running late": the model found the message itself
    # Nothing typed, or words the user never said ("Send Emmett a message" -> "hello"): ask.
    return ToolCall(call.id, "ask_user", {"question": ASK_MESSAGE}, call.stray_text)


PLACE = re.compile(r"\b(?:go|switch|head|jump)\s+(?:back\s+|over\s+)?to\s+(?P<place>[\w][\w .'-]*?)\s+(?:and|then)\b",
                   re.IGNORECASE)


def place_tab(utterance: str, snapshot: dict) -> dict | None:
    """ "Go to Discord and message Sam": the one unselected tab named after that place, if any."""
    match = PLACE.search(utterance)
    if not match:
        return None
    place = match.group("place").strip().casefold()
    if place in (snapshot.get("window") or "").casefold().split(" - google chrome")[0]:
        return None  # already there
    tabs = [item for item in snapshot.get("elements", []) if item.get("role") == "tab"
            and re.search(rf"\b{re.escape(place)}\b", (item.get("label") or "").casefold())]
    if len(tabs) != 1 or "selected" in tabs[0].get("states", []):
        return None
    return tabs[0]
