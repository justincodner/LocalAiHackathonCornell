"""Speech commands handled without a model round trip."""

import re


COMMANDS = {
    "stop": frozenset({"stop", "cancel", "wait", "never mind", "nevermind", "be quiet", "quiet"}),
    "repeat": frozenset({"repeat", "say that again", "repeat that", "again"}),
    "slower": frozenset({"slower", "speak slower", "slow down", "talk slower"}),
    "faster": frozenset({"faster", "speak faster", "speed up", "talk faster"}),
    "yes": frozenset({
        "yes", "yeah", "yep", "yes please", "send it", "do it", "go ahead", "confirm", "ok", "okay",
    }),
    "no": frozenset({"no", "no thanks", "nope", "no cancel", "dont", "do not", "no dont", "no stop"}),
    "forget": frozenset({
        "forget that", "forget everything", "forget our conversation", "forget the conversation",
        "new topic", "change of topic", "change the subject", "clear the conversation", "start a new conversation",
    }),
}


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", "", text.casefold()).split())


def classify(text: str) -> str | None:
    normalized = normalize(text)
    command = next((name for name, phrases in COMMANDS.items() if normalized in phrases), None)
    if command is None and normalized.split(" ", 1)[0] == "stop":
        return "stop"  # spec 9.5: "stop" at the start of a request cancels with no model call
    return command


# Spoken lead-ins that carry no request: "Yeah, what's on my screen?" is the same question.
_LEAD_IN = re.compile(r"^(?:(?:yeah|yes|yep|ok|okay|so|um+|uh+|hey|hi|well|and|alright|oh|now|beacon|please)\s+)+")


def without_lead_in(words: str) -> str:
    """Normalized words without leading fillers like "yeah", "ok", "hey beacon", "please"."""
    return _LEAD_IN.sub("", words)


# "Based on GTO theory, do I fold or raise?": a framing clause before the question itself.
FRAMING = re.compile(
    r"^\s*(?:based on|according to|using|going by|in terms of|from the (?:perspective|point of view) of|"
    r"given|per|with|thinking about|in)\b[^,?]{1,60},\s*", re.IGNORECASE)
# A request that starts like this is a command even if it ends with a question mark.
COMMAND_START = re.compile(
    r"^(?:please )?(?:open|close|click|press|tap|select|choose|switch|scroll|type|enter|fill|send|submit|delete|"
    r"set|move|change|turn|go|search|play|find|show|read|reply|text|message|call|start|stop|quit)\b")


def question_words(text: str) -> str:
    """The normalized words of a request without a leading framing clause or lead-in ("yeah", "please")."""
    return without_lead_in(normalize(FRAMING.sub("", text, count=1)))


def wants_screen_summary(text: str) -> bool:
    words = without_lead_in(normalize(text)).removesuffix(" please")
    words = re.sub(r"^(?:(?:can|could) you )?tell me ", "", words)
    words = re.sub(r" (?:right now|currently)$", "", words)
    return bool(re.fullmatch(
        r"(?:what(?:s| is) (?:on (?:my|the|this|the current) (?:screen|page|website)|(?:this|the current) (?:page|tab|website))"
        r"|what (?:page|tab|website|app|window) am i (?:on|in|using)"
        r"|(?:can|do) you see (?:my screen|(?:the |this )?(?:page|tab|website)(?: that)? (?:im|i am) (?:on|in))"
        r"|(?:describe|summarize|read) (?:my|the|this|the current) (?:screen|page|website))",
        words,
    ))


def is_question(text: str) -> bool:
    words = question_words(text)
    return wants_advice(text) or bool(re.match(
        r"^(?:whats?|wheres?|whens?|whos?|which|why|hows?|is|are|do|does|did|can|could|"
        r"would|should|will|have|has|explain|tell me|i (?:want|need) to know)\b",
        words,
    )) or (text.rstrip().endswith("?") and not COMMAND_START.match(words))


ASKS_WHAT_TO_DO = re.compile(r"\bwhat (?:do|should|would|shall|can) i do\b|\bwhat(?:s| is) (?:my |the )?best (?:move|play|option)\b"
                             r"|\bwhat would you do\b")


def wants_advice(text: str) -> bool:
    """Asks what to do or which option to take ("do I raise or call?", "What do I do here? Fold or raise")."""
    if ASKS_WHAT_TO_DO.search(normalize(text)):
        return True
    sentences = [part for part in re.split(r"[.?!]+\s*", text) if part.strip()]
    return any(_advice_sentence(part) for part in sentences[1:]) or _advice_sentence(text)


def _advice_sentence(text: str) -> bool:
    words = question_words(text)
    return bool(re.match(
        r"^(?:(?:(?:can|could|would) you |give me |i (?:want|need) )?"
        r"(?:some |any )?(?:advice|advise|recommend(?:ation)?|suggest(?:ion)?|help me (?:decide|choose))"
        r"|(?:should|would) i|what (?:do you think|would you do|should i do))\b", words,
    )) or bool(re.match(r"^(?:do|can|shall) i (?:\w+ ){0,3}\w+ or \w+\b", words))


