from pathlib import Path

import pandas as pd

workbook_path = Path("data/cars.xlsx")
workbook = pd.ExcelFile(workbook_path)

print("sheets:", workbook.sheet_names)

frames = {}
for sheet_name in workbook.sheet_names:
    frame = pd.read_excel(workbook, sheet_name=sheet_name)
    frames[sheet_name] = frame
    empty_rows = frame.isna().all(axis=1).sum()
    print(f"\n== {sheet_name} ==")
    print("rows:", len(frame))
    print("entirely empty rows:", int(empty_rows))
    print("columns:", list(frame.columns))
    print("missing values:")
    print(frame.isna().sum().to_string())
    print("first 3 titles:")
    print(frame["title"].head(3).to_string())


def listing_key(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["make"].astype(str).str.strip().str.lower()
        + "|"
        + frame["model"].astype(str).str.strip().str.lower()
        + "|"
        + frame["year"].astype(str)
    )


raw = frames["raw dataset"]
cleaned = frames["cleaned dataset"]
shared = set(listing_key(raw)) & set(listing_key(cleaned))
print("\nshared make|model|year keys:", len(shared))

text = (
    cleaned["title"].fillna("") + "\n" + cleaned["description"].fillna("")
).str.lower()
print("cleaned rows mentioning 'aed':", int(text.str.contains("aed").sum()))
print("cleaned rows mentioning 'km':", int(text.str.contains("km").sum()))
print("cleaned rows mentioning 'mileage':", int(text.str.contains("mileage").sum()))