from dubizzle_car_assistant.inventory import load_listings

listings = load_listings()
text = (listings["title"] + "\n" + listings["description"]).str.lower()

missing_price = listings["price_aed"].isna()
missing_mileage = listings["mileage_km"].isna()
has_aed = text.str.contains("aed")
has_km = text.str.contains(r"kms?\b", regex=True)
has_mileage_word = text.str.contains(r"mileage|odometer", regex=True)
has_big_number = text.str.contains(r"\d{4,7}|\d{1,3}(?:,\d{3})+", regex=True)

print("listings:", len(listings))
print("missing price:", int(missing_price.sum()))
print("  still contain 'aed':", int((missing_price & has_aed).sum()))
print("  contain a large number:", int((missing_price & has_big_number).sum()))
print(
    "  contain neither 'aed' nor a large number:",
    int((missing_price & ~has_aed & ~has_big_number).sum()),
)
print("missing mileage:", int(missing_mileage.sum()))
print("  still contain km:", int((missing_mileage & has_km).sum()))
print(
    "  contain 'mileage' or 'odometer':",
    int((missing_mileage & has_mileage_word).sum()),
)
print(
    "  contain neither km nor those words:",
    int((missing_mileage & ~has_km & ~has_mileage_word).sum()),
)