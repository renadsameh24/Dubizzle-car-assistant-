from dubizzle_car_assistant.retrieval.semantic import similarity_scores
from dubizzle_car_assistant.retrieval.structured import search_listings


def hybrid_search(
    query: str,
    *,
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    mileage_max: int | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    candidates = search_listings(
        make=make,
        model=model,
        year_min=year_min,
        year_max=year_max,
        price_max=price_max,
        mileage_max=mileage_max,
        limit=100,
    )
    if not candidates:
        return []
    scores = similarity_scores(query)
    ranked = sorted(
        candidates,
        key=lambda row: scores[int(row["listing_id"])],
        reverse=True,
    )
    results: list[dict[str, object]] = []
    for row in ranked[:limit]:
        listing_id = int(row["listing_id"])
        results.append(
            {
                "listing_id": listing_id,
                "score": round(scores[listing_id], 3),
                "year": row["year"],
                "make": row["make"],
                "model": row["model"],
                "price_aed": row["price_aed"],
                "mileage_km": row["mileage_km"],
                "title": row["title"],
                "trim": row["trim"],
            }
        )
    return results