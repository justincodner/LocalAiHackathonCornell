"""B2: the Mock Helper honours the v1 contract and replays action effects by fixture swap."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from mock_helper.server import create_app

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = Draft202012Validator(json.loads((ROOT / "contracts" / "snapshot.schema.json").read_text(encoding="utf-8")))
FIXTURES = sorted(
    {path.stem for folder in ("mock_helper/fixtures", "contracts/fixtures") for path in (ROOT / folder).glob("*.json")}
)


@pytest.fixture
def client():
    with TestClient(create_app()) as session:
        yield session


def post(client, path, body=None):
    response = client.post(path, json=body or {})
    assert response.status_code == 200
    return response.json()


def test_health_reports_contract_version(client):
    body = client.get("/health").json()
    expected = int((ROOT / "contracts" / "VERSION").read_text(encoding="utf-8").strip())
    assert body["ok"] and body["contract_version"] == expected and body["online"] is False


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_replays_as_a_valid_snapshot(name):
    with TestClient(create_app(initial_fixture=name)) as session:
        snapshot = post(session, "/ax/snapshot")
    errors = [error.message for error in SCHEMA.iter_errors(snapshot)]
    assert errors == []
    assert snapshot["text"].startswith(f"APP {snapshot['app']}")


def test_errors_use_the_ok_false_envelope(client):
    body = post(client, "/ax/act", {"snapshot_id": "s_9999", "element_id": 1, "action": "press"})
    assert body == {"ok": False, "error": {"code": "stale_snapshot", "message": body["error"]["message"]}}
    assert post(client, "/ax/act", {"snapshot_id": "s_1"})["error"]["code"] == "bad_request"


def test_only_the_last_three_snapshots_resolve(client):
    first = post(client, "/ax/snapshot")["snapshot_id"]
    for _ in range(3):
        post(client, "/ax/snapshot")
    assert post(client, "/ax/read", {"snapshot_id": first, "element_id": 8})["error"]["code"] == "stale_snapshot"


def test_unknown_element_is_reported(client):
    snapshot_id = post(client, "/ax/snapshot")["snapshot_id"]
    body = post(client, "/ax/act", {"snapshot_id": snapshot_id, "element_id": 999, "action": "press"})
    assert body["error"]["code"] == "element_not_found"


def test_reply_flow_swaps_fixtures_and_emits_app_changed(client):
    with client.websocket_connect("/events") as events:
        inbox = post(client, "/ax/snapshot")
        reply = next(item["id"] for item in inbox["elements"] if item["label"] == "Reply")
        assert post(client, "/ax/act", {"snapshot_id": inbox["snapshot_id"], "element_id": reply, "action": "press"})["ok"]
        assert events.receive_json()["type"] == "app.changed"

        draft = post(client, "/ax/snapshot")
        assert draft["window"] == "Re: Lunch tomorrow?"
        assert post(client, "/input/type", {"text": "I'll be ten minutes late"})["chars"] == 24
        typed = post(client, "/ax/snapshot")
        body = next(item for item in typed["elements"] if item["id"] == typed["focused_id"])
        assert body["value"].endswith("I'll be ten minutes late")
        assert "I'll be ten minutes late" in typed["text"]

        send = next(item["id"] for item in typed["elements"] if item["label"] == "Send")
        post(client, "/ax/act", {"snapshot_id": typed["snapshot_id"], "element_id": send, "action": "press"})
        assert events.receive_json()["type"] == "app.changed"
        assert post(client, "/ax/snapshot")["window"] == inbox["window"]


def test_open_app_switches_fixture_and_reports_close_matches(client):
    assert post(client, "/app/open", {"name": "notes"})["app"] == "Notes"
    assert post(client, "/ax/snapshot")["app"] == "Notes"
    missing = post(client, "/app/open", {"name": "Photoshop"})
    assert missing["error"]["code"] == "app_not_found"


def test_typing_without_an_editable_focus_fails():
    with TestClient(create_app(initial_fixture="notes_list")) as session:
        snapshot = post(session, "/ax/snapshot")
        focused = next((item for item in snapshot["elements"] if item["id"] == snapshot["focused_id"]), None)
        if focused is not None and focused["role"] in {"textfield", "textarea"}:
            pytest.skip("notes_list focuses an editable field")
        assert post(session, "/input/type", {"text": "hello"})["error"]["code"] == "action_failed"


def test_debug_utterance_and_speech_events(client):
    with client.websocket_connect("/events") as events:
        post(client, "/debug/utterance", {"text": "what is on my screen"})
        final = events.receive_json()
        assert final["type"] == "stt.final" and final["text"] == "what is on my screen"
        assert {"utterance_id", "audio_ms", "stt_ms", "ts"} <= final.keys()

        speech_id = post(client, "/tts/speak", {"text": "Hello.", "priority": "interrupt"})["speech_id"]
        started, finished = events.receive_json(), events.receive_json()
        assert (started["type"], started["speech_id"]) == ("tts.started", speech_id)
        assert (finished["type"], finished["interrupted"]) == ("tts.finished", False)


def test_hud_rejects_unknown_states(client):
    assert post(client, "/hud", {"state": "thinking", "caption": "x"})["ok"]
    assert post(client, "/hud", {"state": "dancing"})["error"]["code"] == "bad_request"
