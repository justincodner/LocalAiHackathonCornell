"""Fix names that speech-to-text misheard, using the words on screen.

Dictation knows ordinary English but not the user's contacts, handles or project names:
"Krillion" comes back as "crillion" and "@Kingsnipeshot" as "KingSneapeshot". The screen
usually shows the right spelling, so a transcript word that is not ordinary English and
sounds like an on-screen word is replaced by that word.

Pure and stdlib only. Conservative by design:
- only words that are not common English are ever rewritten ("send", "hello" never are);
- the replacement must be on screen, sound alike (phonetic key) AND look alike (difflib);
- inside the message the user dictated, only a name-like screen word (capitalised or an
  @handle) with a higher score can replace a word;
- when two screen words fit about equally well, nothing changes.
"""

import difflib
import functools
import re

# Chrome appends "- Memory usage - 383 MB" to tab titles; Discord prefixes "(8623)" unread counts.
NOISE = [
    re.compile(r"\s*-\s*(?:High\s+)?memory usage\s*-\s*[\d.,]+\s*[KMG]B", re.IGNORECASE),
    re.compile(r"\(\d+\+?\)"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),  # email addresses are spelled, not heard
    re.compile(r"https?://\S+"),
]
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']*")
SPOKEN = re.compile(r"[A-Za-z0-9]+(?:'(?!s\b)[A-Za-z]+)*")  # "Chen's" -> "Chen" + "'s", "I'm" stays

MIN_LENGTH = 4  # shorter words are too easy to confuse
NAME_SCORE = 0.80  # a word in the command part ("to crillion")
BODY_SCORE = 0.88  # a word in the dictated message: only clear name matches
AMBIGUITY_GAP = 0.04  # two candidates this close: leave the word alone
MAX_PHRASE = 3  # "king snipe shot" -> "Kingsnipeshot"

# Words dictation spells correctly. A transcript word in this set is never rewritten, and a
# screen word in it is never offered as a replacement. Function words, everyday vocabulary,
# and the commands and UI words Beacon hears most.
COMMON = frozenset("""
a about above after again against all almost alone along already also always am among an and
another any anybody anyone anything anyway are around as ask asked at away back be because
been before behind being below between both but by can cannot could did do does doing done
dont down during each either else enough even ever every everyone everything few for from
further get gets getting give go goes going gone got had has have having he her here hers
herself him himself his how however i if im in into is isnt it its itself ive just keep kind
last least less let lets like little lot made make many may maybe me might mine more most much
must my myself near need never new next no none nor not nothing now of off often oh ok okay on
once one only onto or other others our ours out over own please put quite rather really right
said same say says see seem she should since so some somebody someone something sometimes soon
still such sure take than thank thanks that thats the their theirs them then there these they
thing things think this those though through thus till to today together tomorrow tonight too
toward under until up upon us very want wanted was way we well were what whats when where
whether which while who whole whom whose why will with within without would yeah yes yet you
your youre yours yourself
hello hi hey bye goodbye morning afternoon evening night thanks thank sorry welcome love miss
cool nice great good bad fine awesome sweet lol haha omg wow wait later late early soon free
busy home work school office lunch dinner breakfast coffee meeting call party movie game
games play playing played tonight weekend week month year day days hour hours minute minutes
time times friend friends family mom dad brother sister baby team guys everyone people person
man woman boy girl kid kids house car phone computer money food water happy birthday congrats
running coming leaving going heading arriving stuck traffic ready almost there here back
yes no maybe sure definitely probably absolutely exactly totally
send sent sending reply replied replying respond message messages messaging text texts
texting email emails mail chat chats dm write wrote type typed typing tell told say ask
answer forward click clicked press pressed tap select choose pick open opened close closed
switch scroll scrolling move go find search read show hide start stop cancel delete remove
archive save submit post share copy paste undo redo edit add create make new join leave
mute unmute call video voice play pause skip turn check uncheck fill enter return escape
button buttons link links tab tabs window windows page pages screen app apps field box list
menu item items row rows option options setting settings icon file files folder folders
document documents note notes photo photos picture pictures image images inbox draft drafts
channel channels server servers group groups direct friend profile account user users
contact contacts conversation conversations thread threads notification notifications
memory usage google chrome safari firefox discord slack messages mail notes finder calendar
music spotify zoom teams outlook gmail youtube twitter facebook instagram reddit github
first second third fourth fifth last top bottom left right middle above below next previous
up down front back side home end start latest newest oldest recent
""".split())

