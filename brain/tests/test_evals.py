"""B5: the evaluation set has the required shape and its scorer judges calls correctly."""

import importlib.util
import json
from pathlib import Path

import pytest

from beacon_brain import risk
from beacon_brain.llm import ToolCall
from beacon_brain.tools import validate

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
run_evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_evals)

CASES = run_evals.load_cases()
MOCK = ROOT / "mock_helper" / "fixtures"


def screen(case):
    path = run_evals.fixture_path(case, MOCK)
    if not path.is_file():
        path = ROOT / "contracts" / "fixtures" / path.name
    return json.loads(path.read_text(encoding="utf-8"))


def test_thirty_scored_cases_plus_one_injection():
    assert len(CASES) == 31
    assert sum(case["category"] != "injection" for case in CASES) == 30


def test_inputs_exist_for_a_synthetic_run():
    run_evals.load_fixtures(CASES, MOCK)


def label_id(fixture, *phrases):
    return next(item["id"] for item in fixture["elements"]
                if any(phrase.casefold() in item["label"].casefold() for phrase in phrases))


def test_scorer_accepts_the_right_target_and_rejects_the_wrong_one():
    case = next(case for case in CASES if case["id"] == "mail_click_reply")
    fixture = screen(case)
    right = ToolCall("c", "click", {"id": label_id(fixture, "Reply")})
    wrong = ToolCall("c", "click", {"id": label_id(fixture, "New Message")})
    assert run_evals.score_call(case, fixture, right) is None
    assert "expected target" in run_evals.score_call(case, fixture, wrong)
    assert "expected click" in run_evals.score_call(case, fixture, ToolCall("c", "say", {"text": "Hi"}))


def test_risky_action_passes_only_when_the_guard_stops_it():
    case = next(case for case in CASES if case["id"] == "form_password_refusal")
    fixture = screen(case)
    password = label_id(fixture, "password")
    assert run_evals.score_call(case, fixture, ToolCall("c", "type_text", {"text": "x", "id": password})) is None
    plain = dict(fixture, elements=[
        dict(item, label="Username", value="") if item["id"] == password else item for item in fixture["elements"]
    ])
    typed = ToolCall("c", "type_text", {"text": "x", "id": password})
    assert "risk guard" in run_evals.score_call(case, plain, typed)


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["expect"].get("tool") in {"click", "type_text"} and case["category"] == "action"],
    ids=lambda case: case["id"],
)
def test_grounding_never_blocks_a_correct_action(case):
    """No regression: the right call for every action scenario survives the grounding check unchanged."""
    from beacon_brain import grounding

    fixture = screen(case)
    expect = case["expect"]
    phrases = [expect["target_label"]] if "target_label" in expect else expect.get("target_label_contains_any", [])
    roles = {"click": {"button", "link", "row", "tab", "menuitem", "checkbox", "radio"},
             "type_text": {"textfield", "textarea"}}[expect["tool"]]
    target = next(item["id"] for item in fixture["elements"] if item["role"] in roles
                  and any(phrase.casefold() in item["label"].casefold() for phrase in phrases))
    args = {"id": target, **expect.get("args", {})}
    call = ToolCall("c", expect["tool"], args)
    assert grounding.check(call, fixture, case["utterance"], fresh_request=True) == call
    assert run_evals.score_call(case, fixture, call) is None


@pytest.mark.parametrize("case", [case for case in CASES if case["category"] == "ambiguous"], ids=lambda c: c["id"])
def test_grounding_turns_observed_2b_mistakes_into_questions(case):
    """The calls the 2B actually made on these cases (docs/bench.md) now become ask_user."""
    from beacon_brain import grounding

    fixture = screen(case)
    observed = {
        "mail_ambiguous_button": lambda: ToolCall("c", "click", {"id": 2}),
        "draft_missing_message": lambda: ToolCall("c", "type_text", {"text": "Sounds good, see you then!", "id": 4}),
        "notes_ambiguous_selection": lambda: ToolCall("c", "click", {"id": 2}),
        "form_missing_continue": lambda: ToolCall("c", "click", {"id": label_id(fixture, "Submit")}),
    }[case["id"]]()
    checked = grounding.check(observed, fixture, case["utterance"], fresh_request=True)
    assert run_evals.score_call(case, fixture, checked) is None


def test_injection_case_never_accepts_a_click():
    case = next(case for case in CASES if case["category"] == "injection")
    fixture = screen(case)
    assert run_evals.score_call(case, fixture, ToolCall("c", "click", {"id": fixture["elements"][0]["id"]}))


@pytest.mark.parametrize("case", [case for case in CASES if case["expect"].get("risk")], ids=lambda case: case["id"])
def test_risk_expectations_match_the_guard(case):
    """A correct model action on each risky case must be stopped by the guard in code."""
    fixture = screen(case)
    expect = case["expect"]
    if expect["risk"] == "confirm":
        if "press_key" in expect.get("tool_any", []):
            call = ToolCall("c", "press_key", expect["args_by_tool"]["press_key"])
        else:
            call = ToolCall("c", "click", {"id": label_id(fixture, expect["target_label"])})
    else:
        call = ToolCall("c", "type_text", {"text": "example", "id": label_id(fixture, "password")})
    assert validate(call, fixture) is None
    assert risk.check(call, fixture) == expect["risk"]
