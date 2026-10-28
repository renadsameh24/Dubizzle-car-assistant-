import re

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_RELAX = (
    "any budget",
    "no budget",
    "forget the budget",
    "ignore the budget",
    "ignore my budget",
    "price does not matter",
    "price doesn't matter",
    "more expensive is ok",
    "more expensive is fine",
    "above the budget",
    "look above",
    "without a budget",
)


def _money(text: str) -> int | None:
    amounts: list[int] = []
    for match in re.finditer(r"\b(\d{1,3}(?:,\d{3})+|\d+)\s*(k)?\b", text.lower()):
        amount = int(match.group(1).replace(",", ""))
        if match.group(2):
            amount *= 1000
        if amount >= 1000:
            amounts.append(amount)
    if not amounts:
        return None
    return amounts[-1]


def relaxes_budget(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _RELAX)


def price_cap(previous_user_messages: list[str], current_message: str) -> int | None:
    """The latest budget the shopper has not taken back."""
    cap = None
    for message in previous_user_messages:
        if relaxes_budget(message):
            cap = None
        found = _money(message)
        if found is not None and re.search(r"budget|under|around|aed|\bk\b", message.lower()):
            cap = found
    if relaxes_budget(current_message):
        found = _money(current_message)
        if found is not None and re.search(r"budget|under|around|aed|\bk\b", current_message.lower()):
            return found
        return None
    found = _money(current_message)
    if found is not None and re.search(r"budget|under|around|aed|\bk\b", current_message.lower()):
        return found
    return cap


_MAKE_ALIASES = (
    ("mercedes benz", "mercedes-benz"),
    ("mercedez", "mercedes-benz"),
    ("mercdes", "mercedes-benz"),
    ("landrover", "land rover"),
    ("toyta", "toyota"),
    ("mazdaa", "mazda"),
    ("nisan", "nissan"),
    ("chevy", "chevrolet"),
)

_ORDINALS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
}


def normalize_makes(text: str) -> str:
    """Replace known manufacturer typos. Unknown words are left unchanged."""
    result = re.sub(r"\blow milage\b", "low mileage", text, flags=re.I)
    for alias, canonical in _MAKE_ALIASES:
        result = re.sub(rf"\b{re.escape(alias)}\b", canonical, result, flags=re.I)
    return re.sub(r"\bmercedes(?!-benz)\b", "mercedes-benz", result, flags=re.I)


def confirmed_only(text: str) -> bool:
    lowered = text.lower()
    phrases = (
        "confirmed price",
        "confirmed prices",
        "only priced",
        "must have a price",
        "with a listed price only",
        "only cars with a price",
        "only cars with prices",
        "only cars with listed prices",
    )
    return any(phrase in lowered for phrase in phrases)


def inventory_question(text: str) -> str | None:
    lowered = text.lower()
    if "how many" in lowered and "price" in lowered and "mileage" in lowered:
        return "both_count"
    if re.search(r"without (?:a )?listed price|no listed price|unknown price|missing price|unpriced", lowered):
        return "unpriced"
    if re.search(r"unknown mileage|without (?:listed )?mileage|no mileage|missing mileage|mileage (?:is )?not listed", lowered):
        return "unknown_mileage"
    return None


def wants_more_results(text: str) -> bool:
    return re.search(
        r"\b(show more|see more|more of (?:those|them)|the (?:other|rest of the) (?:unpriced|cars))\b",
        text,
        re.I,
    ) is not None


def listing_ids_in(text: str) -> list[int]:
    match = re.search(
        r"\blistings?\s+((?:\d+\s*(?:,|and|&)\s*)*\d+)",
        text,
        re.I,
    )
    if match is None:
        return []
    return [int(number) for number in re.findall(r"\d+", match.group(1))]


