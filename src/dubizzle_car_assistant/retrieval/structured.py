import sqlite3
import threading

import pandas as pd

from dubizzle_car_assistant.inventory import DATA_PATH, load_listings

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None
_loaded_stamp: tuple[object, ...] | None = None


def connect() -> sqlite3.Connection:
    """Reuse one read-only inventory database for this process.

    The spreadsheet is loaded again only when the file changes. Callers must
    not close the connection.
    """
    global _connection, _loaded_stamp
    stamp = (str(DATA_PATH), DATA_PATH.stat().st_mtime_ns, DATA_PATH.stat().st_size)
    with _lock:
        if _connection is not None and _loaded_stamp == stamp:
            return _connection
        if _connection is not None:
            _connection.close()
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        frame = load_listings().astype(object).where(lambda column: pd.notna(column), None)
        frame.to_sql("listings", connection, index=False, if_exists="replace")
        _connection = connection
        _loaded_stamp = stamp
        return connection


def _clauses(
    *,
    make: str | None = None,
    makes: list[str] | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    mileage_max: int | None = None,
    price_is_null: bool = False,
    mileage_is_null: bool = False,
) -> tuple[str, list[object]] | None:
    if makes is not None and len(makes) == 0:
        return None
    clauses = ["1 = 1"]
    parameters: list[object] = []
    if makes is not None:
        placeholders = ", ".join("?" for _ in makes)
        clauses.append(f"make IN ({placeholders})")
        parameters.extend(item.strip().lower() for item in makes)
    elif make is not None:
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
    if price_is_null:
        clauses.append("price_aed IS NULL")
    elif price_max is not None:
        clauses.append("price_aed IS NOT NULL AND price_aed <= ?")
        parameters.append(price_max)
    if mileage_is_null:
        clauses.append("mileage_km IS NULL")
    elif mileage_max is not None:
        clauses.append("mileage_km IS NOT NULL AND mileage_km <= ?")
        parameters.append(mileage_max)
    return " AND ".join(clauses), parameters


def count_listings(**filters: object) -> int:
    built = _clauses(**filters)  # type: ignore[arg-type]
    if built is None:
        return 0
    where, parameters = built
    connection = connect()
    with _lock:
        row = connection.execute(
            f"SELECT COUNT(*) AS total FROM listings WHERE {where}",
            parameters,
        ).fetchone()
    return int(row["total"])


def inventory_counts() -> dict[str, int]:
    connection = connect()
    with _lock:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN price_aed IS NOT NULL THEN 1 ELSE 0 END) AS priced,
                SUM(CASE WHEN mileage_km IS NOT NULL THEN 1 ELSE 0 END) AS with_mileage,
                SUM(CASE WHEN price_aed IS NOT NULL AND mileage_km IS NOT NULL THEN 1 ELSE 0 END) AS both_known,
                SUM(CASE WHEN price_aed IS NULL THEN 1 ELSE 0 END) AS unpriced,
                SUM(CASE WHEN mileage_km IS NULL THEN 1 ELSE 0 END) AS unknown_mileage
            FROM listings
            """
        ).fetchone()
    return {key: int(row[key]) for key in row.keys()}


_ORDERS = {
    "default": "CASE WHEN price_aed IS NULL THEN 1 ELSE 0 END, price_aed, listing_id",
    "cheapest": "price_aed, listing_id",
    "expensive": "price_aed DESC, listing_id",
    "newest": "year DESC, listing_id",
    "lowest_mileage": "mileage_km, listing_id",
}


def get_listing_row(listing_id: int) -> dict[str, object] | None:
    connection = connect()
    with _lock:
        row = connection.execute(
            """
            SELECT listing_id, year, make, model, trim, price_aed, mileage_km, title
            FROM listings
            WHERE listing_id = ?
            """,
            (listing_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def search_listings(
    *,
    make: str | None = None,
    makes: list[str] | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    mileage_max: int | None = None,
    price_is_null: bool = False,
    mileage_is_null: bool = False,
    order: str = "default",
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, object]]:
    if order in {"cheapest", "expensive"}:
        price_is_null = False
    if order == "lowest_mileage":
        mileage_is_null = False
    built = _clauses(
        make=make,
        makes=makes,
        model=model,
        year_min=year_min,
        year_max=year_max,
        price_max=price_max,
        mileage_max=mileage_max,
        price_is_null=price_is_null,
        mileage_is_null=mileage_is_null,
    )
    if built is None:
        return []
    where, parameters = built
    if order == "cheapest" or order == "expensive":
        where += " AND price_aed IS NOT NULL"
    elif order == "lowest_mileage":
        where += " AND mileage_km IS NOT NULL"
    parameters.extend([limit, max(0, offset)])
    ordering = _ORDERS.get(order, _ORDERS["default"])
    query = f"""
        SELECT listing_id, year, make, model, trim, price_aed, mileage_km, title
        FROM listings
        WHERE {where}
        ORDER BY {ordering}
        LIMIT ? OFFSET ?
    """
    connection = connect()
    with _lock:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]