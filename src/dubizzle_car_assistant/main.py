import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from dubizzle_car_assistant.inventory import load_listings
from dubizzle_car_assistant.retrieval.structured import search_listings
from dubizzle_car_assistant.retrieval.hybrid import hybrid_search
from dubizzle_car_assistant.agent import answer
from dubizzle_car_assistant.memory.session import append_turn, get_history
from dubizzle_car_assistant.memory.profile import load_profile

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
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ListingMatch]:
    if q is not None and q.strip():
        rows = hybrid_search(
            q.strip(),
            make=make,
            model=model,
            year_min=year_min,
            year_max=year_max,
            price_max=price_max,
            mileage_max=mileage_max,
            limit=limit,
        )
    else:
        rows = search_listings(
            make=make,
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

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=64)
    user_id: str | None = Field(default=None, min_length=1, max_length=64)

class ChatResponse(BaseModel):
    session_id: str
    reply: str


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    history = get_history(request.session_id)
    profile = load_profile(request.user_id) if request.user_id else None
    reply = answer(request.message, history=history, profile=profile)
    append_turn(request.session_id, "user", request.message)
    append_turn(request.session_id, "assistant", reply)
    return ChatResponse(session_id=request.session_id, reply=reply)