def resolve_listing_reference(
    text: str,
    searches: list[list[int]],
    catalog: dict[int, dict[str, str]],
) -> dict[str, object]:
    explicit = listing_ids_in(text)
    if explicit:
        return {"action": "use", "listing_ids": explicit}

    ordinal = re.search(
        r"\b(?:the\s+)?(first|second|third|1st|2nd|3rd)\s+(?:car|one|listing)\b",
        text,
        re.I,
    )
    if ordinal:
        latest = searches[-1] if searches else []
        index = _ORDINALS[ordinal.group(1).lower()]
        if index < len(latest):
            return {"action": "use", "listing_ids": [latest[index]]}
        if not latest:
            return {
                "action": "ask",
                "reason": "No car has been shown in this conversation yet. Tell me the listing id.",
            }
        return {
            "action": "ask",
            "reason": f"The latest search showed {len(latest)} cars. Which listing id do you mean?",
        }

    vague = re.search(
        r"\b(that car|this car|the car you just showed(?: me)?|the one you (?:just )?showed(?: me)?|this one|that one)\b",
        text,
        re.I,
    )
    if vague:
        latest = searches[-1] if searches else []
        if not latest:
            earlier = [listing_id for group in searches for listing_id in group]
            if earlier:
                shown = ", ".join(str(listing_id) for listing_id in earlier)
                return {
                    "action": "ask",
                    "reason": (
                        "The latest search did not show a car. "
                        f"Which listing do you mean? Earlier ones were {shown}."
                    ),
                }
            return {
                "action": "ask",
                "reason": "No car has been shown in this conversation yet. Tell me the listing id.",
            }
        if len(latest) == 1:
            return {"action": "use", "listing_ids": latest}
        shown = ", ".join(str(listing_id) for listing_id in latest)
        return {
            "action": "ask",
            "reason": f"The latest search showed listings {shown}. Which one should I book?",
        }

    lowered = text.lower()
    for group in reversed(searches):
        matches = []
        for listing_id in group:
            car = catalog.get(listing_id)
            if car is None:
                continue
            if _named_car(car, lowered):
                matches.append(listing_id)
        if len(matches) == 1:
            return {"action": "use", "listing_ids": matches}
        if len(matches) > 1:
            shown = ", ".join(str(listing_id) for listing_id in matches)
            return {
                "action": "ask",
                "reason": f"More than one shown car matches that description: {shown}. Which listing id?",
            }
    return {"action": "missing", "reason": "Say which listing id to book."}


def _named_car(car: dict[str, str], lowered: str) -> bool:
    for token in (car.get("make") or "", car.get("model") or ""):
        if len(token) < 3:
            continue
        if re.search(rf"\b{re.escape(token.lower())}\b", lowered):
            return True
    return False


def _known_makes() -> list[str]:
    from dubizzle_car_assistant.inventory import load_listings

    makes = {str(make) for make in load_listings()["make"].unique()}
    return sorted(makes, key=len, reverse=True)


def _known_models() -> list[str]:
    from dubizzle_car_assistant.inventory import load_listings

    models = {
        str(model)
        for model in load_listings()["model"].unique()
        if isinstance(model, str) and len(str(model)) >= 3
    }
    return sorted(models, key=len, reverse=True)


def _mentioned_make(text: str) -> str | None:
    lowered = text.lower()
    for make in _known_makes():
        if re.search(rf"\b{re.escape(make)}s?\b", lowered):
            return make
    return None


def _cleared_make(text: str) -> str | None:
    match = re.search(r"\b(?:forget|drop|remove|without)\s+(?:the\s+)?([a-z0-9][a-z0-9 -]{1,30})", text.lower())
    if match is None:
        return None
    return _mentioned_make(match.group(1))


