import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parents[3] / "data" / "memory.sqlite"


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS liked_cars (
            user_id TEXT NOT NULL,
            listing_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, listing_id),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
    )
    return connection


def remember_user(user_id: str, name: str) -> None:
    connection = connect()
    try:
        connection.execute(
            """
            INSERT INTO users (user_id, name)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET name = excluded.name
            """,
            (user_id.strip(), name.strip()),
        )
        connection.commit()
    finally:
        connection.close()


def add_preference(user_id: str, note: str) -> None:
    connection = connect()
    try:
        connection.execute(
            "INSERT INTO preferences (user_id, note) VALUES (?, ?)",
            (user_id.strip(), note.strip()),
        )
        connection.commit()
    finally:
        connection.close()


def like_car(user_id: str, listing_id: int) -> None:
    connection = connect()
    try:
        connection.execute(
            """
            INSERT INTO liked_cars (user_id, listing_id)
            VALUES (?, ?)
            ON CONFLICT(user_id, listing_id) DO NOTHING
            """,
            (user_id.strip(), listing_id),
        )
        connection.commit()
    finally:
        connection.close()


def load_profile(user_id: str) -> dict[str, object]:
    connection = connect()
    try:
        user = connection.execute(
            "SELECT name FROM users WHERE user_id = ?",
            (user_id.strip(),),
        ).fetchone()
        preferences = connection.execute(
            "SELECT note FROM preferences WHERE user_id = ? ORDER BY id",
            (user_id.strip(),),
        ).fetchall()
        likes = connection.execute(
            "SELECT listing_id FROM liked_cars WHERE user_id = ? ORDER BY listing_id",
            (user_id.strip(),),
        ).fetchall()
    finally:
        connection.close()
    return {
        "user_id": user_id.strip(),
        "name": None if user is None else user["name"],
        "preferences": [row["note"] for row in preferences],
        "liked_listing_ids": [int(row["listing_id"]) for row in likes],
    }