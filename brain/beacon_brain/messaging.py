"""Read a request to send a chat message in code: who it goes to and what it says.

Texting is Beacon's main use, so however the user phrases it, the send should not depend on the
2B model:

    "send this: on my way"              "send the following, see you at 8"
    "tell him I'm running late"         "tell David I'm running late"
    "reply sounds good"                 "say happy birthday"
    "text Clav I'm outside"             "let David know I'll be there"
    "send see you soon to David"        "I'm going to the hackathon in the morning"  (a chat is open)

parse() returns the recipient (None: the chat already open) and the message. A bare sentence
with no send verb is only a message when a chat app is in front (decide() says whether to send
it, ask once, or leave it to the other paths). The model is only asked when a send verb is
clearly there but the message can't be cut out of the words (extract_messages / checked_body).
"""

import difflib
import re
from dataclasses import dataclass

I = re.IGNORECASE

# Fillers before the request: "hey beacon, can you please ...".
LEAD = re.compile(r"^(?:(?:hey|hi|ok(?:ay)?|so|um+|uh+|alright|yeah|now|and|then|also|beacon|please|just|quickly|"
                  r"can\s+you|could\s+you|would\s+you|will\s+you|go\s+ahead\s+and|i\s+want\s+you\s+to|"
                  r"i\s+need\s+you\s+to|i'd\s+like\s+you\s+to|i\s+would\s+like\s+you\s+to)\b[\s,.:]*)+", I)
TRAIL = re.compile(r"[\s,]+(?:please|thanks|thank\s+you)[.!]?$", I)
PRONOUN = r"(?:him|her|them|everyone|everybody|the\s+group|the\s+chat|the\s+guys|y'?all|this\s+person)"
NAME = r"(?P<name>[A-Za-z][\w'&.-]*)"
# At the end of the request a surname can follow: "send hello to Sarah Chen".
LAST_NAME = r"(?P<name>[A-Za-z][\w'&.-]*(?:\s+(?-i:[A-Z][a-z][\w'-]*))?)"
NOT_NAMES = {"me", "us", "myself", "a", "an", "the", "it", "this", "that", "what", "how", "who", "something",
             "anything", "everyone", "you", "my", "yourself", "beacon", "i", "if", "whether", "when"}
MESSAGE_NOUN = r"(?:(?:a|an|the|this|my)\s+)?(?:(?:quick|short|new|little)\s+)?(?:message|text|imessage|dm|note|reply|response)"
INTRO = (r"(?:(?:the\s+)?following(?:\s+message)?|this(?:\s+message)?|this\s+text|that|these\s+words|"
         rf"{MESSAGE_NOUN})")
SAYING = r"(?:saying|that\s+says|which\s+says|to\s+say|says|with|that|reading)"
HERE = (r"(?:here|in\s+here|(?:in|to|into|on)\s+(?:this|the|our|my)\s+(?:current\s+)?"
        r"(?:chat|conversation|thread|channel|group(?:\s+chat)?|dm))")
SEPARATOR = r"\s*[,:;.-]\s*|\s+"