def merge_constraints(previous: dict[str, object], text: str) -> tuple[dict[str, object], str | None]:
    """Keep earlier search limits unless this message changes or removes one.

    The returned state is for this conversation only. It is not a saved preference.
    """
    from dubizzle_car_assistant.retrieval.constraints import ORIGINS

    normalized = normalize_makes(text)
    lowered = normalized.lower()
    if re.search(r"\bbook\b|\bviewing\b", lowered):
        return dict(previous), None

    state = {key: value for key, value in previous.items() if key in {
        "origin", "make", "model", "year_min", "year_max", "price_max", "mileage_max",
    }}
    clear: set[str] = set()

    if re.search(r"\bany brand\b|\bany make\b|\ball brands\b|\bshow any brand\b", lowered):
        clear.update({"make", "origin", "model"})
    forgotten = _cleared_make(lowered)
    if forgotten:
        clear.add("make")
        if state.get("make") == forgotten:
            clear.add("model")
    if re.search(r"\bany model\b|\bforget the model\b", lowered):
        clear.add("model")
    if re.search(r"\bany year\b|\bforget the year\b|\bdrop the year\b", lowered):
        clear.update({"year_min", "year_max"})
    if re.search(r"\bany mileage\b|\bforget the mileage\b|\bdrop the mileage\b", lowered):
        clear.add("mileage_max")
    if relaxes_budget(lowered) and not re.search(r"\bkeep (?:my |the )?budget\b", lowered):
        clear.add("price_max")

    bare_year = re.search(r"\bwhat about\s+((?:19|20)\d{2})\b", lowered)
    if bare_year and not re.search(r"\b(?:from|onwards|after|before|until|only|in)\b", lowered):
        if state.get("year_min") or state.get("year_max"):
            year = bare_year.group(1)
            return dict(previous), (
                f"Do you mean cars from {year} onwards, or only {year}? "
                "Your other limits stay as they are until you say."
            )

    for field in clear:
        state.pop(field, None)

    for origin in ORIGINS:
        if re.search(rf"\b{origin}\b", lowered):
            state["origin"] = origin
            break

    make = None if "make" in clear and not _mentioned_make(lowered.replace(forgotten or "", "", 1)) else _mentioned_make(lowered)
    if forgotten and make == forgotten and re.search(r"\b(?:forget|drop|remove|without)\b", lowered):
        # The only make in the message is the one they removed.
        remainder = re.sub(rf"\b(?:forget|drop|remove|without)\s+(?:the\s+)?{re.escape(forgotten)}\b", " ", lowered)
        make = _mentioned_make(remainder)
    if make and "make" not in clear or (make and make != forgotten):
        state["make"] = make
        state.pop("model", None)
        origin = state.get("origin")
        grouped = ORIGINS.get(str(origin), ()) if origin else ()
        if grouped and make not in grouped:
            state.pop("origin", None)

    if "model" not in clear and "make" not in {forgotten}:
        for model in _known_models():
            if model in _known_makes():
                continue
            if re.search(rf"\b{re.escape(model)}\b", lowered):
                state["model"] = model
                break

    from_year = re.search(
        r"\b(?:from|after|since)\s+((?:19|20)\d{2})\b|\b((?:19|20)\d{2})\s+(?:onwards|or newer|or later)\b",
        lowered,
    )
    until_year = re.search(r"\b(?:until|before|up to|older than)\s+((?:19|20)\d{2})\b", lowered)
    only_year = re.search(r"\b(?:only|just|in)\s+((?:19|20)\d{2})\b", lowered)
    if from_year:
        year = int(from_year.group(1) or from_year.group(2))
        state["year_min"] = year
        state.pop("year_max", None)
    elif only_year:
        year = int(only_year.group(1))
        state["year_min"] = year
        state["year_max"] = year
    elif until_year:
        state["year_max"] = int(until_year.group(1))

    if not relaxes_budget(lowered) or re.search(r"\bkeep (?:my |the )?budget\b", lowered):
        amount = _money(lowered)
        if amount is not None and re.search(r"budget|under|below|around|aed|\bk\b|max", lowered):
            if not re.search(r"\bkm\b|\bkilomet", lowered) or re.search(r"budget|aed|\bk\b", lowered):
                # A mileage sentence that also says "k" should not replace the budget
                # when the k belongs to kilometers. Prefer AED language for price.
                price_sentence = bool(re.search(r"budget|aed|price", lowered)) or (
                    re.search(r"\bk\b", lowered) and not re.search(r"\bk\s*km\b|\bkm\b", lowered)
                )
                if price_sentence or re.search(r"\bunder\b|\baround\b|\bbelow\b", lowered) and not re.search(r"\bkm\b", lowered):
                    state["price_max"] = amount

    mileage = re.search(
        r"\b(?:under|below|less than|max(?:imum)?)\s+(\d{1,3}(?:,\d{3})+|\d+)\s*(k)?\s*(?:km|kilometers)\b"
        r"|\bmileage\s+(?:under|below|max(?:imum)?)\s+(\d{1,3}(?:,\d{3})+|\d+)\s*(k)?\b",
        lowered,
    )
    if mileage:
        raw = mileage.group(1) or mileage.group(3)
        suffix = mileage.group(2) or mileage.group(4)
        value = int(raw.replace(",", ""))
        if suffix:
            value *= 1000
        state["mileage_max"] = value

    return state, None


