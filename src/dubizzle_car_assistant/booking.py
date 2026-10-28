import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dubizzle_car_assistant.conversation import listing_ids_in, resolve_listing_reference
from dubizzle_car_assistant.inventory import load_listings
from dubizzle_car_assistant.memory.profile import connect

DUBAI = ZoneInfo("Asia/Dubai")
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

def viewing_rejection(when: datetime) -> str | None:
    if when.weekday() == 6:
        return "Viewings are available Monday to Saturday."
    minutes = when.hour * 60 + when.minute
    opens = 8 * 60
    closes = 20 * 60
    if minutes < opens or minutes >= closes:
        return "Viewings run from 08:00 to 20:00."
    return None

def _as_dubai(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=DUBAI)
    return when.astimezone(DUBAI)


def book_viewing(
    listing_id: int,
    when: datetime,
    now: datetime | None = None,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    replaces: dict[str, object] | None = None,
) -> dict[str, object]:
    rejection = viewing_rejection(when)
    if rejection is not None:
        return {"booked": False, "reason": rejection}

    slot = _as_dubai(when)
    current = _as_dubai(now) if now is not None else datetime.now(DUBAI)
    slot_text = slot.strftime("%Y-%m-%d %H:%M")
    if slot < current:
        return {
            "booked": False,
            "reason": (
                f"{slot_text} has already passed in Asia/Dubai. "
                f"The current Dubai time used for this check is {current.strftime('%Y-%m-%d %H:%M')}."
            ),
        }

    matches = load_listings().loc[lambda frame: frame["listing_id"] == listing_id]
    if matches.empty:
        return {"booked": False, "reason": f"Listing {listing_id} was not found."}

    row = matches.iloc[0]
    connection = connect()
    try:
        if replaces is not None and user_id:
            connection.execute(
                """
                DELETE FROM bookings
                WHERE listing_id = ? AND slot = ? AND user_id = ?
                """,
                (int(replaces["listing_id"]), replaces["slot"], user_id),
            )
        elif replaces is not None:
            connection.execute(
                """
                DELETE FROM bookings
                WHERE listing_id = ? AND slot = ? AND session_id = ?
                """,
                (int(replaces["listing_id"]), replaces["slot"], session_id),
            )
        connection.execute(
            """
            INSERT INTO bookings (listing_id, slot, session_id, user_id)
            VALUES (?, ?, ?, ?)
            """,
            (int(row["listing_id"]), slot_text, session_id, user_id),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        return {"booked": False, "reason": "That viewing is already booked."}
    finally:
        connection.close()
    updated = replaces is not None and (
        int(replaces["listing_id"]) != int(row["listing_id"]) or replaces["slot"] != slot_text
    )
    return {
        "booked": True,
        "updated": updated,
        "listing_id": int(row["listing_id"]),
        "year": int(row["year"]),
        "make": row["make"],
        "model": row["model"],
        "when": slot_text,
        "summary": _summary(int(row["listing_id"]), int(row["year"]), row["make"], row["model"], slot_text, updated=updated),
    }

def _soonest(today, weekday: int, hour: int, minute: int, now: datetime):
    delta = (weekday - today.weekday()) % 7
    candidate = today + timedelta(days=delta)
    slot = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=DUBAI)
    if slot <= _as_dubai(now):
        candidate = candidate + timedelta(days=7)
    return candidate


def _clock(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*:?\s*(a\.?m\.?|p\.?m\.?)\b",
        text,
        re.I,
    )
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower().replace(".", "")
        if hour > 12 or minute > 59:
            return None
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return hour, minute
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _has_day_word(text: str) -> bool:
    return re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|weekend|next week|20\d{2}-\d{2}-\d{2})\b",
        text,
        re.I,
    ) is not None


def is_booking_change(text: str) -> bool:
    if re.search(
        r"\b(instead|make it|move it|change it|change the (?:time|day|date|car)|reschedule|switch it|switch to)\b",
        text,
        re.I,
    ):
        return True
    return re.search(r"\bchange to\b", text, re.I) is not None and (
        _clock(text) is not None or _has_day_word(text)
    )


