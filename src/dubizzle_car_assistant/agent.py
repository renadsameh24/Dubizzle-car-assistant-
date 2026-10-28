import json

from dubizzle_car_assistant.retrieval.semantic import load_gemini_key
from dubizzle_car_assistant.retrieval.hybrid import hybrid_search
from dubizzle_car_assistant.memory.profile import (
    add_preference,
    like_car,
    load_profile,
    remember_user,
)
from dubizzle_car_assistant.booking import request_viewing
from dubizzle_car_assistant.leads import request_lead

CHAT_MODEL = "gemini/gemini-3.1-flash-lite"

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
            "Book a viewing for one listing. when is local time as "
            "YYYY-MM-DD HH:MM. Viewings are Monday to Saturday, 08:00 to 20:00."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "listing_id": {"type": "integer"},
                "when": {
                    "type": "string",
                    "description": "Local viewing time, for example 2026-10-26 08:00.",
                },
            },
            "required": ["listing_id", "when"],
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
        model=CHAT_MODEL,
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

def run_search(arguments: dict[str, object]) -> list[dict[str, object]]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("search_cars requires a query string")
    filters = {
        field: arguments[field]
        for field in SEARCH_FIELDS
        if arguments.get(field) is not None
    }
    rows = hybrid_search(query.strip(), limit=5, **filters)
    return [
        {
            "listing_id": row["listing_id"],
            "year": row["year"],
            "make": row["make"],
            "model": row["model"],
            "price_aed": row["price_aed"],
            "mileage_km": row["mileage_km"],
        }
        for row in rows
    ]

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
        remember_user(user_id, "Visitor")

    if isinstance(preference, str) and preference.strip():
        add_preference(user_id, preference)
        saved.append("preference")

    if type(liked_listing_id) is int:
        like_car(user_id, liked_listing_id)
        saved.append("liked_listing_id")

    if not saved:
        return {"saved": False, "error": "Nothing to save."}
    return {"saved": True, "fields": saved}

SYSTEM_PROMPT = (
    "You are a dubizzle car shopping assistant. "
    "The inventory is available only through search_cars. "
    "After the tool returns, mention only those cars. "
    "Use their listing id, make, model, year, and price. "
    "If price_aed or mileage_km is null, say it is not listed. "
    "If the user refers to a car you already showed, answer from the conversation. "
    "Call search_cars again only when they ask for a different car. "
    "Do not invent listings, prices, or mileage. "
    "When the user gives their name, a car preference, or a listing they like, "
    "call save_visitor_memory, then confirm only the fields that tool saved. "
    "When the user asks to book a viewing, call book_viewing. "
    "Confirm the booking only when book_viewing returns booked true. "
    "When book_viewing returns booked false, tell the user the reason and do not say it is booked. "
    "When the user states both a maximum budget and what car they need, call save_lead. "
    "Say the lead was saved only when save_lead returns saved true. "
    "When it returns saved false, tell the user which detail is missing. "
    "If the user asks for something that is not about buying, viewing, or comparing cars, "
    "politely decline and offer to help with dubizzle cars. "
    "Do not answer the unrelated question. "
    "Do not name or recommend any other car website or marketplace."    
)


def answer(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    profile: dict[str, object] | None = None,
    user_id: str | None = None,
) -> str:
    load_gemini_key()
    import litellm

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
            "It is not the inventory. Call search_cars when they want to see cars."
        )

    messages: list[dict[str, object]] = [
        {"role": "system", "content": system},
    ]

    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_message})

    first = litellm.completion(
        model=CHAT_MODEL,
        messages=messages,
        tools=[SEARCH_TOOL, SAVE_TOOL, BOOK_TOOL, LEAD_TOOL],
        tool_choice="auto",
    )
    message = first.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return message.content or ""

    messages.append(message.model_dump(exclude_none=True))
    for call in tool_calls:
        arguments = json.loads(call.function.arguments or "{}")
        if call.function.name == "search_cars":
            payload: object = run_search(arguments)
        elif call.function.name == "save_visitor_memory":
            payload = save_visitor_memory(user_id, arguments)
        elif call.function.name == "book_viewing":
            payload = request_viewing(arguments)
        elif call.function.name == "save_lead":
            visitor_name = None if profile is None else profile.get("name")
            payload = request_lead(
                user_id,
                arguments,
                name=visitor_name if isinstance(visitor_name, str) else None,
            )
        else:
            payload = {"error": f"Unknown tool {call.function.name}"}
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.function.name,
                "content": json.dumps(payload),
            }
        )
    second = litellm.completion(model=CHAT_MODEL, messages=messages)
    return second.choices[0].message.content or ""