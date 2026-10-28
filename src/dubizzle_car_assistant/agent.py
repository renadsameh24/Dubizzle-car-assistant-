import json
import logging
import os
import re
import time
from datetime import datetime

from dubizzle_car_assistant.inventory import load_listings
from dubizzle_car_assistant.retrieval.constraints import makes_for
from dubizzle_car_assistant.retrieval.rerank import is_showy, prefers_listed_price
from dubizzle_car_assistant.retrieval.semantic import load_gemini_key
from dubizzle_car_assistant.retrieval.hybrid import hybrid_search
from dubizzle_car_assistant.retrieval.structured import get_listing_row, inventory_counts, search_listings
from dubizzle_car_assistant.retrieval.evidence import advertiser_evidence, has_features, requested_features
from dubizzle_car_assistant.memory.profile import (
    add_preference,
    like_car,
    load_profile,
    remember_user,
)
from dubizzle_car_assistant.booking import (
    DUBAI,
    asks_open_times,
    bare_listing_id,
    latest_schedule,
    open_times_reply,
    request_viewing,
    uses_saved_booking,
)
from dubizzle_car_assistant.conversation import (
    confirmed_only,
    inherited_constraints,
    inventory_question,
    is_detail_question,
    listing_subject,
    merge_constraints,
    message_names_constraint,
    named_listing_ids,
    normalize_makes,
    opens_one_listing,
    price_cap,
    preference_save,
    ranking_request,
    recall_question,
    refers_to_previous_search,
    wants_global_catalog,
    wants_more_results,
)
from dubizzle_car_assistant.memory.session import (
    get_search_state,
    get_searches,
    record_search,
    save_search_state,
)
from dubizzle_car_assistant.leads import request_lead

DEFAULT_CHAT_MODEL = "gemini/gemini-flash-lite-latest"
DEFAULT_CHAT_FALLBACK = "gemini/gemini-3.5-flash-lite"
logger = logging.getLogger("dubizzle")

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_cars",
        "description": (
            "Search the dubizzle inventory. Put subjective words such as "
            "family, comfortable, or sporty in query. Put a stated make, model, "
            "year, price, or mileage limit in the matching field. Price is AED."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the user wants the car to be like.",
                },
                "make": {"type": "string"},
                "model": {"type": "string"},
                "year_min": {"type": "integer"},
                "year_max": {"type": "integer"},
                "price_max": {
                    "type": "integer",
                    "description": "Maximum sale price in AED, not a monthly payment.",
                },
                "mileage_max": {
                    "type": "integer",
                    "description": "Maximum odometer reading in kilometers.",
                },
                "origin": {
                    "type": "string",
                    "enum": ["japanese", "korean", "german", "american", "british", "italian"],
                    "description": (
                        "Use when the user names a country or region, such as Japanese. "
                        "Do not put that word in make."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

SAVE_TOOL = {
    "type": "function",
    "function": {
        "name": "save_visitor_memory",
        "description": (
            "Save the current visitor's name, a car preference, or a listing "
            "they like. Call this when they state one of those."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "preference": {
                    "type": "string",
                    "description": "A car preference in the visitor's own words.",
                },
                "liked_listing_id": {"type": "integer"},
            },
        },
    },
}

BOOK_TOOL = {
    "type": "function",
    "function": {
        "name": "book_viewing",
        "description": (
            "Book viewings in Asia/Dubai. Copy the shopper's own day and time words into when. "
            "Do not calculate the calendar date yourself. "
            "Use listing_ids when they name more than one car."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "listing_id": {"type": "integer"},
                "listing_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "when": {
                    "type": "string",
                    "description": "The shopper's own words, for example next Saturday at 9 AM.",
                },
            },
            "required": ["when"],
        },
    },
}

LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "save_lead",
        "description": (
            "Save a sales lead when the visitor has both a maximum budget "
            "in AED and a description of the car they need."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "price_max_aed": {
                    "type": "integer",
                    "description": "Maximum sale price in AED, not a monthly payment.",
                },
                "needs": {
                    "type": "string",
                    "description": "What the visitor wants, in their own words.",
                },
            },
            "required": ["price_max_aed", "needs"],
        },
    },
}

def propose_search(user_message: str) -> dict[str, object]:
    load_gemini_key()
    import litellm

    response = litellm.completion(
        model=chat_models()[0],
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a dubizzle car shopping assistant. "
                    "The inventory is available only through search_cars. "
                    "Do not invent listings."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        tools=[SEARCH_TOOL],
        tool_choice="auto",
    )
    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return {"tool": None, "text": message.content}
    call = tool_calls[0]
    return {
        "tool": call.function.name,
        "arguments": json.loads(call.function.arguments),
    }

SEARCH_FIELDS = ("make", "model", "year_min", "year_max", "price_max", "mileage_max")
LOCK_FIELDS = ("origin", "make", "model", "year_min", "year_max", "price_max", "mileage_max")


class ChatAnswer:
    def __init__(
        self,
        reply: str,
        listings: list[dict[str, object]] | None = None,
        *,
        answered: bool = True,
    ):
        self.reply = reply
        self.listings = listings or []
        self.answered = answered


def dropped_constraint(arguments: dict[str, object], locked: dict[str, object] | None) -> str | None:
    if not locked:
        return None
    for field, value in locked.items():
        if arguments.get(field) != value:
            return "Do not drop a constraint the user already stated."
    return None


