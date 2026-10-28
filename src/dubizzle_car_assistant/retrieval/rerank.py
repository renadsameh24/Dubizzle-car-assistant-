import re

_STOP = {
    "with",
    "that",
    "this",
    "from",
    "have",
    "want",
    "looking",
    "something",
    "around",
    "under",
    "about",
    "car",
    "cars",
}

_PRACTICAL = (
    "reliable",
    "everyday",
    "practical",
    "family",
    "commute",
    "affordable",
    "cheap",
    "inexpensive",
    "budget",
    "expensive",
)
_SPORTY_ASK = ("sport", "fast", "supercar", "convertible", "luxury", "track")
_SHOWY = (
    "lamborghini",
    "ferrari",
    "mclaren",
    "bentley",
    "rolls-royce",
    "camaro",
    "mustang",
    "aventador",
    "huracan",
    "revuelto",
)


def is_showy(row: dict[str, object]) -> bool:
    text = f"{row.get('make') or ''} {row.get('model') or ''}".lower()
    return any(word in text for word in _SHOWY)


def prefers_listed_price(query: str) -> bool:
    question = query.lower()
    wants_practical = any(word in question for word in _PRACTICAL)
    wants_showy = any(word in question for word in _SPORTY_ASK)
    return wants_practical and not wants_showy


def adjustment(query: str, row: dict[str, object]) -> float:
    """Nudge ranking using the current words. This does not call an embedding API."""
    text = " ".join(
        str(row.get(field) or "")
        for field in ("make", "model", "trim", "title")
    ).lower()
    question = query.lower()
    score = 0.0
    wants_practical = any(word in question for word in _PRACTICAL)
    wants_showy = any(word in question for word in _SPORTY_ASK)
    if wants_practical and not wants_showy:
        if any(word in text for word in _SHOWY):
            score -= 0.25
        price = row.get("price_aed")
        if price is None:
            score -= 0.05
        else:
            score += 0.08
            if int(price) <= 50000:
                score += 0.12
            elif int(price) >= 150000:
                score -= 0.15
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", question)
        if len(token) > 3 and token not in _STOP
    ]
    score += 0.04 * sum(1 for token in tokens if token in text)
    return score
