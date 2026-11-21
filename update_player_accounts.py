import csv
import time
from pathlib import Path

import requests

from features import split_players


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
PLAYER_ACCOUNTS_FILE = BASE_DIR / "player_accounts.csv"
OPENDOTA_BASE = "https://api.opendota.com/api"
FIELDNAMES = ["player", "account_id", "source", "notes"]


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDNAMES} for row in rows)


def player_display_name(player):
    return (player.get("name") or player.get("personaname") or "").strip()


def collect_saved_players():
    players = set()
    for row in read_csv(PRO_MATCHES_FILE):
        players.update(split_players(row.get("radiant_players", "")))
        players.update(split_players(row.get("dire_players", "")))
    return {player for player in players if player and not player.startswith("account_")}


def fetch_match_players(match_id):
    from utils import opendota_get_json
    data = opendota_get_json(f"/matches/{match_id}", timeout=30)
    if isinstance(data, dict) and data.get("error") == "rate_limit":
        return []
    return data.get("players", []) if isinstance(data, dict) else []


def build_alias_map():
    saved_players = collect_saved_players()
    wanted = {player.lower(): player for player in saved_players}
    found = {}
    for row in read_csv(PRO_MATCHES_FILE):
        missing = set(wanted) - set(found)
        if not missing:
            break
        match_id = row.get("match_id", "")
        if not match_id:
            continue
        try:
            match_players = fetch_match_players(match_id)
        except requests.RequestException as error:
            print(f"Falha ao buscar match {match_id}: {error}")
            continue
        for player in match_players:
            name = player_display_name(player)
            account_id = player.get("account_id")
            if not name or not account_id:
                continue
            key = name.lower()
            if key in wanted and key not in found:
                found[key] = {
                    "player": wanted[key],
                    "account_id": str(account_id),
                    "source": "opendota match details",
                    "notes": f"found in match {match_id}",
                }
        time.sleep(0.35)
    return found


def update_player_accounts():
    existing_rows = read_csv(PLAYER_ACCOUNTS_FILE)
    rows_by_player = {row.get("player", "").strip().lower(): row for row in existing_rows if row.get("player")}
    discovered = build_alias_map()
    rows_by_player.update(discovered)

    rows = sorted(rows_by_player.values(), key=lambda row: row.get("player", "").lower())
    write_csv(PLAYER_ACCOUNTS_FILE, rows)
    print(f"Players salvos: {len(rows)}.")
    print(f"Novos/encontrados pela OpenDota: {len(discovered)}.")


if __name__ == "__main__":
    update_player_accounts()