def _cards(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    listings = load_listings().set_index("listing_id")
    cards: list[dict[str, object]] = []
    for row in rows:
        listing_id = int(row["listing_id"])
        source = listings.loc[listing_id]
        photo = str(source["photo_url"] or "").strip()
        cards.append(
            {
                "listing_id": listing_id,
                "year": int(row["year"]),
                "make": row["make"],
                "model": row["model"],
                "trim": source["trim"],
                "price_aed": None if row["price_aed"] is None else int(row["price_aed"]),
                "mileage_km": None if row["mileage_km"] is None else int(row["mileage_km"]),
                "title": row["title"],
                "photo_url": photo if photo.startswith("http") else "",
                "match_kind": str(row.get("match_kind") or "confirmed"),
                "reason": str(row.get("reason") or ""),
                "evidence": str(row.get("evidence") or ""),
            }
        )
    return cards


def next_shown(payload: dict[str, object], shown: list[dict[str, object]]) -> list[dict[str, object]]:
    """A search with no rows clears cards. A narrower follow-up lookup keeps the broader set."""
    if "cards" not in payload:
        return shown
    cards = payload.get("cards")
    cards = list(cards) if isinstance(cards, list) else []
    if shown and cards:
        new_ids = {int(card["listing_id"]) for card in cards}
        old_ids = {int(card["listing_id"]) for card in shown}
        if new_ids < old_ids:
            return shown
    return cards


def _whole_number(value: object, field: str, *, low: int | None = None, high: int | None = None) -> tuple[int | None, str | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, f"{field} must be a whole number."
    number: int | None = None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
            number = int(cleaned)
    if number is None:
        return None, f"{field} must be a whole number."
    if low is not None and number < low:
        return None, f"{field} is out of range."
    if high is not None and number > high:
        return None, f"{field} is out of range."
    return number, None


def _money_text(value: object) -> str:
    if value is None:
        return "price not listed"
    return f"{int(value):,} AED"


def _distance_text(value: object) -> str:
    if value is None:
        return "mileage not listed"
    return f"{int(value):,} km"


def _reason(row: dict[str, object], filters: dict[str, object], kind: str) -> str:
    parts: list[str] = []
    if filters.get("origin"):
        parts.append(f"{filters['origin']} make")
    if filters.get("make"):
        parts.append(str(filters["make"]))
    if filters.get("year_min"):
        parts.append(f"year {row['year']} is from {int(filters['year_min'])} onwards")
    if filters.get("year_max") and filters.get("year_max") != filters.get("year_min"):
        parts.append(f"year {row['year']} is up to {int(filters['year_max'])}")
    price_max = filters.get("price_max")
    if kind == "confirmed" and price_max is not None and row.get("price_aed") is not None:
        parts.append(f"listed price {_money_text(row['price_aed'])} is within {_money_text(price_max)}")
    if kind == "potential" and price_max is not None and row.get("price_aed") is None:
        parts.append("price is not listed, so this is not confirmed as inside the budget")
    mileage_max = filters.get("mileage_max")
    if kind == "confirmed" and mileage_max is not None and row.get("mileage_km") is not None:
        parts.append(f"mileage {_distance_text(row['mileage_km'])} is within {_distance_text(mileage_max)}")
    if kind == "potential" and mileage_max is not None and row.get("mileage_km") is None:
        parts.append("mileage is not listed, so this is not confirmed as inside the mileage limit")
    return ". ".join(parts)


POTENTIAL_PAGE = 3
_STATE_FIELDS = ("origin", "make", "model", "year_min", "year_max", "price_max", "mileage_max")


def _apply_active(arguments: dict[str, object], active: dict[str, object] | None) -> None:
    if not active:
        return
    for field in _STATE_FIELDS:
        if active.get(field) is not None:
            arguments[field] = active[field]


def _validated_arguments(arguments: dict[str, object]) -> str | None:
    for field, low, high in (
        ("year_min", 1900, 2100),
        ("year_max", 1900, 2100),
        ("price_max", 0, 100_000_000),
        ("mileage_max", 0, 1_000_000),
    ):
        number, error = _whole_number(arguments.get(field), field, low=low, high=high)
        if error:
            return error
        if arguments.get(field) is not None:
            arguments[field] = number
    for field in ("make", "model", "origin", "query"):
        value = arguments.get(field)
        if value is not None and not isinstance(value, str):
            return f"{field} must be text."
    return None


def _annotate(
    rows: list[dict[str, object]],
    *,
    kind: str,
    filters: dict[str, object],
    features: list[str],
) -> list[dict[str, object]]:
    annotated = []
    for row in rows:
        listing_id = int(row["listing_id"])
        if features and not has_features(listing_id, features):
            continue
        item = dict(row)
        item["match_kind"] = kind
        item["reason"] = _reason(item, filters, kind)
        item["evidence"] = advertiser_evidence(listing_id, features)
        annotated.append(item)
    return annotated


def _potential_page(
    *,
    query: str,
    filters: dict[str, object],
    confirmed_ids: set[int],
    features: list[str],
    offset: int,
    strict: bool,
) -> tuple[list[dict[str, object]], int]:
    price_max = filters.get("price_max")
    mileage_max = filters.get("mileage_max")
    if strict or (price_max is None and mileage_max is None):
        return [], 0
    base = {key: value for key, value in filters.items() if key not in {"price_max", "mileage_max"}}
    pool = search_listings(limit=100, **base)
    if prefers_listed_price(query):
        practical = [row for row in pool if not is_showy(row)]
        if practical:
            pool = practical
    eligible = []
    for row in pool:
        listing_id = int(row["listing_id"])
        if listing_id in confirmed_ids:
            continue
        price = row["price_aed"]
        mileage = row["mileage_km"]
        if price_max is not None and price is not None and int(price) > int(price_max):
            continue
        if mileage_max is not None and mileage is not None and int(mileage) > int(mileage_max):
            continue
        missing_price = price_max is not None and price is None
        missing_mileage = mileage_max is not None and mileage is None
        if not missing_price and not missing_mileage:
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: int(row["listing_id"]))
    page = eligible[offset : offset + POTENTIAL_PAGE]
    return _annotate(page, kind="potential", filters=filters, features=features), len(eligible)


def describe_results(payload: dict[str, object]) -> str:
    confirmed = payload.get("results") if isinstance(payload.get("results"), list) else []
    potential = payload.get("potential") if isinstance(payload.get("potential"), list) else []
    lines: list[str] = []
    if not confirmed and not potential:
        return str(payload.get("note") or "No listing matched.")
    if confirmed:
        lines.append("Confirmed matches:")
        for row in confirmed:
            lines.append(
                f"- {row['year']} {str(row['make']).title()} {str(row['model']).title()} "
                f"(listing {row['listing_id']}, {_money_text(row.get('price_aed'))}, {_distance_text(row.get('mileage_km'))})"
            )
    if potential:
        lines.append(
            "Potential matches — price or mileage needs confirmation. "
            "These are not confirmed as inside the budget or mileage limit:"
        )
        for row in potential:
            lines.append(
                f"- {row['year']} {str(row['make']).title()} {str(row['model']).title()} "
                f"(listing {row['listing_id']}, {_money_text(row.get('price_aed'))}, {_distance_text(row.get('mileage_km'))})"
            )
    note = payload.get("note")
    if note:
        lines.append(str(note))
    return "\n".join(lines)


def ground_reply(text: str, cards: list[dict[str, object]], fallback: str) -> str:
    """Drop a reply that names a car the search did not return."""
    if not text.strip():
        return fallback
    allowed = {int(card["listing_id"]) for card in cards}
    mentioned = {int(number) for number in re.findall(r"\blisting\s+(\d+)\b", text, re.I)}
    if mentioned - allowed:
        return fallback
    from dubizzle_car_assistant.inventory import load_listings

    card_makes = {str(card["make"]).lower() for card in cards}
    catalog = {str(make).lower() for make in load_listings()["make"].unique()}
    for make in catalog:
        if make in card_makes:
            continue
        if re.search(rf"\b{re.escape(make)}\b", text, re.I):
            return fallback
    potential_ids = {int(card["listing_id"]) for card in cards if card.get("match_kind") == "potential"}
    if potential_ids and re.search(r"\bunder (?:the |your )?budget\b", text, re.I):
        if not re.search(r"\bnot\b[\s\S]{0,80}under (?:the |your )?budget", text, re.I):
            return fallback
    return text


def run_search(
    arguments: dict[str, object],
    locked: dict[str, object] | None = None,
    remembered_price_max: int | None = None,
    *,
    active: dict[str, object] | None = None,
    user_text: str = "",
    potential_offset: int = 0,
) -> dict[str, object]:
    arguments = dict(arguments)
    _apply_active(arguments, active)
    if isinstance(arguments.get("query"), str):
        arguments["query"] = normalize_makes(arguments["query"])
    if isinstance(arguments.get("make"), str):
        arguments["make"] = normalize_makes(arguments["make"])
    invalid = _validated_arguments(arguments)
    if invalid:
        return {"error": invalid, "results": [], "potential": [], "cards": []}
    kept_budget = False
    if remembered_price_max is not None:
        model_max = arguments.get("price_max")
        arguments["price_max"] = remembered_price_max
        kept_budget = model_max != remembered_price_max
    if locked:
        for field, value in locked.items():
            arguments[field] = value
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "search_cars requires a query string", "results": [], "potential": [], "cards": []}
    origin = arguments.get("origin")
    make = arguments.get("make") if isinstance(arguments.get("make"), str) else None
    try:
        makes = makes_for(origin if isinstance(origin, str) else None, make)
    except ValueError as error:
        return {"error": str(error), "results": [], "potential": [], "cards": []}
    filters = {
        field: arguments[field]
        for field in SEARCH_FIELDS
        if field != "make" and arguments.get(field) is not None
    }
    if makes is not None:
        filters["makes"] = makes
    soft_budget = False
    if filters.get("price_max") is None and prefers_listed_price(query) and not confirmed_only(user_text):
        filters["price_max"] = 50000
        arguments["price_max"] = 50000
        soft_budget = True
    features = requested_features(f"{user_text} {query}")
    strict = confirmed_only(user_text)
    excluded: list[dict[str, object]] = []
    if arguments.get("price_max") is not None:
        unpriced_filters = {key: value for key, value in filters.items() if key != "price_max"}
        excluded = search_listings(price_is_null=True, limit=100, **unpriced_filters)
    started = time.perf_counter()
    rows = hybrid_search(query.strip(), limit=5, **filters)
    if filters.get("price_max") is not None:
        rows = sorted(rows, key=lambda row: (int(row["price_aed"]), int(row["listing_id"])))
    logger.info("search_ms=%.0f filters=%s results=%s", (time.perf_counter() - started) * 1000, filters, len(rows))
    confirmed = _annotate(rows, kind="confirmed", filters=filters, features=features)
    potential, potential_total = _potential_page(
        query=query,
        filters=filters,
        confirmed_ids={int(row["listing_id"]) for row in confirmed},
        features=features,
        offset=max(0, potential_offset),
        strict=strict,
    )
    note = None
    if excluded and arguments.get("price_max") is not None:
        if strict:
            note = (
                f"{len(excluded)} cars matched the other constraints but have no listed price. "
                "They are hidden because this search asked for confirmed prices only."
            )
        else:
            note = (
                f"{len(excluded)} cars matched the other constraints but have no listed price, "
                "so they were not treated as inside the budget."
            )
            if potential_total > len(potential):
                note += f" Showing {len(potential)} of {potential_total}. Ask to see more."
    if kept_budget:
        budget_note = (
            f"Kept the shopper's earlier budget of {int(arguments['price_max']):,} AED. "
            "Ask before looking above it."
        )
        note = f"{note} {budget_note}".strip() if note else budget_note
    elif soft_budget:
        soft_note = (
            "No exact budget was given, so only cars with a listed price of 50,000 AED or less are included."
        )
        note = f"{note} {soft_note}".strip() if note else soft_note
    if features and not confirmed and not potential:
        note = (note or "") + " No advertisement in this search states that feature."
    if not confirmed:
        note = (note or "") + " No listing satisfied every stated constraint."
    compact = [
        {
            "listing_id": row["listing_id"],
            "year": row["year"],
            "make": row["make"],
            "model": row["model"],
            "price_aed": row["price_aed"],
            "mileage_km": row["mileage_km"],
            "title": row["title"],
            "match_kind": "confirmed",
            "reason": row.get("reason") or "",
            "evidence": row.get("evidence") or "",
        }
        for row in confirmed
    ]
    potential_compact = [
        {
            "listing_id": row["listing_id"],
            "year": row["year"],
            "make": row["make"],
            "model": row["model"],
            "price_aed": row["price_aed"],
            "mileage_km": row["mileage_km"],
            "title": row["title"],
            "match_kind": "potential",
            "reason": row.get("reason") or "",
            "evidence": row.get("evidence") or "",
        }
        for row in potential
    ]
    return {
        "results": compact,
        "potential": potential_compact,
        "potential_total": potential_total,
        "applied_filters": {key: value for key, value in arguments.items() if key in LOCK_FIELDS and value is not None},
        "excluded_unpriced": len(excluded) if arguments.get("price_max") is not None else 0,
        "note": note,
        "features": features,
        "cards": _cards(confirmed + potential),
    }

