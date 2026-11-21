"""Atualiza hero_matchups.csv usando STRATZ (patch atual + alto MMR).

Diferencas chave vs update_matchups.py (OpenDota):
- Fonte: STRATZ heroVsHeroMatchup (sample 10x maior que /heroes/X/matchups)
- Filtro: bracketBasicIds=DIVINE_IMMORTAL (alto MMR, mais proxy de pro)
- Patch: STRATZ recalcula automaticamente conforme novo patch entra
- Smoothing: counter_score com Bayesian prior (puxa low-sample p/ zero)

Uso:
    python update_matchups_stratz.py                  # bracket Divine+Immortal
    python update_matchups_stratz.py --bracket ALL    # todos os brackets
    python update_matchups_stratz.py --prior 50       # smoothing prior (default 30)
"""
import argparse
import csv
import time
from pathlib import Path

import requests

from fetch_stratz_live import load_config, STRATZ_GRAPHQL


BASE_DIR = Path(__file__).parent
HERO_STATS_FILE = BASE_DIR / "hero_stats.csv"
HERO_MATCHUPS_FILE = BASE_DIR / "hero_matchups.csv"

HERO_ALIASES = {
    "Outworld Destroyer": "Outworld Devourer",
    "Ringmaster": "Ring Master",
}


def read_hero_stats():
    with HERO_STATS_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        return [row["hero"] for row in csv.DictReader(file) if row.get("hero")]


def fetch_stratz_heroes(api_key):
    """Mapeia nome → heroId via constants do STRATZ."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    q = "{ constants { heroes { id displayName } } }"
    r = requests.post(STRATZ_GRAPHQL, json={"query": q}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    by_name = {}
    by_id = {}
    for h in (data.get("data") or {}).get("constants", {}).get("heroes") or []:
        name = h["displayName"]
        by_name[name] = int(h["id"])
        by_id[int(h["id"])] = name
    return by_name, by_id


def fetch_hero_matchups(api_key, hero_id, bracket_ids, match_limit, max_retries=4):
    """Pega matchups (vs + winsAverage + matchCount) de um heroi com retry exponencial."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    q = f"""
    {{
      heroStats {{
        heroVsHeroMatchup(heroId: {hero_id}, matchLimit: {match_limit}) {{
          advantage {{
            heroId
            vs {{
              heroId2
              matchCount
              winsAverage
            }}
          }}
        }}
      }}
    }}
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.post(STRATZ_GRAPHQL, json={"query": q}, headers=headers, timeout=60)
            if r.status_code == 429:
                # rate limit, espera mais
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            if "errors" in data:
                raise RuntimeError(data["errors"])
            adv = (data.get("data") or {}).get("heroStats", {}).get("heroVsHeroMatchup", {}).get("advantage", [])
            rows = []
            for group in adv:
                for v in (group.get("vs") or []):
                    rows.append({
                        "enemy_hero_id": int(v["heroId2"]),
                        "games_played": int(v["matchCount"]),
                        "winrate": float(v["winsAverage"]) * 100,
                    })
            return rows
        except (requests.RequestException, ConnectionResetError) as e:
            last_error = e
            wait = 2 ** attempt
            time.sleep(wait)
    raise RuntimeError(f"Falhou apos {max_retries} retries: {last_error}")


def smoothed_counter_score(games, winrate_pct, prior_games=30):
    """Bayesian smoothing: puxa matchups com pouca amostra pra 0.

    score = (wins - 0.5*games) / (games + prior) * 5
    Range: ~[-2.5, +2.5]. Clamp pra esse range pra evitar outliers.
    """
    if games <= 0:
        return 0.0
    wins = winrate_pct / 100.0 * games
    raw = (wins - 0.5 * games) / (games + prior_games)
    score = raw * 5  # ~[-2.5, 2.5] em magnitude
    return round(max(-2.5, min(2.5, score)), 3)


def main():
    parser = argparse.ArgumentParser(description="Atualiza hero_matchups.csv via STRATZ (patch-aware).")
    parser.add_argument("--bracket", default="DIVINE_IMMORTAL",
                        help="Bracket(s) separados por virgula. Default: DIVINE_IMMORTAL.")
    parser.add_argument("--prior", type=int, default=30,
                        help="Prior de Bayesian smoothing (matches sinteticos). Default 30.")
    parser.add_argument("--match-limit", type=int, default=100,
                        help="STRATZ matchLimit = threshold MINIMO de games por par. Default 100.")
    parser.add_argument("--min-games", type=int, default=20,
                        help="Descarta pares com menos de N games. Default 20.")
    parser.add_argument("--sleep-ms", type=int, default=500,
                        help="Pausa entre queries (rate limit free 150/min). Default 500ms.")
    args = parser.parse_args()

    api_key = load_config().get("stratz_api_key", "").strip()
    if not api_key:
        raise SystemExit("stratz_api_key nao configurada em config.local.json")

    print("Carregando lista de herois STRATZ...")
    name_to_id, id_to_name = fetch_stratz_heroes(api_key)
    local_heroes = read_hero_stats()
    print(f"Local heroes: {len(local_heroes)}, STRATZ heroes: {len(name_to_id)}")

    bracket_ids = [b.strip() for b in args.bracket.split(",") if b.strip()]

    all_rows = []
    failed = 0
    for index, hero in enumerate(local_heroes, start=1):
        stratz_name = HERO_ALIASES.get(hero, hero)
        hero_id = name_to_id.get(stratz_name)
        if not hero_id:
            print(f"  [{index}/{len(local_heroes)}] {hero}: sem id na STRATZ. Skip.")
            failed += 1
            continue

        try:
            matchups = fetch_hero_matchups(api_key, hero_id, bracket_ids, args.match_limit)
        except (requests.RequestException, RuntimeError) as e:
            print(f"  [{index}/{len(local_heroes)}] {hero}: ERRO {str(e)[:100]}")
            failed += 1
            time.sleep(args.sleep_ms / 1000 * 2)
            continue

        kept = 0
        for m in matchups:
            enemy = id_to_name.get(m["enemy_hero_id"])
            if not enemy or m["games_played"] < args.min_games:
                continue
            score = smoothed_counter_score(m["games_played"], m["winrate"], args.prior)
            all_rows.append({
                "own_hero": hero,
                "enemy_hero": enemy,
                "games_played": m["games_played"],
                "wins": int(m["games_played"] * m["winrate"] / 100),
                "winrate": round(m["winrate"], 2),
                "counter_score": score,
            })
            kept += 1
        if index % 10 == 0 or index == len(local_heroes):
            print(f"  [{index}/{len(local_heroes)}] {hero}: {kept} matchups validos. Total acumulado: {len(all_rows)}")

        time.sleep(args.sleep_ms / 1000)

    print(f"\nSalvando {len(all_rows)} matchups (descartados {failed} herois)...")
    with HERO_MATCHUPS_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["own_hero", "enemy_hero", "games_played", "wins", "winrate", "counter_score"])
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Salvo em {HERO_MATCHUPS_FILE.name}.")
    print(f"Source: STRATZ heroVsHeroMatchup, bracket={args.bracket}, prior={args.prior}.")


if __name__ == "__main__":
    main()
