import argparse
import csv
import time
from collections import defaultdict
from pathlib import Path

import requests

from features import normalize_hero_name


BASE_DIR = Path(__file__).parent
PLAYER_ACCOUNTS_FILE = BASE_DIR / "player_accounts.csv"
OUTPUT_FILE = BASE_DIR / "player_hero_external.csv"
OPENDOTA_BASE = "https://api.opendota.com/api"
FIELDNAMES = ["player", "hero", "games", "winrate", "comfort", "source", "url"]


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def safe_text(value):
    return str(value).encode("ascii", "replace").decode("ascii")


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows, fieldnames):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)


def load_hero_names():
    from utils import opendota_get_json
    data = opendota_get_json("/constants/heroes", timeout=30)
    if isinstance(data, dict) and data.get("error") == "rate_limit":
        raise SystemExit("OpenDota rate limit (429). Configure opendota_api_key em config.local.json.")
    return {
        int(hero_id): normalize_hero_name(d.get("localized_name", ""))
        for hero_id, d in data.items()
    }


def player_won(row):
    is_radiant = int(row.get("player_slot") or 0) < 128
    radiant_win = bool(row.get("radiant_win"))
    return radiant_win if is_radiant else not radiant_win


def comfort_from_winrate(winrate, games):
    if games < 5:
        return 0.0
    return round(max(min((winrate - 50) / 10, 1.5), -1.5), 3)


def fetch_player_matches(account_id, days, limit, retries=3):
    from utils import opendota_get_json
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            data = opendota_get_json(
                f"/players/{account_id}/matches",
                params={"date": days, "limit": limit},
                timeout=45, max_retries=1,
            )
            if isinstance(data, dict) and data.get("error") == "rate_limit":
                last_error = RuntimeError("OpenDota rate limit (429). Configure opendota_api_key.")
                print(f"Falha {attempt}/{retries} em {account_id}: rate_limit")
                time.sleep(1.5 * attempt)
                continue
            return data
        except requests.RequestException as error:
            last_error = error
            print(f"Falha {attempt}/{retries} em {account_id}: {error}")
            time.sleep(1.5 * attempt)
    raise last_error


def rows_for_player(player, account_id, hero_names, days, limit, min_games):
    stats = defaultdict(lambda: {"games": 0, "wins": 0})
    for row in fetch_player_matches(account_id, days, limit):
        hero = hero_names.get(int(row.get("hero_id") or 0), "")
        if not hero:
            continue
        stats[hero]["games"] += 1
        stats[hero]["wins"] += 1 if player_won(row) else 0

    rows = []
    for hero, value in stats.items():
        games = value["games"]
        if games < min_games:
            continue
        winrate = value["wins"] / games * 100 if games else 50.0
        rows.append(
            {
                "player": player,
                "hero": hero,
                "games": games,
                "winrate": round(winrate, 2),
                "comfort": comfort_from_winrate(winrate, games),
                "source": f"opendota-{days}d",
                "url": f"https://www.opendota.com/players/{account_id}",
            }
        )
    return rows


def merge_rows(existing_rows, new_rows):
    merged = {}
    for row in existing_rows:
        key = (row.get("player", "").strip().lower(), normalize_hero_name(row.get("hero", "")))
        if key[0] and key[1]:
            merged[key] = {field: row.get(field, "") for field in FIELDNAMES}

    for row in new_rows:
        key = (row["player"].strip().lower(), normalize_hero_name(row["hero"]))
        current = merged.get(key)
        if current is None or as_float(row.get("games")) >= as_float(current.get("games")):
            merged[key] = {field: row.get(field, "") for field in FIELDNAMES}

    return sorted(
        merged.values(),
        key=lambda row: (row.get("player", "").lower(), -as_float(row.get("games")), row.get("hero", "")),
    )


def update_from_accounts(days, limit, min_games, only_player=""):
    accounts = read_csv(PLAYER_ACCOUNTS_FILE)
    if only_player:
        accounts = [row for row in accounts if row.get("player", "").strip().lower() == only_player.strip().lower()]
    if not accounts:
        raise SystemExit("Nenhum player encontrado em player_accounts.csv.")

    hero_names = load_hero_names()
    new_rows = []
    for account in accounts:
        player = account.get("player", "").strip()
        account_id = str(account.get("account_id", "")).strip()
        if not player or not account_id:
            continue
        print(f"Buscando {safe_text(player)} ({account_id}) ultimos {days} dias...")
        try:
            new_rows.extend(rows_for_player(player, account_id, hero_names, days, limit, min_games))
        except requests.RequestException as error:
            print(f"Ignorando {safe_text(player)}: {error}")
        time.sleep(0.25)

    merged = merge_rows(read_csv(OUTPUT_FILE), new_rows)
    write_csv(OUTPUT_FILE, merged, FIELDNAMES)
    print(f"Salvei {len(new_rows)} linhas novas/atualizadas em {OUTPUT_FILE.name}.")


def main():
    parser = argparse.ArgumentParser(description="Atualiza comfort player/heroi dos ultimos N dias via OpenDota.")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--min-games", type=int, default=3)
    parser.add_argument("--player", default="", help="Atualiza apenas um player do player_accounts.csv.")
    args = parser.parse_args()
    update_from_accounts(args.days, args.limit, args.min_games, args.player)


if __name__ == "__main__":
    main()
