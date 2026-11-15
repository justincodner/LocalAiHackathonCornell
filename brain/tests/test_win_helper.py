"""The Windows Helper's pure parts: the snapshot pipeline and key chords (no desktop needed)."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Helper")

from win_helper import snapshot as snap  # noqa: E402


def node(role, name="", children=(), **kw):
    return snap.Node(role=role, name=name, children=list(children), rect=kw.pop("rect", (0, 0, 100, 20)), **kw)


def build(*children, max_elements=120):
    return snap.build(node("window", "W", children), include_root=False, max_elements=max_elements)


def lines(built):
    return snap.render("App", "Win", built.focused_id, built.lines).splitlines()[1:]


def test_web_form_labels_are_not_repeated():
    built = build(node("group", "", [
        node("group", "First name", [node("text", "First name")]),
        node("textfield", "First name", value=""),
        node("button", "Submit"),
    ]))
    assert lines(built) == ['[1] textfield "First name" value=""', '[2] button "Submit"']


def test_page_focus_is_not_reported_but_field_focus_is():
    built = build(node("group", "Page", [node("textfield", "Email", value="", focused=True)], focused=True))
    assert lines(built) == ['[1] group "Page"', '  [2] textfield "Email" value="" (focused)']
    assert built.focused_id == 2


def test_passwords_are_hidden_and_checkbox_state_is_a_value():
    built = build(node("textfield", "Password", value="hunter2", password=True),
                  node("checkbox", "Remember me", toggle=1))
    assert lines(built) == ['[1] textfield "Password" value="(hidden)"',
                            '[2] checkbox "Remember me" value="on" (checked)']


def test_unlabelled_button_borrows_a_neighbour_and_icon_glyphs_vanish():
    built = build(node("button", "Search"), node("button", ""), node("text", "Results "))
    assert lines(built) == ['[1] button "Search"', "[2] button \"unlabeled, near 'Search'\" (inferred)",
                            '[3] text "Results"']


def test_long_lists_keep_twenty_rows():
    built = build(node("list", "Inbox", [node("row", f"Message {i}") for i in range(25)]))
    out = lines(built)
    assert out[0] == '[1] list "Inbox" 25 rows'
    assert len([line for line in out if "row \"Message" in line]) == 20
    assert out[-1].strip() == "... 5 more rows (scroll or use find)"


def test_offscreen_and_dropped_nodes_leave_no_trace_and_max_elements_truncates():
    built = build(node("drop", "scrollbar"), *[node("button", f"B{i}") for i in range(5)], max_elements=3)
    assert [e.label for e in built.elements] == ["B0", "B1", "B2"]
    assert built.truncated


def test_humanize_identifiers():
    assert snap.humanize("sendButton") == "send button"
    assert snap.humanize("URLField") == "url field"
    assert snap.humanize("button-3") == "button"
    assert snap.humanize("a1b2c3d4-1234-5678") is None


def test_mac_chords_map_to_windows_keys():
    from win_helper import keys
    ctrl, shift, alt = 0x11, 0x10, 0x12
    assert keys.parse("cmd+return") == keys.Chord((ctrl,), 0x0D)
    assert keys.parse("escape") == keys.Chord((), 0x1B)
    assert keys.parse("cmd+shift+d").modifiers == (ctrl, shift)
    assert keys.parse("cmd+q") == keys.Chord((alt,), 0x73)  # Alt+F4
    assert keys.parse("delete").key == 0x08  # the Mac's delete is Backspace
    with pytest.raises(keys.ChordError):
        keys.parse("cmd+zz")
    with pytest.raises(keys.ChordError):
        keys.parse("")