_BOOKING_WORD = r"book\w*|viewings?|appointments?|schedul\w*|reserv\w*"


def is_recall(text: str) -> bool:
    """A question about a saved viewing, matched by meaning rather than one sentence."""
    if not text or asks_open_times(text) or is_booking_change(text) or _clock(text) is not None:
        return False
    if re.search(
        r"\b(?:can i|could i|i want to|i(?:'d| would) like to|let me|please)\s+(?:book|schedule|reserve)\b",
        text,
        re.I,
    ):
        return False
    return re.search(
        rf"\b(?:what|when|which|who)\b.{{0,80}}\b(?:{_BOOKING_WORD})\b"
        rf"|\b(?:{_BOOKING_WORD})\b.{{0,80}}\b(?:what|when|which|who)\b"
        rf"|\b(?:did|do|have)\s+i\b.{{0,60}}\b(?:{_BOOKING_WORD})\b"
        rf"|\bmy\s+(?:booking|viewing|appointment)s?\b"
        rf"|\b(?:remind me|tell me)\b.{{0,50}}\b(?:{_BOOKING_WORD})\b",
        text,
        re.I | re.S,
    ) is not None


def _question_without_a_new_slot(text: str) -> bool:
    """A question that names no new day or hour, so it cannot be a new booking."""
    if not text or asks_open_times(text) or is_booking_change(text):
        return False
    if _clock(text) is not None or _has_day_word(text):
        return False
    if re.search(r"\b(?:book|schedule|reserve)\b", text, re.I):
        return False
    return re.search(r"\?|\b(?:what|when|which|who|did i|do i|have i|am i)\b", text, re.I) is not None


def _partial_schedule(text: str) -> bool:
    has_clock = _clock(text) is not None
    has_day = _has_day_word(text)
    return (has_clock and not has_day) or (has_day and not has_clock)


def uses_saved_booking(text: str) -> bool:
    """A follow-up the assistant should answer from the saved appointment."""
    return is_recall(text) or is_booking_change(text)


def asks_open_times(text: str) -> bool:
    """The shopper wants the hours that are still free, not a new booking."""
    return re.search(
        r"what time(?:s)? (?:is|are) (?:not booked|free|available|open)|"
        r"which time(?:s)? (?:is|are) (?:not booked|free|available|open)|"
        r"times? (?:that are )?not booked|available (?:viewing )?times|open slots",
        text,
        re.I,
    ) is not None


def bare_listing_id(text: str) -> int | None:
    match = re.fullmatch(r"\s*(?:listing\s+)?(\d{1,4})\s*", text, re.I)
    if match is None:
        return None
    return int(match.group(1))


def latest_schedule(history: list[dict[str, str]] | None, now: datetime) -> datetime | None:
    """The most recent day and time the shopper already named."""
    previous = None
    found = None
    for turn in history or []:
        if turn.get("role") != "user":
            continue
        resolved = resolve_schedule(str(turn.get("content") or ""), now, previous=previous)
        if "when" in resolved and isinstance(resolved["when"], datetime):
            previous = resolved["when"]
            found = resolved["when"]
    return found


def open_hours(listing_id: int, day) -> list[str]:
    """On-the-hour viewing times from 08:00 through 19:00 that are not taken."""
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT slot FROM bookings WHERE listing_id = ? AND slot LIKE ?",
            (listing_id, f"{day.strftime('%Y-%m-%d')}%"),
        ).fetchall()
    finally:
        connection.close()
    taken = {str(row["slot"])[11:16] for row in rows}
    return [f"{hour:02d}:00" for hour in range(8, 20) if f"{hour:02d}:00" not in taken]