# Each pattern names its parts: name (absent: the open chat), body, and pronoun for "tell him".
PATTERNS = [
    # "Send this to David: on my way", "send the following message to Clav, see you".
    re.compile(rf"^(?:send|post|write|type\s+and\s+send)\s+{INTRO}\s+to\s+(?:{PRONOUN}|{NAME})\s*[,:;.-]\s*(?P<body>.+)$", I),
    # "Send this: on my way", "send the following, see you", "reply with this: ok", "post here: hi".
    re.compile(rf"^(?:send|post|reply|respond|write|type\s+and\s+send)(?:\s+(?:back|with|saying))?"
               rf"(?:\s+{INTRO})?(?:\s+{HERE})?\s*[,:;]\s*(?P<body>.+)$", I),
    # "Send a message to David saying I'm late", "send a text to Clav that I'm here".
    re.compile(rf"^(?:send|write|drop|shoot)\s+(?:{PRONOUN}\s+)?{MESSAGE_NOUN}\s+to\s+(?:{PRONOUN}|{NAME})"
               rf"\s*[,:;]?\s*(?:{SAYING})\s*[,:]?\s*(?P<body>.+)$", I),
    # "Send a message to David, I'm late", "send a text to Clav: see you".
    re.compile(rf"^(?:send|write|drop|shoot)\s+{MESSAGE_NOUN}\s+to\s+(?:{PRONOUN}|{NAME})\s*[,:;]\s*(?P<body>.+)$", I),
    # "Send David a message saying hi", "shoot him a text that I'm here", "drop Clav a note: hi".
    re.compile(rf"^(?:send|write|drop|shoot)\s+(?:{PRONOUN}|{NAME})\s+{MESSAGE_NOUN}\s*[,:;]?\s*"
               rf"(?:(?:{SAYING})\s*[,:]?\s*)?(?P<body>.+)$", I),
    # "Send a message saying I'm late", "send a text that says hi", "send a reply, sounds good".
    re.compile(rf"^(?:send|write|post)\s+{MESSAGE_NOUN}(?:\s+{HERE})?\s*(?:[,:;]\s*|\s+(?:{SAYING})\s*[,:]?\s*)(?P<body>.+)$", I),
    # "Send to David, how are you", "send to him: hi", "reply to Clav with sounds good", "write back to David ok".
    re.compile(rf"^(?:send|post|write|reply|respond|text)(?:\s+back)?\s+to\s+(?:{PRONOUN}|{NAME})"
               rf"(?:\s*[,:;]\s*|\s+(?:{SAYING})\s*[,:]?\s*)(?P<body>.+)$", I),
    # "Reply to David sounds good" (no comma): one name word, then the message.
    re.compile(rf"^(?:reply|respond|write\s+back)\s+to\s+(?:{PRONOUN}|(?P<name>[A-Za-z][\w'&.-]*))\s+(?P<body>.+)$", I),
    # "Reply sounds good", "reply with ok", "respond saying I'm in", "write back see you then".
    re.compile(rf"^(?:reply|respond|write\s+back|answer)(?:\s+(?:back|with|saying|that))*\s*[,:]?\s+(?P<body>.+)$", I),
    # "Tell him I'm running late", "tell David that I'm here", "text Clav I'm outside", "ask David if he's free".
    re.compile(rf"^(?:tell|text|message|dm|imessage|ping|ask|remind|inform|notify)\s+(?:{PRONOUN}|{NAME})(?:\s+back)?"
               rf"(?:\s*[,:;]\s*|\s+)(?:(?:that|saying|to\s+say|and\s+say)\s*[,:]?\s+)?(?P<body>.+)$", I),
    # "Let him know I'm on my way", "let David know that the game starts at 8".
    re.compile(rf"^let\s+(?:{PRONOUN}|{NAME})\s+know\s*[,:]?\s*(?:that\s+)?(?P<body>.+)$", I),
    # "Say hi to David", "send see you soon to Clav", "write good luck to him".
    re.compile(rf"^(?:say|send|write|post)\s+(?P<body>.+?)\s+to\s+(?:{PRONOUN}|{LAST_NAME})$", I),
    # "Send on my way here", "say hi guys in this chat".
    re.compile(rf"^(?:send|post|say|write)\s+(?P<body>.+?)\s+{HERE}$", I),
    # "Say happy birthday", "send see you at 8", "type and send on my way".
    re.compile(rf"^(?:say|send|type\s+and\s+send|post)\s*[,:]?\s+(?!{MESSAGE_NOUN}\b)(?P<body>.+)$", I),
]
# Speech-to-text hears "send to Clav, ..." as "send the Clav, ...": a name, a comma, the message.
MISHEARD_TO = re.compile(rf"^(?:send|reply|text|write)\s+(?:the|tu|two|too)\s+(?P<name>[A-Za-z][\w'&.-]*)\s*[,:;]\s*(?P<body>.+)$", I)
# The message is still to come ("send a message to David"), or an existing draft is meant ("send it").
ANY_NAME = r"[A-Za-z][\w'&.-]*"
NO_BODY = re.compile(rf"^(?:send|write|post|drop|shoot|reply|respond)\s+(?:{PRONOUN}\s+|{ANY_NAME}\s+)?{MESSAGE_NOUN}"
                     rf"(?:\s+to\s+(?:{PRONOUN}|{ANY_NAME}))?(?:\s+{HERE})?$|^(?:send|post)\s+(?:it|that|this|them|the\s+draft)"
                     rf"(?:\s+(?:now|off|already|over))?(?:\s+to\s+(?:{PRONOUN}|{ANY_NAME}))?$|^(?:reply|respond)(?:\s+to\s+(?:{PRONOUN}|{ANY_NAME}))?$", I)
