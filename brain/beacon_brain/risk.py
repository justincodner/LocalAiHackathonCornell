"""Screen action checks independent of the model's tool selection."""

import re

from .tools import SCREEN_ACTIONS, element_for


RESTRICTED_APPS = {
    "system settings", "system preferences", "keychain access", "terminal", "iterm", "iterm2",
    "1password", "bitwarden", "dashlane", "keepass", "keepassxc", "lastpass", "passwords",
    "proton pass", "keeper", "password manager",
}
RESTRICTED_LABEL = re.compile(r"password|card\s+number", re.IGNORECASE)
CONFIRM_LABEL = re.compile(
    r"\b(?:send|submit|delete|remove|trash|post|publish|pay|buy|purchase|order|empty|erase|sign\s+out)\b",
    re.IGNORECASE,
)
COMMIT_ROLES = {"button", "link", "menuitem", "row", "cell", "popup"}
CONFIRM_KEYS = {"cmd+return", "cmd+delete", "cmd+shift+d", "cmd+q", "cmd+w"}
KEY_ALIASES = {"command": "cmd", "control": "ctrl", "option": "opt", "alt": "opt"}


def normal_keys(keys: str) -> str:
    *modifiers, key = keys.split("+")
    return "+".join([*sorted(KEY_ALIASES.get(part, part) for part in modifiers), key])


KEY_VERBS = {
    "cmd+return": {"send", "submit", "post"}, "cmd+delete": {"delete", "remove", "trash"},
    "cmd+shift+d": {"send"}, "cmd+q": {"quit"}, "cmd+w": {"close"},
}
NEGATION = re.compile(r"\b(?:don'?t|do\s+not|not|never|no|without|cancel)\s+(?:\w+\s+)?$")


def _says(summary: str, phrase: str) -> bool:
    """The summary contains phrase as whole words, not negated ("don't delete" never counts)."""
    text = " ".join(re.findall(r"[a-z0-9']+", summary.casefold()))
    for match in re.finditer(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", text):
        if not NEGATION.search(text[:match.start()]):
            return True
    return False


def covered_by(summary: str, call, snapshot: dict) -> bool:
    """True when an approved confirm summary names exactly this commitment.

    The model often confirms "Send to Sarah Chen: I'll be late" before clicking Send; asking
    again would be a double confirmation (spec 7.4). To be safe the summary must name the
    control's whole label ("pay now", not "payment") or, for a chord, its verb, un-negated.
    """
    if call.name == "press_key":
        return any(_says(summary, verb) for verb in KEY_VERBS.get(normal_keys(call.arguments["keys"]), ()))
    label = " ".join(re.findall(r"[a-z0-9']+", ((element_for(call, snapshot) or {}).get("label") or "").casefold()))
    return bool(label) and _says(summary, label)


def check(call, snapshot: dict) -> str:
    if call.name not in SCREEN_ACTIONS:
        return "allow"  # reading or speaking never changes the Mac
    element = element_for(call, snapshot) or {}
    label = element.get("label") or ""
    if RESTRICTED_LABEL.search(label) or element.get("value") == "(hidden)":
        return "refuse"
    destination = call.arguments.get("name", "") if call.name == "open_app" else ""
    if snapshot["app"].strip().casefold() in RESTRICTED_APPS or destination.strip().casefold() in RESTRICTED_APPS:
        return "refuse"
    # Toggling "Send me event updates" sends nothing; only pressable controls commit an action.
    if call.name == "click" and element.get("role") in COMMIT_ROLES and CONFIRM_LABEL.search(label):
        return "confirm"
    if call.name == "press_key" and normal_keys(call.arguments["keys"]) in CONFIRM_KEYS:
        return "confirm"
    return "allow"
