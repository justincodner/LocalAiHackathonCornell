"""Screen answers stay on what the screen shows: invented names, self-contradicting yes/no
answers, routing personal questions to the screen, counting lists, and showing field state."""

from beacon_brain import page_summary, questions


VIDEO_PAGE = ["YouTube Home", "How Volcanoes Form: A Visual Guide by Science Explained",
              "Lo-fi beats to study to by Chill Hop Radio"]


def test_a_name_that_is_not_on_screen_is_unsupported():
    assert questions.unsupported_names("The latest email is from Sarah Chen.", VIDEO_PAGE) == ["Sarah Chen"]
    assert questions.unsupported_names('The first video is "Lo-fi beats to study to".', VIDEO_PAGE) == []
    assert questions.unsupported_names("You are on YouTube. How Volcanoes Form is first.", VIDEO_PAGE) == []
    assert questions.unsupported_names("The page does not show any email.", VIDEO_PAGE) == []


def test_a_name_from_the_request_is_supported():
    assert questions.unsupported_names("I don't see Priya Patel here.", VIDEO_PAGE, "Is Priya Patel here?") == []


def test_yes_followed_by_a_zero_count_contradicts_itself():
    assert questions.contradicts_itself("Are there any open pull requests?", "Yes, there are 0 open pull requests.")
    assert not questions.contradicts_itself("Are there any open pull requests?", "No, there are 0 open pull requests.")
    assert not questions.contradicts_itself("Is Wi-Fi on?", "Yes, Wi-Fi is on. It is not paused.")
    assert not questions.contradicts_itself("How many are open?", "Yes, none.")  # not a yes/no question


def test_questions_about_the_user_or_now_look_at_the_screen():
    for text in ("Do I need to buy coffee?", "What's the weather right now?", "How many events do I have today?"):
        assert questions.about_user_or_now(text), text
    for text in ("What's the capital of France?", "When was she born?", "How do I boil an egg?",
                 "Can you tell me who wrote Hamlet?"):
        assert not questions.about_user_or_now(text), text
    assert questions.looks_up_screen("Is there anything about space?")
    assert questions.looks_up_screen("Which video is first?")
    assert not questions.looks_up_screen("Is there life on Mars")


def test_overlap_matches_stems_on_screen():
    assert questions.overlaps_screen("What's on my grocery list?", ["Groceries", "Eggs"])
    assert questions.overlaps_screen("Do I need to buy coffee?", ["Coffee beans"])
    assert not questions.overlaps_screen("Do I need an umbrella?", ["Groceries", "Eggs"])


def test_how_many_questions_get_exact_list_counts():
    snapshot = {"window": "Downloads", "elements": [
        {"id": 1, "role": "list", "label": "Sidebar", "depth": 0},
        {"id": 2, "role": "row", "label": "Recents", "depth": 1},
        {"id": 3, "role": "row", "label": "Desktop", "depth": 1},
        {"id": 4, "role": "list", "label": "Downloads", "depth": 0},
        {"id": 5, "role": "row", "label": "a.pdf", "depth": 1},
        {"id": 6, "role": "row", "label": "b.zip", "depth": 1},
        {"id": 7, "role": "row", "label": "c.docx", "depth": 1},
    ]}
    assert questions.count_hint("How many files are here?", snapshot) == (
        '\nCOUNTS (exact): list "Sidebar" holds 2 items; list "Downloads" holds 3 items.')
    assert questions.count_hint("Which file is newest?", snapshot) == ""


def test_screen_questions_see_choices_and_values_but_not_secrets():
    def element(role, label, value=None, states=()):
        return {"role": role, "label": label, "value": value, "states": list(states), "depth": 1}

    assert page_summary.displayed_state(element("radio", "Express", states=["selected"]), "Express") == "Express (selected)"
    assert page_summary.displayed_state(element("checkbox", "Shuffle", "off"), "Shuffle") == "Shuffle: off"
    assert page_summary.displayed_state(element("textfield", "Password", "hunter2"), "Password") == "Password"