def save_visitor_memory(
    user_id: str | None,
    arguments: dict[str, object],
) -> dict[str, object]:
    if user_id is None or not user_id.strip():
        return {"saved": False, "error": "No visitor id was provided."}

    name = arguments.get("name")
    preference = arguments.get("preference")
    liked_listing_id = arguments.get("liked_listing_id")
    saved: list[str] = []

    if isinstance(name, str) and name.strip():
        remember_user(user_id, name)
        saved.append("name")

    needs_user = (isinstance(preference, str) and preference.strip()) or type(liked_listing_id) is int
    if needs_user and "name" not in saved and not load_profile(user_id)["name"]:
        remember_user(user_id, user_id.strip())

    if isinstance(preference, str) and preference.strip():
        add_preference(user_id, preference)
        saved.append("preference")

    if type(liked_listing_id) is int:
        like_car(user_id, liked_listing_id)
        saved.append("liked_listing_id")

    if not saved:
        return {"saved": False, "error": "Nothing to save."}
    result: dict[str, object] = {"saved": True, "fields": saved}
    if isinstance(preference, str) and preference.strip():
        result["preference"] = preference.strip()
    if type(liked_listing_id) is int:
        result["liked_listing_id"] = liked_listing_id
    return result

SYSTEM_PROMPT = (
    "You are a dubizzle car shopping assistant. "
    "The inventory is available only through search_cars. "
    "Mention only cars in that tool's results list. "
    "Use their listing id, make, model, year, and price. "
    "If price_aed or mileage_km is null, say it is not listed. "
    "A stated budget, make, origin, year, or mileage limit is a hard constraint. "
    "If they want something affordable but give no number, still call search_cars and do not invent a price_max. "
    "Cars with no listed price do not satisfy a budget. "
    "If results is empty, say that no listing matched. Use note if it is present. "
    "Do not recommend cars from a different brand or country to fill the gap. "
    "Do not search again without a constraint the user stated. "
    "If the user refers to a car you already showed, answer from the conversation. "
    "Call search_cars again only when they ask for a different car. "
    "Do not invent listings, prices, mileage, or features. "
    "When the user gives their name, a car preference, or a listing they like, "
    "call save_visitor_memory, then confirm only the fields that tool saved. "
    "When the user asks to book a viewing, call book_viewing and copy their day and time into when. "
    "Do not convert next Saturday or Monday into your own calendar date. "
    "If they change only the time, day, or car, call book_viewing with their new words. "
    "The saved booking belongs to the visitor and keeps the details they did not change. "
    "If they have more than one booking, ask which one before changing it. "
    "If they ask what they booked, call book_viewing with their question and repeat its summary. "
    "Confirm a booking only when book_viewing returns booked true, and use the summary it returns. "
    "When it returns booked false, tell the user that exact reason. "
    "If the latest search had no cars, do not book an older car. "
    "Keep an earlier budget unless the user changes it or clearly drops it. "
    "When the user states both a maximum budget and what car they need, call save_lead. "
    "Say the lead was saved only when save_lead returns saved true. "
    "When it returns saved false, tell the user which detail is missing. "
    "If the user asks for something that is not about buying, viewing, or comparing cars, "
    "politely decline and offer to help with dubizzle cars. "
    "Do not answer the unrelated question. "
    "Do not name or recommend any other car website or marketplace. "
    "Confirmed matches satisfy every hard limit with known values. "
    "Potential matches are missing a price or mileage, so never say they are under the budget or mileage limit. "
    "Mention a feature only when the tool result includes that advertiser claim. "
    "Do not offer to book a viewing or save a lead unless the user is trying to do that. "
    "A search limit is not a permanent preference. Call save_visitor_memory only when they ask to be remembered or name a car they like. "
    "A question about one car already identified does not need a new search."
)

