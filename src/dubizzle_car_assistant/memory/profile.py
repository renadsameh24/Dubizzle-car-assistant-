import secrets
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            listing_id INTEGER NOT NULL,
            slot TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            PRIMARY KEY (listing_id, slot)
        )
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(bookings)")}
    if "session_id" not in columns:
        connection.execute("ALTER TABLE bookings ADD COLUMN session_id TEXT")
    if "user_id" not in columns:
        connection.execute("ALTER TABLE bookings ADD COLUMN user_id TEXT")
    user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "return_code" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN return_code TEXT")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            preview TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS shown_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            listing_ids TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS search_state (
            session_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            ON CONFLICT(user_id) DO UPDATE SET
                name = CASE
                    WHEN users.name IS NULL
                        OR TRIM(users.name) = ''
                        OR LOWER(TRIM(users.name)) = 'visitor'
                    THEN excluded.name
                    ELSE users.name
                END
            """,
            (user_id.strip(), name.strip()),
        )
        connection.commit()
    finally:
        connection.close()


def add_preference(user_id: str, note: str) -> None:
    connection = connect()
    try:
        existing = connection.execute(
            "SELECT id FROM preferences WHERE user_id = ? AND note = ?",
            (user_id.strip(), note.strip()),
        ).fetchone()
        if existing is None:
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


def clean_name(name: str) -> str:
    return " ".join(name.strip().split())


def _same_name(stored: str | None, typed: str) -> bool:
    if not stored or clean_name(stored).casefold() == "visitor":
        return False
    return clean_name(stored).casefold() == typed.casefold()


def _find_customer(connection: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    typed = clean_name(name)
    if not typed:
        return None
    rows = connection.execute("SELECT user_id, name FROM users").fetchall()
    named = [row for row in rows if _same_name(row["name"], typed)]
    if named:
        return named[0]
    for row in rows:
        if clean_name(row["user_id"]).casefold() == typed.casefold():
            return row
    return None


def _opened(row: sqlite3.Row) -> dict[str, object]:
    from dubizzle_car_assistant.memory.session import list_conversations

    display = row["name"] or row["user_id"]
    return {
        "ok": True,
        "user_id": row["user_id"],
        "name": display,
        "conversations": list_conversations(row["user_id"]),
        "booking_count": _booking_count(row["user_id"]),
    }


def create_named_visitor(name: str) -> dict[str, object]:
    """Create a shopper the customer can find again by the same name."""
    cleaned = clean_name(name)
    if not cleaned:
        return {"ok": False, "message": "Enter your name."}
    if len(cleaned) > 80:
        return {"ok": False, "message": "Use a name of 80 characters or fewer."}
    connection = connect()
    try:
        if _find_customer(connection, cleaned) is not None:
            return {
                "ok": False,
                "taken": True,
                "message": "That name is already saved. Choose I'm returning, or use a different name.",
            }
        user_id = f"visitor-{secrets.token_hex(3)}"
        connection.execute(
            "INSERT INTO users (user_id, name) VALUES (?, ?)",
            (user_id, cleaned),
        )
        connection.commit()
    finally:
        connection.close()
    return {"ok": True, "user_id": user_id, "name": cleaned}


def open_by_name(name: str) -> dict[str, object]:
    """Open the saved shopper for this name. An unknown name includes no profile."""
    cleaned = clean_name(name)
    if not cleaned:
        return {"ok": False, "message": "Enter your name."}
    connection = connect()
    try:
        row = _find_customer(connection, cleaned)
    finally:
        connection.close()
    if row is None:
        return {"ok": False, "message": "There is no saved customer with that name."}
    stored = row["name"]
    if not stored or clean_name(stored).casefold() == "visitor":
        connection = connect()
        try:
            connection.execute(
                "UPDATE users SET name = ? WHERE user_id = ?",
                (cleaned, row["user_id"]),
            )
            connection.commit()
            row = connection.execute(
                "SELECT user_id, name FROM users WHERE user_id = ?",
                (row["user_id"],),
            ).fetchone()
        finally:
            connection.close()
    return _opened(row)


def _booking_count(user_id: str) -> int:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM bookings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        connection.close()
    return int(row["total"])