# A send verb is in the request, but no pattern above cut out the message: the model may.
SEND_WORD = re.compile(r"\b(?:send|text|tell|reply|respond|message|dm|ask|remind|let\s+\w+(?:\s+\w+)?\s+know)\b", I)
SEND_START = re.compile(r"^(?:send|text|tell|reply|respond|message|dm|ask|remind|let|shoot|drop|write|say|post|"
                        r"imessage|ping|answer|inform|notify)\b", I)
# Words that are a request to Beacon, not words for a friend.
COMMAND = re.compile(r"^(?:open|close|quit|click|press|tap|hit|select|choose|switch|scroll|type|enter|fill|submit|"
                     r"delete|remove|set|move|change|turn|go|search|look|play|pause|find|show|read|call|start|stop|"
                     r"mute|unmute|minimize|maximize|zoom|copy|paste|undo|redo|save|print|reload|refresh|summari[sz]e|"
                     r"describe|explain|tell\s+me|what|what's|whats|where|when|who|why|how|which|is\s+there|are\s+there|"
                     r"can\s+i|could\s+i|should\s+i|do\s+i|does|did\s+i|help|navigate|bring|take\s+me|launch|"
                     r"forget|repeat|slower|faster|nevermind|never\s+mind|cancel|new\s+tab|next|previous|back|forward)\b", I)
REQUEST_TO_BEACON = re.compile(r"^(?:i\s+(?:want|need|would\s+like|wanna|'d\s+like)\s+(?:you\s+)?to|i'd\s+like\s+(?:you\s+)?to|"
                               r"let's|lets|help\s+me|i'm\s+trying\s+to|im\s+trying\s+to|i'm\s+looking\s+for|"
                               r"show\s+me|give\s+me|get\s+me|find\s+me)\b", I)
SCREEN_WORDS = re.compile(r"\b(?:screen|page|window|tab|app|beacon|website|site|browser|button|cursor|mouse)\b", I)
# A sentence written to a friend: first person, a greeting, a common chat reply.
CHATTY = re.compile(r"^(?:i|i'm|im|i'll|ill|i've|ive|i'd|i\s+am|i\s+was|i\s+will|i\s+just|i\s+think|i\s+can't|i\s+cant|"
                    r"we|we're|were|we'll|we've|our|my|me\s+too|see\s+you|see\s+ya|cya|sounds|on\s+my\s+way|omw|running|"
                    r"lol|lmao|haha+|hah|thanks|thank\s+you|thx|ty|ok(?:ay)?|sure|cool|nice|great|awesome|sweet|bet|yes|"
                    r"yeah|yep|yup|no|nope|nah|hey|hi|hello|yo|sup|good\s+(?:morning|night|luck)|goodnight|gn|gm|love\s+you|"
                    r"miss\s+you|happy|congrats|congratulations|sorry|my\s+bad|no\s+worries|all\s+good|just|almost|"
                    r"be\s+there|leaving|heading|here|outside|home|that's|thats|it's|its|you're|youre|u\s+r)\b", I)
# A question to a friend ("Are you coming tonight?"), not to Beacon ("Are there any unread emails?").
FRIEND_QUESTION = re.compile(r"^(?:are|did|do|have|were|will|would|can|could|want|wanna|you|u|r|how\s+(?:are|was|is)|"
                             r"what\s+(?:are|did|do|time\s+(?:are|do|should|is)|about)|where\s+(?:are|r)|when\s+(?:are|do|is|will|should)|"
                             r"who's\s+(?:coming|in|down)|anyone|any\s+(?:plans|updates)|you\s+(?:up|free|there|coming|around))\b"
                             r".*\b(?:you|u|ya|we|us|y'all|anyone|plans|coming|tonight|tomorrow|later)\b", I)
BEACON_QUESTION = re.compile(r"^(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:open|click|read|tell\s+me|show|find|go|"
                             r"search|send|type|scroll|press|summari[sz]e|describe|explain|help|switch|play|look|check|"
                             r"close|text|message|reply|see)\b|\b(?:on\s+(?:my|the|this)\s+screen|this\s+page|beacon)\b", I)
NOT_CONTENT = {"a message", "message", "a text", "text", "the message", "a note", "him", "her", "them", "it", "this",
               "that", "something", "a reply", "the following", "an imessage", "a quick message", "it again",
               "that again", "again", "anything", "the draft", "a response", "back", "now"}


