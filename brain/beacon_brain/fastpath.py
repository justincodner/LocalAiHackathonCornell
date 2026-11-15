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


def wants_screen_summary(text: str) -> bool:
    words = normalize(text).removeprefix("please ").removesuffix(" please")
    words = re.sub(r" (?:right now|currently)$", "", words)
    return bool(re.fullmatch(
        r"(?:what(?:s| is) (?:on (?:my|the|this|the current) (?:screen|page|website)|(?:this|the current) (?:page|tab|website))"
        r"|what (?:page|tab|website|app|window) am i (?:on|in|using)"
        r"|(?:can|do) you see (?:my screen|(?:the |this )?(?:page|tab|website)(?: that)? (?:im|i am) (?:on|in))"
        r"|(?:describe|summarize|read) (?:my|the|this|the current) (?:screen|page|website))",
        words,
    ))


def is_question(text: str) -> bool:
    words = normalize(text).removeprefix("please ")
    return wants_advice(text) or bool(re.match(
        r"^(?:whats?|wheres?|whens?|whos?|which|why|hows?|is|are|do|does|did|can|could|"
        r"would|should|will|have|has|explain|tell me|i (?:want|need) to know)\b",
        words,
    ))


def wants_advice(text: str) -> bool:
    words = normalize(text).removeprefix("please ")
    return bool(re.match(
        r"^(?:(?:(?:can|could|would) you |give me |i (?:want|need) )?"
        r"(?:some |any )?(?:advice|advise|recommend(?:ation)?|suggest(?:ion)?|help me (?:decide|choose))"
        r"|(?:should|would) i|what (?:do you think|would you do|should i do))\b", words,
    ))


def needs_screen(text: str) -> bool:
    """Fast route for questions that explicitly refer to the user or their screen."""
    words = normalize(text)
    explicit = re.search(
        r"\b(?:on screen|on (?:the|this|my) screen|this (?:page|tab|website|window|form|dialog)|"
        r"the current (?:page|tab|website|window)|i (?:am|was) (?:viewing|looking at))\b|"
        r"\b(?:here|from this screen)(?: right now)?(?: please)?$",
        words,
    )
    personal = re.match(r"^(?:what(?:s| is| are)|where(?:s| is| are)|how (?:much|many)\b.*?)\s+my\b", words)
    choice = re.search(r"\b(?:what should i do|which option should i (?:pick|choose)|"
                       r"(?:should i|whether i should) (?:\w+ ){1,4}or \w+(?: \d+)?)(?: please)?$", words)
    return bool(explicit or personal or choice)


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
