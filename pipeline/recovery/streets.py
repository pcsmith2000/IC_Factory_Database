"""Decide whether two spellings name the same street.

Geocodio returns USPS-canonical street names: "US-231" for "Hwy 231", "E 1st St" for "East
First St.", "Commercial Cir" for "Commercial Circle", "SW 252nd St" for "SW 252 Street".  The
first campaign pass rejected 85 rooftop-grade results as `street_name_mismatch` on exactly those
spellings, so the check is made on a canonical form rather than on the raw strings.

The canonical form is deliberately narrow.  Two spellings are the same street only when

  * the house number and city already agree (the caller checks those first);
  * the distinctive words agree exactly, or differ by one typographical edit in a word of six
    or more letters ("Delany" / "Delaney", "Satrun" / "Saturn");
  * a numbered route agrees on its number ("Route 522" / "US-522"; "Hwy 231" / "US-231") and
    disagrees when the numbers differ ("GA Highway 3" / "US-19", even though they are one road);
  * suffixes agree, or one side has none ("120 Fairview" / "Fairview St"); "Alamo Dr" against
    "Alamo Rd" is a different street until something says otherwise;
  * directionals agree, or one side has none ("Airport Rd" / "S Airport Rd"); "SW Silver Springs"
    against "W Silver Springs" is refused.

Every accepted pair carries the rule that accepted it, so the assertion's evidence says whether
the street matched verbatim, by canonical spelling, or by a one-edit tolerance.
"""
from __future__ import annotations

import re

DIRECTIONS = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "n": "n", "s": "s", "e": "e", "w": "w", "ne": "ne", "nw": "nw", "se": "se", "sw": "sw",
}
SUFFIXES = {
    "street": "st", "st": "st", "str": "st",
    "road": "rd", "rd": "rd",
    "avenue": "ave", "ave": "ave", "av": "ave", "aven": "ave",
    "boulevard": "blvd", "blvd": "blvd", "boul": "blvd",
    "drive": "dr", "dr": "dr", "drv": "dr",
    "lane": "ln", "ln": "ln", "la": "ln",
    "court": "ct", "ct": "ct", "crt": "ct",
    "circle": "cir", "cir": "cir", "circ": "cir", "cl": "cir",
    "place": "pl", "pl": "pl",
    "terrace": "ter", "ter": "ter", "terr": "ter", "tce": "ter",
    "parkway": "pkwy", "pkwy": "pkwy", "pky": "pkwy", "pkway": "pkwy",
    "trail": "trl", "trl": "trl", "tr": "trl",
    "way": "way", "wy": "way",
    "connector": "conn", "conn": "conn",
    "plaza": "plz", "plz": "plz",
    "square": "sq", "sq": "sq",
    "expressway": "expy", "expy": "expy", "expwy": "expy",
    "turnpike": "tpke", "tpke": "tpke", "tpk": "tpke",
    "pike": "pike",
    "alley": "aly", "aly": "aly",
    "crossing": "xing", "xing": "xing",
    "extension": "ext", "ext": "ext",
    "path": "path", "run": "run", "row": "row", "walk": "walk", "bend": "bend", "pass": "pass",
    "point": "pt", "pt": "pt", "cove": "cv", "cv": "cv", "ridge": "rdg", "rdg": "rdg",
    "hollow": "holw", "holw": "holw", "landing": "lndg", "lndg": "lndg",
    "bypass": "byp", "byp": "byp", "causeway": "cswy", "cswy": "cswy",
    "grove": "grv", "grv": "grv", "heights": "hts", "hts": "hts", "hill": "hl", "hl": "hl",
    "junction": "jct", "jct": "jct", "manor": "mnr", "mnr": "mnr", "meadows": "mdws",
    "mdws": "mdws", "park": "park", "spur": "spur", "station": "sta", "sta": "sta",
    "trace": "trce", "trce": "trce", "valley": "vly", "vly": "vly", "view": "vw", "vw": "vw",
    "village": "vlg", "vlg": "vlg", "loop": "loop", "mall": "mall", "center": "ctr", "ctr": "ctr",
    "centre": "ctr",
}
SUFFIX_CODES = set(SUFFIXES.values())
# Words that read as a route class in front of a number.  They are all folded to one token so
# "US Hwy 231", "Highway 231" and "US-231" compare on the number alone; a state name or code
# in front of them ("GA Highway 3", "State Route 17") is the same class.
ROUTE_WORDS = {"us", "hwy", "highway", "hiway", "hgwy", "route", "rte", "rt", "sr", "state",
               "interstate", "i", "county", "cr", "c", "r", "fm", "farm", "market", "loop", "ranch",
               "township", "twp", "tsr"}
