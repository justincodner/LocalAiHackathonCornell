import pytest

from beacon_brain import fastpath, risk, tools
from beacon_brain.llm import ToolCall


def call(tool_name, **arguments):
    return ToolCall("test", tool_name, arguments)


def screen(label="Reply", *, app="Mail", value=None, disabled=False, role="button"):
    return {
        "app": app,
        "focused_id": 1,
        "elements": [{"id": 1, "role": role, "label": label, "value": value,
                      "states": ["disabled"] if disabled else []}],
    }


@pytest.mark.parametrize("tool, expected", [
    (call("unknown"), "Unknown tool"),
    (call("click", id=2), "absent"),
    (call("click", id=True), "Invalid arguments"),
    (call("click", id=1, extra=True), "Invalid arguments"),
    (call("type_text", text=""), "Invalid arguments"),
    (call("type_text", text="x" * 2001), "Invalid arguments"),
    (call("press_key", keys="cmd++return"), "Invalid arguments"),
])
def test_tool_validation(tool, expected):
    assert expected in tools.validate(tool, screen())


@pytest.mark.parametrize("value, expected", [("true", True), ("Off", False), ("checked", True), (True, True)])
def test_checkbox_values_are_normalized(value, expected):
    fixed = tools.normalize(call("set_value", id=1, value=value), screen("Updates", role="checkbox"))
    assert fixed.arguments["value"] is expected


def test_text_values_are_left_alone():
    fixed = tools.normalize(call("set_value", id=1, value="true"), screen("Nickname", role="textfield"))
    assert fixed.arguments["value"] == "true"


def test_disabled_element_is_invalid():
    assert "disabled" in tools.validate(call("click", id=1), screen(disabled=True))


@pytest.mark.parametrize("label", [
    "Send", "Submit", "Delete", "Remove", "Trash", "Post", "Publish", "Pay", "Buy", "Purchase",
    "Order", "Empty Trash", "Erase", "Sign out",
])
def test_risky_labels_require_confirmation(label):
    assert risk.check(call("click", id=1), screen(label)) == "confirm"


@pytest.mark.parametrize("keys", ["cmd+return", "command+delete", "shift+cmd+d", "cmd+q", "cmd+w"])
def test_risky_chords_require_confirmation(keys):
    assert risk.check(call("press_key", keys=keys), screen()) == "confirm"


@pytest.mark.parametrize("app", ["System Settings", "Keychain Access", "Terminal", "1Password"])
def test_restricted_apps_refuse_actions(app):
    assert risk.check(call("click", id=1), screen(app=app)) == "refuse"
    assert risk.check(call("open_app", name=app), screen()) == "refuse"


@pytest.mark.parametrize("label, value", [("Password", None), ("Card number", None), ("PIN", "(hidden)")])
def test_restricted_fields_refuse_implicit_typing(label, value):
    assert risk.check(call("type_text", text="secret"), screen(label, value=value)) == "refuse"


def test_ordinary_actions_are_allowed():
    assert risk.check(call("click", id=1), screen()) == "allow"


def test_reading_is_never_refused_even_for_sensitive_labels():
    assert risk.check(call("read_element", id=1), screen("IT Desk - Password expiry")) == "allow"
    assert risk.check(call("say", text="hi"), screen(app="Terminal")) == "allow"


def test_typing_into_the_focused_secure_field_is_refused():
    assert risk.check(call("type_text", text="hunter2"), screen("Code", value="(hidden)")) == "refuse"


def test_set_value_on_sensitive_field_is_refused():
    assert risk.check(call("set_value", id=1, value="4111"), screen("Card number")) == "refuse"


def test_harmless_chords_are_allowed():
    assert risk.check(call("press_key", keys="escape"), screen()) == "allow"
    assert risk.check(call("press_key", keys="cmd+shift+e"), screen()) == "allow"


def test_toggling_a_checkbox_labelled_send_needs_no_confirmation():
    assert risk.check(call("click", id=1), screen("Send me event updates", role="checkbox")) == "allow"
    assert risk.check(call("set_value", id=1, value=True), screen("Send me event updates", role="checkbox")) == "allow"


def test_confirm_labels_match_whole_words_only():
    assert risk.check(call("click", id=1), screen("Sender details")) == "allow"
    assert risk.check(call("click", id=1), screen("Send Message")) == "confirm"


@pytest.mark.parametrize("summary, label, covered", [
    ("Send to Sarah Chen: I'll be ten minutes late.", "Send", True),
    ("Pay now: $40 to Acme.", "Pay now", True),
    ("Send Sarah the payment reminder", "Pay now", False),
    ("Ask Sarah to pay me back", "Pay now", False),
    ("Reply to Sarah with my signature", "Sign out", False),
    ("Open the orders page", "Place order", False),
    ("Don't delete anything, just reply", "Delete", False),
    ("Do not send it yet", "Send", False),
    ("Paste the sentence", "Send", False),
    ("Send to Sarah Chen.", "Delete", False),
])
def test_a_model_confirm_covers_only_the_exact_commitment(summary, label, covered):
    assert risk.covered_by(summary, call("click", id=1), screen(label)) is covered


@pytest.mark.parametrize("summary, keys, covered", [
    ("Send this reply to Sarah.", "cmd+return", True),
    ("Close the reply window", "cmd+q", False),
    ("Quit Safari", "cmd+q", True),
    ("Close the reply window", "cmd+w", True),
])
def test_a_model_confirm_covers_chords_by_verb(summary, keys, covered):
    assert risk.covered_by(summary, call("press_key", keys=keys), screen()) is covered


@pytest.mark.parametrize("phrase, command", [
    ("Stop!", "stop"), ("Stop reading", "stop"), ("cancel", "stop"), ("wait", "stop"),
    ("Send it.", "yes"), ("Yes", "yes"), ("go ahead", "yes"), ("do it", "yes"),
    ("No", "no"), ("No, cancel.", "no"),
    ("Repeat", "repeat"), ("repeat that", "repeat"), ("Slower.", "slower"), ("faster", "faster"),
])
def test_fastpath(phrase, command):
    assert fastpath.classify(phrase) == command


@pytest.mark.parametrize("phrase", ["click reply", "send it to Sarah instead", "yes and also cc Bob", "stopwatch"])
def test_fastpath_leaves_real_requests_for_the_model(phrase):
    assert fastpath.classify(phrase) is None
