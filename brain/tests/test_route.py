from pathlib import Path

import pytest
import yaml

from beacon_brain.route import ACTION, QUESTION, classify

SCENARIOS = Path(__file__).resolve().parents[2] / "evals" / "scenarios.yaml"
CASES = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
EXPECTED_BY_CATEGORY = {
    "read": QUESTION,
    "injection": QUESTION,
    "action": ACTION,
    "ambiguous": ACTION,
    "risky": ACTION,
}
ROUTED_CASES = [c for c in CASES if c["category"] in EXPECTED_BY_CATEGORY]


def test_every_scenario_category_is_routed():
    assert {c["category"] for c in CASES} <= set(EXPECTED_BY_CATEGORY)


@pytest.mark.parametrize("case", ROUTED_CASES, ids=[c["id"] for c in ROUTED_CASES])
def test_scenario_routes_by_category(case):
    assert classify(case["utterance"]) == EXPECTED_BY_CATEGORY[case["category"]]


@pytest.mark.parametrize(
    "utterance",
    [
        "What's on my screen?",
        "Read the message from Sarah",
        "Where do I pay?",
        "Read the whole page",
        "What changed?",
    ],
)
def test_demo_questions(utterance):
    assert classify(utterance) == QUESTION


@pytest.mark.parametrize(
    "utterance",
    [
        "Reply that I'll be ten minutes late",
        "Send it",
        "Delete all my emails",
        "Click reply",
        "Type: I'll be ten minutes late",
        "Open Notes",
        "Scroll down",
        "Press escape",
    ],
)
def test_demo_actions(utterance):
    assert classify(utterance) == ACTION


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Please read the message from Sarah", QUESTION),
        ("Hey Beacon, what's on my screen?", QUESTION),
        ("Okay so um, where's the submit button?", QUESTION),
        ("Can you click reply?", ACTION),
        ("Could you please open Notes", ACTION),
        ("Can you tell me what's on screen?", QUESTION),
        ("Can you read this?", QUESTION),
        ("Would you describe this page", QUESTION),
        ("Can I reply to this?", QUESTION),
        ("Beacon, scroll down", ACTION),
        ("Read the message from Sarah and reply that I'm late", ACTION),
        ("Summarize the page then close it", ACTION),
        ("Read the to and from fields", QUESTION),
        ("Find the search box", QUESTION),
        ("Find out who sent this", QUESTION),
        ("Is the email sent?", QUESTION),
        ("Is there a reply button?", QUESTION),
        ("whats on my screen", QUESTION),
        ("What’s on my screen?", QUESTION),
        ("Do I have any new mail?", QUESTION),
        ("Do it", ACTION),
        ("Reply to Sarah that I'll be late", ACTION),
        ("Stop", ACTION),
        ("", ACTION),
        ("   ...  ", ACTION),
    ],
)
def test_politeness_modals_and_chains(utterance, expected):
    assert classify(utterance) == expected


@pytest.mark.parametrize(
    "utterance",
    [
        "Who sent this? Reply to them",
        "Where is the submit button? Click it",
        "Which one is from Sarah? open it",
        "Read this, reply yes",
        "Read this, please reply yes",
        "What's this? Then tap it.",
        "Find the pay button and tap it",
        "Find the pay button and hit it",
        "Read the draft; mark it as read",
    ],
)
def test_question_chained_to_later_action_clause(utterance):
    assert classify(utterance) == ACTION


@pytest.mark.parametrize(
    "utterance",
    [
        "What's on my screen, please?",
        "Where is the send button?",
        "Read the email about the move",
        "Is the email sent?",
        "Where do I enter my address?",
        "Who sent this? What did they say?",
        "Read it, slowly.",
    ],
)
def test_action_words_in_noun_position_stay_questions(utterance):
    assert classify(utterance) == QUESTION