ROUTE_FILLERS = {"rd", "road", "hwy", "highway", "route", "rte"}   # "County Rd 3", "Farm Road 12"
PREFIXES = {"saint": "st", "fort": "ft", "mount": "mt", "mt": "mt", "ft": "ft"}
ORDINAL_WORDS = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
                 "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
                 "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
                 "fifteenth": "15", "sixteenth": "16", "seventeenth": "17", "eighteenth": "18",
                 "nineteenth": "19", "twentieth": "20"}
UNIT_MARKERS = {"suite", "ste", "unit", "bldg", "building", "lot", "apt", "apartment", "bay",
                "room", "rm", "floor", "fl", "hangar", "space", "spc", "dept", "trlr", "office"}
US_STATE_CODES = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo "
                     "mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy "
                     "dc".split())


def _tokens(text: str) -> list[str]:
    text = str(text or "").lower()
    text = re.sub(r"(?<=[a-z])-(?=\d)|(?<=\d)-(?=[a-z])", " ", text)   # us-231, i-45, 11-w
    text = re.sub(r"[.,'’]", "", text)
    text = re.sub(r"#", " # ", text)
    text = re.sub(r"\bunited states\b", "us", text)
    text = re.sub(r"\bu\s+s\b(?=\s+(?:\d|hwy|highway|route|rte))", "us", text)
    return [t for t in re.split(r"[\s/]+", text) if t]


def _strip_unit(tokens: list[str]) -> list[str]:
    """Drop a trailing unit designator: 'Bay B', 'Lot#104', 'Suite 200', '# 3'."""
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t == "#":
            # "C R # 3" is county road 3, not a unit: a route word before a bare '#' keeps the number.
            if out and out[-1] in ROUTE_WORDS and re.fullmatch(r"\d+[a-z]?", nxt):
                i += 1
                continue
            break
        if t in UNIT_MARKERS and out and (nxt == "#" or re.fullmatch(r"[a-z]", nxt) or re.search(r"\d", nxt) or not nxt):
            break
        out.append(t)
        i += 1
    return out


def _number(token: str) -> str:
    """'340th' -> '340', '11w' -> '11w', 'main' -> ''."""
    m = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?([a-z]?)", token)
    return m.group(1) + m.group(2) if m else ""


