"""O‘zbekistonda keng tarqalgan mashina marka/model ro‘yxati (tanlash uchun, lotin yozuv).

E'lon va qidiruv bir xil katalogni ishlatadi — moslashuv barqaror bo‘ladi."""

from __future__ import annotations

# Tartib: klaviaturada sahifalar uchun barqaror.
# "Boshqa" — foydalanuvchi marka/modelni o‘zi yozadi.
BRAND_MODELS: dict[str, list[str]] = {
    "Chevrolet": [
        "Matiz",
        "Cobalt",
        "Nexia",
        "Spark",
        "Damas",
        "Labo",
        "Gentra",
        "Lacetti",
        "Malibu",
        "Cruze",
        "Tracker",
        "Captiva",
    ],
    "Ravon": ["R2", "R4", "Gentra", "Nexia R3", "Damas"],
    "Daewoo": ["Nexia", "Matiz", "Lanos", "Gentra"],
    "Lada": ["Granta", "Vesta", "Priora", "2114", "Largus", "Niva", "Kalina"],
    "Kia": ["Rio", "Cerato", "Ceed", "Sportage", "Sorento", "Soul", "Optima", "K5"],
    "Hyundai": ["Accent", "Solaris", "Elantra", "Sonata", "Tucson", "Santa Fe", "Creta"],
    "Toyota": ["Camry", "Corolla", "RAV4", "Land Cruiser", "Prado", "Highlander", "Yaris"],
    "Nissan": ["Juke", "Qashqai", "X-Trail", "Patrol", "Sentra", "Altima"],
    "Volkswagen": ["Polo", "Jetta", "Passat", "Tiguan", "Golf"],
    "Mercedes-Benz": ["E-Class", "C-Class", "GLE", "GLC", "Sprinter"],
    "BMW": ["3 Series", "5 Series", "X3", "X5", "X7"],
    "BYD": ["Song Plus", "Atto 3", "Han"],
    "Chery": ["Tiggo 4", "Tiggo 7", "Tiggo 8"],
    "Geely": ["Coolray", "Atlas", "Monjaro"],
    "Haval": ["H6", "Jolion", "F7"],
    "Renault": ["Logan", "Sandero", "Duster", "Kaptur"],
    "Skoda": ["Octavia", "Rapid", "Superb"],
    "Mazda": ["3", "6", "CX-5", "CX-9"],
    "Boshqa": [],
}

# Marka + bazaviy model tanlangach ko‘rinadigan aniq variantlar (masalan Nexia 1/2/3).
MODEL_VARIANTS: dict[str, dict[str, list[str]]] = {
    "Chevrolet": {"Nexia": ["Nexia 1", "Nexia 2", "Nexia 3"]},
    "Daewoo": {"Nexia": ["Nexia 1", "Nexia 2", "Nexia 3"]},
}

BRAND_ORDER: tuple[str, ...] = tuple(BRAND_MODELS.keys())

# Klaviatura balandligini kamaytirish: 3 ustun, har sahifada cheklangan tugmalar.
BRANDS_PER_PAGE = 9
MODELS_PER_PAGE = 12
GRID_COLS = 3


def subvariants_for(brand: str, base_model: str) -> list[str] | None:
    """Agar bazaviy model uchun aniq podturlar bo‘lsa ro‘yxat, aks holda None."""
    subs = MODEL_VARIANTS.get(brand, {}).get(base_model)
    return subs if subs else None
