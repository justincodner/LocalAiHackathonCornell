"""Route a spoken request to the question model or the action model.

The Brain runs two local models. Questions ("what's on my screen?", "read the
message from Sarah") take a single model call whose answer is spoken back, so
answer quality matters most: they go to the larger, more accurate model.
Actions ("click reply", "type: hello") take several steps, and every step is
already checked by code guards (grounding, risk, confirmation), so they go to
the smaller, faster model.

The router looks only at the utterance text, with no model call, so it is fast
and deterministic. Misrouting only costs speed or a little accuracy, never
safety: both models run behind the same guards. When unsure, it says ACTION.
"""

import re

QUESTION = "question"
ACTION = "action"

# Leading words dropped before deciding. Multi-word entries are token tuples.
# Modal openers are dropped too, so "can you click reply" is judged on
# "click reply" and "can you read this" on "read this".
FILLER = (
    ("hey", "beacon"),
    ("beacon",),
    ("please",),
    ("ok",),
    ("okay",),
    ("so",),
    ("um",),
    ("uh",),
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("will", "you"),
)

# Spoken/typed spellings folded to one form before matching.
ALIASES = {"whats": "what's"}

WH_WORDS = frozenset(
    "what what's where where's who who's whose which when why how".split()
)
AUXILIARIES = frozenset("is are was were does do did has have".split())
READ_VERBS = frozenset(
    "read describe summarize summarise list explain find".split()
)
# Two-word question openers whose first word alone is not one.
QUESTION_PHRASES = (("tell", "me"), ("can", "i"))

# "do it" / "do this" is a command, not an inverted question.
IMPERATIVE_DO_OBJECTS = frozenset("it this that".split())

# A question start chained to an action is an action: either an action verb
# after one of these words ("read it and reply ..."), or a later clause that
# starts with an action verb ("who sent this? reply to them").
CHAIN_WORDS = frozenset("and then".split())
ACTION_VERBS = frozenset(
    "reply send click type write open delete remove press scroll forward "
    "archive select check uncheck fill submit post close go tap hit mark "
    "draft answer move enter paste save compose flag".split()
)

_CLAUSE_BREAK = re.compile(r"[.?!;,]+")
_NOT_WORD = re.compile(r"[^\w']+")


def _tokens(utterance: str) -> list[str]:
    text = utterance.casefold().replace("’", "'").replace("_", " ")
    words = (w.strip("'") for w in _NOT_WORD.split(text))
    return [ALIASES.get(w, w) for w in words if w]


def _strip_filler(words: list[str]) -> list[str]:
    stripped = True
    while stripped:
        stripped = False
        for prefix in FILLER:
            if tuple(words[: len(prefix)]) == prefix:
                words = words[len(prefix):]
                stripped = True
    return words


def _starts_like_question(words: list[str]) -> bool:
    if not words:
        return False
    first = words[0]
    if first == "do" and len(words) > 1 and words[1] in IMPERATIVE_DO_OBJECTS:
        return False
    if first in WH_WORDS or first in AUXILIARIES or first in READ_VERBS:
        return True
    return any(tuple(words[: len(p)]) == p for p in QUESTION_PHRASES)


def _chains_action(words: list[str]) -> bool:
    for i, word in enumerate(words):
        if word in CHAIN_WORDS and any(w in ACTION_VERBS for w in words[i + 1:]):
            return True
    return False


def classify(utterance: str) -> str:
    """Return QUESTION or ACTION for a spoken request."""
    clauses = [_strip_filler(_tokens(c)) for c in _CLAUSE_BREAK.split(utterance)]
    clauses = [c for c in clauses if c]  # drop filler-only clauses ("please")
    if not clauses or not _starts_like_question(clauses[0]):
        return ACTION
    if any(c[0] in ACTION_VERBS for c in clauses[1:]):
        return ACTION
    if _chains_action([w for c in clauses for w in c]):
        return ACTION
    return QUESTION
