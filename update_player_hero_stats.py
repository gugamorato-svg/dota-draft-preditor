import csv
from collections import defaultdict
from pathlib import Path

from features import canonical_player_name, load_player_aliases, split_heroes, split_players


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OUTPUT_FILE = BASE_DIR / "player_hero_stats.csv"
TEAM_OUTPUT_FILE = BASE_DIR / "player_hero_team_stats.csv"
FIELDNAMES = ["player", "hero", "games", "winrate", "comfort", "notes"]
TEAM_FIELDNAMES = ["team", "player", "hero", "games", "winrate", "comfort", "notes"]


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def main():
    if not PRO_MATCHES_FILE.exists():
        raise SystemExit("pro_matches.csv nao encontrado.")

    aliases = load_player_aliases(BASE_DIR)
    stats = defaultdict(lambda: {"games": 0, "wins": 0})
    team_stats = defaultdict(lambda: {"games": 0, "wins": 0})
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        has_players = {"radiant_players", "dire_players"}.issubset(set(reader.fieldnames or []))
        if not has_players:
            print("pro_matches.csv ainda nao tem colunas radiant_players/dire_players.")
            print("Mantive player_hero_stats.csv como esta. Para popular comfort real, colete partidas com nomes dos players.")
            return

        for row in reader:
            radiant_picks = split_heroes(row.get("radiant_picks", ""))
            dire_picks = split_heroes(row.get("dire_picks", ""))
            radiant_players = split_players(row.get("radiant_players", ""))
            dire_players = split_players(row.get("dire_players", ""))
            radiant_win = int(as_float(row.get("radiant_win"), 0)) == 1

            for index, hero in enumerate(radiant_picks):
                if index < len(radiant_players):
                    player = canonical_player_name(radiant_players[index], aliases)
                    key = (player, hero)
                    team_key = (row.get("radiant_team", "Radiant"), player, hero)
                    stats[key]["games"] += 1
                    stats[key]["wins"] += 1 if radiant_win else 0
                    team_stats[team_key]["games"] += 1
                    team_stats[team_key]["wins"] += 1 if radiant_win else 0
            for index, hero in enumerate(dire_picks):
                if index < len(dire_players):
                    player = canonical_player_name(dire_players[index], aliases)
                    key = (player, hero)
                    team_key = (row.get("dire_team", "Dire"), player, hero)
                    stats[key]["games"] += 1
                    stats[key]["wins"] += 0 if radiant_win else 1
                    team_stats[team_key]["games"] += 1
                    team_stats[team_key]["wins"] += 0 if radiant_win else 1

    rows = []
    for (player, hero), value in stats.items():
        games = value["games"]
        winrate = value["wins"] / games * 100 if games else 50
        rows.append(
            {
                "player": player,
                "hero": hero,
                "games": games,
                "winrate": round(winrate, 2),
                "comfort": round(max(min((winrate - 50) / 10, 1.5), -1.5), 3),
                "notes": "gerado de pro_matches.csv",
            }
        )

    rows.sort(key=lambda row: (row["comfort"], row["games"]), reverse=True)
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    team_rows = []
    for (team, player, hero), value in team_stats.items():
        games = value["games"]
        winrate = value["wins"] / games * 100 if games else 50
        team_rows.append(
            {
                "team": team,
                "player": player,
                "hero": hero,
                "games": games,
                "winrate": round(winrate, 2),
                "comfort": round(max(min((winrate - 50) / 10, 1.5), -1.5), 3),
                "notes": "gerado de pro_matches.csv por time atual",
            }
        )

    team_rows.sort(key=lambda row: (row["team"], row["player"], row["comfort"], row["games"]), reverse=True)
    with TEAM_OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=TEAM_FIELDNAMES)
        writer.writeheader()
        writer.writerows(team_rows)

    print(f"Salvei {len(rows)} linhas em {OUTPUT_FILE}.")
    print(f"Salvei {len(team_rows)} linhas em {TEAM_OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
