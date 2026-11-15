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
DIRECT = re.compile(r"^(?:(?:please|can you|could you|hey)\W+)*(?:text|message|tell|dm)\s+(?P<name>[A-Z][\w'-]+)\s+"
                    r"(?:that\s+|saying\s+)?(?P<body>.+)$", re.IGNORECASE)
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
    direct = DIRECT.match(command)
    if direct and direct.group("name") not in {"a", "an", "the"} and _clean(direct.group("body")):
        return command, _clean(direct.group("body"))
    obj = SEND_OBJECT.search(command.rstrip(".!?"))
    if obj and _clean(obj.group("body")):
        return command, _clean(obj.group("body"))
    return command, _clean(after)


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


def recipient(utterance: str) -> str | None:
    command, _ = command_and_content(utterance)
    match = next((m for m in RECIPIENT.finditer(command) if m.group("name") not in NOT_NAMES), None)
    return match.group("name") if match else None


def message_field(snapshot: dict) -> dict | None:
    fields = [item for item in snapshot["elements"] if item["role"] in {"textfield", "textarea"}
              and not {"disabled", "inferred"} & set(item.get("states", []))]
    labeled = [item for item in fields if FIELD_LABEL.search((item.get("label") or "").strip())]
    focused = next((item for item in labeled if item["id"] == snapshot.get("focused_id")), None)
    return focused or (labeled[-1] if labeled else None)


def on_screen(name: str, snapshot: dict) -> bool:
    """The named person appears on screen, allowing for speech-to-text spelling ("Emmit")."""
    import difflib

    shown = words(f"{snapshot.get('window', '')}\n{snapshot['text']}")
    return name.casefold() in shown or bool(difflib.get_close_matches(name.casefold(), shown, n=1, cutoff=0.7))


def plan(call, utterance: str, answers: list[tuple[str, str]], snapshot: dict):
    """Before anything is typed for a send request, the type_text or question that should run.

    Returns None to keep the model's call: no message field, or the named person's chat
    isn't the one open (then the model's own question about the recipient is right).
    """
    from .llm import ToolCall

    field = message_field(snapshot)
    if field is None:
        return None
    name = recipient(utterance)
    if name and not on_screen(name, snapshot):
        return None
    content = message_for(utterance, answers)
    if not name and not content:
        return None  # "send it": about a draft that already exists
    if content:
        if call.name == "type_text" and call.arguments.get("text", "").strip() == content:
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