# Words the snapshot format itself adds ([7] textarea "Message" value="" (focused)).
SNAPSHOT_WORDS = frozenset("""
textarea textfield statictext heading value focused disabled selected checked expanded collapsed
checkbox radio menuitem popup toolbar group inferred unlabeled cell image slider stepper
""".split())

# Joining words into a compound only makes sense for name pieces, never for these.
NOT_PIECES = COMMON


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


@functools.lru_cache(maxsize=4096)
def phonetic(word: str) -> str:
    """A rough sound key: spellings that are said the same get the same key.

    Like Metaphone, simplified: unify letters that sound alike, then drop vowels (except a
    leading one), h after consonants, and doubled letters.
    """
    w = _letters(word)
    if not w:
        return ""
    for old, new in (("x", "ks"), ("tch", "ch"), ("ph", "f"), ("ck", "k"), ("sch", "sk"), ("sh", "x"), ("ch", "x"),
                     ("th", "0"), ("wh", "w"), ("gh", "g"), ("dg", "j"), ("qu", "kw"), ("q", "k"),
                     ("z", "s")):
        w = w.replace(old, new)
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    w = re.sub(r"^kn|^gn|^wr", lambda m: m.group(0)[1], w)
    head, tail = w[0], w[1:]
    if head in "aeiouy":
        head = "a"
    tail = re.sub(r"[aeiouyw]", "", tail)
    tail = re.sub(r"(?<=[^aeiou])h", "", tail)
    key = head + tail
    return re.sub(r"(.)\1+", r"\1", key)


def similarity(heard: str, shown: str, floor: float = 0.0) -> float:
    """0..1: how likely `shown` is what was said when dictation wrote `heard`.

    Below `floor` it may return 0 early, which keeps a full web page's words fast to scan.
    """
    a, b = _letters(heard), _letters(shown)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if not 0.7 <= len(a) / len(b) <= 1.43:
        return 0.0
    pa, pb = phonetic(a), phonetic(b)
    if pa[:1] != pb[:1] and a[:1] != b[:1]:
        return 0.0  # names rarely get their first sound wrong
    spelled = difflib.SequenceMatcher(None, a, b, autojunk=False)
    sounded = difflib.SequenceMatcher(None, pa, pb, autojunk=False)
    if 0.4 * spelled.quick_ratio() + 0.6 * sounded.quick_ratio() < floor:
        return 0.0
    return 0.4 * spelled.ratio() + 0.6 * sounded.ratio()


def clean_screen_text(text: str) -> str:
    for pattern in NOISE:
        text = pattern.sub(" ", text)
    return text


def _split_camel(word: str) -> list[str]:
    return re.findall(r"[A-Z]?[a-z0-9']+|[A-Z]+(?![a-z])", word) or [word]


def vocabulary(snapshot: dict) -> dict[str, tuple[str, bool]]:
    """Words on screen that could be names: {letters key: (display spelling, looks like a name)}.

    A name-like word is capitalised, an @handle, or mixes case; plain lowercase words can
    still be matched in a command ("open crillion" -> "krillion") but not inside a message.
    """
    parts = [snapshot.get("app") or "", snapshot.get("window") or "", snapshot.get("text") or ""]
    for element in snapshot.get("elements") or []:
        parts += [element.get("label") or "", str(element.get("value") or "")]
    found: dict[str, tuple[str, bool]] = {}

    def add(display: str, name_like: bool) -> None:
        key = _letters(display)
        if len(key) < MIN_LENGTH or key.isdigit() or key in COMMON or key in SNAPSHOT_WORDS:
            return
        old = found.get(key)
        # Prefer the spelling that shows how it's written: "Kingsnipeshot" over "kingsnipeshot".
        if old is None or (name_like and not old[1]) or (name_like == old[1] and display[:1].isupper()
                                                         and not old[0][:1].isupper()):
            found[key] = (display, name_like or (old[1] if old else False))

    for part in parts:
        text = clean_screen_text(part)
        for line in re.split(r"[\n|\"\[\]]", text):
            tokens = [(m.group(0), line[max(0, m.start() - 1):m.start()] == "@") for m in TOKEN.finditer(line)]
            for i, (token, handle) in enumerate(tokens):
                add(token, handle or token[:1].isupper() or token != token.lower())
                pieces = _split_camel(token)
                if len(pieces) > 1:
                    for piece in pieces:
                        add(piece, piece[:1].isupper())
            # "Sarah Chen" said as one blurred word, or two capitalised words written as one.
            for i in range(len(tokens) - 1):
                first, second = tokens[i][0], tokens[i + 1][0]
                if first[:1].isupper() and second[:1].isupper() and _letters(first) not in COMMON:
                    add(f"{first} {second}", True)
    return found


