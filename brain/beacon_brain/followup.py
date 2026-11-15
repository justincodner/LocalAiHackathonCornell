"""Turn a follow-up ("How old is he?") into a standalone request before routing it.

Beacon's prompts have no room for a chat history: every step already spends its context on
the screen. So the conversation enters in one place only. When the user's words lean on
what came before (a pronoun, "what about ...", "tell me more"), one short model call
rewrites them as a request that stands on its own, and everything after that (routing,
the question and action prompts, the code guards that read the user's words) sees only
the rewritten request. Self-contained requests skip this and cost nothing.

A small model can answer instead of rewriting, or invent a name. `checked` accepts a
rewrite only if it keeps the user's own words and every word it adds appears in the
conversation, so a rewrite can resolve a reference but never introduce a new fact.
"""

import re

from . import fastpath, route
from .llm import ToolCall

# Words that can only be understood from an earlier exchange. "this" and "here" point at the
# screen, not the conversation, so they are not on the list.
REFERENCE = re.compile(
    r"\b(?:he|she|him|her|his|hers|they|them|their|theirs|it|its|that|those|there|"
    r"one|ones|same|again|instead|too|either|neither|other|another|else|more)\b"
)
OPENER = re.compile(
    r"^(?:and|but|also|or|then|what about|how about|tell me more|which one|"
    r"the (?:first|second|third|last|next|other) one)\b"
)
# Whole utterances that only make sense as a reply ("Why?"); "why is the sky blue" stands alone.
BARE = re.compile(
    r"(?:why|why not|how come|really|since when|go on|keep going|continue|what else|anything else|"
    r"are you sure|where|when|who|how)"
)
# "What time is it?", "is it raining": "it" there refers to nothing.
DUMMY_IT = re.compile(
    r"\bwhat (?:time|day|date) is it\b|\bis it (?:raining|snowing|sunny|cold|hot|warm|late|early)\b|"
    r"\bhow(?:s| is) it going\b|\bthat(?:s| is) (?:it|all)\b"
)
SCREEN_WORDS = re.compile(r"\b(?:this|these|here|screen|page|window|tab)\b")


def is_follow_up(text: str) -> bool:
    """The words depend on an earlier exchange to make sense."""
    words = fastpath.normalize(text).removeprefix("please ").removeprefix("ok ").removeprefix("okay ")
    if not words or DUMMY_IT.search(words):
        return False
    if OPENER.match(words) or BARE.fullmatch(words):
        return True
    return bool(REFERENCE.search(words)) and not SCREEN_WORDS.search(words)


def asks(text: str) -> bool:
    """A question, including an elliptical one ("And Germany?")."""
    return (fastpath.is_question(text) or route.classify(text) == route.QUESTION
            or text.rstrip().endswith("?"))


REWRITE_TOOLS = [{
    "type": "function",
    "function": {
        "name": "rewrite",
        "description": "The user's latest words rewritten as one standalone request.",
        "parameters": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        },
    },
}]

PROMPT = """LATEST continues CONVERSATION. Rewrite LATEST as one complete request that makes sense
on its own: replace words like he, she, it, his, there with the names they mean, and fill in
what LATEST leaves out. Keep the user's own words. Do not answer it. Use only names and words
from CONVERSATION. Call rewrite.

Example:
CONVERSATION:
User: Who discovered penicillin?
Beacon: Alexander Fleming discovered penicillin in 1928.
LATEST: Where was he from?
rewrite(request="Where was Alexander Fleming from?")

Example:
CONVERSATION:
User: How long is the Nile?
Beacon: The Nile is about 6,650 kilometers long.
LATEST: And the Amazon?
rewrite(request="How long is the Amazon?")

Example:
CONVERSATION:
User: What is a black hole?
Beacon: A black hole is a region where gravity is so strong that light cannot escape.
LATEST: Tell me more
rewrite(request="Tell me more about black holes")"""


def messages(context: str, text: str) -> list[dict]:
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"CONVERSATION:\n{context}\nLATEST: {text}"},
    ]


# Words a rewrite may add or drop freely: they carry no facts.
FUNCTION_WORDS = frozenset("""
a an the of to in on at for from by with about as and or but so if then than also too
is are was were be been being am do does did doing done has have had having will would can could
should shall may might must what whats who whos whom whose which when where wheres why how hows
i me my you your we our us he him his she her hers it its they them their theirs that those this these
there here one ones not no yes any some all more most much many very really just still again
please tell say said s t d ll re ve m else other another same instead either neither
""".split())


def _words(text: str) -> list[str]:
    """Lowercase words, without possessive 's or a plural s ("holes" matches "hole")."""
    words = (word.removesuffix("'s").replace("'", "")
             for word in re.findall(r"[a-z0-9']+", text.casefold().replace("’", "'")))
    return [word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
            for word in words]


def checked(call: ToolCall, text: str, context: str) -> str | None:
    """The rewritten request if it is grounded in the conversation, else None."""
    if call.name != "rewrite" or not isinstance(call.arguments.get("request"), str):
        return None
    rewritten = " ".join(call.arguments["request"].split()).strip("\"' ")
    original, new = _words(text), _words(rewritten)
    if not new or len(new) > max(40, 3 * len(original)):
        return None
    content = set(new) - FUNCTION_WORDS
    if not set(original) - FUNCTION_WORDS <= content:
        return None  # dropped the user's own words: an answer, not a rewrite
    if not content - set(original) <= set(_words(context)):
        return None  # added a word the conversation never used: invented
    return rewritten