BUSY_REPLY = "The car assistant is busy right now. Please try again in a minute."
INCOMPLETE_REPLY = "I couldn't complete that. Please try the question again."
RETRY_PAUSES = (1.0,)
_TRY_NEXT_MODEL = (
    "ServiceUnavailableError",
    "RateLimitError",
    "InternalServerError",
    "APIConnectionError",
    "Timeout",
    "NotFoundError",
)
_STOP_STATUSES = {401, 403}


def chat_models() -> list[str]:
    """Primary chat model, then one fallback. Names come from the environment."""
    primary = os.environ.get("GEMINI_CHAT_MODEL", DEFAULT_CHAT_MODEL).strip() or DEFAULT_CHAT_MODEL
    fallback = os.environ.get("GEMINI_CHAT_FALLBACK", DEFAULT_CHAT_FALLBACK).strip()
    models = [primary]
    if fallback and fallback not in models:
        models.append(fallback)
    return models


def _safe_detail(error: BaseException) -> str:
    text = " ".join(str(error).split())
    text = re.sub(r"AIza[0-9A-Za-z_\-]{8,}", "[redacted]", text)
    text = re.sub(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*)\S+", r"\1[redacted]", text)
    return text[:180]


def _pause_seconds(error: BaseException) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is not None and hasattr(headers, "get"):
        raw = headers.get("retry-after")
        if raw:
            try:
                return min(max(float(raw), 0.0), 2.0)
            except ValueError:
                pass
    pause = RETRY_PAUSES[0] if RETRY_PAUSES else 1.0
    return min(max(pause, 0.0), 2.0)