def _best(heard: str, vocab: dict[str, tuple[str, bool]], *, names_only: bool, cutoff: float):
    scored = []
    for key, (display, name_like) in vocab.items():
        if names_only and not name_like:
            continue
        score = similarity(heard, key, cutoff - AMBIGUITY_GAP)
        if score >= cutoff - AMBIGUITY_GAP:
            scored.append((score, display))
    if not scored:
        return None
    scored.sort(reverse=True)
    score, display = scored[0]
    if score < cutoff:
        return None
    rivals = [d for s, d in scored[1:] if score - s < AMBIGUITY_GAP and _letters(d) != _letters(display)]
    return None if rivals else display


def _inflection_on_screen(key: str, vocab: dict) -> bool:
    """ "downloads" with "Download" on screen: the same word, not a misheard name."""
    return any(len(other) >= MIN_LENGTH and abs(len(other) - len(key)) <= 3
               and (key.startswith(other) or other.startswith(key)) for other in vocab)


def body_span(text: str) -> tuple[int, int] | None:
    """Where the message the user dictated sits in the transcript, if it is a send request."""
    from .compose import command_and_content

    for candidate in (text, re.sub(r"([.!?])(?=[A-Za-z])", r"\1 ", text)):
        content = command_and_content(candidate)[1]
        if content:
            start = text.casefold().rfind(content.casefold()[:20].rstrip())
            if start >= 0:
                return start, len(text)
    return None


def correct(text: str, snapshot: dict) -> str:
    """The transcript with misheard on-screen names fixed; unchanged when nothing fits well."""
    vocab = vocabulary(snapshot)
    if not vocab:
        return text
    tokens = list(SPOKEN.finditer(text))
    body = body_span(text)
    replacements: list[tuple[int, int, str]] = []
    i = 0
    while i < len(tokens):
        done = False
        # Longest phrase first: "king snipe shot" before "king".
        for size in range(min(MAX_PHRASE, len(tokens) - i), 0, -1):
            group = tokens[i:i + size]
            if size > 1:
                gaps = [text[a.end():b.start()] for a, b in zip(group, group[1:])]
                if any(gap.strip() for gap in gaps):
                    continue  # punctuation between: not one spoken name
                if any(_letters(t.group(0)) in NOT_PIECES for t in group):
                    continue
            words = [t.group(0) for t in group]
            heard = "".join(words)
            key = _letters(heard)
            if key in vocab:
                if size > 1:  # "king snipe shot": the on-screen name, said in pieces
                    replacements.append((group[0].start(), group[-1].end(), vocab[key][0]))
                i += size
                done = True
                break
            if len(key) < MIN_LENGTH:
                continue
            if size == 1 and (key in COMMON or key.isdigit()):
                continue
            if _inflection_on_screen(key, vocab):
                continue
            in_body = body is not None and group[0].start() >= body[0]
            match = _best(key, vocab, names_only=in_body, cutoff=BODY_SCORE if in_body else NAME_SCORE)
            if match is None:
                continue
            replacements.append((group[0].start(), group[-1].end(), match))
            i += size
            done = True
            break
        if not done:
            i += 1
    for start, end, word in reversed(replacements):
        # A period glued to the next word ("KingSneapeshot.hello") stays glued: compose reads it as
        # "name, then message" and types "hello" without the speech-to-text period.
        text = text[:start] + word + text[end:]
    return text


def matches(heard: str, shown: str) -> bool:
    """A spoken word names this on-screen word despite a dictation spelling (for grounding)."""
    key = _letters(heard)
    other = _letters(shown)
    if len(key) < MIN_LENGTH or key in COMMON or len(other) < MIN_LENGTH:
        return False
    if key.startswith(other) or other.startswith(key):
        return False  # an inflection ("invoices" / "invoice"), which grounding decides itself
    return similarity(key, shown) >= NAME_SCORE
