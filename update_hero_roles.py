import csv
from pathlib import Path
import requests


BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "hero_roles.csv"
OPENDOTA_HERO_STATS_URL = "https://api.opendota.com/api/heroStats"

ROLE_MAP = {
    "Carry": "carry",
    "Mid": "mid",
    "Offlane": "offlane",
    "Support": "support",
    "Hard Support": "hard support",
}

ROLE_PRIORITY = ["carry", "mid", "offlane", "support", "hard support"]

MANUAL_OVERRIDES = {
    "Puck": ("mid", ""),
    "Pangolier": ("mid", "offlane"),
    "Zeus": ("mid", ""),
    "Tiny": ("offlane", "mid"),
    "Storm Spirit": ("mid", ""),
    "Windranger": ("mid", "support"),
    "Hoodwink": ("support", "offlane"),
    "Shadow Shaman": ("support", ""),
    "Beastmaster": ("offlane", ""),
    "Tidehunter": ("offlane", ""),
    "Mars": ("offlane", ""),
    "Magnus": ("offlane", "mid"),
    "Luna": ("carry", ""),
    "Phantom Lancer": ("carry", ""),
    "Faceless Void": ("carry", ""),
    "Dazzle": ("hard support", "support"),
    "Io": ("hard support", ""),
    "Treant Protector": ("hard support", "support"),
    "Chen": ("hard support", "support"),
    "Nature's Prophet": ("carry", "offlane"),
}


def normalize_role_list(roles):
    mapped = [ROLE_MAP.get(role, "") for role in roles]
    mapped = [role for role in mapped if role]
    ordered = [role for role in ROLE_PRIORITY if role in mapped]
    return ordered


def infer_roles(row):
    hero = row.get("localized_name", "").strip()
    if hero in MANUAL_OVERRIDES:
        primary, secondary = MANUAL_OVERRIDES[hero]
        return hero, primary, secondary, "manual override"

    roles = normalize_role_list(row.get("roles", []))
    if not roles:
        return hero, "", "", "opendota heroStats"

    primary = roles[0]
    secondary = roles[1] if len(roles) > 1 else ""
    if primary == "mid" and secondary == "carry":
        secondary = ""
    return hero, primary, secondary, "opendota heroStats"


def main():
    from utils import opendota_get_json
    data = opendota_get_json("/heroStats", timeout=30)
    if isinstance(data, dict) and data.get("error") == "rate_limit":
        raise SystemExit("OpenDota rate limit (429). Configure opendota_api_key em config.local.json.")
    rows = []
    for row in data:
        hero, primary, secondary, source = infer_roles(row)
        if hero:
            rows.append(
                {
                    "hero": hero,
                    "primary_role": primary,
                    "secondary_role": secondary,
                    "source": source,
                }
            )

    rows.sort(key=lambda item: item["hero"].lower())
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["hero", "primary_role", "secondary_role", "source"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Atualizei {len(rows)} herois em {OUTPUT_FILE.name}.")


if __name__ == "__main__":
    main()
