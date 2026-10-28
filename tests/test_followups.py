from datetime import datetime

import litellm

from dubizzle_car_assistant.agent import (
    BUSY_REPLY,
    INCOMPLETE_REPLY,
    answer,
)
from dubizzle_car_assistant.booking import DUBAI, book_viewing
from dubizzle_car_assistant.conversation import merge_constraints
from dubizzle_car_assistant.inventory import load_listings
from dubizzle_car_assistant.memory.profile import like_car, load_profile, remember_user
from dubizzle_car_assistant.memory.session import get_search_state, record_search, save_search_state
from dubizzle_car_assistant.retrieval.evidence import advertiser_evidence
from dubizzle_car_assistant.retrieval.structured import search_listings

JAPANESE = {
    "origin": "japanese",
    "year_min": 2019,
    "price_max": 50000,
}


def use_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )


def block_model(monkeypatch):
    def explode(**kwargs):
        raise AssertionError("this question is answered from the inventory")

    monkeypatch.setattr("litellm.completion", explode)


def test_a_global_cheapest_search_ignores_the_japanese_filter(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    block_model(monkeypatch)
    save_search_state("shopper", dict(JAPANESE))
    result = answer("Show me the cheapest cars in your inventory.", session_id="shopper")
    assert result.listings[0]["listing_id"] == 20
    assert result.listings[0]["make"] == "ford"
    assert result.listings[0]["price_aed"] == 21500
    assert "Mustang" in result.reply
    assert "listing 20" in result.reply
    assert [card["price_aed"] for card in result.listings] == sorted(card["price_aed"] for card in result.listings)
    assert get_search_state("shopper").get("origin") is None
    assert get_search_state("shopper").get("price_max") is None


def test_only_toyotas_keeps_the_year_and_budget(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "dubizzle_car_assistant.retrieval.hybrid.similarity_scores",
        lambda query: {listing_id: 0.2 for listing_id in range(1, 101)},
    )
    save_search_state("shopper", dict(JAPANESE))
    state, question = merge_constraints(dict(JAPANESE), "only Toyotas")
    assert question is None
    assert state["make"] == "toyota"
    assert state["year_min"] == 2019
    assert state["price_max"] == 50000

    class Function:
        name = "search_cars"
        arguments = '{"query": "toyota", "price_max": 40}'

    class ToolCall:
        id = "call-1"
        function = Function()

    class Message:
        def __init__(self, content, tool_calls):
            self.content = content
            self.tool_calls = tool_calls

        def model_dump(self, exclude_none=True):
            data = {"role": "assistant", "content": self.content}
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

    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return Response(Message(None, [ToolCall()]))
        return Response(Message(None, None))

    monkeypatch.setattr("litellm.completion", fake_completion)
    result = answer("only Toyotas", session_id="shopper")
    ids = [card["listing_id"] for card in result.listings]
    assert 61 not in ids
    assert 12 not in ids
    assert result.listings
    assert all(card["make"] == "toyota" for card in result.listings)
    assert all(card["year"] >= 2019 for card in result.listings)
    assert all(card["price_aed"] is None for card in result.listings)
    assert "under the budget" not in result.reply


def test_missing_prices_can_be_global_or_limited_to_the_previous_search(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    block_model(monkeypatch)
    save_search_state("shopper", dict(JAPANESE))
    everything = answer("Show me cars without listed prices", session_id="shopper")
    assert "of 87" in everything.reply
    assert get_search_state("shopper")["origin"] == "japanese"
    assert get_search_state("shopper")["year_min"] == 2019
    limited = answer(
        "Show the unpriced Japanese cars from the previous search",
        session_id="shopper",
    )
    assert "of 6" in limited.reply
    assert "of 87" not in limited.reply
    assert "under the budget" not in limited.reply
    assert limited.listings
    japanese = {"toyota", "lexus", "honda", "nissan", "infiniti", "mazda", "mitsubishi", "suzuki", "subaru"}
    assert all(card["price_aed"] is None for card in limited.listings)
    assert all(card["make"] in japanese for card in limited.listings)
    assert all(card["year"] >= 2019 for card in limited.listings)
    assert get_search_state("shopper")["price_max"] == 50000


def test_price_sorts_are_deterministic():
    cheapest = search_listings(order="cheapest", limit=5)
    expensive = search_listings(order="expensive", limit=1)
    newest = search_listings(order="newest", limit=1)
    lowest = search_listings(order="lowest_mileage", limit=3)
    assert [row["listing_id"] for row in cheapest][0] == 20
    assert expensive[0]["listing_id"] == 15
    assert expensive[0]["price_aed"] == 1349999
    assert newest[0]["year"] == 2025
    assert [row["mileage_km"] for row in lowest] == [0, 0, 0]
    assert [row["listing_id"] for row in lowest] == [28, 54, 66]


def test_a_sportage_follow_up_shows_only_that_car(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    block_model(monkeypatch)
    record_search("shopper", [5, 14, 87, 40])
    named = answer("The 2020 Kia Sportage", session_id="shopper")
    assert [card["listing_id"] for card in named.listings] == [87]
    result = answer("What's the asking price and mileage?", session_id="shopper")
    assert [card["listing_id"] for card in result.listings] == [87]
    assert result.listings[0]["make"] == "kia"
    assert result.listings[0]["model"] == "sportage"
    assert result.listings[0]["price_aed"] is None
    assert result.listings[0]["mileage_km"] is None
    assert "not listed" in result.reply
    assert "Mercedes" not in result.reply
    assert "Hiace" not in result.reply
    assert "Kaiyi" not in result.reply


def test_an_ambiguous_price_question_does_not_show_other_cards(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    block_model(monkeypatch)
    record_search("shopper", [12, 61])
    result = answer("What's the asking price and mileage?", session_id="shopper")
    assert result.listings == []
    assert "Which car" in result.reply


def test_advertisement_excerpts_stay_on_the_named_car(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    block_model(monkeypatch)
    frame = load_listings()
    description = str(frame.loc[frame["listing_id"] == 87, "description"].iloc[0])
    quote = advertiser_evidence(87, ["panoramic roof"])
    assert quote.startswith("Advertiser claim:")
    body = quote.split(": ", 1)[1]
    assert "Panoramic roof" in body
    assert "KNDP63AC9L7790787" not in quote
    collapsed = " ".join(description.split())
    position = 0
    for word in body.split():
        found = collapsed.find(word, position)
        assert found != -1
        position = found + len(word)
    leather = advertiser_evidence(27, ["leather seats"])
    assert "Leather" in leather
    assert "+971" not in leather
    assert "522051890" not in leather
    save_search_state("shopper", {"focus_listing_id": 87, "last_features": ["panoramic roof"]})
    result = answer("What does the advertisement actually say about that feature?", session_id="shopper")
    assert [card["listing_id"] for card in result.listings] == [87]
    assert "Advertiser claim:" in result.listings[0]["evidence"]
    assert "Panoramic roof" in result.listings[0]["evidence"]
    assert "KNDP" not in result.listings[0]["evidence"]
    assert "independently verified" in result.reply
    accident = answer("What does the advertisement say about accident history?", session_id="shopper")
    assert [card["listing_id"] for card in accident.listings] == [87]
    assert "does not state" in accident.reply
    assert "accident-free" not in accident.reply.lower()


def test_provider_failures_are_retried_and_do_not_invent_cars(monkeypatch):
    monkeypatch.setattr("dubizzle_car_assistant.agent.RETRY_PAUSES", (0, 0))
    calls = {"n": 0}

    def rate_limited(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise litellm.RateLimitError(message="slow down", llm_provider="gemini", model="gemini")
        message = type("Message", (), {"content": "I need a moment.", "tool_calls": None})()
        return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

    monkeypatch.setattr("litellm.completion", rate_limited)
    recovered = answer("Show me a family car")
    assert calls["n"] == 2
    assert recovered.reply == "I need a moment."
    assert recovered.listings == []

    def unavailable(**kwargs):
        raise litellm.ServiceUnavailableError(message="down", llm_provider="gemini", model="gemini")

    monkeypatch.setattr("litellm.completion", unavailable)
    assert answer("Show me a family car").reply == BUSY_REPLY

    def rejected(**kwargs):
        raise litellm.APIError(status_code=400, message="bad request", llm_provider="gemini", model="gemini")

    monkeypatch.setattr("litellm.completion", rejected)
    failed = answer("Show me a family car")
    assert failed.reply == INCOMPLETE_REPLY
    assert failed.listings == []


def test_malformed_tool_arguments_do_not_keep_unrelated_cards(monkeypatch):
    class Function:
        name = "search_cars"
        arguments = "not-json"

    class ToolCall:
        id = "call-1"
        function = Function()

    class Message:
        def __init__(self, content, tool_calls):
            self.content = content
            self.tool_calls = tool_calls

        def model_dump(self, exclude_none=True):
            data = {"role": "assistant", "content": self.content or ""}
            if self.tool_calls:
                data["tool_calls"] = [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "search_cars", "arguments": "not-json"},
                    }
                ]
            return data

    class Response:
        def __init__(self, message):
            self.choices = [type("Choice", (), {"message": message})()]

    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return Response(Message("", [ToolCall()]))
        return Response(Message("Listing 44 is a better choice.", None))

    monkeypatch.setattr("litellm.completion", fake_completion)
    result = answer("Show me a family car")
    assert result.listings == []
    assert "44" not in result.reply
    assert "not valid" in result.reply


def test_a_listing_question_does_not_drop_a_saved_booking_or_a_liked_car(tmp_path, monkeypatch):
    use_memory(monkeypatch, tmp_path)
    block_model(monkeypatch)
    remember_user("renad", "Renad")
    like_car("renad", 12)
    booked = book_viewing(
        12,
        datetime(2026, 10, 31, 10, 0),
        now=datetime(2026, 10, 28, 9, 0, tzinfo=DUBAI),
        session_id="shopper",
        user_id="renad",
    )
    assert booked["booked"] is True
    save_search_state("shopper", {"focus_listing_id": 87})
    looked = answer("What's the asking price and mileage?", session_id="shopper", user_id="renad")
    assert [card["listing_id"] for card in looked.listings] == [87]
    recalled = answer("What did I book?", session_id="shopper", user_id="renad")
    assert "12" in recalled.reply
    assert load_profile("renad")["liked_listing_ids"] == [12]