def open_times_reply(listing_id: int | None, when: datetime | None) -> str:
    if not isinstance(listing_id, int):
        return "Say which listing id, and I will list the open viewing times."
    if when is None:
        return "Say which day, and I will list the open viewing times."
    slot = _as_dubai(when)
    closed = viewing_rejection(datetime(slot.year, slot.month, slot.day, 10, 0, tzinfo=DUBAI))
    frame = load_listings().set_index("listing_id")
    if listing_id not in frame.index:
        return f"Listing {listing_id} was not found."
    car = frame.loc[listing_id]
    label = f"{car['year']} {str(car['make']).title()} {str(car['model']).title()} (listing {listing_id})"
    day_label = slot.strftime("%A %d %B %Y")
    if closed and "Monday" in closed:
        return f"Viewings are available Monday to Saturday. {day_label} is closed."
    free = open_hours(listing_id, slot.date())
    if not free:
        return f"No viewing times are open for the {label} on {day_label}."
    return f"Open viewing times for the {label} on {day_label}: {', '.join(free)}."


def _summary(listing_id: int, year: int, make: str, model: str, slot_text: str, *, updated: bool = False, recalled: bool = False) -> str:
    label = datetime.strptime(slot_text, "%Y-%m-%d %H:%M").strftime("%A %d %B %Y at %H:%M")
    car = f"{year} {str(make).title()} {str(model).title()} (listing {listing_id})"
    if recalled:
        return f"You booked the {car} for {label}. The saved time is {slot_text}."
    if updated:
        return f"Updated the viewing for the {car} to {label}. The saved time is {slot_text}."
    return f"Booked the {car} for {label}. The saved time is {slot_text}."


def resolve_schedule(
    text: str,
    now: datetime,
    previous: datetime | None = None,
) -> dict[str, object]:
    """Turn the shopper's own words into a Dubai viewing time."""
    current = _as_dubai(now)
    saved = _as_dubai(previous) if previous is not None else None
    lowered = text.lower()
    clock = _clock(text)
    if clock is None and saved is not None and (_has_day_word(text) or is_booking_change(text)):
        clock = (saved.hour, saved.minute)
    if clock is None:
        return {"reason": "No time was given. Say the hour, for example 9:00 AM."}
    hour, minute = clock

    if re.search(r"\b(next|this)\s+weekend\b|\bnext\s+week\b", lowered):
        saturday = _soonest(current.date(), 5, hour, minute, current)
        sunday = _soonest(current.date(), 6, hour, minute, current)
        return {
            "ambiguous": True,
            "reason": (
                "That day is ambiguous. "
                f"Do you mean Saturday {saturday.strftime('%d %B %Y')} "
                f"or Sunday {sunday.strftime('%d %B %Y')} at {hour:02d}:{minute:02d}?"
            ),
        }

    weekday_match = re.search(
        r"\b(?:(next|this)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lowered,
    )
    day = None
    if weekday_match:
        day = _soonest(current.date(), WEEKDAYS[weekday_match.group(2)], hour, minute, current)
    elif re.search(r"\btomorrow\b", lowered):
        day = current.date() + timedelta(days=1)
    elif re.search(r"\btoday\b", lowered):
        day = current.date()
    else:
        explicit = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", lowered)
        if explicit:
            day = datetime(
                int(explicit.group(1)),
                int(explicit.group(2)),
                int(explicit.group(3)),
            ).date()
    if day is None and saved is not None:
        day = saved.date()
    if day is None:
        return {"reason": "No day was given. Say the day, for example next Saturday."}

    when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=DUBAI)
    label = when.strftime("%A %d %B %Y at %H:%M")
    return {"when": when, "label": label}


def _catalog() -> dict[int, dict[str, str]]:
    frame = load_listings()
    return {
        int(row.listing_id): {"make": str(row.make), "model": str(row.model)}
        for row in frame.itertuples(index=False)
    }


ASK = "ask"


def visitor_bookings(user_id: str) -> list[dict[str, object]]:
    connection = connect()
    try:
        rows = connection.execute(
            """
            SELECT listing_id, slot
            FROM bookings
            WHERE user_id = ?
            ORDER BY created_at, rowid
            """,
            (user_id,),
        ).fetchall()
    finally:
        connection.close()
    return [{"listing_id": int(row["listing_id"]), "slot": row["slot"]} for row in rows]


