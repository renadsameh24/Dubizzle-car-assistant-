from datetime import datetime

from dubizzle_car_assistant.booking import DUBAI, is_recall, request_viewing, resolve_schedule
from dubizzle_car_assistant.conversation import price_cap, resolve_listing_reference

NOW = datetime(2026, 10, 28, 11, 0, tzinfo=DUBAI)


def test_next_saturday_at_9_is_a_future_dubai_morning(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    result = request_viewing(
        {"when": "2024-01-01 09:00"},
        user_text="Book a viewing for listing 61 next Saturday at 9 AM",
        now=NOW,
    )
    assert result["booked"] is True
    assert result["listing_id"] == 61
    assert result["when"] == "2026-10-31 09:00"
    assert "passed" not in result.get("reason", "")


def test_monday_at_2_pm_is_2_november(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    result = request_viewing(
        {"listing_id": 61, "when": "Monday at 2 PM"},
        user_text="Monday at 2 PM",
        now=NOW,
    )
    assert result["booked"] is True
    assert result["when"] == "2026-11-02 14:00"


def test_sunday_is_rejected_with_the_opening_rule():
    schedule = resolve_schedule("Sunday at 10 AM", NOW)
    result = request_viewing(
        {"listing_id": 61, "when": "Sunday at 10 AM"},
        user_text="Book listing 61 on Sunday at 10 AM",
        now=NOW,
    )
    assert schedule["when"].weekday() == 6
    assert result["booked"] is False
    assert "Monday to Saturday" in result["reason"]
    assert "passed" not in result["reason"]


def test_a_verified_past_slot_is_named():
    result = request_viewing(
        {"listing_id": 61, "when": "2026-10-26 08:00"},
        now=NOW,
    )
    assert result["booked"] is False
    assert "2026-10-26 08:00" in result["reason"]
    assert "passed" in result["reason"]


def test_missing_day_or_time_is_specific():
    missing_time = resolve_schedule("next Saturday", NOW)
    missing_day = resolve_schedule("at 9 AM", NOW)
    assert missing_time["reason"].startswith("No time was given")
    assert missing_day["reason"].startswith("No day was given")


def test_next_weekend_asks_which_day_and_does_not_book(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    result = request_viewing(
        {"listing_id": 61, "when": "next weekend at 9 AM"},
        user_text="Book listing 61 next weekend at 9 AM",
        now=NOW,
    )
    assert result["booked"] is False
    assert "ambiguous" in result["reason"] or "Do you mean" in result["reason"]
    assert "passed" not in result["reason"]


def test_booking_after_an_empty_search_asks_which_car():
    result = request_viewing(
        {"listing_id": 12, "when": "Monday at 2 PM"},
        user_text="Book the car you just showed me on Monday at 2 PM",
        now=NOW,
        shown_searches=[[61], []],
    )
    assert result["booked"] is False
    assert "latest search" in result["reason"]
    assert "12" not in result["reason"] or "Earlier" in result["reason"]


def test_a_named_earlier_car_is_the_one_that_was_shown(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    result = request_viewing(
        {"listing_id": 12, "when": "Monday at 2 PM"},
        user_text="Book the Camry on Monday at 2 PM",
        now=NOW,
        shown_searches=[[12, 61]],
    )
    assert result["booked"] is True
    assert result["listing_id"] == 61


def test_an_earlier_budget_stays_on_a_sporty_request(monkeypatch):
    assert price_cap(["My budget is around 50k"], "Show me something sporty but practical") == 50000
    assert price_cap(["My budget is around 50k"], "Forget the budget and show me luxury") is None
    monkeypatch.setattr(
        "dubizzle_car_assistant.retrieval.hybrid.similarity_scores",
        lambda query: {listing_id: 0.4 for listing_id in range(1, 101)},
    )
    from dubizzle_car_assistant.agent import run_search

    payload = run_search(
        {"query": "sporty but practical luxury"},
        remembered_price_max=50000,
    )
    assert payload["results"]
    assert all(row["price_aed"] is not None and row["price_aed"] <= 50000 for row in payload["results"])
    assert all(row["listing_id"] != 44 for row in payload["results"])
    assert "50,000" in payload["note"] or "50000" in payload["note"]


def test_two_listings_are_booked_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    result = request_viewing(
        {"when": "Monday at 2 PM"},
        user_text="Book viewings for listings 44 and 28 on Monday at 2 PM",
        now=NOW,
    )
    assert result["booked"] is True
    assert sorted(item["listing_id"] for item in result["bookings"]) == [28, 44]
    assert all(item["when"] == "2026-11-02 14:00" and item["booked"] for item in result["bookings"])
    duplicate = request_viewing(
        {"when": "Monday at 2 PM"},
        user_text="Book viewings for listings 44 and 28 on Monday at 2 PM",
        now=NOW,
    )
    assert duplicate["booked"] is False
    assert all("already booked" in item["reason"] for item in duplicate["bookings"])


def _rows():
    from dubizzle_car_assistant.memory.profile import connect

    connection = connect()
    try:
        return connection.execute(
            "SELECT listing_id, slot FROM bookings ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()


def test_a_time_change_keeps_the_saved_day_and_replaces_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    booked = request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
    )
    assert booked["when"] == "2026-10-31 10:00"
    moved = request_viewing(
        {"listing_id": 61, "when": "2 PM"},
        user_text="Actually, make it 2 PM instead",
        now=NOW,
        session_id="shopper",
    )
    assert moved["booked"] is True
    assert moved["updated"] is True
    assert moved["listing_id"] == 12
    assert moved["when"] == "2026-10-31 14:00"
    rows = _rows()
    assert [(row["listing_id"], row["slot"]) for row in rows] == [(12, "2026-10-31 14:00")]
    assert rows[0]["slot"] in moved["summary"]


def test_a_day_change_keeps_the_saved_time(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
    )
    moved = request_viewing(
        {},
        user_text="Move it to Monday",
        now=NOW,
        session_id="shopper",
    )
    assert moved["booked"] is True
    assert moved["listing_id"] == 12
    assert moved["when"] == "2026-11-02 10:00"
    assert [(row["listing_id"], row["slot"]) for row in _rows()] == [(12, "2026-11-02 10:00")]


def test_moving_to_sunday_keeps_the_saved_booking(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
    )
    refused = request_viewing(
        {},
        user_text="Move it to Sunday",
        now=NOW,
        session_id="shopper",
    )
    assert refused["booked"] is False
    assert "Monday to Saturday" in refused["reason"]
    assert [(row["listing_id"], row["slot"]) for row in _rows()] == [(12, "2026-10-31 10:00")]


def test_changing_the_car_keeps_the_saved_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
    )
    moved = request_viewing(
        {"listing_id": 12},
        user_text="Actually, book the Camry instead",
        now=NOW,
        session_id="shopper",
    )
    assert moved["booked"] is True
    assert moved["listing_id"] == 61
    assert moved["when"] == "2026-10-31 10:00"
    assert [(row["listing_id"], row["slot"]) for row in _rows()] == [(61, "2026-10-31 10:00")]


def test_what_did_i_book_matches_the_saved_row(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
    )
    recalled = request_viewing(
        {"when": "Saturday at 9 AM", "listing_id": 44},
        user_text="What did I book?",
        now=NOW,
        session_id="shopper",
    )
    slot = _rows()[0]["slot"]
    assert recalled["recalled"] is True
    assert recalled["bookings"][0]["when"] == slot
    assert slot in recalled["summary"]
    assert len(_rows()) == 1


def test_what_car_and_time_recalls_the_saved_booking(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 9 AM",
        now=NOW,
        session_id="shopper",
        user_id="jana",
    )

    def explode(**kwargs):
        raise AssertionError("a saved booking is recalled without the model")

    monkeypatch.setattr("litellm.completion", explode)
    from dubizzle_car_assistant.agent import answer

    result = answer(
        "What car and time do I currently have booked?",
        session_id="shopper",
        user_id="jana",
        now=NOW,
    )
    assert "Mazda" in result.reply
    assert "09:00" in result.reply
    assert "No time was given" not in result.reply


def test_booking_questions_recall_whatever_the_wording(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 9 AM",
        now=NOW,
        session_id="shopper",
        user_id="jana",
    )

    def explode(**kwargs):
        raise AssertionError("a booking question is answered from the saved row")

    monkeypatch.setattr("litellm.completion", explode)
    from dubizzle_car_assistant.agent import answer

    for question in (
        "when is my viewing?",
        "which car did I reserve?",
        "did I already schedule something?",
        "have I booked anything?",
        "remind me of my appointment",
    ):
        result = answer(question, session_id="shopper", user_id="jana", now=NOW)
        assert "Mazda" in result.reply, question
        assert "09:00" in result.reply, question
        assert "No time was given" not in result.reply

    tool_call = request_viewing(
        {"when": "tomorrow at 9 AM", "listing_id": 44},
        user_text="so what am I down for?",
        now=NOW,
        session_id="shopper",
        user_id="jana",
    )
    assert tool_call["recalled"] is True
    assert "09:00" in tool_call["summary"]
    assert "No time was given" not in tool_call["summary"]
    assert len(_rows()) == 1

    assert is_recall("Any decent Japanese cars from 2019 onwards?") is False
    assert is_recall("What cars can I book?") is False
    fresh = request_viewing(
        {},
        user_text="Book a viewing",
        now=NOW,
        session_id="shopper",
        user_id="jana",
    )
    assert fresh.get("recalled") is not True
    assert "No day or time was given" in str(fresh.get("reason"))


def test_change_to_a_clock_time_uses_the_saved_booking(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
        user_id="jana",
    )

    def explode(**kwargs):
        raise AssertionError("changing the hour uses the saved booking")

    monkeypatch.setattr("litellm.completion", explode)
    from dubizzle_car_assistant.agent import answer

    result = answer("change to 9:am that day", session_id="shopper", user_id="jana", now=NOW)
    assert [(row["listing_id"], row["slot"]) for row in _rows()] == [(12, "2026-10-31 09:00")]
    assert "09:00" in result.reply
    assert "Mazda" in result.reply


def test_the_chat_reply_matches_the_rescheduled_sqlite_row(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    request_viewing(
        {"listing_id": 12},
        user_text="Book listing 12 next Saturday at 10 AM",
        now=NOW,
        session_id="shopper",
    )

    def explode(**kwargs):
        raise AssertionError("the saved booking should answer this without the model")

    monkeypatch.setattr("litellm.completion", explode)
    from dubizzle_car_assistant.agent import answer

    result = answer("Actually, make it 2 PM instead", session_id="shopper", now=NOW)
    rows = _rows()
    assert [(row["listing_id"], row["slot"]) for row in rows] == [(12, "2026-10-31 14:00")]
    assert rows[0]["slot"] in result.reply
    assert "12" in result.reply


def test_a_search_without_a_query_still_returns_the_mazda(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    monkeypatch.setattr(
        "dubizzle_car_assistant.retrieval.hybrid.similarity_scores",
        lambda query: {listing_id: 0.5 for listing_id in range(1, 101)},
    )

    class Function:
        name = "search_cars"
        arguments = '{"make": "Mazda", "model": "3", "year_min": 2019, "year_max": 2019}'

    class ToolCall:
        id = "call-1"
        function = Function()

    class Message:
        def __init__(self, content, tool_calls):
            self.content = content
            self.tool_calls = tool_calls

        def model_dump(self, exclude_none=True):
            data = {"role": "assistant"}
            if self.tool_calls:
                data["tool_calls"] = [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "search_cars", "arguments": self.tool_calls[0].function.arguments},
                    }
                ]
            return data

    class Response:
        def __init__(self, message):
            self.choices = [type("Choice", (), {"message": message})()]

    state = {"n": 0}

    def fake_completion(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return Response(Message(None, [ToolCall()]))
        return Response(Message(None, None))

    monkeypatch.setattr("litellm.completion", fake_completion)
    from dubizzle_car_assistant.agent import answer

    result = answer("Show me the 2019 Mazda 3", session_id="mazda-test")
    assert result.reply
    assert any(card["listing_id"] == 12 for card in result.listings)


def test_the_named_mazda_is_shown_without_the_constraint_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    from dubizzle_car_assistant.agent import answer
    from dubizzle_car_assistant.memory.session import save_search_state

    save_search_state("shopper", {"make": "mazda", "price_max": 40000, "focus_listing_id": 12})

    def explode(**kwargs):
        raise AssertionError("a named car is shown from the catalog")

    monkeypatch.setattr("litellm.completion", explode)
    result = answer("Show me the 2019 Mazda 3.", session_id="shopper")
    assert "constraint" not in result.reply.lower()
    assert [card["listing_id"] for card in result.listings] == [12]
    assert "35,000" in result.reply


def test_a_tool_error_is_the_reason_the_shopper_should_hear():
    choice = resolve_listing_reference(
        "book that car",
        [[12], []],
        {12: {"make": "mazda", "model": "3"}},
    )
    assert choice["action"] == "ask"
    assert "latest search" in choice["reason"]


def test_a_time_change_keeps_the_car_and_lists_open_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    from dubizzle_car_assistant.agent import answer
    from dubizzle_car_assistant.booking import book_viewing
    from dubizzle_car_assistant.memory.session import save_search_state

    book_viewing(12, datetime(2026, 10, 31, 10, 0), now=NOW)
    book_viewing(12, datetime(2026, 10, 31, 14, 0), now=NOW)
    save_search_state("shopper", {"focus_listing_id": 12})
    history = [
        {
            "role": "user",
            "content": "I'd like to book a viewing for Saturday, October 31, 2026, at 10 AM.",
        },
        {"role": "assistant", "content": "That viewing is already booked."},
    ]

    def explode(**kwargs):
        raise AssertionError("this booking follow-up is answered from the saved car and day")

    monkeypatch.setattr("litellm.completion", explode)
    changed = answer(
        "can we make it that same day but at 2pm?",
        history=history,
        session_id="shopper",
        user_id="jana",
        now=NOW,
    )
    assert "14:00" in changed.reply
    assert "already booked" in changed.reply
    assert "09:00" in changed.reply
    assert "10:00" not in changed.reply.split("Open times", 1)[-1]
    history.append({"role": "user", "content": "can we make it that same day but at 2pm?"})
    history.append({"role": "assistant", "content": changed.reply})
    free = answer("what time is not booked?", history=history, session_id="shopper", user_id="jana", now=NOW)
    assert "09:00" in free.reply
    assert "13:00" in free.reply
    assert "10:00" not in free.reply
    assert "14:00" not in free.reply
    picked = answer("12", history=history, session_id="shopper", user_id="jana", now=NOW)
    assert "14:00" in picked.reply
    assert "already booked" in picked.reply
