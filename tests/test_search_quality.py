from fastapi.testclient import TestClient

from dubizzle_car_assistant.agent import ground_reply, next_shown, run_search
from dubizzle_car_assistant.conversation import (
    merge_constraints,
    normalize_makes,
    price_cap,
    resolve_listing_reference,
)
from dubizzle_car_assistant.inventory import load_listings
from dubizzle_car_assistant.leads import save_lead
from dubizzle_car_assistant.main import app
from dubizzle_car_assistant.memory.profile import like_car, load_profile, remember_user
from dubizzle_car_assistant.retrieval.evidence import advertiser_evidence
from dubizzle_car_assistant.retrieval.structured import count_listings, inventory_counts, search_listings

CATALOG = {
    12: {"make": "mazda", "model": "3"},
    61: {"make": "toyota", "model": "camry"},
}


def use_scores(monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.retrieval.hybrid.similarity_scores",
        lambda query: {listing_id: 0.2 for listing_id in range(1, 101)},
    )


def test_counts_match_the_cleaned_sheet():
    frame = load_listings()
    counts = inventory_counts()
    assert counts["total"] == len(frame) == 100
    assert counts["priced"] == int(frame["price_aed"].notna().sum()) == 13
    assert counts["with_mileage"] == int(frame["mileage_km"].notna().sum()) == 39
    assert counts["both_known"] == int((frame["price_aed"].notna() & frame["mileage_km"].notna()).sum()) == 12
    assert counts["unpriced"] == 87
    assert counts["unknown_mileage"] == 61
    assert count_listings(price_is_null=True) == 87
    assert count_listings(mileage_is_null=True) == 61


def test_missing_price_and_mileage_pages_do_not_overlap():
    first = search_listings(price_is_null=True, limit=5, offset=0)
    second = search_listings(price_is_null=True, limit=5, offset=5)
    assert len(first) == 5
    assert len(second) == 5
    assert {row["listing_id"] for row in first}.isdisjoint({row["listing_id"] for row in second})
    assert all(row["price_aed"] is None for row in first + second)
    unknown = search_listings(mileage_is_null=True, limit=5, offset=0)
    assert all(row["mileage_km"] is None for row in unknown)


def test_budget_splits_confirmed_and_potential_matches(monkeypatch):
    use_scores(monkeypatch)
    payload = run_search(
        {
            "query": "reliable everyday car",
            "origin": "japanese",
            "year_min": 2019,
            "price_max": 50000,
        },
        user_text="Japanese cars from 2019 under 50000 AED",
    )
    assert [row["listing_id"] for row in payload["results"]] == [12]
    assert payload["results"][0]["price_aed"] == 35000
    assert payload["potential_total"] == 6
    assert len(payload["potential"]) == 3
    assert all(row["price_aed"] is None for row in payload["potential"])
    assert "under the budget" not in payload["note"]
    assert "no listed price" in payload["note"]
    assert payload["cards"][0]["match_kind"] == "confirmed"
    assert payload["cards"][0]["photo_url"].startswith("http")
    assert {card["match_kind"] for card in payload["cards"]} == {"confirmed", "potential"}


def test_a_strict_price_search_hides_unpriced_cars(monkeypatch):
    use_scores(monkeypatch)
    payload = run_search(
        {"query": "japanese cars", "origin": "japanese", "year_min": 2019, "price_max": 50000},
        user_text="only cars with confirmed prices under 50k",
    )
    assert [row["listing_id"] for row in payload["results"]] == [12]
    assert payload["potential"] == []
    assert "confirmed prices only" in payload["note"]


def test_catalog_questions_use_sql_and_do_not_call_the_model(monkeypatch):
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("catalog questions are answered from SQL")),
    )
    from dubizzle_car_assistant.agent import answer

    counted = answer("How many cars have both price and mileage?")
    assert "12 of 100" in counted.reply
    assert counted.listings == []
    unpriced = answer("Show me cars without listed prices")
    assert "of 87" in unpriced.reply
    assert unpriced.listings
    assert all(card["price_aed"] is None for card in unpriced.listings)
    assert all(card["match_kind"] == "inventory" for card in unpriced.listings)
    unknown = answer("Which cars have unknown mileage?")
    assert "of 61" in unknown.reply
    assert all(card["mileage_km"] is None for card in unknown.listings)


