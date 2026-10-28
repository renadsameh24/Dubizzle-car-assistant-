import json
import uuid

from dubizzle_car_assistant.memory.profile import connect


def _conversation(connection, session_id: str, user_id: str | None) -> None:
    row = connection.execute(
        "SELECT user_id FROM conversations WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO conversations (session_id, user_id) VALUES (?, ?)",
            (session_id, user_id),
        )
        return
    if user_id and row["user_id"] not in (None, user_id):
        raise PermissionError("This conversation belongs to another visitor.")
    if user_id and row["user_id"] is None:
        connection.execute(
            "UPDATE conversations SET user_id = ? WHERE session_id = ?",
            (user_id, session_id),
        )


def start_conversation(user_id: str) -> str:
    session_id = uuid.uuid4().hex
    connection = connect()
    try:
        connection.execute(
            "INSERT INTO conversations (session_id, user_id) VALUES (?, ?)",
            (session_id, user_id.strip()),
        )
        connection.commit()
    finally:
        connection.close()
    return session_id


def list_conversations(user_id: str) -> list[dict[str, str]]:
    connection = connect()
    try:
        rows = connection.execute(
            """
            SELECT session_id, updated_at, preview
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC, session_id
            """,
            (user_id.strip(),),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "session_id": row["session_id"],
            "updated_at": row["updated_at"],
            "preview": row["preview"],
        }
        for row in rows
    ]


def conversation_owner(session_id: str) -> str | None:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT user_id FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return row["user_id"]


def get_history(session_id: str) -> list[dict[str, str]]:
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def append_turn(session_id: str, role: str, content: str, user_id: str | None = None) -> None:
    connection = connect()
    try:
        _conversation(connection, session_id, user_id)
        connection.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        preview = connection.execute(
            "SELECT preview FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if role == "user" and preview is not None and not preview["preview"]:
            connection.execute(
                """
                UPDATE conversations
                SET preview = ?, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (content.strip()[:80], session_id),
            )
        else:
            connection.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (session_id,),
            )
        connection.commit()
    finally:
        connection.close()


def get_searches(session_id: str) -> list[list[int]]:
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT listing_ids FROM shown_searches WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    return [list(json.loads(row["listing_ids"])) for row in rows]


def get_search_state(session_id: str) -> dict[str, object]:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT state_json FROM search_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {}
    loaded = json.loads(row["state_json"])
    return loaded if isinstance(loaded, dict) else {}


def save_search_state(session_id: str, state: dict[str, object]) -> None:
    connection = connect()
    try:
        _conversation(connection, session_id, None)
        connection.execute(
            """
            INSERT INTO search_state (session_id, state_json)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, json.dumps(state)),
        )
        connection.commit()
    finally:
        connection.close()


def record_search(session_id: str, listing_ids: list[int]) -> None:
    connection = connect()
    try:
        _conversation(connection, session_id, None)
        connection.execute(
            "INSERT INTO shown_searches (session_id, listing_ids) VALUES (?, ?)",
            (session_id, json.dumps([int(listing_id) for listing_id in listing_ids])),
        )
        connection.commit()
    finally:
        connection.close()
