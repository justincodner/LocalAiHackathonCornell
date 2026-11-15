"""Match a dictated name to the chat names actually listed, so "braydin" opens Brayden.

Speech-to-text spells names the way they sound: "Brayden" comes back as "braydin", "Emmett" as
"emmit", "Kai" as "Kay". Matching is only ever against the names the user's chat list shows, so a
near miss can pick the listed chat it sounds like, never a contact who isn't there.

Two measures, the better one wins:

- spelling: edit distance between the spoken and listed names ("braydin" / "brayden": 1 letter);
- sound: a phonetic key that keeps the consonants and drops the vowels after the first letter
  ("emmit" and "emmett" are both "amt"), close to Soundex but with the common English spellings
  folded (ph -> f, ck -> k, soft c -> s).

resolve() returns the one listed name meant, two names when they are genuinely tied (the caller
asks which), or nothing.
"""

import re

ACCEPT = 0.75    # below this a listed name is not what was said
SAFE = 0.85      # a different first letter needs a closer spelling ("Ella" is not "Bella")
TIE = 0.05       # two names closer than this: ask which one
FIRST_WORD = 0.95  # "emmit" for "Emmett Lee": the first name alone, slightly below a full match


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").casefold().replace("'", ""))


def norm(text: str) -> str:
    return " ".join(words(text))


def distance(a: str, b: str) -> int:
    """Levenshtein distance: letters inserted, deleted or changed to turn a into b."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def spelling(a: str, b: str) -> float:
    a, b = a.replace(" ", ""), b.replace(" ", "")
    if not a or not b:
        return 0.0
    return 1 - distance(a, b) / max(len(a), len(b))


FOLD = [("sch", "sk"), ("ph", "f"), ("ck", "k"), ("gh", "g"), ("kn", "n"), ("wr", "r"), ("sh", "x"), ("ch", "x"),
        ("th", "0"), ("dg", "j"), ("q", "k"), ("x", "ks"), ("z", "s")]


def sound_key(text: str) -> str:
    """The consonant skeleton of each word: "Brayden" and "braydin" are both "brdn"."""
    keys = []
    for word in words(text):
        word = re.sub(r"[^a-z]", "", word)
        if not word:
            continue
        for spelled, sound in FOLD:
            word = word.replace(spelled, sound)
        word = re.sub(r"c(?=[eiy])", "s", word).replace("c", "k")
        head = "a" if word[0] in "aeiou" else word[0]
        key = re.sub(r"(.)\1+", r"\1", head + re.sub(r"[aeiouyhw]", "", word[1:]))
        keys.append(key)
    return " ".join(keys)


def similarity(spoken: str, listed: str) -> float:
    """1 for the same name; ~0.9 for the same sound; the spelling score otherwise; 0 when unalike."""
    a, b = norm(spoken), norm(listed)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    spelled = spelling(a, b)
    if sound_key(a) == sound_key(b) and spelled >= 0.5:
        return 0.8 + 0.2 * spelled
    if a[0] != b[0] and spelled < SAFE:
        return 0.0
    return spelled if spelled >= ACCEPT else 0.0


def score(spoken: str, listed: str) -> float:
    best = similarity(spoken, listed)
    parts = words(listed)
    if len(words(spoken)) == 1 and len(parts) > 1 and not re.search(r"[&,]", listed):
        best = max(best, FIRST_WORD * similarity(spoken, parts[0]))  # "emmit" -> "Emmett Lee"
    return best


def resolve(spoken: str, listed: list[str]) -> list[str]:
    """The listed name(s) the spoken one means: [name], [best, runner-up] on a tie, or [].

    [] also when the spoken name is already part of a listed one ("David" in "David & Clav"): the
    exact-name paths decide those.
    """
    spoken_n = norm(spoken)
    names = list(dict.fromkeys(name for name in listed if norm(name)))
    if not spoken_n or not names:
        return []
    exact = next((name for name in names if norm(name) == spoken_n), None)
    if exact is not None:
        return [exact]
    if any(re.search(rf"\b{re.escape(spoken_n)}\b", norm(name)) for name in names):
        return []
    scored = sorted(((score(spoken, name), -i, name) for i, name in enumerate(names)), reverse=True)
    scored = [(value, name) for value, _, name in scored if value >= ACCEPT]
    if not scored:
        return []
    if len(scored) > 1 and scored[0][0] - scored[1][0] < TIE:
        return [scored[0][1], scored[1][1]]
    return [scored[0][1]]
