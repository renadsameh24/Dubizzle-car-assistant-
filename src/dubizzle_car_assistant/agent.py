import json

from dubizzle_car_assistant.retrieval.semantic import load_gemini_key
from dubizzle_car_assistant.retrieval.hybrid import hybrid_search

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

SYSTEM_PROMPT = (
    "You are a dubizzle car shopping assistant. "
    "The inventory is available only through search_cars. "
    "After the tool returns, mention only those cars. "
    "Use their listing id, make, model, year, and price. "
    "If price_aed or mileage_km is null, say it is not listed. "
    "Do not invent listings, prices, or mileage."
)


def answer(user_message: str) -> str:
    load_gemini_key()
    import litellm

    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    first = litellm.completion(
        model=CHAT_MODEL,
        messages=messages,
        tools=[SEARCH_TOOL],
        tool_choice="auto",
    )
    message = first.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return message.content or ""

    messages.append(message.model_dump(exclude_none=True))
    for call in tool_calls:
        if call.function.name != "search_cars":
            payload: object = {"error": f"Unknown tool {call.function.name}"}
        else:
            payload = run_search(json.loads(call.function.arguments))
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