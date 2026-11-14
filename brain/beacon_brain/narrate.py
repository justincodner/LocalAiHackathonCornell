"""Speech templates for actions and confirmations."""

import re

from .tools import element_for

# An id plus the "at"/"on" that only introduced it ("located at element [5]"); a bare
# "on" or "at" elsewhere ("Do Not Disturb is on.") is left alone.
_SPOKEN_ID = re.compile(
    r"\s*(?:\b(?:at|on)\s+)?(?:\(\s*)?(?:(?:element|id|item|number)\s*)?\[\d+\](?:\s*\))?", re.IGNORECASE,
)


def clean_speech(text: str) -> str:
    """Remove element ids from model-written speech (spec 9.3: ids are never spoken)."""
    cleaned = _SPOKEN_ID.sub("", text)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return " ".join(cleaned.split()) or text


def action_text(call, snapshot: dict) -> str:
    args = call.arguments
    element = element_for(call, snapshot) or {}
    label = element.get("label") or "the selected control"
    if call.name == "click":
        return f"Clicking {label}."
    if call.name == "type_text":
        return f"Typing. {args['text']}" if len(args["text"].split()) > 6 else "Typing."
    if call.name == "open_app":
        return f"Opening {args['name']}."
    if call.name == "scroll":
        return f"Scrolling {args['direction']}."
    if call.name == "press_key":
        spoken = args["keys"].replace("cmd", "command").replace("+", " ")
        return f"Pressing {spoken}."
    return f"Updating {label}."


_COMMIT_VERB = r"(?:sent|submitted|deleted|removed|trashed|posted|published|paid|bought|purchased|ordered|erased|saved)"
# Only Beacon claiming it did something: "Sent a message…", "I've sent…", "The reply was sent…".
# Content it is describing ("Sarah sent you an invite", "the deleted file") is not a claim.
COMMIT_CLAIM = re.compile(
    rf"^\s*(?:(?:ok|okay|done|great|alright|all\s+set|perfect)[,.!]?\s+)?"
    rf"(?:(?:i|i've|i\s+have|we|we've)\s+)?(?:just\s+|successfully\s+)?{_COMMIT_VERB}\b"
    rf"|\b(?:was|were|is|are|has\s+been|have\s+been|is\s+now|got)\s+(?:successfully\s+)?{_COMMIT_VERB}\b"
    rf"|\b{_COMMIT_VERB}\s+and\s+{_COMMIT_VERB}\b"
    rf"|\bi\s+(?:have\s+|'ve\s+)?(?:just\s+)?{_COMMIT_VERB}\b"
    rf"|^\s*(?:(?:your|the|my)\s+)?(?:message|messages|reply|email|emails|mail|form|post|order|draft|file|note"
    rf"|it|everything|payment)\s+(?:was\s+|were\s+)?{_COMMIT_VERB}\b",
    re.IGNORECASE,
)


def past_action(call, snapshot: dict) -> str:
    """A factual past-tense phrase for an action that ran, used to replace a false summary."""
    args = call.arguments
    label = (element_for(call, snapshot) or {}).get("label") or "the control"
    if call.name == "click":
        return f"clicked {label}"
    if call.name == "type_text":
        return f"typed: {args['text']}"
    if call.name == "set_value":
        return f"set {label} to {args['value']}"
    if call.name == "open_app":
        return f"opened {args['name']}"
    if call.name == "scroll":
        return f"scrolled {args['direction']}"
    return f"pressed {args.get('keys', '').replace('+', ' ')}"


def honest_summary(summary: str, actions: list[str], committed: bool) -> str:
    """Spec 9.3 + safety: never tell the user something was sent unless a confirmed action did it.

    committed is also true after Return/Enter was pressed, since that sends in many apps.
    """
    if committed or not COMMIT_CLAIM.search(summary):
        return summary
    if not actions:
        return "Nothing was sent or changed."
    text = "; then ".join(actions)
    return f"I {text}. Nothing has been sent yet."


def confirm_text(call, snapshot: dict) -> str:
    if call.name == "click":
        return f"Click {element_for(call, snapshot)['label']} in {snapshot['app']}"
    return f"Press {call.arguments['keys'].replace('+', ' ')} in {snapshot['app']}"
