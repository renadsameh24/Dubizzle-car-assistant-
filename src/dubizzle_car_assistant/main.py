import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from dubizzle_car_assistant.inventory import load_listings
from dubizzle_car_assistant.retrieval.structured import search_listings
from dubizzle_car_assistant.retrieval.constraints import makes_for
from dubizzle_car_assistant.retrieval.hybrid import hybrid_search
from dubizzle_car_assistant.agent import answer
from dubizzle_car_assistant.memory.session import (
    append_turn,
    conversation_owner,
    get_history,
    list_conversations,
    start_conversation,
)
from dubizzle_car_assistant.memory.profile import (
    create_named_visitor,
    load_profile,
    open_by_name,
)

app = FastAPI(title="dubizzle car assistant")


class ListingSummary(BaseModel):
    listing_id: int
    year: int
    make: str
    model: str
    trim: str
    price_aed: int | None
    mileage_km: int | None

class ListingDetail(ListingSummary):
    title: str
    description: str
    photo_url: str

class ListingMatch(ListingSummary):
    title: str
    score: float | None = None

def optional_int(value: object) -> int | None:
    if pd.isna(value):
        return None
    return int(value)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/listings")
def get_listings(limit: int = Query(default=5, ge=1, le=100)) -> list[ListingSummary]:
    frame = load_listings().head(limit)
    return [
        ListingSummary(
            listing_id=int(row.listing_id),
            year=int(row.year),
            make=row.make,
            model=row.model,
            trim=row.trim,
            price_aed=optional_int(row.price_aed),
            mileage_km=optional_int(row.mileage_km),
        )
        for row in frame.itertuples(index=False)
    ]

@app.get("/listings/search")
def search_inventory(
    q: str | None = None,
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = Query(default=None, ge=1900, le=2100),
    year_max: int | None = Query(default=None, ge=1900, le=2100),
    price_max: int | None = Query(default=None, ge=0),
    mileage_max: int | None = Query(default=None, ge=0),
    origin: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ListingMatch]:
    try:
        makes = makes_for(origin, make)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if q is not None and q.strip():
        rows = hybrid_search(
            q.strip(),
            makes=makes,
            model=model,
            year_min=year_min,
            year_max=year_max,
            price_max=price_max,
            mileage_max=mileage_max,
            limit=limit,
        )
    else:
        rows = search_listings(
            makes=makes,
            model=model,
            year_min=year_min,
            year_max=year_max,
            price_max=price_max,
            mileage_max=mileage_max,
            limit=limit,
        )
    return [ListingMatch.model_validate(row) for row in rows]

@app.get("/listings/{listing_id}")
def get_listing(listing_id: int) -> ListingDetail:
    matches = load_listings().loc[lambda frame: frame["listing_id"] == listing_id]
    if matches.empty:
        raise HTTPException(status_code=404, detail=f"Listing {listing_id} was not found")
    row = matches.iloc[0]
    return ListingDetail(
        listing_id=int(row["listing_id"]),
        year=int(row["year"]),
        make=row["make"],
        model=row["model"],
        trim=row["trim"],
        price_aed=optional_int(row["price_aed"]),
        mileage_km=optional_int(row["mileage_km"]),
        title=row["title"],
        description=row["description"],
        photo_url=row["photo_url"],
    )

class VisitorName(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class VisitorSession(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=64)
    user_id: str | None = Field(default=None, min_length=1, max_length=64)

class CarCard(BaseModel):
    listing_id: int
    year: int
    make: str
    model: str
    trim: str = ""
    price_aed: int | None = None
    mileage_km: int | None = None
    title: str = ""
    photo_url: str = ""
    match_kind: str = "confirmed"
    reason: str = ""
    evidence: str = ""


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    listings: list[CarCard] = []
    answered: bool = True


@app.post("/visitors")
def post_visitor(request: VisitorName) -> dict[str, object]:
    return create_named_visitor(request.name)


@app.post("/visitors/open")
def post_open_visitor(request: VisitorName) -> dict[str, object]:
    return open_by_name(request.name)


@app.post("/conversations")
def post_conversation(request: VisitorSession) -> dict[str, str]:
    return {"session_id": start_conversation(request.user_id)}


@app.get("/conversations")
def get_conversations(user_id: str) -> list[dict[str, str]]:
    return list_conversations(user_id)


@app.get("/conversations/{session_id}")
def get_conversation(session_id: str, user_id: str) -> dict[str, object]:
    owner = conversation_owner(session_id)
    if owner != user_id.strip():
        raise HTTPException(status_code=403, detail="That conversation belongs to another customer.")
    return {"session_id": session_id, "messages": get_history(session_id)}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    if request.user_id:
        owner = conversation_owner(request.session_id)
        if owner not in (None, request.user_id):
            raise HTTPException(status_code=403, detail="That conversation belongs to another customer.")
    history = get_history(request.session_id)
    profile = load_profile(request.user_id) if request.user_id else None
    result = answer(
        request.message,
        history=history,
        profile=profile,
        user_id=request.user_id,
        session_id=request.session_id,
    )
    append_turn(request.session_id, "user", request.message, user_id=request.user_id)
    append_turn(request.session_id, "assistant", result.reply, user_id=request.user_id)
    return ChatResponse(
        session_id=request.session_id,
        reply=result.reply,
        listings=result.listings,
        answered=result.answered,
    )