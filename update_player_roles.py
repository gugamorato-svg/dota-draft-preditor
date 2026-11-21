import csv
from collections import defaultdict
from pathlib import Path


BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "player_roles.csv"
SOURCE_FILES = [
    BASE_DIR / "player_hero_external.csv",
    BASE_DIR / "player_hero_stats.csv",
    BASE_DIR / "player_hero_team_stats.csv",
]
FIELDNAMES = ["player", "role", "notes"]

HERO_ROLE_WEIGHTS = {
    "Abaddon": {"hard support": 0.8, "offlane": 0.6},
    "Alchemist": {"carry": 1.0, "mid": 0.6},
    "Ancient Apparition": {"hard support": 1.2},
    "Anti-Mage": {"carry": 1.2},
    "Arc Warden": {"carry": 1.0, "mid": 0.8},
    "Axe": {"offlane": 1.2},
    "Bane": {"hard support": 1.1, "support": 0.5},
    "Batrider": {"offlane": 0.8, "support": 0.7, "mid": 0.5},
    "Beastmaster": {"offlane": 1.2},
    "Bounty Hunter": {"support": 1.0},
    "Bristleback": {"offlane": 0.9, "carry": 0.5},
    "Broodmother": {"offlane": 0.8, "mid": 0.8},
    "Centaur Warrunner": {"offlane": 1.2},
    "Chen": {"hard support": 1.2},
    "Clinkz": {"carry": 0.9, "mid": 0.5},
    "Clockwerk": {"support": 1.1, "hard support": 0.7, "offlane": 0.3},
    "Crystal Maiden": {"hard support": 1.2},
    "Dark Seer": {"offlane": 1.2},
    "Dark Willow": {"support": 1.0, "hard support": 0.6},
    "Dawnbreaker": {"offlane": 0.9, "carry": 0.5, "support": 0.4},
    "Dazzle": {"hard support": 0.9, "support": 0.7},
    "Death Prophet": {"mid": 0.8, "offlane": 0.7},
    "Disruptor": {"hard support": 1.2},
    "Doom": {"offlane": 1.0, "carry": 0.4},
    "Dragon Knight": {"mid": 0.8, "offlane": 0.6, "carry": 0.4},
    "Earth Spirit": {"support": 1.2},
    "Earthshaker": {"support": 0.9, "offlane": 0.6},
    "Elder Titan": {"hard support": 0.9, "support": 0.6, "offlane": 0.4},
    "Ember Spirit": {"mid": 1.2},
    "Enchantress": {"hard support": 0.9, "support": 0.7},
    "Enigma": {"offlane": 0.9, "support": 0.5},
    "Faceless Void": {"carry": 1.2},
    "Grimstroke": {"hard support": 0.9, "support": 0.7},
    "Gyrocopter": {"carry": 0.9, "hard support": 0.4},
    "Hoodwink": {"support": 1.1, "hard support": 0.5},
    "Huskar": {"mid": 1.0, "carry": 0.6},
    "Invoker": {"mid": 1.2},
    "Io": {"hard support": 1.2},
    "Jakiro": {"hard support": 1.0, "support": 0.5},
    "Juggernaut": {"carry": 1.2},
    "Keeper of the Light": {"support": 0.8, "hard support": 0.8},
    "Kez": {"carry": 0.9, "mid": 0.7},
    "Kunkka": {"mid": 0.8, "offlane": 0.6},
    "Legion Commander": {"offlane": 1.2},
    "Leshrac": {"mid": 0.9, "carry": 0.5},
    "Lich": {"hard support": 1.2},
    "Lifestealer": {"carry": 1.2},
    "Lina": {"mid": 0.8, "carry": 0.7},
    "Lion": {"support": 0.8, "hard support": 0.8},
    "Lone Druid": {"carry": 0.8, "mid": 0.6, "offlane": 0.4},
    "Luna": {"carry": 1.2},
    "Lycan": {"offlane": 0.8, "carry": 0.6},
    "Magnus": {"offlane": 0.9, "mid": 0.5},
    "Mars": {"offlane": 1.2},
    "Medusa": {"carry": 1.2},
    "Meepo": {"mid": 1.0, "carry": 0.7},
    "Mirana": {"support": 0.9, "hard support": 0.5},
    "Monkey King": {"carry": 0.8, "mid": 0.6, "support": 0.3},
    "Morphling": {"carry": 1.1, "mid": 0.5},
    "Muerta": {"carry": 0.9, "mid": 0.5, "support": 0.3},
    "Naga Siren": {"carry": 1.0, "support": 0.4},
    "Nature's Prophet": {"offlane": 0.7, "carry": 0.6, "support": 0.5},
    "Necrophos": {"offlane": 0.7, "mid": 0.6},
    "Night Stalker": {"offlane": 1.1},
    "Nyx Assassin": {"support": 1.1},
    "Ogre Magi": {"hard support": 0.8, "support": 0.7},
    "Omniknight": {"hard support": 0.8, "offlane": 0.5},
    "Oracle": {"hard support": 1.2},
    "Outworld Destroyer": {"mid": 1.1},
    "Pangolier": {"offlane": 0.8, "mid": 0.7},
    "Phantom Assassin": {"carry": 1.2},
    "Phantom Lancer": {"carry": 1.2},
    "Phoenix": {"hard support": 0.8, "support": 0.7, "offlane": 0.4},
    "Primal Beast": {"offlane": 0.8, "mid": 0.7},
    "Puck": {"mid": 1.2},
    "Pudge": {"support": 0.6, "offlane": 0.5},
    "Pugna": {"support": 0.7, "mid": 0.6},
    "Queen of Pain": {"mid": 1.2},
    "Razor": {"carry": 0.7, "offlane": 0.6, "mid": 0.6},
    "Riki": {"support": 0.6, "carry": 0.6},
    "Ringmaster": {"support": 0.9, "hard support": 0.7},
    "Rubick": {"support": 1.2},
    "Sand King": {"offlane": 1.0, "support": 0.4},
    "Shadow Demon": {"hard support": 0.9, "support": 0.7},
    "Shadow Fiend": {"mid": 0.9, "carry": 0.7},
    "Shadow Shaman": {"support": 0.8, "hard support": 0.8},
    "Silencer": {"hard support": 0.8, "support": 0.7},
    "Skywrath Mage": {"support": 1.0},
    "Slardar": {"offlane": 1.0, "support": 0.4},
    "Slark": {"carry": 1.2},
    "Snapfire": {"support": 0.9, "hard support": 0.5, "mid": 0.3},
    "Sniper": {"carry": 0.8, "mid": 0.7},
    "Spectre": {"carry": 1.2},
    "Spirit Breaker": {"support": 0.8, "offlane": 0.5},
    "Storm Spirit": {"mid": 1.2},
    "Sven": {"carry": 1.2},
    "Techies": {"support": 1.1, "hard support": 0.5},
    "Templar Assassin": {"mid": 0.8, "carry": 0.7},
    "Terrorblade": {"carry": 1.2},
    "Tidehunter": {"offlane": 1.2},
    "Timbersaw": {"offlane": 0.9, "mid": 0.5},
    "Tiny": {"carry": 0.7, "mid": 0.6, "support": 0.5},
    "Treant Protector": {"hard support": 1.0, "support": 0.5},
    "Troll Warlord": {"carry": 1.2},
    "Tusk": {"support": 1.0, "offlane": 0.4},
    "Underlord": {"offlane": 1.2},
    "Ursa": {"carry": 1.2},
    "Vengeful Spirit": {"support": 0.8, "hard support": 0.7},
    "Venomancer": {"support": 0.7, "offlane": 0.6},
    "Viper": {"mid": 0.7, "offlane": 0.7, "carry": 0.4},
    "Visage": {"offlane": 0.7, "mid": 0.6},
    "Void Spirit": {"mid": 1.0, "offlane": 0.4},
    "Warlock": {"hard support": 1.2},
    "Weaver": {"carry": 0.8, "support": 0.5},
    "Windranger": {"mid": 0.7, "carry": 0.6, "support": 0.5},
    "Winter Wyvern": {"hard support": 0.9, "support": 0.6},
    "Witch Doctor": {"hard support": 1.2},
    "Zeus": {"mid": 0.9, "support": 0.5},
}

