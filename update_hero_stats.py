import csv
from pathlib import Path
import requests
from features import normalize_hero_name


BASE_DIR = Path(__file__).parent
HERO_STATS_FILE = BASE_DIR / "hero_stats.csv"
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OPENDOTA_HERO_STATS_URL = "https://api.opendota.com/api/heroStats"


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def split_heroes(value):
    return [normalize_hero_name(hero) for hero in (value or "").split("|") if hero]


def load_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def fetch_opendota_hero_stats():
    from utils import opendota_get_json
    data = opendota_get_json("/heroStats", timeout=30)
    if isinstance(data, dict) and data.get("error") == "rate_limit":
        raise SystemExit("OpenDota rate limit (429). Configure opendota_api_key em config.local.json.")
    stats_by_hero = {}
    for row in data:
        hero = normalize_hero_name(row.get("localized_name", ""))
        if not hero:
            continue

        public_picks = 0
        public_wins = 0
        for bracket in range(1, 9):
            picks = int(row.get(f"{bracket}_pick") or 0)
            wins = int(row.get(f"{bracket}_win") or 0)
            public_picks += picks
            public_wins += wins

        immortal_picks = int(row.get("8_pick") or 0)
        immortal_wins = int(row.get("8_win") or 0)
        high_mmr_source = "OpenDota rank 8"
        if not immortal_picks:
            immortal_picks = int(row.get("7_pick") or 0)
            immortal_wins = int(row.get("7_win") or 0)
            high_mmr_source = "OpenDota rank 7 fallback"
        stats_by_hero[hero] = {
            "patch_wr": round(public_wins / public_picks * 100, 2) if public_picks else None,
            "immortal_wr": round(immortal_wins / immortal_picks * 100, 2) if immortal_picks else None,
            "public_picks": public_picks,
            "immortal_picks": immortal_picks,
            "high_mmr_source": high_mmr_source,
        }
    return stats_by_hero


def main():
    hero_rows = load_csv(HERO_STATS_FILE)
    matches = load_csv(PRO_MATCHES_FILE)
    if not hero_rows:
        raise SystemExit("hero_stats.csv esta vazio ou nao existe.")
    if not matches:
        raise SystemExit("pro_matches.csv esta vazio ou nao existe.")

    totals = {
        normalize_hero_name(row["hero"]): {"picks": 0, "bans": 0, "wins": 0}
        for row in hero_rows
        if row.get("hero")
    }
    existing_heroes = {normalize_hero_name(row.get("hero", "")) for row in hero_rows}

    total_matches = 0
    for match in matches:
        radiant_picks = split_heroes(match.get("radiant_picks"))
        dire_picks = split_heroes(match.get("dire_picks"))
        radiant_bans = split_heroes(match.get("radiant_bans"))
        dire_bans = split_heroes(match.get("dire_bans"))
        if len(radiant_picks) < 5 or len(dire_picks) < 5:
            continue

        total_matches += 1
        radiant_win = int(as_float(match.get("radiant_win"), 0)) == 1

        for hero in radiant_picks:
            totals.setdefault(hero, {"picks": 0, "bans": 0, "wins": 0})
            totals[hero]["picks"] += 1
            totals[hero]["wins"] += 1 if radiant_win else 0
        for hero in dire_picks:
            totals.setdefault(hero, {"picks": 0, "bans": 0, "wins": 0})
            totals[hero]["picks"] += 1
            totals[hero]["wins"] += 0 if radiant_win else 1
        for hero in radiant_bans + dire_bans:
            totals.setdefault(hero, {"picks": 0, "bans": 0, "wins": 0})
            totals[hero]["bans"] += 1

    try:
        opendota_stats = fetch_opendota_hero_stats()
        print(f"OpenDota heroStats carregado para {len(opendota_stats)} herois.")
    except requests.RequestException as error:
        opendota_stats = {}
        print(f"Aviso: nao consegui buscar OpenDota heroStats: {error}")

    updated_rows = []
    for row in hero_rows:
        hero = normalize_hero_name(row.get("hero", ""))
        row["hero"] = hero
        stats = totals.get(hero, {"picks": 0, "bans": 0, "wins": 0})
        public_stats = opendota_stats.get(hero, {})
        if public_stats.get("patch_wr") is not None:
            row["patch_wr"] = public_stats["patch_wr"]
        if public_stats.get("immortal_wr") is not None:
            row["immortal_wr"] = public_stats["immortal_wr"]
        picks = stats["picks"]
        bans = stats["bans"]
        row["pro_pickrate"] = round(picks / total_matches * 100, 2) if total_matches else row.get("pro_pickrate", 0)
        row["pro_banrate"] = round(bans / total_matches * 100, 2) if total_matches else row.get("pro_banrate", 0)
        row["pro_winrate"] = round(stats["wins"] / picks * 100, 2) if picks else 50.0
        updated_rows.append(row)

    for hero in sorted(set(totals) - existing_heroes):
        stats = totals[hero]
        picks = stats["picks"]
        updated_rows.append(
            {
                "hero": hero,
                "patch_wr": 50.0,
                "immortal_wr": 50.0,
                "pro_pickrate": round(picks / total_matches * 100, 2) if total_matches else 0,
                "pro_banrate": round(stats["bans"] / total_matches * 100, 2) if total_matches else 0,
                "pro_winrate": round(stats["wins"] / picks * 100, 2) if picks else 50.0,
                "comfort": 0.0,
            }
        )

    with HERO_STATS_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "hero",
                "patch_wr",
                "immortal_wr",
                "pro_pickrate",
                "pro_banrate",
                "pro_winrate",
                "comfort",
            ],
        )
        writer.writeheader()
        writer.writerows(updated_rows)

    print(
        f"Atualizei {len(updated_rows)} herois usando {total_matches} partidas profissionais "
        "e meta publica/Immortal da OpenDota quando disponivel."
    )


if __name__ == "__main__":
    main()