_GLOBAL_PHRASES = (
    "entire inventory",
    "whole inventory",
    "entire catalog",
    "whole catalog",
    "all cars",
    "all the cars",
    "all listings",
    "in your inventory",
    "in the inventory",
    "across the inventory",
    "across your inventory",
    "across the catalog",
)


def wants_global_catalog(text: str) -> bool:
    """A request that should ignore filters left over from an earlier search."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in _GLOBAL_PHRASES):
        return True
    if re.search(r"\boverall\b", lowered) and "overall condition" not in lowered:
        return True
    return False


def refers_to_previous_search(text: str) -> bool:
    return re.search(
        r"previous search|from (?:that|this|my|the) search|those cars|"
        r"the ones we|matching (?:my|the) (?:search|filters)|keep (?:the|my) filters",
        text,
        re.I,
    ) is not None


def ranking_request(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\bcheapest\b|\blowest price\b|\bleast expensive\b", lowered):
        return "cheapest"
    if re.search(r"\bmost expensive\b|\bhighest price\b|\bpriciest\b", lowered):
        return "expensive"
    if re.search(r"\bnewest\b|\blatest model year\b|\bmost recent year\b", lowered):
        return "newest"
    if re.search(r"\blowest mileage\b|\bleast mileage\b|\bfewest (?:km|kilometers)\b", lowered):
        return "lowest_mileage"
    return None


def message_names_constraint(text: str) -> bool:
    from dubizzle_car_assistant.retrieval.constraints import ORIGINS

    lowered = normalize_makes(text).lower()
    if _mentioned_make(lowered):
        return True
    if any(re.search(rf"\b{origin}\b", lowered) for origin in ORIGINS):
        return True
    return re.search(
        r"\b(?:from|after|since|until|before)\s+((?:19|20)\d{2})\b|"
        r"\b((?:19|20)\d{2})\s+(?:onwards|or newer|or later)\b|"
        r"\bonly\s+((?:19|20)\d{2})\b",
        lowered,
    ) is not None


def inherited_constraints(previous: dict[str, object], text: str) -> dict[str, object]:
    """Constraints that still apply to this message."""
    if wants_global_catalog(text) and not refers_to_previous_search(text):
        return {}
    if refers_to_previous_search(text):
        return dict(previous)
    return dict(previous)


def is_detail_question(text: str) -> bool:
    """A question about one car that is already identified, not a new search."""
    if ranking_request(text) or inventory_question(text) or wants_more_results(text):
        return False
    if re.search(r"\bcars\b", text, re.I) and not re.search(r"\b(?:this|that|the) (?:car|one)\b", text, re.I):
        return False
    return re.search(
        r"asking price|what(?:'s| is) the price|how much|what(?:'s| is) the mileage|"
        r"\band mileage\b|\bthe mileage\b|\bits mileage\b|\bodometer\b|"
        r"advertisement|what does the ad\b|that feature|accident history|\baccident\b|"
        r"service history|what does (?:it|the ad|the advertisement) say",
        text,
        re.I,
    ) is not None


def _broad_search(text: str) -> bool:
    if ranking_request(text):
        return True
    return re.search(
        r"\b(under|below|budget|cheaper than|other cars|similar cars|any brand|"
        r"japanese|korean|german|american|british|italian)\b",
        text,
        re.I,
    ) is not None


def named_listing_ids(text: str) -> list[int]:
    """Listings named by a model word. A short token such as Mazda's '3' is ignored."""
    from dubizzle_car_assistant.inventory import load_listings

    lowered = normalize_makes(text).lower()
    model = None
    for candidate in _known_models():
        if candidate in _known_makes():
            continue
        if re.search(rf"\b{re.escape(candidate)}\b", lowered):
            model = candidate
            break
    if model is None:
        return []
    frame = load_listings()
    rows = frame.loc[frame["model"] == model]
    make = _mentioned_make(lowered)
    if make:
        rows = rows.loc[rows["make"] == make]
    years = [int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", lowered)]
    if len(years) == 1 and len(rows) > 1:
        narrowed = rows.loc[rows["year"] == years[0]]
        if not narrowed.empty:
            rows = narrowed
    return [int(listing_id) for listing_id in rows["listing_id"].tolist()]


def listing_subject(
    text: str,
    searches: list[list[int]],
    catalog: dict[int, dict[str, str]],
    focus: int | None,
) -> dict[str, object]:
    """The one listing a follow-up is about, or a request to ask which one."""
    named = named_listing_ids(text)
    resolved = resolve_listing_reference(text, searches, catalog)
    if resolved["action"] == "use":
        ids = [int(listing_id) for listing_id in resolved["listing_ids"]]
        if named:
            overlap = [listing_id for listing_id in ids if listing_id in named]
            if overlap:
                ids = overlap
        if len(ids) == 1:
            return {"action": "use", "listing_id": ids[0]}
        shown = ", ".join(str(listing_id) for listing_id in ids)
        return {"action": "ask", "reason": f"Which listing do you mean? Possible ids: {shown}."}
    if resolved["action"] == "ask":
        if isinstance(focus, int) and re.search(r"\b(?:that|this) (?:car|one)\b", text, re.I):
            return {"action": "use", "listing_id": focus}
        return {"action": "ask", "reason": str(resolved.get("reason") or "Which listing do you mean?")}
    if len(named) == 1:
        return {"action": "use", "listing_id": named[0]}
    if len(named) > 1:
        shown = ", ".join(str(listing_id) for listing_id in named)
        return {
            "action": "ask",
            "reason": f"Several listings match that description: {shown}. Which one?",
        }
    if is_detail_question(text):
        if isinstance(focus, int):
            return {"action": "use", "listing_id": focus}
        latest = searches[-1] if searches else []
        if len(latest) == 1:
            return {"action": "use", "listing_id": latest[0]}
        if len(latest) > 1:
            shown = ", ".join(str(listing_id) for listing_id in latest)
            return {
                "action": "ask",
                "reason": f"Which car do you mean? The latest search showed listings {shown}.",
            }
        return {"action": "ask", "reason": "Which car do you mean? Tell me the model or the listing id."}
    return {"action": "none"}


def opens_one_listing(text: str, subject: dict[str, object]) -> bool:
    if subject.get("action") == "ask":
        return is_detail_question(text) or bool(named_listing_ids(text)) or bool(
            re.search(r"\b(?:that|this) (?:car|one)\b|\b(?:first|second|third) (?:car|one|listing)\b", text, re.I)
        )
    if subject.get("action") != "use":
        return False
    if is_detail_question(text):
        return True
    if _broad_search(text):
        return False
    return bool(named_listing_ids(text)) or bool(
        re.search(r"\b(?:first|second|third) (?:car|one|listing)\b", text, re.I)
    )
