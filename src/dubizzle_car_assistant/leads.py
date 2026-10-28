import csv
from datetime import datetime
from pathlib import Path

LEADS_PATH = Path(__file__).resolve().parents[2] / "leads.csv"
LEAD_FIELDS = ["created_at", "user_id", "name", "price_max_aed", "needs"]


def save_lead(
    *,
    user_id: str,
    price_max_aed: int,
    needs: str,
    name: str | None = None,
) -> dict[str, object]:
    if not isinstance(user_id, str) or not user_id.strip():
        return {"saved": False, "reason": "A visitor id is required."}
    if type(price_max_aed) is not int or price_max_aed < 0:
        return {"saved": False, "reason": "A price maximum in AED is required."}
    if not isinstance(needs, str) or not needs.strip():
        return {"saved": False, "reason": "The visitor's needs are required."}

    row = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "user_id": user_id.strip(),
        "name": "" if name is None else name.strip(),
        "price_max_aed": price_max_aed,
        "needs": needs.strip(),
    }
    new_file = not LEADS_PATH.exists()
    with LEADS_PATH.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEAD_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    return {"saved": True, "price_max_aed": price_max_aed, "needs": needs.strip()}

def request_lead(
    user_id: str | None,
    arguments: dict[str, object],
    name: str | None = None,
) -> dict[str, object]:
    price_max_aed = arguments.get("price_max_aed")
    needs = arguments.get("needs")
    if type(price_max_aed) is not int:
        return {"saved": False, "reason": "A price maximum in AED is required."}
    if not isinstance(needs, str):
        needs = ""
    if user_id is None:
        return {"saved": False, "reason": "A visitor id is required."}
    return save_lead(
        user_id=user_id,
        price_max_aed=price_max_aed,
        needs=needs,
        name=name,
    )