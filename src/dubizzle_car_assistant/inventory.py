import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "cars.xlsx"
SHEET_NAME = "cleaned dataset"

CASH_PRICE = re.compile(r"aed\s*([\d,]+(?:\.\d+)?)\s*(?:/-)?\s*cash")
AED_AMOUNT = re.compile(r"aed\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*aed")
MONTHLY_PRICE = re.compile(
    r"\s*(?:\.\d+)?\s*(?:/-)?\s*(?:monthly|monthy|per\s+month|/\s*mo\b)"
)
MILEAGE = re.compile(
    r"(?P<label>mileage|odometer)?"
    r"[^0-9]{0,24}"
    r"(?P<number>\d{1,3}(?:[,\s]\d{3})+|\d+)"
    r"\s*(?:\(\s*)?kms?\b",
    re.I,
)


def parse_price_aed(text: str) -> int | None:
    lowered = text.lower()
    cash_amounts = [
        int(float(raw.replace(",", ""))) for raw in CASH_PRICE.findall(lowered)
    ]
    if cash_amounts:
        return cash_amounts[-1]

    amounts: list[int] = []
    for match in AED_AMOUNT.finditer(lowered):
        if MONTHLY_PRICE.match(lowered, match.end()):
            continue
        raw = match.group(1) or match.group(2)
        amounts.append(int(float(raw.replace(",", ""))))
    if not amounts:
        return None
    return max(amounts)

def parse_mileage_km(text: str) -> int | None:
    lowered = text.lower().replace("\n", " ")
    labeled: list[int] = []
    plain: list[int] = []
    for match in MILEAGE.finditer(lowered):
        number_at = match.start("number")
        after = lowered[match.end() : match.end() + 16]
        before = lowered[max(0, number_at - 24) : number_at]
        if "/h" in after or "unlimited" in before or "up to" in before:
            continue
        if re.search(r"or\s+1\s+year|whichever", after):
            continue
        if re.search(r"\d\s*-\s*$", before):
            continue
        value = int(re.sub(r"[,\s]", "", match.group("number")))
        if value > 1_000_000:
            continue
        if match.group("label"):
            labeled.append(value)
        else:
            plain.append(value)
    if labeled:
        return labeled[0]
    if plain:
        return plain[0]
    return None
@lru_cache(maxsize=4)
def _read_listings(path: str) -> pd.DataFrame:
    frame = pd.read_excel(path, sheet_name=SHEET_NAME)
    listings = pd.DataFrame(
        {
            "listing_id": frame["Listing_ID"].astype(int),
            "year": frame["year"].astype(int),
            "make": frame["make"].astype(str).str.strip().str.lower(),
            "model": frame["model"].astype(str).str.strip().str.lower(),
            "trim": frame["trim"].astype(str).str.strip().str.lower(),
            "title": frame["title"].astype(str).str.strip(),
            "description": frame["description"].astype(str).str.strip(),
            "photo_url": frame["photo_url"].astype(str).str.strip(),
        }
    )
    text = listings["title"] + "\n" + listings["description"]
    listings["price_aed"] = text.map(parse_price_aed).astype("Int64")
    listings["mileage_km"] = text.map(parse_mileage_km).astype("Int64")
    return listings


def load_listings(path: Path = DATA_PATH) -> pd.DataFrame:
    return _read_listings(str(path)).copy()