def completion_or_none(**kwargs: object) -> tuple[object | None, str | None]:
    """Call Gemini. On 503, 429, or a retired model, try the fallback once.

    The second value is 'transient' or 'failed' when there is no response.
    The provider message is logged without the API key.
    """
    import litellm

    transient_types = (
        litellm.ServiceUnavailableError,
        litellm.RateLimitError,
        litellm.InternalServerError,
        litellm.APIConnectionError,
        litellm.Timeout,
        litellm.NotFoundError,
    )
    models = kwargs.pop("models", None)
    kwargs.pop("model", None)
    if not isinstance(models, list) or not models:
        models = chat_models()
    last_kind = "failed"
    for index, model in enumerate(models):
        kwargs["model"] = model
        started = time.perf_counter()
        try:
            response = litellm.completion(**kwargs)
            logger.info(
                "gemini_ok model=%s ms=%.0f",
                model,
                (time.perf_counter() - started) * 1000,
            )
            return response, None
        except Exception as error:
            status = getattr(error, "status_code", None)
            try_next = (
                isinstance(error, transient_types)
                or type(error).__name__ in _TRY_NEXT_MODEL
                or status in {404, 408, 429, 500, 502, 503, 504}
            )
            if status in _STOP_STATUSES:
                try_next = False
            last_kind = "transient" if try_next else "failed"
            logger.warning(
                "gemini_error type=%s status=%s model=%s try_next=%s detail=%s",
                type(error).__name__,
                status,
                model,
                try_next,
                _safe_detail(error),
            )
            if not try_next or index == len(models) - 1:
                return None, last_kind
            time.sleep(_pause_seconds(error))
    return None, last_kind


def _fallback(payloads: list[dict[str, object]], shown: list[dict[str, object]]) -> str:
    """A reply built from the tool result when the model returns no text."""
    for payload in reversed(payloads):
        if payload.get("summary"):
            return str(payload["summary"])
        if payload.get("booked") is True:
            when = payload.get("resolved_when") or payload.get("when")
            return f"Booked listing {payload.get('listing_id')} for {when}."
        if payload.get("booked") is False and payload.get("reason"):
            return str(payload["reason"])
        bookings = payload.get("bookings")
        if isinstance(bookings, list) and bookings:
            lines = []
            for item in bookings:
                if item.get("booked"):
                    lines.append(f"Listing {item.get('listing_id')} is booked for {item.get('when')}.")
                else:
                    lines.append(f"Listing {item.get('listing_id')}: {item.get('reason')}")
            return " ".join(lines)
        if payload.get("error"):
            return str(payload["error"])
        if payload.get("saved") is True:
            note = payload.get("preference")
            if isinstance(note, str) and note.strip():
                return f"Saved. You want {note.strip()}."
            liked = payload.get("liked_listing_id")
            if type(liked) is int:
                return f"Saved listing {liked} as a car you like."
            return "Saved."
        if "results" in payload:
            return describe_results(payload)
    if shown:
        return "Here are the matching cars."
    return INCOMPLETE_REPLY


def _finish(text: str, payloads: list[dict[str, object]], shown: list[dict[str, object]]) -> ChatAnswer:
    fallback = _fallback(payloads, shown)
    return ChatAnswer(ground_reply(text or fallback, shown, fallback), shown)


def _listing_filters(state: dict[str, object], *, price_is_null: bool = False, mileage_is_null: bool = False) -> dict[str, object]:
    origin = state.get("origin") if isinstance(state.get("origin"), str) else None
    make = state.get("make") if isinstance(state.get("make"), str) else None
    try:
        makes = makes_for(origin, make)
    except ValueError:
        makes = []
    filters: dict[str, object] = {}
    if makes is not None:
        filters["makes"] = makes
    if isinstance(state.get("model"), str):
        filters["model"] = state["model"]
    if isinstance(state.get("year_min"), int):
        filters["year_min"] = state["year_min"]
    if isinstance(state.get("year_max"), int):
        filters["year_max"] = state["year_max"]
    if price_is_null:
        filters["price_is_null"] = True
    elif isinstance(state.get("price_max"), int):
        filters["price_max"] = state["price_max"]
    if mileage_is_null:
        filters["mileage_is_null"] = True
    elif isinstance(state.get("mileage_max"), int):
        filters["mileage_max"] = state["mileage_max"]
    return filters


def _catalog_scope(text: str, state: dict[str, object]) -> dict[str, object]:
    """Bare catalog questions stay global. A reference to the previous search keeps its limits."""
    if wants_global_catalog(text) and not refers_to_previous_search(text):
        return {}
    if refers_to_previous_search(text):
        merged, _ = merge_constraints(state, text)
        return {field: merged[field] for field in _STATE_FIELDS if field in merged}
    if message_names_constraint(text):
        merged, _ = merge_constraints({}, text)
        return {field: merged[field] for field in _STATE_FIELDS if field in merged}
    return {}


def _catalog_answer(
    kind: str,
    state: dict[str, object],
    session_id: str | None,
    offset: int = 0,
    text: str = "",
) -> ChatAnswer:
    counts = inventory_counts()
    if kind == "both_count":
        reply = (
            f"{counts['both_known']} of {counts['total']} listings have both a listed price and a listed mileage. "
            f"{counts['priced']} have a price. {counts['with_mileage']} have mileage. "
            "Missing values stay unknown."
        )
        return ChatAnswer(reply, [])
    scoped = _catalog_scope(text, state)
    page_size = 5
    if kind == "unpriced":
        filters = _listing_filters(scoped, price_is_null=True)
        label = "no listed price"
    else:
        filters = _listing_filters(scoped, mileage_is_null=True)
        label = "unknown mileage"
    rows = search_listings(limit=page_size, offset=offset, **filters)
    total = count_from_filters(filters)
    return _catalog_page(kind, state, session_id, offset, filters, rows=rows, total=total, text=text)


