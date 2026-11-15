import pytest

from beacon_brain import compose, grounding, screen_words
from beacon_brain.llm import ToolCall

DISCORD_TITLE = "(8623) Discord | @Kingsnipeshot - Memory usage - 383 MB"


def discord_screen() -> dict:
    return {
        "snapshot_id": "s_dc", "app": "Google Chrome", "window": DISCORD_TITLE, "focused_id": 7,
        "text": (f'APP Google Chrome | WINDOW "{DISCORD_TITLE}" | FOCUS [7]\n'
                 '[3] tab "(8623) Discord | @Kingsnipeshot"\n[5] text "kingsnipeshot"\n'
                 '[6] heading "Kingsnipeshot"\n[7] textarea "Message @Kingsnipeshot" value="" (focused)'),
        "elements": [
            {"id": 3, "role": "tab", "label": "(8623) Discord | @Kingsnipeshot", "states": []},
            {"id": 5, "role": "text", "label": "kingsnipeshot", "states": []},
            {"id": 6, "role": "heading", "label": "Kingsnipeshot", "states": []},
            {"id": 7, "role": "textarea", "label": "Message @Kingsnipeshot", "value": "", "states": ["focused"]},
        ],
    }


def slack_screen() -> dict:
    return {
        "snapshot_id": "s_sl", "app": "Slack", "window": "Krillion", "focused_id": None,
        "text": '[3] row "Krillion - standup at 10"\n[4] row "Sarah Chen - Lunch?"\n[8] button "Send"',
        "elements": [
            {"id": 3, "role": "row", "label": "Krillion - standup at 10", "states": []},
            {"id": 4, "role": "row", "label": "Sarah Chen - Lunch?", "states": []},
            {"id": 8, "role": "button", "label": "Send", "states": []},
        ],
    }


def test_vocabulary_strips_tab_noise():
    vocab = screen_words.vocabulary(discord_screen())
    assert vocab["kingsnipeshot"] == ("Kingsnipeshot", True)
    assert not {"memory", "usage", "8623", "textarea", "value"} & set(vocab)


@pytest.mark.parametrize("heard, fixed", [
    ("open crillion", "open Krillion"),
    ("message crillion I'm running late", "message Krillion I'm running late"),
    ("click the crillion row", "click the Krillion row"),
    ("text sara chen hi", "text Sarah Chen hi"),
])
def test_misheard_names_on_screen(heard, fixed):
    assert screen_words.correct(heard, slack_screen()) == fixed


@pytest.mark.parametrize("heard, fixed", [
    ("send a message to KingSneapeshot hello", "send a message to Kingsnipeshot hello"),
    # Dictation glued the name to the message with a period.
    ("send a message to KingSneapeshot.hello.", "send a message to Kingsnipeshot.hello."),
    ("Send a message to king snipe shot saying hello", "Send a message to Kingsnipeshot saying hello"),
    ("send a message to kingsneapeshot", "send a message to Kingsnipeshot"),
])
def test_discord_handle(heard, fixed):
    assert screen_words.correct(heard, discord_screen()) == fixed


def test_glued_period_lets_compose_find_the_message():
    fixed = screen_words.correct("send a message to KingSneapeshot.hello.", discord_screen())
    assert compose.recipient(fixed) == "Kingsnipeshot"
    assert compose.on_screen("Kingsnipeshot", discord_screen())
    assert compose.command_and_content(fixed)[1] == "hello"


@pytest.mark.parametrize("said", [
    "send hello to Sarah", "Send a message to Emmett hello", "message Sarah are you free tonight",
    "click send", "scroll down", "open discord", "Click Sarah Chen's message in the list.",
])
def test_ordinary_words_untouched(said):
    assert screen_words.correct(said, slack_screen()) == said
    assert screen_words.correct(said, discord_screen()) == said


def test_message_body_untouched():
    # "Krilion" in the body is close to a name and gets fixed; ordinary words never do.
    screen = slack_screen()
    assert screen_words.correct("send a message to crillion saying the standup moved", screen) == (
        "send a message to Krillion saying the standup moved")
    screen["text"] += '\n[9] text "Hollow"'
    assert screen_words.correct("send hello to crillion", screen) == "send hello to Krillion"


def test_body_only_takes_names():
    screen = slack_screen()
    screen["text"] += '\n[9] text "grillion fries"'  # lowercase: not a name, so not in a message
    assert screen_words.correct("tell Sarah that grilion is great", screen) == "tell Sarah that grilion is great"


def test_no_screen_match_unchanged():
    empty = {"app": "Notes", "window": "", "text": "", "elements": []}
    assert screen_words.correct("send a message to KingSneapeshot hello", empty) == (
        "send a message to KingSneapeshot hello")
    assert screen_words.correct("open crillion", discord_screen()) == "open crillion"
    assert screen_words.correct("call Bartholomew", slack_screen()) == "call Bartholomew"


def test_ambiguous_names_unchanged():
    screen = slack_screen()
    screen["text"] += '\n[9] row "Krillian - hi"'
    assert screen_words.correct("open crillien", screen) == "open crillien"  # equally close to both
    assert screen_words.correct("open crillion", screen) == "open Krillion"  # clearly the first


def test_inflection_not_rewritten():
    screen = slack_screen()
    screen["text"] += '\n[9] link "Download"'
    assert screen_words.correct("open downloads", screen) == "open downloads"


def test_phonetic_key():
    assert screen_words.phonetic("crillion") == screen_words.phonetic("Krillion")
    assert screen_words.phonetic("KingSneapeshot") == screen_words.phonetic("Kingsnipeshot")


def test_grounding_clicks_misheard_name():
    screen = slack_screen()
    call = ToolCall("c1", "click", {"id": 4})
    checked = grounding.check(call, screen, "click crillion", fresh_request=True)
    assert checked.name == "click" and checked.arguments["id"] == 3
