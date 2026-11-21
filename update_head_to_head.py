import csv
from collections import defaultdict, deque
from pathlib import Path

from features import team_key


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OUTPUT_FILE = BASE_DIR / "head_to_head.csv"
WINDOW = 8


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
    display_names = {}
    history = defaultdict(lambda: deque(maxlen=WINDOW))

    for row in load_matches():
        radiant_team = row.get("radiant_team", "Radiant")
        dire_team = row.get("dire_team", "Dire")
        radiant_key = team_key(radiant_team)
        dire_key = team_key(dire_team)
        if not radiant_key or not dire_key or radiant_key == dire_key:
            continue

        display_names.setdefault(radiant_key, radiant_team)
        display_names.setdefault(dire_key, dire_team)
        radiant_win = row.get("radiant_win") == "1"
        pair = tuple(sorted((radiant_key, dire_key)))
        history[pair].append((radiant_key, 1 if radiant_win else 0))

    rows = []
    for (team_a, team_b), values in history.items():
        games = len(values)
        wins_a = sum(1 for winner_key, won in values if (winner_key == team_a and won) or (winner_key != team_a and not won))
        rows.append(
            {
                "team_a": display_names.get(team_a, team_a),
                "team_b": display_names.get(team_b, team_b),
                "games": games,
                "team_a_wins": wins_a,
                "team_b_wins": games - wins_a,
                "team_a_winrate": round(wins_a / games * 100, 2) if games else 50.0,
            }
        )

    rows.sort(key=lambda row: (row["team_a"], row["team_b"]))
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["team_a", "team_b", "games", "team_a_wins", "team_b_wins", "team_a_winrate"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salvei head-to-head de {len(rows)} pares em {OUTPUT_FILE.name}.")


if __name__ == "__main__":
    main()