def _catalog_page(
    kind: str,
    state: dict[str, object],
    session_id: str | None,
    offset: int,
    filters: dict[str, object],
    rows: list[dict[str, object]] | None = None,
    total: int | None = None,
    text: str = "",
) -> ChatAnswer:
    page_size = 5
    label = "no listed price" if kind == "unpriced" else "unknown mileage"
    if rows is None:
        rows = search_listings(limit=page_size, offset=offset, **filters)
    if total is None:
        total = count_from_filters(filters)
    scoped = _catalog_scope(text, state) if text else {}
    if not scoped and filters.get("makes"):
        scoped = {field: state[field] for field in _STATE_FIELDS if field in state}
    limit_note = _scope_note(scoped)
    for row in rows:
        row["match_kind"] = "inventory"
        row["reason"] = "This is a catalog listing. A missing price or mileage is unknown, not zero."
        if limit_note:
            row["reason"] = f"{row['reason']} {limit_note}"
    cards = _cards(rows)
    if not rows:
        reply = f"There are no further cars with {label}. {total} in total."
    else:
        reply = f"Showing {offset + 1}–{offset + len(rows)} of {total} cars with {label}."
        if limit_note:
            reply += f" {limit_note}"
        else:
            reply += " Ask to see more."
    if session_id:
        stored = dict(state)
        stored["list_mode"] = kind
        stored["list_offset"] = offset
        stored["list_filters"] = filters
        save_search_state(session_id, stored)
    return ChatAnswer(reply, cards)


def count_from_filters(filters: dict[str, object]) -> int:
    from dubizzle_car_assistant.retrieval.structured import count_listings

    return count_listings(**filters)


def _scope_note(scoped: dict[str, object]) -> str:
    parts = []
    if scoped.get("origin"):
        parts.append(f"{scoped['origin']} makes")
    if scoped.get("make"):
        parts.append(str(scoped["make"]))
    if scoped.get("model"):
        parts.append(str(scoped["model"]))
    if scoped.get("year_min") and scoped.get("year_max") == scoped.get("year_min"):
        parts.append(f"from {int(scoped['year_min'])} only")
    elif scoped.get("year_min"):
        parts.append(f"from {int(scoped['year_min'])} onwards")
    elif scoped.get("year_max"):
        parts.append(f"up to {int(scoped['year_max'])}")
    if not parts:
        return ""
    return "Limited to " + ", ".join(parts) + ". A missing price or mileage is unknown, not inside an earlier budget."


_RANK_LABELS = {
    "cheapest": "lowest listed price",
    "expensive": "highest listed price",
    "newest": "newest model year",
    "lowest_mileage": "lowest listed mileage",
}


def _ranking_answer(kind: str, constraints: dict[str, object], session_id: str | None, global_scope: bool) -> ChatAnswer:
    filters = _listing_filters(constraints)
    filters.pop("price_max", None)
    filters.pop("mileage_max", None)
    if kind in {"cheapest", "expensive"} and isinstance(constraints.get("price_max"), int) and not global_scope:
        filters["price_max"] = constraints["price_max"]
    if kind == "lowest_mileage" and isinstance(constraints.get("mileage_max"), int) and not global_scope:
        filters["mileage_max"] = constraints["mileage_max"]
    rows = search_listings(order=kind, limit=5, **filters)
    scope = "the whole inventory" if global_scope or not constraints else "the current limits"
    label = _RANK_LABELS[kind]
    for row in rows:
        row["match_kind"] = "confirmed"
        row["reason"] = f"{label.capitalize()} in {scope}."
    cards = _cards(rows)
    if not rows:
        reply = f"No car in {scope} has a listed value for this comparison."
    else:
        first = rows[0]
        reply = (
            f"The {label} in {scope} is the {first['year']} {str(first['make']).title()} "
            f"{str(first['model']).title()} "
            f"(listing {first['listing_id']}, {_money_text(first.get('price_aed'))}, "
            f"{_distance_text(first.get('mileage_km'))}). "
            f"Showing {len(rows)} cars, ordered by that comparison. "
            "Cars with a missing price or mileage are left out of a comparison they cannot confirm."
        )
    if session_id:
        stored = {field: constraints[field] for field in _STATE_FIELDS if field in constraints}
        save_search_state(session_id, stored)
        record_search(session_id, [int(row["listing_id"]) for row in rows])
    return ChatAnswer(reply, cards)


def _features_for(text: str, state: dict[str, object], history: list[dict[str, str]] | None) -> list[str]:
    features = requested_features(text)
    if features:
        return features
    saved = state.get("last_features")
    if isinstance(saved, list) and saved:
        return [str(item) for item in saved]
    for turn in reversed(history or []):
        if turn.get("role") == "user":
            earlier = requested_features(str(turn.get("content") or ""))
            if earlier:
                return earlier
    return []


def _detail_answer(
    listing_id: int,
    text: str,
    state: dict[str, object],
    history: list[dict[str, str]] | None,
    session_id: str | None,
) -> ChatAnswer:
    row = get_listing_row(listing_id)
    if row is None:
        return ChatAnswer(f"Listing {listing_id} is not in the inventory.", [])
    features = _features_for(text, state, history)
    evidence = advertiser_evidence(listing_id, features) if features else ""
    name = f"{row['year']} {str(row['make']).title()} {str(row['model']).title()}"
    asking_words = re.search(r"advertisement|what does the ad|that feature|accident|service history", text, re.I)
    if asking_words and features:
        if evidence:
            reply = (
                f"For the {name} (listing {listing_id}), {evidence} "
                "This is the advertiser's wording, not an independently verified specification."
            )
        else:
            reply = (
                f"The advertisement for the {name} (listing {listing_id}) does not state that feature. "
                "Nothing was added beyond the advertisement."
            )
    else:
        reply = (
            f"The {name} (listing {listing_id}): {_money_text(row.get('price_aed'))}, "
            f"{_distance_text(row.get('mileage_km'))}."
        )
        if evidence:
            reply += f" {evidence} This is the advertiser's wording, not an independently verified specification."
    row["match_kind"] = "inventory"
    row["reason"] = "This is the car you asked about."
    row["evidence"] = evidence
    if session_id:
        stored = {field: state[field] for field in _STATE_FIELDS if field in state}
        stored["focus_listing_id"] = listing_id
        if features:
            stored["last_features"] = features
        elif state.get("last_features"):
            stored["last_features"] = state["last_features"]
        save_search_state(session_id, stored)
        record_search(session_id, [listing_id])
    return ChatAnswer(reply, _cards([row]))


