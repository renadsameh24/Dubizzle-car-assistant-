import re

from dubizzle_car_assistant.inventory import load_listings

# Phrases the advertisement itself must contain. A match is an advertiser
# claim, not an independently checked fact.
FEATURE_PATTERNS = {
    "gcc": r"\bgcc\b",
    "panoramic roof": r"panoramic(?:\s+sun)?\s*roof|panoramic",
    "leather seats": r"\bleather\b",
    "reversing camera": r"revers(?:e|ing)\s+camera|rear\s+camera|backup\s+camera|\bcamera\b",
    "warranty": r"warrant",
    "service history": r"service history|\bfsh\b|full service",
    "automatic": r"\bautomatic\b|\bcvt\b",
    "manual": r"\bmanual\b",
    "accident history": r"\baccident\b",
}

_ASKS = (
    ("panoramic roof", r"panoramic"),
    ("leather seats", r"\bleather\b"),
    ("reversing camera", r"revers(?:e|ing) camera|rear camera|backup camera"),
    ("service history", r"service history|\bfsh\b"),
    ("accident history", r"\baccident\b"),
    ("gcc", r"\bgcc\b"),
    ("warranty", r"warrant"),
    ("automatic", r"\bautomatic\b"),
    ("manual", r"\bmanual\b"),
)

_BOILERPLATE = re.compile(
    r"do not hesitate|call us|for any info|showroom|financing|car insurance|"
    r"worldwide export|window tint|ceramic coat|https?://|open all week|whatsapp|"
    r"\bgoogle\b",
    re.I,
)
_CHASSIS = re.compile(
    r"\bchassis\s*(?:number|no\.?|#)?\s*[:\-]?\s*[A-Za-z0-9-]*\d[A-Za-z0-9-]*\b",
    re.I,
)
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_PHONE = re.compile(r"(?:\+\d[\d\s().-]{6,}\d)|(?:\b\d{8,}\b)")


def requested_features(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for name, pattern in _ASKS:
        if re.search(pattern, lowered):
            found.append(name)
    return found


def _listing_text(listing_id: int) -> str:
    frame = load_listings()
    rows = frame.loc[frame["listing_id"] == listing_id]
    if rows.empty:
        return ""
    row = rows.iloc[0]
    return f"{row['title']}\n{row['description']}"


def has_features(listing_id: int, features: list[str]) -> bool:
    if not features:
        return True
    text = _listing_text(listing_id).lower()
    return all(re.search(FEATURE_PATTERNS[feature], text) for feature in features)


def _clauses(text: str) -> list[str]:
    cleaned = _CHASSIS.sub(" ", text)
    cleaned = _VIN.sub(" ", cleaned)
    cleaned = _PHONE.sub(" ", cleaned)
    pieces = re.split(r"[\n\r]+|(?:\s+[•●|]\s+)|(?:\s+-\s+)|(?<=[.!?])\s+", cleaned)
    clauses = []
    for piece in pieces:
        piece = " ".join(piece.split()).strip(" -•|,;:")
        if len(piece) < 3 or _BOILERPLATE.search(piece):
            continue
        clauses.append(piece)
    return clauses


def _phrase_around(clause: str, pattern: str, limit: int = 180) -> str:
    match = re.search(pattern, clause, re.I)
    if match is None or len(clause) <= limit:
        return clause
    left = clause.rfind(" ", 0, match.start())
    right = clause.find(" ", match.end())
    if left == -1:
        left = 0
    else:
        left += 1
    if right == -1:
        right = len(clause)
    window = max(40, (limit - (match.end() - match.start())) // 2)
    start = max(left, match.start() - window)
    end = min(right if right > match.end() else len(clause), match.end() + window)
    start = clause.rfind(" ", 0, start)
    start = 0 if start == -1 else start + 1
    end_space = clause.find(" ", end)
    end = len(clause) if end_space == -1 else end_space
    snippet = clause[start:end].strip(" ,;:-")
    if len(snippet) > limit:
        cut = snippet.rfind(" ", 0, limit)
        snippet = snippet[: cut if cut > 20 else limit].strip()
    return snippet


def advertiser_evidence(listing_id: int, features: list[str]) -> str:
    """A readable advertiser quote. Empty when the ad does not state the feature."""
    if not features:
        return ""
    clauses = _clauses(_listing_text(listing_id))
    quotes = []
    for feature in features:
        pattern = FEATURE_PATTERNS[feature]
        matches = [clause for clause in clauses if re.search(pattern, clause, re.I)]
        if not matches:
            continue
        chosen = max(matches, key=len)
        if len(chosen) > 220:
            chosen = _phrase_around(chosen, pattern)
        if chosen and chosen not in quotes:
            quotes.append(chosen)
    if not quotes:
        return ""
    return "Advertiser claim: " + " ".join(quotes)
