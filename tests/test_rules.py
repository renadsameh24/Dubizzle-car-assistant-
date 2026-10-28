from datetime import datetime

from dubizzle_car_assistant.booking import book_viewing, request_viewing, viewing_rejection
from dubizzle_car_assistant.leads import save_lead


def test_viewing_hours():
    assert viewing_rejection(datetime(2026, 10, 26, 8, 0)) is None
    assert viewing_rejection(datetime(2026, 10, 31, 19, 30)) is None
    assert "08:00" in viewing_rejection(datetime(2026, 10, 26, 20, 0))
    assert "Monday" in viewing_rejection(datetime(2026, 11, 1, 10, 0))


def test_book_viewing_requires_a_real_listing_and_open_hours():
    booked = book_viewing(61, datetime(2026, 10, 26, 8, 0))
    assert booked["booked"] is True
    assert booked["listing_id"] == 61
    assert book_viewing(999, datetime(2026, 10, 26, 10, 0))["booked"] is False
    assert book_viewing(61, datetime(2026, 11, 1, 10, 0))["booked"] is False


def test_request_viewing_rejects_bad_arguments():
    assert request_viewing({"listing_id": 61, "when": "next monday"})["booked"] is False
    assert request_viewing({"listing_id": "61", "when": "2026-10-26 08:00"})["booked"] is False


def test_save_lead_requires_needs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.leads.LEADS_PATH",
        tmp_path / "leads.csv",
    )
    saved = save_lead(
        user_id="visitor-9",
        price_max_aed=45000,
        needs="white SUV",
        name="Visitor Nine",
    )
    refused = save_lead(user_id="visitor-9", price_max_aed=45000, needs="   ")
    text = (tmp_path / "leads.csv").read_text()
    assert saved["saved"] is True
    assert refused["saved"] is False
    assert text.count("visitor-9") == 1
    assert "white SUV" in text

def test_answer_asks_the_user_to_retry_when_the_model_is_busy(monkeypatch):
    import litellm

    from dubizzle_car_assistant.agent import BUSY_REPLY, answer

    def busy(*args, **kwargs):
        raise litellm.ServiceUnavailableError(
            message="busy",
            llm_provider="gemini",
            model="gemini/gemini-3.1-flash-lite",
        )

    monkeypatch.setattr(litellm, "completion", busy)
    assert answer("Show me a family car") == BUSY_REPLY