def _memory_answer(profile: dict[str, object] | None) -> ChatAnswer:
    """Answer a saved-preference question from the profile, without calling Gemini."""
    if profile is None or not str(profile.get("name") or "").strip():
        return ChatAnswer("I don't have a saved name or car preference for this visit yet.")
    name = str(profile["name"]).strip()
    preferences = [str(item).strip() for item in (profile.get("preferences") or []) if str(item).strip()]
    liked = [int(item) for item in (profile.get("liked_listing_ids") or [])]
    if preferences:
        reply = f"Your name is {name}, and you said you want {preferences[-1]}."
    else:
        reply = f"Your name is {name}. You have not told me what kind of car you want yet."
    if not liked:
        return ChatAnswer(reply)
    labels = []
    for listing_id in liked:
        row = get_listing_row(listing_id)
        if row is None:
            labels.append(f"listing {listing_id}")
        else:
            labels.append(
                f"the {row['year']} {str(row['make']).title()} {str(row['model']).title()} (listing {listing_id})"
            )
    reply += " You liked " + ", ".join(labels) + "."
    return ChatAnswer(reply)


def _save_preference_answer(
    target: str,
    state: dict[str, object],
    user_id: str | None,
) -> ChatAnswer:
    """Store a preference from the current car, or from the words they used."""
    if user_id is None or not user_id.strip():
        return ChatAnswer("Tell me your name first, then I can save that preference.")
    if target == "focus":
        focus = state.get("focus_listing_id")
        if not isinstance(focus, int):
            return ChatAnswer("Which car should I save? Tell me the listing, or the make and model.")
        row = get_listing_row(focus)
        if row is None:
            return ChatAnswer("Which car should I save? Tell me the listing, or the make and model.")
        note = f"the {row['year']} {str(row['make']).title()} {str(row['model']).title()} (listing {focus})"
        save_visitor_memory(user_id, {"preference": note, "liked_listing_id": focus})
        return ChatAnswer(f"Saved. You want {note}.")
    save_visitor_memory(user_id, {"preference": target})
    return ChatAnswer(f"Saved. You want {target}.")


def _provider_reply(kind: str | None, payloads: list[dict[str, object]], shown: list[dict[str, object]]) -> ChatAnswer:
    if payloads:
        return _finish("", payloads, shown)
    if kind == "transient":
        return ChatAnswer(BUSY_REPLY, shown, answered=False)
    return ChatAnswer(INCOMPLETE_REPLY, shown, answered=False)


def _more_answer(state: dict[str, object], session_id: str | None) -> ChatAnswer | None:
    mode = state.get("list_mode")
    if mode in {"unpriced", "unknown_mileage"}:
        offset = int(state.get("list_offset") or 0) + 5
        saved = state.get("list_filters")
        if isinstance(saved, dict):
            return _catalog_page(str(mode), state, session_id, offset, saved)
        return _catalog_answer(str(mode), state, session_id, offset)
    if not any(state.get(field) is not None for field in _STATE_FIELDS):
        return None
    offset = int(state.get("potential_offset") or 0) + POTENTIAL_PAGE
    payload = run_search(
        {"query": "more cars matching the current limits"},
        active=state,
        user_text="show more",
        potential_offset=offset,
        remembered_price_max=state.get("price_max") if isinstance(state.get("price_max"), int) else None,
    )
    if session_id:
        stored = dict(state)
        stored["potential_offset"] = offset
        save_search_state(session_id, stored)
        record_search(
            session_id,
            [int(row["listing_id"]) for row in (payload.get("results") or [])]
            + [int(row["listing_id"]) for row in (payload.get("potential") or [])],
        )
    return ChatAnswer(describe_results(payload), list(payload.get("cards") or []))

