"""Baixa synergies (same-team co-pick) de TODOS os herois via Stratz GraphQL.

Stratz tem dados muito mais ricos que nossa amostra de 803 partidas pro:
pares com 800-2000 jogos cada.

Gera `learned_synergies_stratz.csv` com schema compativel ao
`learned_synergies.csv` atual:
    hero_a, hero_b, games, wins, winrate, synergy_score, source

Normalizacao da escala Stratz (range [-2, +10] aprox) -> nossa (-2, +2):
    synergy_score = clamp(stratz_synergy / 4.5, -2.0, 2.0)

Uso:
    python fetch_stratz_synergies.py                # baixa todos
    python fetch_stratz_synergies.py --limit 10     # so 10 herois (teste)
    python fetch_stratz_synergies.py --dry-run      # nao escreve
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from utils import normalize_hero_name

ROOT = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
CONFIG_FILE = ROOT / "config.local.json"
HERO_CACHE = ROOT / "hero_id_map.cache.json"
OUTPUT_FILE = ROOT / "learned_synergies_stratz.csv"
STRATZ_URL = "https://api.stratz.com/graphql"

SYNERGY_SCALE = 4.5  # divisor para normalizar pro range (-2, +2)
SYNERGY_CAP = 2.0
MIN_MATCH_COUNT = 30  # filtro minimo de qualidade
# Filtro de rank: usar DIVINE_IMMORTAL (top ~15% dos players, mais proximo do pro).
# Default 'ALL' inclui Herald ate Immortal misturado e introduz noise.
# Outros valores validos: UNCALIBRATED, HERALD_GUARDIAN, CRUSADER_ARCHON,
# LEGEND_ANCIENT, DIVINE_IMMORTAL, FILTERED, ALL.
BRACKET = "DIVINE_IMMORTAL"

QUERY = """query Hero($heroId: Short!) {
  heroStats {
    heroVsHeroMatchup(heroId: $heroId, bracketBasicIds: """ + BRACKET + """) {
      advantage {
        heroId
        with {
          heroId2
          matchCount
          winCount
          synergy
          winRateHeroId1
          winRateHeroId2
        }
      }
    }
  }
}"""


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))


def load_hero_id_map():
    raw = json.loads(HERO_CACHE.read_text(encoding="utf-8-sig"))
    # raw: {"1": "Anti-Mage", ...}
    return {int(k): v for k, v in raw.items()}


def fetch_hero_synergies(api_key, hero_id, retries=3):
    """Retorna a lista de 'with' (synergies same-team) para um hero."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API",
    }
    for attempt in range(retries):
        try:
            r = requests.post(
                STRATZ_URL,
                json={"query": QUERY, "variables": {"heroId": hero_id}},
                headers=headers,
                timeout=30,
            )
            if r.status_code == 429:
                wait = 2 ** attempt + 2
                print(f"  [429] esperando {wait}s...", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors'][:2]}")
            adv = (((data.get("data") or {}).get("heroStats") or {})
                   .get("heroVsHeroMatchup") or {}).get("advantage", [])
            if not adv:
                return []
            return adv[0].get("with") or []
        except (requests.RequestException, RuntimeError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Maximo de herois a buscar (0 = todos).")
    parser.add_argument("--dry-run", action="store_true", help="Nao escreve arquivo.")
    args = parser.parse_args()

    cfg = load_config()
    api_key = cfg.get("stratz_api_key", "").strip()
    if not api_key:
        raise SystemExit("stratz_api_key vazia em config.local.json")

    hero_map = load_hero_id_map()
    print(f"Total herois: {len(hero_map)}")
    hero_ids = sorted(hero_map.keys())
    if args.limit:
        hero_ids = hero_ids[:args.limit]
        print(f"Limitado a {len(hero_ids)} herois")

    # Dedupe pelos pares (each pair appears 2x: A->B and B->A)
    pair_data = {}  # (id_a, id_b) sorted -> dict
    for i, hero_id in enumerate(hero_ids, start=1):
        name = hero_map.get(hero_id, f"unknown_{hero_id}")
        print(f"[{i}/{len(hero_ids)}] {hero_id:>3} {name}", flush=True)
        try:
            withs = fetch_hero_synergies(api_key, hero_id)
        except Exception as e:
            print(f"  ERRO: {e}", flush=True)
            continue

        for w in withs:
            mc = int(w.get("matchCount", 0))
            if mc < MIN_MATCH_COUNT:
                continue
            id_a = hero_id
            id_b = int(w.get("heroId2", 0))
            if not id_b:
                continue
            key = tuple(sorted([id_a, id_b]))
            existing = pair_data.get(key)
            # Mantem o que tem mais matches (entries duplicadas)
            if existing and existing["games"] >= mc:
                continue
            wins = int(w.get("winCount", 0))
            synergy_raw = float(w.get("synergy") or 0)
            wr = wins / mc * 100 if mc else 0.0
            score = max(-SYNERGY_CAP, min(SYNERGY_CAP, synergy_raw / SYNERGY_SCALE))
            pair_data[key] = {
                "id_a": id_a, "id_b": id_b,
                "hero_a": normalize_hero_name(hero_map.get(id_a, "")),
                "hero_b": normalize_hero_name(hero_map.get(id_b, "")),
                "games": mc,
                "wins": wins,
                "winrate": round(wr, 2),
                "synergy_raw": round(synergy_raw, 3),
                "synergy_score": round(score, 3),
            }
        time.sleep(0.5)  # ser educado com a API

    print(f"\nTotal pares unicos: {len(pair_data)}")
    if args.dry_run:
        print("--dry-run: nao escreve.")
        # Mostra alguns
        sorted_pairs = sorted(pair_data.values(), key=lambda x: -x["games"])[:20]
        for p in sorted_pairs:
            print(f"  {p['hero_a']:<20} + {p['hero_b']:<20} games={p['games']:>5} synergy_raw={p['synergy_raw']:>+6.3f} score={p['synergy_score']:>+5.2f}")
        return

    # Salva CSV
    rows = sorted(pair_data.values(), key=lambda x: (-x["synergy_score"], -x["games"]))
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["hero_a", "hero_b", "games", "wins", "winrate",
                           "synergy_score", "synergy_raw", "source"]
        )
        writer.writeheader()
        for p in rows:
            writer.writerow({**{k: p[k] for k in
                               ["hero_a", "hero_b", "games", "wins", "winrate",
                                "synergy_score", "synergy_raw"]},
                             "source": "Stratz heroVsHeroMatchup.with"})

    print(f"Salvei {len(rows)} pares em {OUTPUT_FILE}")
    print(f"Cobertura: nossa base atual = 1976 pares, Stratz = {len(rows)} pares")


if __name__ == "__main__":
    main()
