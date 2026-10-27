import sqlite3

import pandas as pd

from dubizzle_car_assistant.inventory import load_listings


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    frame = load_listings().astype(object).where(lambda column: pd.notna(column), None)
    frame.to_sql("listings", connection, index=False, if_exists="replace")
    return connection

def search_listings(
    *,
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    mileage_max: int | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    clauses = ["1 = 1"]
    parameters: list[object] = []
    if make is not None:
        clauses.append("make = ?")
        parameters.append(make.strip().lower())
    if model is not None:
        clauses.append("model = ?")
        parameters.append(model.strip().lower())
    if year_min is not None:
        clauses.append("year >= ?")
        parameters.append(year_min)
    if year_max is not None:
        clauses.append("year <= ?")
        parameters.append(year_max)
    if price_max is not None:
        clauses.append("price_aed IS NOT NULL AND price_aed <= ?")
        parameters.append(price_max)
    if mileage_max is not None:
        clauses.append("mileage_km IS NOT NULL AND mileage_km <= ?")
        parameters.append(mileage_max)
    parameters.append(limit)
    query = f"""
        SELECT listing_id, year, make, model, trim, price_aed, mileage_km, title
        FROM listings
        WHERE {" AND ".join(clauses)}
        ORDER BY listing_id
        LIMIT ?
    """
    connection = connect()
    try:
        rows = connection.execute(query, parameters).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]