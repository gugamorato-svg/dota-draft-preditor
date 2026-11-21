"""Baixa counters (own_hero vs enemy_hero) de TODOS os herois via Stratz GraphQL.

Stratz `heroVsHeroMatchup.advantage[0].vs` filtrado por DIVINE_IMMORTAL.

Diferenca pra synergies:
- Counter NAO eh simetrico: (own=Io, enemy=SF) != (own=SF, enemy=Io)
- Cada hero gera N entries vs outros heros, salvas TODAS (sem dedup como sorted)

Saida: `hero_counters_stratz.csv` com schema compativel ao `hero_matchups.csv`:
    own_hero, enemy_hero, games_played, wins, winrate, counter_score, source

Uso:
    python fetch_stratz_counters.py
    python fetch_stratz_counters.py --limit 10 --dry-run
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
OUTPUT_FILE = ROOT / "hero_counters_stratz.csv"
STRATZ_URL = "https://api.stratz.com/graphql"

COUNTER_SCALE = 4.5  # mesma escala usada pra synergy (consistente)
COUNTER_CAP = 2.5    # mesma escala do hero_matchups.csv atual
MIN_MATCH_COUNT = 30
BRACKET = "DIVINE_IMMORTAL"

QUERY = """query Hero($heroId: Short!) {
  heroStats {
    heroVsHeroMatchup(heroId: $heroId, bracketBasicIds: """ + BRACKET + """) {
      advantage {
        heroId
        vs {
          heroId2
          matchCount
          winCount
          synergy
        }
      }
    }
  }
}"""


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))


def load_hero_id_map():
    raw = json.loads(HERO_CACHE.read_text(encoding="utf-8-sig"))
    return {int(k): v for k, v in raw.items()}


def fetch_hero_counters(api_key, hero_id, retries=3):
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
                headers=headers, timeout=30,
            )
            if r.status_code == 429:
                wait = 2 ** attempt + 2
                print(f"  [429] esperando {wait}s...", flush=True)
                time.sleep(wait); continue
            r.raise_for_status()
            data = r.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors'][:2]}")
            adv = (((data.get("data") or {}).get("heroStats") or {})
                   .get("heroVsHeroMatchup") or {}).get("advantage", [])
            if not adv:
                return []
            return adv[0].get("vs") or []
        except (requests.RequestException, RuntimeError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt); continue
            raise
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    api_key = cfg.get("stratz_api_key", "").strip()
    if not api_key:
        raise SystemExit("stratz_api_key vazia em config.local.json")

    hero_map = load_hero_id_map()
    hero_ids = sorted(hero_map.keys())
    if args.limit:
        hero_ids = hero_ids[:args.limit]

    rows = []
    for i, hero_id in enumerate(hero_ids, start=1):
        name = hero_map.get(hero_id, f"hero_{hero_id}")
        print(f"[{i}/{len(hero_ids)}] {hero_id:>3} {name}", flush=True)
        try:
            vs = fetch_hero_counters(api_key, hero_id)
        except Exception as e:
            print(f"  ERRO: {e}", flush=True)
            continue

        for v in vs:
            mc = int(v.get("matchCount", 0))
            if mc < MIN_MATCH_COUNT:
                continue
            enemy_id = int(v.get("heroId2", 0))
            if not enemy_id:
                continue
            wins = int(v.get("winCount", 0))
            synergy_raw = float(v.get("synergy") or 0)
            wr = wins / mc * 100 if mc else 0.0
            score = max(-COUNTER_CAP, min(COUNTER_CAP, synergy_raw / COUNTER_SCALE))
            rows.append({
                "own_hero": normalize_hero_name(hero_map.get(hero_id, "")),
                "enemy_hero": normalize_hero_name(hero_map.get(enemy_id, "")),
                "games_played": mc,
                "wins": wins,
                "winrate": round(wr, 2),
                "counter_score": round(score, 3),
                "counter_raw": round(synergy_raw, 3),
                "source": f"Stratz heroVsHeroMatchup.vs (bracket={BRACKET})",
            })
        time.sleep(0.5)

    print(f"\nTotal entries: {len(rows)}")
    if args.dry_run:
        for r in sorted(rows, key=lambda x: -x["counter_score"])[:10]:
            print(f"  {r['own_hero']:<18} vs {r['enemy_hero']:<18} games={r['games_played']:>5} wr={r['winrate']:>5.1f}% score={r['counter_score']:+.2f}")
        return

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as f:
        fields = ["own_hero", "enemy_hero", "games_played", "wins", "winrate",
                  "counter_score", "counter_raw", "source"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(rows, key=lambda x: -x["counter_score"]):
            w.writerow(r)
    print(f"Salvei em {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
