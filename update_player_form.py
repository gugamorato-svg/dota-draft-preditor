import csv
from collections import defaultdict, deque
from pathlib import Path

from features import canonical_player_name, load_player_aliases


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OUTPUT_FILE = BASE_DIR / "player_form.csv"
WINDOW = 10


def split(value):
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def load_matches():
    if not PRO_MATCHES_FILE.exists():
        return []
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        return sorted(list(csv.DictReader(file)), key=lambda row: as_float(row.get("start_time"), 0))


def main():
    aliases = load_player_aliases(BASE_DIR)
    history = defaultdict(lambda: deque(maxlen=WINDOW))
    for row in load_matches():
        radiant_win = row.get("radiant_win") == "1"
        for player in split(row.get("radiant_players")):
            history[canonical_player_name(player, aliases).strip().lower()].append(1 if radiant_win else 0)
        for player in split(row.get("dire_players")):
            history[canonical_player_name(player, aliases).strip().lower()].append(0 if radiant_win else 1)

    rows = []
    for player, values in history.items():
        games = len(values)
        wins = sum(values)
        rows.append(
            {
                "player": player,
                "recent_games": games,
                "recent_wins": wins,
                "recent_form": round(wins / games * 100, 2) if games else 50.0,
            }
        )
    rows.sort(key=lambda row: row["recent_form"], reverse=True)

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["player", "recent_games", "recent_wins", "recent_form"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salvei forma recente de {len(rows)} players em {OUTPUT_FILE.name}.")


if __name__ == "__main__":
    main()
