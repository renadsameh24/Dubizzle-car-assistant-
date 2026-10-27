import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from dubizzle_car_assistant.inventory import load_listings

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