def canonical(street: str) -> dict:
    """Split a street name into comparable parts.

    Returns {'core': [...], 'route': '231' or '', 'suffix': 'st' or '', 'dirs': {'n', ...}}.
    """
    tokens = _strip_unit(_tokens(street))
    # A unit letter that survived upstream stripping ("3120 D NW 16 Terrace", "131 B Ava Drive")
    # is a leading bare letter that is neither a directional nor a route class ("I 45", "U S").
    while tokens and (re.fullmatch(r"\d+[a-z]?", tokens[0]) or
                      (re.fullmatch(r"[a-z]", tokens[0]) and tokens[0] not in DIRECTIONS and tokens[0] not in ROUTE_WORDS)):
        tokens.pop(0)
    dirs: set[str] = set()
    core: list[str] = []
    suffix = ""
    route = ""
    i = 0
    while i < len(tokens):
        t = ORDINAL_WORDS.get(tokens[i], tokens[i])
        if _number(t) and not re.fullmatch(r"\d+[a-z]?", t):
            t = _number(t)                                    # 252nd -> 252
        if t in DIRECTIONS:
            dirs.add(DIRECTIONS[t])
            i += 1
            continue
        if t in ROUTE_WORDS or (t in US_STATE_CODES and i + 1 < len(tokens) and tokens[i + 1] in ROUTE_WORDS):
            # consume the whole route phrase up to and including its number
            j = i
            number = ""
            while j < len(tokens):
                u = tokens[j]
                if _number(u):
                    number = _number(u)
                    j += 1
                    break
                if u in ROUTE_WORDS or u in US_STATE_CODES or u in ROUTE_FILLERS:
                    j += 1
                    continue
                break
            if number:
                route = number
                i = j
                continue
            if route and t in ROUTE_FILLERS:
                i += 1                                        # "US-24 Hwy": the class word repeated after the number
                continue
            # "Loop" or "State" with no number is an ordinary word (Loop Rd, State St)
            if t in SUFFIXES and i + 1 == len(tokens) and (core or route):
                suffix = SUFFIXES[t]
            else:
                core.append(t)
            i += 1
            continue
        if t in PREFIXES and i + 1 < len(tokens):
            core.append(PREFIXES[t])
            i += 1
            continue
        if t in SUFFIXES and (core or route):
            # the last suffix-like word is the suffix; earlier ones are part of the name
            # ("Park Place" -> core park, suffix pl)
            rest = tokens[i + 1:]
            if all(r in DIRECTIONS or r in SUFFIXES for r in rest):
                if suffix and suffix != SUFFIXES[t]:
                    core.append(suffix)
                suffix = SUFFIXES[t]
                i += 1
                continue
        core.append(t)
        i += 1
    if not core and not route and suffix:
        core, suffix = [suffix], ""
    return {"core": core, "route": route, "suffix": suffix, "dirs": dirs}


def _edit_distance_le1(a: str, b: str) -> bool:
    """Damerau-Levenshtein distance <= 1: one insertion, deletion, substitution or transposition."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    if la > lb:
        a, b = b, a
    for i in range(len(a) + 1):
        if a[:i] == b[:i] and a[i:] == b[i + 1:]:
            return True
    return False


def _cores_agree(a: list[str], b: list[str]) -> str:
    """'' when the distinctive words differ, else the rule that reconciles them."""
    if a == b:
        return "canonical"
    # "27th Street Terrace" against "27th Ter": one side carries a redundant suffix word
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    if len(longer) == len(shorter) + 1 and longer[-1] in SUFFIX_CODES and longer[:-1] == shorter:
        return "canonical"
    if len(a) == len(b):
        edits = [(x, y) for x, y in zip(a, b) if x != y]
        if len(edits) == 1 and all(len(w) >= 6 and w.isalpha() for w in edits[0]) and _edit_distance_le1(*edits[0]):
            return "one_edit"
    return ""


def street_equivalent(left: str, right: str) -> tuple[bool, str]:
    """(same street?, rule).  Rules: exact · canonical · one_edit; otherwise the reason refused."""
    if not left or not right:
        return False, "missing_street"
    if re.sub(r"[^a-z0-9]", "", left.lower()) == re.sub(r"[^a-z0-9]", "", right.lower()):
        return True, "exact"
    a, b = canonical(left), canonical(right)
    if a["route"] != b["route"]:
        return False, "route_number_differs"
    if a["suffix"] and b["suffix"] and a["suffix"] != b["suffix"]:
        return False, "suffix_differs"
    if a["dirs"] and b["dirs"] and a["dirs"] != b["dirs"]:
        return False, "direction_differs"
    if not a["core"] and not b["core"]:
        return (True, "canonical") if a["route"] else (False, "no_street_words")
    rule = _cores_agree(a["core"], b["core"])
    return (True, rule) if rule else (False, "street_words_differ")
