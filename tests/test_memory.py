from datetime import datetime

from fastapi.testclient import TestClient

from dubizzle_car_assistant.booking import DUBAI, request_viewing, visitor_bookings
from dubizzle_car_assistant.main import app
from dubizzle_car_assistant.memory.profile import (
    add_preference,
    connect,
    create_named_visitor,
    load_profile,
    open_by_name,
)
from dubizzle_car_assistant.memory.session import (
    append_turn,
    get_history,
    list_conversations,
    start_conversation,
)

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=DUBAI)


def use_database(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )


def test_a_customer_comes_back_by_name(tmp_path, monkeypatch):
    use_database(monkeypatch, tmp_path)
    created = create_named_visitor("Renad")
    add_preference(created["user_id"], "white SUV under 50000 AED")
    unknown = open_by_name("someone else")
    assert unknown["ok"] is False
    assert "conversations" not in unknown
    assert "Renad" not in unknown["message"]
    opened = open_by_name(" renad ")
    assert opened["ok"] is True
    assert opened["user_id"] == created["user_id"]
    assert opened["name"] == "Renad"
    again = create_named_visitor("renad")
    assert again["ok"] is False
    assert again["taken"] is True
    assert "conversations" not in again
    connection = connect()
    connection.execute("INSERT INTO users (user_id) VALUES ('sam')")
    connection.commit()
    connection.close()
    legacy = open_by_name("Sam")
    assert legacy["ok"] is True
    assert legacy["user_id"] == "sam"
    assert legacy["name"] == "Sam"
    assert open_by_name("Visitor")["ok"] is False


def test_continue_a_previous_chat_or_start_a_fresh_one(tmp_path, monkeypatch):
    use_database(monkeypatch, tmp_path)
    created = create_named_visitor("Renad")
    add_preference(created["user_id"], "family car under 50000 AED")
    first = start_conversation(created["user_id"])
    append_turn(first, "user", "Show me a Mazda", user_id=created["user_id"])
    append_turn(first, "assistant", "Listing 12 is the Mazda.", user_id=created["user_id"])
    second = start_conversation(created["user_id"])
    assert get_history(second) == []
    assert get_history(first)[1]["content"] == "Listing 12 is the Mazda."
    previews = {row["preview"] for row in list_conversations(created["user_id"])}
    assert "Show me a Mazda" in previews
    assert len(list_conversations(created["user_id"])) == 2
    assert load_profile(created["user_id"])["preferences"] == ["family car under 50000 AED"]


def test_a_new_session_still_finds_and_reschedules_the_saved_booking(tmp_path, monkeypatch):
    use_database(monkeypatch, tmp_path)
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="chat-a",
        user_id="renad",
    )
    moved = request_viewing(
        {"listing_id": 61},
        user_text="Actually, make it 2 PM instead",
        now=NOW,
        session_id="chat-b",
        user_id="renad",
    )
    assert moved["booked"] is True
    assert moved["listing_id"] == 12
    assert moved["when"] == "2026-10-31 14:00"
    rows = visitor_bookings("renad")
    assert rows == [{"listing_id": 12, "slot": "2026-10-31 14:00"}]
    assert rows[0]["slot"] in moved["summary"]
    recalled = request_viewing(
        {},
        user_text="What did I book?",
        now=NOW,
        session_id="chat-c",
        user_id="renad",
    )
    assert recalled["bookings"][0]["when"] == "2026-10-31 14:00"
    assert get_history("chat-c") == []


def test_several_bookings_ask_which_one_before_changing(tmp_path, monkeypatch):
    use_database(monkeypatch, tmp_path)
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="chat-a",
        user_id="renad",
    )
    second = request_viewing(
        {"listing_id": 61},
        user_text="Also book listing 61 on Monday at 11 AM",
        now=NOW,
        session_id="chat-a",
        user_id="renad",
    )
    assert second["booked"] is True
    assert second["listing_id"] == 61
    before = visitor_bookings("renad")
    asked = request_viewing(
        {},
        user_text="Actually, make it 2 PM instead",
        now=NOW,
        session_id="chat-b",
        user_id="renad",
    )
    assert asked["booked"] is False
    assert "more than one" in asked["summary"].lower()
    assert "2026-10-31 10:00" in asked["summary"]
    assert "2026-11-02 11:00" in asked["summary"]
    assert visitor_bookings("renad") == before
    changed = request_viewing(
        {},
        user_text="Make listing 12 2 PM instead",
        now=NOW,
        session_id="chat-b",
        user_id="renad",
    )
    assert changed["booked"] is True
    assert changed["when"] == "2026-10-31 14:00"
    assert sorted(row["slot"] for row in visitor_bookings("renad")) == [
        "2026-10-31 14:00",
        "2026-11-02 11:00",
    ]


def test_saved_chats_and_bookings_survive_a_new_connection(tmp_path, monkeypatch):
    use_database(monkeypatch, tmp_path)
    created = create_named_visitor("Renad")
    session_id = start_conversation(created["user_id"])
    append_turn(session_id, "user", "Book the Mazda", user_id=created["user_id"])
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id=session_id,
        user_id=created["user_id"],
    )
    connection = connect()
    connection.close()
    assert get_history(session_id)[0]["content"] == "Book the Mazda"
    assert visitor_bookings(created["user_id"])[0]["slot"] == "2026-10-31 10:00"
    opened = open_by_name("Renad")
    assert opened["conversations"][0]["preview"] == "Book the Mazda"
    assert opened["booking_count"] == 1


def test_a_name_opens_the_saved_chat_and_another_customer_cannot(tmp_path, monkeypatch):
    use_database(monkeypatch, tmp_path)
    created = create_named_visitor("Renad")
    session_id = start_conversation(created["user_id"])
    append_turn(session_id, "user", "My budget is 50000 AED", user_id=created["user_id"])
    client = TestClient(app)
    missing = client.post("/visitors/open", json={"name": "someone else"})
    assert missing.status_code == 200
    assert missing.json()["ok"] is False
    assert "50000" not in missing.text
    other = create_named_visitor("Lina")
    hidden = client.get(
        f"/conversations/{session_id}",
        params={"user_id": other["user_id"]},
    )
    assert hidden.status_code == 403
    assert "50000" not in hidden.text
    opened = client.post("/visitors/open", json={"name": "Renad"})
    assert opened.status_code == 200
    assert opened.json()["conversations"][0]["session_id"] == session_id
    loaded = client.get(
        f"/conversations/{session_id}",
        params={"user_id": created["user_id"]},
    )
    assert loaded.json()["messages"][0]["content"] == "My budget is 50000 AED"
    continued = client.post(
        "/chat",
        json={
            "message": "What did I book?",
            "session_id": session_id,
            "user_id": created["user_id"],
        },
    )
    assert continued.status_code == 200