def session_bookings(session_id: str) -> list[dict[str, object]]:
    connection = connect()
    try:
        rows = connection.execute(
            """
            SELECT listing_id, slot
            FROM bookings
            WHERE session_id = ?
            ORDER BY created_at, rowid
            """,
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    return [{"listing_id": int(row["listing_id"]), "slot": row["slot"]} for row in rows]


def _named_listings(text: str, catalog: dict[int, dict[str, str]]) -> list[int]:
    lowered = text.lower()
    hits: list[int] = []
    for listing_id, car in catalog.items():
        for token in (car["make"], car["model"]):
            if len(token) < 3:
                continue
            if re.search(rf"\b{re.escape(token.lower())}\b", lowered):
                hits.append(listing_id)
                break
    return hits


def _is_additional(text: str) -> bool:
    return re.search(r"\b(also|as well|another|too)\b", text, re.I) is not None and not is_booking_change(text)


def _target_booking(
    saved: list[dict[str, object]],
    text: str,
    listing_ids: list[int],
) -> dict[str, object] | str | None:
    if not saved or _is_additional(text):
        return None
    if len(saved) > 1:
        matches = [row for row in saved if int(row["listing_id"]) in listing_ids]
        if len(matches) == 1:
            return matches[0]
        return ASK
    named = [row for row in saved if int(row["listing_id"]) in listing_ids]
    if named and not is_booking_change(text):
        return named[-1]
    if is_booking_change(text) or _partial_schedule(text) or not listing_ids:
        return saved[-1]
    if listing_ids == [int(saved[0]["listing_id"])]:
        return saved[0]
    return None


def _ask_which(saved: list[dict[str, object]]) -> dict[str, object]:
    frame = load_listings().set_index("listing_id")
    parts = []
    for row in saved:
        listing_id = int(row["listing_id"])
        car = frame.loc[listing_id]
        parts.append(f"listing {listing_id} {car['make']} {car['model']} at {row['slot']}")
    reason = "You have more than one saved booking. Which one should I change? " + "; ".join(parts) + "."
    return {"booked": False, "reason": reason, "summary": reason}


def _recall(saved: list[dict[str, object]], *, for_visitor: bool) -> dict[str, object]:
    if not saved:
        if for_visitor:
            reason = "You do not have a saved booking yet."
        else:
            reason = "You do not have a booking in this conversation yet."
        return {"booked": False, "recalled": True, "reason": reason, "summary": reason}
    details = []
    lines = []
    catalog = _catalog()
    frame = load_listings().set_index("listing_id")
    for row in saved:
        listing_id = int(row["listing_id"])
        car = frame.loc[listing_id]
        slot_text = str(row["slot"])
        lines.append(_summary(listing_id, int(car["year"]), car["make"], car["model"], slot_text, recalled=True))
        details.append(
            {
                "listing_id": listing_id,
                "when": slot_text,
                "make": catalog[listing_id]["make"],
                "model": catalog[listing_id]["model"],
            }
        )
    summary = " ".join(lines)
    return {"booked": True, "recalled": True, "summary": summary, "bookings": details}


def _conflict_reply(result: dict[str, object], listing_id: int, when: datetime) -> dict[str, object]:
    if result.get("booked") is not False or "already booked" not in str(result.get("reason")):
        return result
    slot = _as_dubai(when)
    free = open_hours(listing_id, slot.date())
    shown = ", ".join(free) if free else "none"
    reason = (
        f"{slot.strftime('%A %d %B %Y at %H:%M')} is already booked for listing {listing_id}. "
        f"Open times that day: {shown}."
    )
    updated = dict(result)
    updated["reason"] = reason
    updated["summary"] = reason
    return updated


def request_viewing(
    arguments: dict[str, object],
    *,
    user_text: str | None = None,
    now: datetime | None = None,
    shown_searches: list[list[int]] | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    focus_listing_id: int | None = None,
    attempted: datetime | None = None,
    slot_at: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now(DUBAI)
    text = user_text or ""
    if user_id:
        saved = visitor_bookings(user_id)
    elif session_id:
        saved = session_bookings(session_id)
    else:
        saved = []
    if text and (is_recall(text) or (saved and _question_without_a_new_slot(text))):
        return _recall(saved, for_visitor=bool(user_id))

    if slot_at is not None and type(arguments.get("listing_id")) is int:
        listing_id = int(arguments["listing_id"])
        when = _as_dubai(slot_at)
        result = book_viewing(
            listing_id,
            when,
            now=current,
            session_id=session_id,
            user_id=user_id,
        )
        result = _conflict_reply(result, listing_id, when)
        result["resolved_when"] = when.strftime("%A %d %B %Y at %H:%M")
        return result

    model_when = arguments.get("when")
    source = text
    if not _clock(source) and not _has_day_word(source) and not is_booking_change(source):
        source = model_when if isinstance(model_when, str) else ""
    if not isinstance(source, str) or not source.strip():
        return {"booked": False, "reason": "No day or time was given.", "summary": "No day or time was given."}

    catalog = _catalog()
    spoken = text or source
    explicit = listing_ids_in(spoken)
    named = _named_listings(spoken, catalog) if not explicit else []
    if len(named) > 1 and saved:
        current_ids = {int(row["listing_id"]) for row in saved}
        others = [listing_id for listing_id in named if listing_id not in current_ids]
        if len(others) == 1:
            named = others
    if len(named) > 1:
        shown = ", ".join(str(listing_id) for listing_id in named)
        reason = f"More than one car matches that description: {shown}. Which listing id?"
        return {"booked": False, "reason": reason, "summary": reason}
    chosen = resolve_listing_reference(spoken, shown_searches or [], catalog)
    listing_ids = explicit or named
    if not listing_ids and chosen["action"] == "use":
        listing_ids = [int(listing_id) for listing_id in chosen["listing_ids"]]
    elif not listing_ids and chosen["action"] == "ask" and not saved:
        return {"booked": False, "reason": chosen["reason"], "summary": chosen["reason"]}
    elif not listing_ids:
        raw_ids = arguments.get("listing_ids")
        if isinstance(raw_ids, list):
            listing_ids.extend(int(listing_id) for listing_id in raw_ids if type(listing_id) is int)
        if type(arguments.get("listing_id")) is int and not saved:
            listing_ids.append(int(arguments["listing_id"]))
        listing_ids = list(dict.fromkeys(listing_ids))

    target = _target_booking(saved, spoken, explicit or named)
    if target == ASK:
        return _ask_which(saved)
    if target is not None and not (explicit or named):
        listing_ids = [int(target["listing_id"])]
    elif target is not None and is_booking_change(spoken) and (explicit or named):
        listing_ids = explicit or named
    previous = None
    if target is not None:
        previous = datetime.strptime(str(target["slot"]), "%Y-%m-%d %H:%M").replace(tzinfo=DUBAI)
    elif attempted is not None:
        previous = _as_dubai(attempted)
    if not listing_ids and isinstance(focus_listing_id, int) and (
        is_booking_change(spoken) or _partial_schedule(spoken)
    ):
        listing_ids = [focus_listing_id]
    if not listing_ids:
        reason = "Say which listing id to book."
        return {"booked": False, "reason": reason, "summary": reason}

    schedule = resolve_schedule(source, current, previous=previous)
    if "when" not in schedule:
        return {"booked": False, "reason": schedule["reason"], "summary": schedule["reason"]}

    replace_with = target if target is not None and len(listing_ids) == 1 else None
    bookings = [
        book_viewing(
            listing_id,
            schedule["when"],
            now=current,
            session_id=session_id,
            user_id=user_id,
            replaces=replace_with,
        )
        for listing_id in listing_ids
    ]
    if len(bookings) == 1:
        result = _conflict_reply(dict(bookings[0]), listing_ids[0], schedule["when"])
        result["resolved_when"] = schedule["label"]
        return result
    return {
        "booked": all(item["booked"] for item in bookings),
        "resolved_when": schedule["label"],
        "summary": " ".join(str(item.get("summary") or item.get("reason")) for item in bookings),
        "bookings": bookings,
    }