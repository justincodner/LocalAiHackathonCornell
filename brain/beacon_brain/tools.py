"""Validate Bonsai tool calls and dispatch them through the Helper contract."""

import difflib
import json
from dataclasses import replace

from jsonschema import Draft202012Validator

from .config import ROOT


TOOL_DEFINITIONS = json.loads((ROOT / "contracts" / "tools.json").read_text(encoding="utf-8"))
VALIDATORS = {
    tool["function"]["name"]: Draft202012Validator(tool["function"]["parameters"])
    for tool in TOOL_DEFINITIONS
}
SCREEN_ACTIONS = {"click", "type_text", "set_value", "press_key", "scroll", "open_app"}


def element_for(call, snapshot: dict) -> dict | None:
    element_id = call.arguments.get("id")
    if element_id is None and call.name == "type_text":
        element_id = snapshot.get("focused_id")
    return next((item for item in snapshot["elements"] if item["id"] == element_id), None)


TRUTHY = {"true", "on", "yes", "checked", "1"}
FALSY = {"false", "off", "no", "unchecked", "0"}


def normalize(call, snapshot: dict):
    """Fix harmless argument shapes small models get wrong, e.g. set_value "true" on a checkbox."""
    if call.name != "set_value" or not isinstance(call.arguments.get("value"), str):
        return call
    element = element_for(call, snapshot) or {}
    word = call.arguments["value"].strip().casefold()
    if element.get("role") in {"checkbox", "radio"} and word in TRUTHY | FALSY:
        return replace(call, arguments={**call.arguments, "value": word in TRUTHY})
    return call


def validate(call, snapshot: dict) -> str | None:
    schema = VALIDATORS.get(call.name)
    if schema is None:
        return f"Unknown tool {call.name}"
    error = next(schema.iter_errors(call.arguments), None)
    if error is not None:
        return f"Invalid arguments for {call.name}: {error.message}"
    if "id" in call.arguments:
        element = element_for(call, snapshot)
        if element is None:
            return f"Element {call.arguments['id']} is absent from the latest SCREEN"
        if "disabled" in element["states"]:
            return f"Element {element['id']} is disabled"
    return None


def element_ref(snapshot: dict, element_id: int) -> dict:
    return {"snapshot_id": snapshot["snapshot_id"], "element_id": element_id}


async def execute(helper, call, snapshot: dict, previous_snapshot: dict | None) -> dict:
    args = call.arguments
    name = call.name
    if name == "find":
        return {"ok": True, "matches": await helper.find(args["query"])}
    if name == "what_changed":
        old_lines = previous_snapshot["text"].splitlines() if previous_snapshot else []
        lines = difflib.ndiff(old_lines, snapshot["text"].splitlines())
        return {"ok": True, "changes": [line for line in lines if line.startswith(("+ ", "- "))][:50]}
    if name == "click":
        return await helper.request("/ax/act", {**element_ref(snapshot, args["id"]), "action": "press"})
    if name == "set_value":
        return await helper.request("/ax/act", {
            **element_ref(snapshot, args["id"]), "action": "set_value", "value": args["value"],
        })
    if name == "type_text":
        if "id" in args:
            await helper.request("/ax/act", {**element_ref(snapshot, args["id"]), "action": "focus"})
        return await helper.request("/input/type", {"text": args["text"]})
    if name == "press_key":
        return await helper.request("/input/key", args)
    if name == "scroll":
        target = element_ref(snapshot, args["id"]) if "id" in args else {}
        return await helper.request("/input/scroll", {"direction": args["direction"], "amount": "page", **target})
    if name == "open_app":
        return await helper.request("/app/open", args)
    raise ValueError(f"Tool {name} must be handled by the Brain")
