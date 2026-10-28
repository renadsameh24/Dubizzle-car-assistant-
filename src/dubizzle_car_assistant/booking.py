from datetime import datetime
from dubizzle_car_assistant.inventory import load_listings

def viewing_rejection(when: datetime) -> str | None:
    if when.weekday() == 6:
        return "Viewings are available Monday to Saturday."
    minutes = when.hour * 60 + when.minute
    opens = 8 * 60
    closes = 20 * 60
    if minutes < opens or minutes >= closes:
        return "Viewings run from 08:00 to 20:00."
    return None

def book_viewing(listing_id: int, when: datetime) -> dict[str, object]:
    rejection = viewing_rejection(when)
    if rejection is not None:
        return {"booked": False, "reason": rejection}

    matches = load_listings().loc[lambda frame: frame["listing_id"] == listing_id]
    if matches.empty:
        return {"booked": False, "reason": f"Listing {listing_id} was not found."}

    row = matches.iloc[0]
    return {
        "booked": True,
        "listing_id": int(row["listing_id"]),
        "year": int(row["year"]),
        "make": row["make"],
        "model": row["model"],
        "when": when.strftime("%Y-%m-%d %H:%M"),
    }

def request_viewing(arguments: dict[str, object]) -> dict[str, object]:
    listing_id = arguments.get("listing_id")
    when_text = arguments.get("when")
    if type(listing_id) is not int:
        return {"booked": False, "reason": "listing_id must be an integer."}
    if not isinstance(when_text, str) or not when_text.strip():
        return {"booked": False, "reason": "when must be a time string."}
    try:
        when = datetime.strptime(when_text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return {"booked": False, "reason": "when must look like 2026-10-26 08:00."}
    return book_viewing(listing_id, when)