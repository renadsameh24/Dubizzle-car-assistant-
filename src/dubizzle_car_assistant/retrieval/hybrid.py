import logging
import time

from dubizzle_car_assistant.retrieval.rerank import adjustment, is_showy, prefers_listed_price
from dubizzle_car_assistant.retrieval.semantic import similarity_scores
from dubizzle_car_assistant.retrieval.structured import search_listings

logger = logging.getLogger("dubizzle")


def hybrid_search(
    query: str,
    *,
    make: str | None = None,
    makes: list[str] | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    mileage_max: int | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    candidates = search_listings(
        make=make,
        makes=makes,
        model=model,
        year_min=year_min,
        year_max=year_max,
        price_max=price_max,
        mileage_max=mileage_max,
        limit=100,
    )
    if prefers_listed_price(query):
        practical = [row for row in candidates if not is_showy(row)]
        if practical:
            candidates = practical
    if not candidates:
        return []
    started = time.perf_counter()
    scores = similarity_scores(query)
    logger.info("semantic_ms=%.0f query=%r candidates=%s", (time.perf_counter() - started) * 1000, query, len(candidates))
    prefer_price = prefers_listed_price(query)

    def sort_key(row: dict[str, object]) -> tuple[int, float]:
        semantic = scores.get(int(row["listing_id"]), 0.0) + adjustment(query, row)
        if prefer_price:
            return (1 if row["price_aed"] is not None else 0, semantic)
        return (1, semantic)

    ranked = sorted(candidates, key=sort_key, reverse=True)
    results: list[dict[str, object]] = []
    for row in ranked[:limit]:
        listing_id = int(row["listing_id"])
        results.append(
            {
                "listing_id": listing_id,
                "score": round(scores.get(listing_id, 0.0) + adjustment(query, row), 3),
                "year": row["year"],
                "make": row["make"],
                "model": row["model"],
                "trim": row["trim"],
                "price_aed": row["price_aed"],
                "mileage_km": row["mileage_km"],
                "title": row["title"],
            }
        )
    return results