UI_VERBS = (r"(?:pay|check ?out|buy|purchase|place (?:my |the |an )?order|order|submit|sign (?:in|up|out)|"
            r"log (?:in|out)|login|register|search|send|reply|click|press|tap|type|enter|find|go)")
LOCATE = re.compile(
    rf"^(?:please )?(?:(?:where|how) (?:do|can|should) i (?:go to |click to )?{UI_VERBS}\b"
    r"|where(?:s| is| are) (?:the |a |an )?.+ (?:button|link|field|box|menu|tab|option)$)"
)


def needs_screen(text: str) -> bool:
    """Fast route for questions that explicitly refer to the user or their screen."""
    words = question_words(text)
    explicit = re.search(
        r"\b(?:on screen|on (?:the|this|my) screen|this (?:page|tab|website|window|form|dialog)|"
        r"the current (?:page|tab|website|window)|i (?:am|was) (?:viewing|looking at))\b|"
        r"\b(?:here|from this screen)(?: right now)?(?: please)?$",
        words,
    )
    personal = re.match(r"^(?:what(?:s| is| are)|where(?:s| is| are)|how (?:much|many)\b.*?)\s+my\b", words)
    choice = re.search(r"\b(?:what should i do|which option should i (?:pick|choose)|"
                       r"(?:should i|whether i should) (?:\w+ ){1,4}or \w+(?: \d+)?)(?: please)?$"
                       r"|^(?:should|do|can|shall) i (?:\w+ ){0,3}\w+ or \w+\b", words)
    # "Look at my poker situation. What do I do here?": advice about what is on screen.
    situation = wants_advice(text) and re.search(r"\b(?:here|this|my|screen|situation|hand|now|page|game)\b", words)
    return bool(explicit or personal or choice or situation or LOCATE.match(words))


def asks_for_action(text: str) -> bool:
    return bool(re.match(
        r"^(?:can|could|would|will) you (?:please )?"
        r"(?:click|press|tap|select|choose|open|switch|scroll|type|enter|fill|send|submit|delete|close|set|move|change|turn)\b",
        normalize(text).removeprefix("please "),
    ))


SEND_REQUEST = re.compile(r"\b(?:send|reply|respond)\b|^(?:please )?(?:text|message|dm)\b")
NO_SEND = re.compile(r"\b(?:dont|do not|not|never|without) (?:\w+ )?(?:send|sending|reply|replying)\b")


def wants_send(text: str) -> bool:
    """The user asked for a message to go out ("send hello to Emmett", "text Sam I'm late")."""
    words = normalize(text).removeprefix("please ")
    if re.match(r"(?:click|press|tap|hit|select|open|choose)\b", words):
        return False  # "click reply" presses a button; it doesn't ask for a message to go out
    return bool(SEND_REQUEST.search(words)) and not NO_SEND.search(words)


FOLLOW_THROUGH = re.compile(
    r"\b(?:and|then)\s+(?:then\s+)?(?:do|make|take|press|click|tap|choose|pick|select|play|send|type|open|"
    r"submit|fill|reply|hit|go)\b|\b(?:do it|do that|go ahead and)\b"
)


def wants_follow_through(text: str) -> bool:
    """The user asked for a decision or step AND an action ("decide fold or raise and do it")."""
    return bool(FOLLOW_THROUGH.search(normalize(text)))


DECIDE = re.compile(r"\b(?:decide|you (?:choose|pick|decide)|your call|up to you|best (?:move|option|choice|play))\b")


def wants_decision(text: str) -> bool:
    """The user handed the choice to Beacon, so asking them which option is not an answer."""
    return bool(DECIDE.search(normalize(text)))


REPEAT = re.compile(r"\b(?:again|twice|thrice|times|more|repeatedly|keep|until|each|every|all)\b")


def wants_repeat(text: str) -> bool:
    """The user asked for the same step more than once ("scroll down twice", "press tab 3 times")."""
    return bool(REPEAT.search(normalize(text)))


SINGLE_CLICK = re.compile(r"^(?:can you |could you |would you )?(?:click|press|tap|hit|push|select|choose)\b")
MORE_STEPS = re.compile(r"\b(?:and|then|after|before|also|next)\b|,")


def single_click(text: str) -> bool:
    """ "Press call", "click the Reply button": one click is the whole request."""
    words = normalize(text).removeprefix("please ")
    return bool(SINGLE_CLICK.match(words)) and not MORE_STEPS.search(text.casefold()) and not wants_repeat(text)


def single_scroll(text: str) -> bool:
    words = normalize(text).removeprefix("please ").removesuffix(" please")
    return bool(re.fullmatch(r"(?:can you |could you )?scroll(?: the page)? (?:up|down|left|right)(?: a bit)?", words))
