sessions: dict[str, list[dict[str, str]]] = {}


def get_history(session_id: str) -> list[dict[str, str]]:
    return list(sessions.get(session_id, []))


def append_turn(session_id: str, role: str, content: str) -> None:
    sessions.setdefault(session_id, []).append({"role": role, "content": content})