from fastapi.testclient import TestClient

from dubizzle_car_assistant.agent import dropped_constraint
from dubizzle_car_assistant.main import app
from dubizzle_car_assistant.retrieval.constraints import makes_for
from dubizzle_car_assistant.retrieval.rerank import adjustment
from dubizzle_car_assistant.retrieval.structured import search_listings


def test_japanese_budget_keeps_only_priced_cars():
    makes = makes_for("japanese", None)
    rows = search_listings(makes=makes, year_min=2019, price_max=50000, limit=100)
    assert [row["listing_id"] for row in rows] == [12]
    assert rows[0]["make"] == "mazda"
    assert rows[0]["price_aed"] == 35000
    unpriced = search_listings(makes=makes, year_min=2019, price_is_null=True, limit=100)
    assert len(unpriced) == 6
    assert all(row["price_aed"] is None for row in unpriced)


def test_unknown_price_does_not_pass_a_budget():
    rows = search_listings(price_max=50000, limit=100)
    assert rows
    assert all(row["price_aed"] is not None and row["price_aed"] <= 50000 for row in rows)


def test_everyday_query_penalizes_sports_cars_without_a_price():
    question = "something reliable for everyday driving, nothing too expensive"
    camaro = {"make": "chevrolet", "model": "camaro", "trim": "", "title": "Camaro", "price_aed": None}
    mazda = {"make": "mazda", "model": "3", "trim": "", "title": "Mazda 3", "price_aed": 35000}
    assert adjustment(question, mazda) > adjustment(question, camaro)


def test_search_cannot_drop_a_stated_budget():
    message = dropped_constraint(
        {"query": "cars"},
        {"origin": "japanese", "year_min": 2019, "price_max": 50000},
    )
    assert message is not None
    assert "constraint" in message


def test_run_search_reports_cars_with_no_price(monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.retrieval.hybrid.similarity_scores",
        lambda query: {listing_id: 0.2 for listing_id in range(1, 101)},
    )
    from dubizzle_car_assistant.agent import run_search

    payload = run_search(
        {
            "query": "reliable everyday car",
            "origin": "japanese",
            "year_min": 2019,
            "price_max": 50000,
        }
    )
    assert [row["listing_id"] for row in payload["results"]] == [12]
    assert payload["excluded_unpriced"] == 6
    assert "no listed price" in payload["note"]
    assert payload["cards"][0]["price_aed"] == 35000
    assert payload["cards"][0]["photo_url"].startswith("http")


def test_vague_budget_does_not_return_unpriced_or_costly_cars(monkeypatch):
    monkeypatch.setattr(
        "dubizzle_car_assistant.retrieval.hybrid.similarity_scores",
        lambda query: {listing_id: 0.2 for listing_id in range(1, 101)},
    )
    from dubizzle_car_assistant.agent import run_search

    payload = run_search({"query": "reliable for everyday driving, nothing too expensive"})
    assert payload["results"]
    assert all(row["price_aed"] is not None and row["price_aed"] <= 50000 for row in payload["results"])
    assert all(row["model"] != "mustang" for row in payload["results"])
    assert "50,000" in payload["note"]


def test_search_endpoint_applies_origin_and_budget():
    client = TestClient(app)
    response = client.get(
        "/listings/search",
        params={"origin": "japanese", "year_min": 2019, "price_max": 50000},
    )
    assert response.status_code == 200
    body = response.json()
    assert [row["listing_id"] for row in body] == [12]
    assert body[0]["price_aed"] == 35000