ROLE_ORDER = ["carry", "mid", "offlane", "support", "hard support"]


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def manual_rows():
    rows = []
    for row in read_csv(OUTPUT_FILE):
        notes = row.get("notes", "").lower()
        if "manual" in notes:
            rows.append({field: row.get(field, "") for field in FIELDNAMES})
    return rows


def infer_roles():
    scores = defaultdict(lambda: defaultdict(float))
    samples = defaultdict(float)

    for source_file in SOURCE_FILES:
        source_weight = 1.0
        if source_file.name == "player_hero_stats.csv":
            source_weight = 1.4
        elif source_file.name == "player_hero_team_stats.csv":
            source_weight = 1.8

        for row in read_csv(source_file):
            player = row.get("player", "").strip()
            hero = row.get("hero", "").strip()
            games = as_float(row.get("games"), 0.0)
            if not player or not hero or games <= 0:
                continue

            weights = HERO_ROLE_WEIGHTS.get(hero)
            if not weights:
                continue

            capped_games = min(games, 30)
            for role, role_weight in weights.items():
                scores[player][role] += capped_games * role_weight * source_weight
            samples[player] += capped_games * source_weight

    inferred = []
    for player, role_scores in scores.items():
        if not role_scores:
            continue
        best_role = max(ROLE_ORDER, key=lambda role: role_scores.get(role, 0.0))
        best_score = role_scores.get(best_role, 0.0)
        total_score = sum(role_scores.values())
        confidence = best_score / total_score if total_score else 0.0
        if samples[player] < 3 or confidence < 0.28:
            continue
        inferred.append(
            {
                "player": player,
                "role": best_role,
                "notes": f"inferido por historico de herois; jogos ponderados {samples[player]:.1f}; confianca {confidence:.2f}",
            }
        )
    return inferred


def main():
    manual = manual_rows()
    manual_players = {row["player"].strip().lower() for row in manual}
    inferred = [
        row for row in infer_roles()
        if row["player"].strip().lower() not in manual_players
    ]
    rows = sorted(manual + inferred, key=lambda row: row["player"].lower())

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salvei {len(rows)} roles em {OUTPUT_FILE}.")
    print(f"Manuais preservados: {len(manual)}. Inferidos: {len(inferred)}.")


if __name__ == "__main__":
    main()