def answer(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    profile: dict[str, object] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> ChatAnswer:
    current = now or datetime.now(DUBAI)
    if user_id or session_id:
        state = get_search_state(session_id) if session_id else {}
        focus = state.get("focus_listing_id")
        focus_id = focus if isinstance(focus, int) else None
        attempted = latest_schedule(history, current)
        if asks_open_times(user_message):
            return ChatAnswer(open_times_reply(focus_id, attempted))
        listed = bare_listing_id(user_message)
        if listed is not None and attempted is not None:
            payload = request_viewing(
                {"listing_id": listed},
                user_text=user_message,
                now=current,
                session_id=session_id,
                user_id=user_id,
                slot_at=attempted,
            )
            return ChatAnswer(str(payload.get("summary") or payload.get("reason") or ""), [])
        if uses_saved_booking(user_message):
            payload = request_viewing(
                {},
                user_text=user_message,
                now=current,
                session_id=session_id,
                user_id=user_id,
                shown_searches=get_searches(session_id) if session_id else [],
                focus_listing_id=focus_id,
                attempted=attempted,
            )
            return ChatAnswer(str(payload.get("summary") or payload.get("reason") or ""), [])
    normalized = normalize_makes(user_message)
    if recall_question(normalized):
        return _memory_answer(profile)
    target = preference_save(normalized)
    if target is not None:
        state = get_search_state(session_id) if session_id else {}
        return _save_preference_answer(target, state, user_id)
    state = get_search_state(session_id) if session_id else {}
    catalog = inventory_question(normalized)
    if catalog:
        return _catalog_answer(catalog, state, session_id, text=normalized)
    if wants_more_results(normalized):
        more = _more_answer(state, session_id)
        if more is not None:
            return more
    global_scope = wants_global_catalog(normalized) and not refers_to_previous_search(normalized)
    merged, clarification = merge_constraints(inherited_constraints(state, normalized), normalized)
    if clarification:
        return ChatAnswer(clarification, [])
    constraints = {field: merged[field] for field in _STATE_FIELDS if field in merged}
    rank = ranking_request(normalized)
    if rank:
        return _ranking_answer(rank, constraints, session_id, global_scope)
    searches = get_searches(session_id) if session_id else []
    focus = state.get("focus_listing_id")
    focus_id = focus if isinstance(focus, int) else None
    if searches or focus_id is not None or named_listing_ids(normalized) or is_detail_question(normalized):
        subject = listing_subject(normalized, searches, _catalog_map(), focus_id)
        if opens_one_listing(normalized, subject):
            if subject.get("action") == "ask":
                return ChatAnswer(str(subject.get("reason") or "Which car do you mean?"), [])
            return _detail_answer(int(subject["listing_id"]), normalized, {**state, **constraints}, history, session_id)
    if session_id:
        changed = any(state.get(field) != constraints.get(field) for field in _STATE_FIELDS)
        stored = dict(constraints)
        stored["potential_offset"] = 0 if changed else int(state.get("potential_offset") or 0)
        if not global_scope and state.get("focus_listing_id") is not None:
            stored["focus_listing_id"] = state["focus_listing_id"]
        if not global_scope and state.get("last_features"):
            stored["last_features"] = state["last_features"]
        save_search_state(session_id, stored)
        state = stored
    active = {field: merged[field] for field in _STATE_FIELDS if field in merged}
    started = time.perf_counter()
    load_gemini_key()

    system = SYSTEM_PROMPT
    if profile is not None and profile.get("name"):
        preferences = profile["preferences"] or []
        liked = profile["liked_listing_ids"] or []
        system += (
            "\n\nReturning visitor:\n"
            f"Name: {profile['name']}\n"
            f"Preferences: {', '.join(preferences) or 'none'}\n"
            f"Liked listing ids: {', '.join(str(item) for item in liked) or 'none'}\n"
            "Use this for their name and stated preferences. "
            "The last preference is the current one. "
            "It is not the inventory. Call search_cars when they want to see cars."
        )

    messages: list[dict[str, object]] = [
        {"role": "system", "content": system},
    ]

    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_message})
    remembered_budget = price_cap(
        [normalize_makes(str(turn["content"])) for turn in (history or []) if turn.get("role") == "user"],
        normalized,
    )
    if isinstance(active.get("price_max"), int):
        remembered_budget = int(active["price_max"])

    tools = [SEARCH_TOOL, SAVE_TOOL, BOOK_TOOL, LEAD_TOOL]
    payloads: list[dict[str, object]] = []
    locked: dict[str, object] | None = None
    shown: list[dict[str, object]] = []
    response, failure = completion_or_none(
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )
    for round_index in range(3):
        if response is None:
            return _provider_reply(failure, payloads, shown)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        text = (message.content or "").strip()
        if not tool_calls:
            if not text:
                logger.warning("gemini_empty_content")
            logger.info("completion_ms=%.0f", (time.perf_counter() - started) * 1000)
            return _finish(text, payloads, shown)
        messages.append(message.model_dump(exclude_none=True))
        for call in tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = None
            if not isinstance(arguments, dict):
                logger.warning("tool_arguments_invalid name=%s", call.function.name)
                payload = {"error": "The tool arguments were not valid.", "results": [], "cards": []}
            elif call.function.name == "search_cars":
                query = arguments.get("query")
                if not isinstance(query, str) or not query.strip():
                    arguments["query"] = normalized
                payload = run_search(
                    arguments,
                    locked,
                    remembered_price_max=remembered_budget,
                    active=active,
                    user_text=normalized,
                    potential_offset=int(state.get("potential_offset") or 0) if session_id else 0,
                )
                if session_id is not None and "error" not in payload:
                    record_search(
                        session_id,
                        [int(row["listing_id"]) for row in payload.get("results") or []]
                        + [int(row["listing_id"]) for row in payload.get("potential") or []],
                    )
                    if payload.get("features"):
                        state["last_features"] = payload["features"]
                    cards = payload.get("cards") or []
                    if isinstance(cards, list) and len(cards) == 1:
                        state["focus_listing_id"] = int(cards[0]["listing_id"])
                    save_search_state(session_id, state)
                if locked is None and "error" not in payload:
                    locked = {
                        field: arguments[field]
                        for field in LOCK_FIELDS
                        if arguments.get(field) is not None
                    }
                shown = next_shown(payload, shown)
            elif call.function.name == "save_visitor_memory":
                payload = save_visitor_memory(user_id, arguments)
            elif call.function.name == "book_viewing":
                payload = request_viewing(
                    arguments,
                    user_text=user_message,
                    now=now,
                    session_id=session_id,
                    user_id=user_id,
                    shown_searches=get_searches(session_id) if session_id else [],
                )
            elif call.function.name == "save_lead":
                visitor_name = None if profile is None else profile.get("name")
                payload = request_lead(
                    user_id,
                    arguments,
                    name=visitor_name if isinstance(visitor_name, str) else None,
                )
            else:
                payload = {"error": f"Unknown tool {call.function.name}"}
            payloads.append(payload)
            model_payload = {key: value for key, value in payload.items() if key != "cards"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.function.name,
                    "content": json.dumps(model_payload, default=str),
                }
            )
        if round_index == 2:
            break
        follow: dict[str, object] = {"messages": messages}
        if round_index == 0:
            follow["tools"] = tools
            follow["tool_choice"] = "auto"
        response, failure = completion_or_none(**follow)
    return _finish("", payloads, shown)


def _catalog_map() -> dict[int, dict[str, str]]:
    frame = load_listings()
    return {
        int(row.listing_id): {"make": str(row.make), "model": str(row.model)}
        for row in frame.itertuples(index=False)
    }