def test_amounts_and_a_truncated_model_budget(monkeypatch):
    use_scores(monkeypatch)
    assert price_cap([], "around 60k") == 60000
    assert price_cap([], "50,000 AED") == 50000
    assert price_cap([], "under 40k") == 40000
    payload = run_search(
        {"query": "toyota", "make": "toyta", "price_max": 40},
        remembered_price_max=40000,
    )
    assert payload["applied_filters"]["price_max"] == 40000
    assert payload["results"]
    assert all(row["price_aed"] <= 40000 for row in payload["results"])
    assert all(row["make"] == "toyota" for row in payload["results"])


def test_search_limits_change_one_at_a_time_and_are_not_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    state, question = merge_constraints({}, "Japanese cars from 2019 onwards under 40k.")
    assert question is None
    assert state == {"origin": "japanese", "year_min": 2019, "price_max": 40000}
    state, question = merge_constraints(state, "What about Toyota?")
    assert state["make"] == "toyota"
    assert state["year_min"] == 2019
    assert state["price_max"] == 40000
    assert state["origin"] == "japanese"
    state, question = merge_constraints(state, "Forget Toyota, show any brand but keep my budget.")
    assert "make" not in state
    assert "origin" not in state
    assert state["year_min"] == 2019
    assert state["price_max"] == 40000
    kept, question = merge_constraints(state, "What about 2020?")
    assert "2020" in question
    assert kept["year_min"] == 2019
    assert load_profile("renad")["preferences"] == []


def test_typos_and_ordinals_and_the_digit_three():
    assert normalize_makes("toyta mercdes mazdaa nisan low milage") == (
        "toyota mercedes-benz mazda nissan low mileage"
    )
    assert resolve_listing_reference("the first car", [[12, 61]], CATALOG)["listing_ids"] == [12]
    assert resolve_listing_reference("the second one", [[12, 61]], CATALOG)["listing_ids"] == [61]
    assert resolve_listing_reference("that Mazda", [[12, 61]], CATALOG)["listing_ids"] == [12]
    assert resolve_listing_reference("the price is 35000", [[12]], CATALOG)["action"] == "missing"


def test_feature_evidence_is_an_advertiser_quote():
    frame = load_listings()
    text = (frame["title"] + "\n" + frame["description"]).str.lower()
    gcc_id = int(frame.loc[text.str.contains(r"\bgcc\b", regex=True), "listing_id"].iloc[0])
    plain_id = int(frame.loc[~text.str.contains(r"\bgcc\b", regex=True), "listing_id"].iloc[0])
    quote = advertiser_evidence(gcc_id, ["gcc"])
    assert quote.startswith("Advertiser claim:")
    assert "gcc" in quote.lower()
    assert advertiser_evidence(plain_id, ["gcc"]) == ""


def test_empty_search_clears_cards_and_reply_cannot_name_another_car():
    broader = [
        {"listing_id": 12, "make": "mazda"},
        {"listing_id": 41, "make": "nissan"},
    ]
    assert next_shown({"cards": [{"listing_id": 12, "make": "mazda"}]}, broader) == broader
    assert next_shown({"cards": []}, [{"listing_id": 12, "make": "mazda"}]) == []
    assert next_shown({"booked": True}, [{"listing_id": 12, "make": "mazda"}]) == [
        {"listing_id": 12, "make": "mazda"}
    ]
    cards = [{"listing_id": 12, "make": "mazda", "match_kind": "confirmed"}]
    reply = ground_reply(
        "Listing 44 is a better Land Rover.",
        cards,
        "Confirmed matches: listing 12 only.",
    )
    assert "44" not in reply
    assert "Land Rover" not in reply


def test_bad_tool_numbers_do_not_crash():
    payload = run_search({"query": "cars", "price_max": "forty", "year_min": "nope"})
    assert payload["cards"] == []
    assert "whole number" in payload["error"]


def test_a_liked_car_is_still_there_in_a_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.memory.profile.DATABASE_PATH",
        tmp_path / "memory.sqlite",
    )
    remember_user("renad", "Renad")
    like_car("renad", 12)
    assert load_profile("renad")["liked_listing_ids"] == [12]
    monkeypatch.setattr("dubizzle_car_assistant.leads.LEADS_PATH", tmp_path / "leads.csv")
    saved = save_lead(user_id="renad", price_max_aed=40000, needs="family car", name="Renad")
    assert saved["saved"] is True


def test_search_endpoint_still_returns_only_the_priced_mazda():
    client = TestClient(app)
    response = client.get(
        "/listings/search",
        params={"origin": "japanese", "year_min": 2019, "price_max": 50000},
    )
    assert response.status_code == 200
    assert [row["listing_id"] for row in response.json()] == [12]
