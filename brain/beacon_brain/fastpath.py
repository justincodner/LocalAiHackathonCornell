"""Speech commands handled without a model round trip."""

import re


COMMANDS = {
    "stop": frozenset({"stop", "cancel", "wait", "never mind", "nevermind", "be quiet", "quiet"}),
    "repeat": frozenset({"repeat", "say that again", "repeat that", "again"}),
    "slower": frozenset({"slower", "speak slower", "slow down", "talk slower"}),
    "faster": frozenset({"faster", "speak faster", "speed up", "talk faster"}),
    "yes": frozenset({
        "yes", "yeah", "yep", "yes please", "send it", "do it", "go ahead", "confirm", "ok", "okay",
    }),
    "no": frozenset({"no", "no thanks", "nope", "no cancel", "dont", "do not", "no dont", "no stop"}),
}


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", "", text.casefold()).split())


def classify(text: str) -> str | None:
    normalized = normalize(text)
    command = next((name for name, phrases in COMMANDS.items() if normalized in phrases), None)
    if command is None and normalized.split(" ", 1)[0] == "stop":
        return "stop"  # spec 9.5: "stop" at the start of a request cancels with no model call
    return command