@dataclass
class Intent:
    body: str | None       # what to send; None when the user hasn't said it yet
    recipient: str | None  # a person or chat named; None means the chat already open
    bare: bool = False     # no send verb at all: a dictated sentence (a chat must be in front)
    question: bool = False  # the bare sentence is a question
    misheard: bool = False  # "send the collab, ...": the name may be a mishearing of the open chat's


def clean(utterance: str) -> str:
    text = " ".join(utterance.replace("“", '"').replace("”", '"').replace("’", "'").split())
    text = LEAD.sub("", text).strip()
    return TRAIL.sub("", text).strip()


def _body(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip().strip(" ,;:-").lstrip(".").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    text = text.strip('"').strip()
    if not text or text.casefold().rstrip(".!?") in NOT_CONTENT:
        return None
    return text


def _name(match: re.Match) -> str | None:
    name = (match.groupdict().get("name") or "").strip(" ,.:;")
    if not name:
        return None
    name = re.sub(r"(?i)^(?:the|my|our)\s+|\s+(?:chat|group|conversation)$", "", name).strip()
    return None if name.casefold() in NOT_NAMES else name


def quoted(text: str) -> tuple[str | None, str] | None:
    """ "Tell David "see you at 8"": the quoted words are the message; the words before name who."""
    match = re.search(r'"(?P<body>[^"]+)"', text)
    if not match or not SEND_WORD.search(text[:match.start()]) and not re.match(r"(?i)^(?:say|ask|write|post)\b", text):
        return None
    before = text[:match.start()].strip(" ,:")
    after = text[match.end():].strip(" ,:.!?")
    who = re.search(rf"(?i)\b(?:to|tell|text|message|ask|let)\s+(?:{PRONOUN}|{NAME})\b", before) or re.match(
        rf"(?i)^to\s+(?:{PRONOUN}|{LAST_NAME})$", after)
    name = _name(who) if who else None
    return name, match.group("body").strip()


def parse(utterance: str) -> Intent | None:
    """Who and what to send, read from the words alone; None when there is no message in it.

    A bare sentence comes back with bare=True: whether it is a message depends on the screen.
    """
    text = clean(utterance)
    if not text:
        return None
    found = quoted(text)
    if found:
        return Intent(found[1], found[0])
    if NO_BODY.match(text.rstrip(".!?")):
        return None
    misheard = MISHEARD_TO.match(text)
    if misheard and _name(misheard) and _body(misheard.group("body")) and not re.match(
            r"(?i)(?:following|message|text|note|draft|same|last|first|whole|rest|reply|answer|response)$", misheard.group("name")):
        return Intent(_body(misheard.group("body")), _name(misheard), misheard=True)
    for pattern in PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        body = _body(match.group("body"))
        if body is None:
            continue
        name = _name(match)
        if name is None and "name" in match.groupdict() and match.group("name") and not re.match(
                rf"(?i)^{PRONOUN}$", match.group("name")):
            # "Tell me a joke", "ask if this is right": "me"/"if" is no one to send to.
            continue
        if re.match(r"(?i)(?:ask|tell|text|message|dm|remind|let)\b", text) and re.match(r"(?i)(?:if|whether|about|how|what|when|where|why)\b", body):
            return None  # "ask David if he's coming": the message needs rewording (the model may)
        if re.match(r"(?i)to\s+\w", body) and re.match(r"(?i)(?:tell|remind|ask|text|message|let)\b", text):
            body = body[3:].strip()  # "tell David to call me" -> "call me"
            body = body[:1].upper() + body[1:]
        return Intent(body, name)
    from . import compose

    to_name = compose.MESSAGE_TO.search(text)
    if to_name and to_name.start() == 0:
        # "Send a message to Emmett see you soon": the message right after the name (a lone
        # capitalised word is a surname instead: "send a message to Sarah Chen").
        body = compose._message_after_name(to_name)
        name = to_name.group("name")
        if not body or name.casefold() in NOT_NAMES:
            return None
        return Intent(_body(body), name)
    if text.rstrip().endswith((",", " -")):
        return None  # cut off mid-sentence ("Since the clav,")
    if SEND_WORD.search(text) or SEND_START.match(text) or COMMAND.match(text) or REQUEST_TO_BEACON.match(text):
        return None
    return Intent(_body(text), None, bare=True, question=text.rstrip().endswith("?"))


def explicit(utterance: str) -> bool:
    """A send request with the message in it ("tell David I'm here"), not a bare sentence."""
    intent = parse(utterance)
    return intent is not None and not intent.bare and intent.body is not None


def wants_message_extraction(utterance: str) -> bool:
    """A send verb is there but code couldn't cut out the message ("could you text David about
    being late"): only then is the model asked for it."""
    text = clean(utterance)
    return (parse(utterance) is None and bool(SEND_START.match(text)) and bool(SEND_WORD.search(text)) and not NO_BODY.match(text.rstrip(".!?"))
            and len(text.split()) >= 4 and not re.match(r"(?i)^(?:tell|show|give)\s+me\b", text))


def bare_kind(intent: Intent) -> str:
    """For a bare sentence with a chat open: "send" (clearly words for a friend), "ask", or "skip"."""
    text = intent.body or ""
    if len(text.split()) < 2 or SCREEN_WORDS.search(text) or text.rstrip().endswith((",", "-", "...")):
        return "skip"
    if intent.question:
        if BEACON_QUESTION.search(text):
            return "skip"
        return "ask" if FRIEND_QUESTION.match(text) else "skip"
    if CHATTY.match(text) and len(text.split()) >= 3:
        return "send"
    return "ask"


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def is_open_chat(name: str, window: str) -> bool:
    """The window title is this person's chat: exactly ("David", not "David & Clav"), or a close
    spelling of the whole title ("Clab" for "Clav")."""
    spoken, title = " ".join(words(name)), " ".join(words(window))
    if not spoken or not title:
        return False
    return spoken == title or difflib.SequenceMatcher(None, spoken, title).ratio() >= 0.75


def chat_title(snapshot: dict) -> str:
    """The open conversation's name from the window title ("David", "Discord | @Sam" -> "Sam")."""
    window = re.sub(r"\s+-\s+(?:Google Chrome|Safari|Arc|Brave|Microsoft Edge|Firefox)$", "", snapshot.get("window") or "")
    return re.sub(r"^(?:\(\d+\)\s*)?(?:Discord|Slack)\s*[|:-]\s*[@#]?", "", window).strip()


EXTRACT_PROMPT = ("You turn a spoken request into the chat message the user's friend will read. Call type_text "
                  "with ONLY that message, written by the user in first person. Never include the request itself "
                  "(no 'text him', 'tell David', 'could you'). Never add new facts.\n"
                  "Request: could you text David about me running late -> type_text \"I'm running late\"\n"
                  "Request: ask him if he's coming tonight -> type_text \"Are you coming tonight?\"\n"
                  "Request: tell Sam about the game being moved to 8 -> type_text \"The game got moved to 8\"")


def extract_messages(utterance: str) -> list[dict]:
    return [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": utterance}]


def checked_body(call, utterance: str) -> str | None:
    """The model's message, kept only if it is made of the user's words (a pronoun or "I'm" aside)."""
    if call is None or call.name != "type_text":
        return None
    body = _body(str(call.arguments.get("text", "")))
    if not body:
        return None
    heard = set(words(utterance)) | {"i", "i'm", "im", "my", "me", "you", "your", "i'll", "am", "be", "will"}
    said = words(body)
    if not said or sum(word not in heard for word in said) > max(1, len(said) // 5):
        return None  # mostly words the user never said
    if " ".join(said) in {" ".join(words(clean(utterance))), " ".join(words(utterance))} or SEND_START.match(body) or re.search(
            rf"(?i)\b(?:could|can|would)\s+you\s+(?:text|tell|send|message|ask)\b|\b(?:text|tell|message|ask)\s+{PRONOUN}\b", body):
        return None  # the request echoed back, or part of it
    return body


WHO = re.compile(rf"^(?:tell|text|message|dm|ask|remind|ping|let|send|shoot|drop)\s+(?:{PRONOUN}|{NAME})\b", I)
TO_WHO = re.compile(rf"\bto\s+{LAST_NAME}$", I)


def named(utterance: str) -> str | None:
    """The person a send request names, for when the model had to find the message."""
    text = clean(utterance)
    match = WHO.match(text) or TO_WHO.search(text.rstrip(".!?"))
    return _name(match) if match else None
