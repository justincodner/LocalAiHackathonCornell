import asyncio

import pytest

from beacon_brain import document
from beacon_brain.config import Config
from beacon_brain.llm import ToolCall
from beacon_brain.loop import Brain
from win_helper.snapshot import Node, page_blocks

from test_loop import ReplayHelper, ScriptedModel, fixture


def block(text, role="text", depth=0, **fields):
    return {"role": role, "depth": depth, "text": text, **fields}


def capture(*blocks, partial=False, page_id="tab-a"):
    return {"source": "ocr" if partial else "dom", "partial": partial, "page_id": page_id,
            "blocks": list(blocks)}


class DocumentHelper(ReplayHelper):
    def __init__(self, *captures):
        super().__init__(fixture("safari_form"))
        self.captures = iter(captures)
        self.page_requests = []

    async def page(self, snapshot=None, **scroll):
        self.page_requests.append(scroll)
        return next(self.captures)


def test_document_chunking_retains_long_text_table_columns_and_form_values():
    value = "Complete address " * 18 + "Suite 504-B"
    page = document.Document.from_payload(capture(
        block("Service agreement", "heading", level=1),
        *[block(f"Paragraph {i}. " + "Terms and conditions apply. " * 7) for i in range(75)],
        block("Shipping address", "textfield", value=value),
        block("Delivery windows", "table"),
        block("", "row", 1), block("Service", "columnheader", 2), block("Arrival", "columnheader", 2),
        block("", "row", 1), block("Standard", "rowheader", 2), block("5 days", "cell", 2),
        block("", "row", 1), block("Express", "rowheader", 2), block("Tomorrow", "cell", 2),
    ))

    body = " ".join(chunk.text for chunk in page.chunks)
    assert value in body
    assert "Paragraph 74." in body
    assert all(len(chunk.text) <= document.CHUNK_SIZE for chunk in page.chunks)
    result = page.quote(page.search("Express")[:1])
    assert "Service agreement" in result
    assert "Express | Arrival: Tomorrow" in result


def test_raw_windows_document_retains_offscreen_structure_and_masks_secure_values():
    root = Node("document", children=[
        Node("heading", name="Order details", heading_level=2),
        Node("table", children=[Node("row", children=[
            Node("cell", children=[Node("text", name="Offscreen text " * 30)]),
            Node("cell", children=[Node("textfield", name="Order ID", value="2026-4712")]),
        ])]),
        Node("textfield", name="Password", value="do not disclose", password=True,
             children=[Node("text", name="do not disclose")]),
    ])
    blocks = page_blocks(root)
    assert blocks[0]["level"] == 2
    page = document.Document.from_payload(capture(*blocks))
    quote = page.quote([1])
    assert "Order ID: 2026-4712" in quote
    assert "Offscreen text " * 29 in quote
    assert "Password: (hidden)" in quote
    assert "do not disclose" not in quote


@pytest.mark.loop
def test_browser_question_investigates_and_rejects_action_calls(tmp_path):
    async def run():
        page = capture(
            block("Help center"), *[block("Instructions for getting started. " * 22) for _ in range(8)],
            block("Escalations"), block("The handoff code is violet."),
        )
        helper = DocumentHelper(page)
        chunk = document.Document.from_payload(page).search("violet")[0]
        model = ScriptedModel(
            ToolCall("i", "click", {"id": 1}),
            ToolCall("s", "search_page", {"query": "escalations"}),
            ToolCall("r", "read_page", {"chunk": chunk}),
            ToolCall("a", "say", {"text": "The handoff code is violet."}),
        )
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "session.jsonl"), "System")
        await brain.handle_utterance("What is the secret phrase on this page?", 0)
        assert "violet" not in model.requests[0]
        assert "violet" in model.requests[2]
        assert "violet" in model.requests[3]
        assert model.tools[0] == ["say", "search_page", "read_page", "scroll_page"]
        assert helper.spoken == ["The handoff code is violet."]
        assert helper.actions == []

    asyncio.run(run())


@pytest.mark.loop
def test_page_scroll_limits_results_to_the_same_page(tmp_path):
    async def run():
        helper = DocumentHelper(
            capture(block("First viewport"), partial=True),
            capture(block("Different tab's account number"), partial=True, page_id="tab-b"),
            capture(block("Second viewport contains the renewal date: December 6."), partial=True),
        )
        model = ScriptedModel(
            ToolCall("s1", "scroll_page", {"direction": "down"}),
            ToolCall("s2", "scroll_page", {"direction": "down"}),
            ToolCall("a", "say", {"text": "The renewal date is December 6."}),
        )
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "session.jsonl"), "System")
        await brain.handle_utterance("When is the renewal on this page?", 0)
        assert helper.page_requests == [{}, *[{"direction": "down", "page_id": "tab-a"}] * 2]
        assert "Different tab" not in model.requests[1]
        assert "December 6" in model.requests[2]
        assert "scroll_page" not in model.tools[2]
        assert helper.spoken == ["The renewal date is December 6."]

    asyncio.run(run())


@pytest.mark.loop
def test_read_this_page_speaks_passages_and_resumes_without_a_model_or_snapshot(tmp_path):
    async def run():
        first = "The customer may cancel or reschedule. " * 22
        last = "Contact support if the confirmation does not arrive."
        page = capture(block(first), block(last))
        helper = DocumentHelper(page, page, {**page, "page_id": "tab-b"})
        model = ScriptedModel()
        brain = helper.brain = Brain(helper, model, Config(log_path=tmp_path / "session.jsonl"), "System")
        await brain.handle_utterance("Read this page", 0)
        await brain.handle_utterance("Continue reading", 0)
        await brain.handle_utterance("Continue reading", 0)
        assert helper.spoken[0].startswith(first.rstrip())
        assert helper.spoken[1] == last
        assert helper.spoken[2].startswith("Say read this page")
        assert len(helper.spoken[0].split()) > 40
        assert not model.requests
        assert not helper.snapshot_calls

    asyncio.run(run())


@pytest.mark.loop
def test_continue_reading_advances_an_ocr_viewport(tmp_path):
    async def run():
        first = capture(block("Front of the scanned letter."), partial=True)
        next_page = capture(block("Signed by the support team."), partial=True)
        helper = DocumentHelper(first, first, next_page)
        brain = helper.brain = Brain(helper, ScriptedModel(), Config(log_path=tmp_path / "session.jsonl"), "System")
        await brain.handle_utterance("Read this page", 0)
        await brain.handle_utterance("Continue reading", 0)
        assert helper.page_requests[-1] == {"direction": "down", "page_id": "tab-a"}
        assert helper.spoken[1].startswith("Signed by the support team.")

    asyncio.run(run())
