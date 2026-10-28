ORIGINS = {
    "japanese": (
        "toyota",
        "lexus",
        "honda",
        "nissan",
        "infiniti",
        "mazda",
        "mitsubishi",
        "suzuki",
        "subaru",
    ),
    "korean": ("hyundai", "kia", "genesis"),
    "german": ("mercedes-benz", "bmw", "audi", "volkswagen", "porsche"),
    "american": ("chevrolet", "ford", "dodge", "jeep", "lincoln", "hummer"),
    "british": (
        "land rover",
        "jaguar",
        "bentley",
        "rolls-royce",
        "mini",
        "aston martin",
        "mclaren",
    ),
    "italian": ("ferrari", "lamborghini", "maserati"),
}


def makes_for(origin: str | None, make: str | None) -> list[str] | None:
    """None means no make filter. An empty list cannot match any listing."""
    group: list[str] | None = None
    if isinstance(origin, str) and origin.strip():
        known = ORIGINS.get(origin.strip().lower())
        if known is None:
            raise ValueError(f"Unknown origin {origin.strip().lower()}")
        group = list(known)
    if isinstance(make, str) and make.strip():
        one = make.strip().lower()
        if group is None:
            return [one]
        return [one] if one in group else []
    return group
