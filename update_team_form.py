import csv
from collections import defaultdict, deque
from pathlib import Path


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OUTPUT_FILE = BASE_DIR / "team_form.csv"


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def main():
    if not PRO_MATCHES_FILE.exists():
        raise SystemExit("pro_matches.csv nao encontrado.")

    history = defaultdict(lambda: deque(maxlen=10))
    matches = []
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        matches = sorted(list(csv.DictReader(file)), key=lambda row: as_float(row.get("start_time"), 0))

    for row in matches:
        radiant = row.get("radiant_team", "").strip() or "Radiant"
        dire = row.get("dire_team", "").strip() or "Dire"
        radiant_win = int(as_float(row.get("radiant_win"), 0)) == 1
        history[radiant].append(1 if radiant_win else 0)
        history[dire].append(0 if radiant_win else 1)

    rows = []
    for team, values in history.items():
        games = len(values)
        wins = sum(values)
        rows.append(
            {
                "team": team,
                "recent_games": games,
                "recent_wins": wins,
                "recent_form": round(wins / games * 100, 2) if games else 50.0,
            }
        )

    rows.sort(key=lambda row: row["recent_form"], reverse=True)
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["team", "recent_games", "recent_wins", "recent_form"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salvei forma recente de {len(rows)} times em {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
