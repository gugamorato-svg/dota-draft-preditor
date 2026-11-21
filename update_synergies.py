"""Atualiza learned_synergies.csv.

Pipeline:
1. Computa synergies LOCAL a partir de pro_matches.csv (Bayesian smoothing).
2. Carrega learned_synergies_stratz.csv se existir (gerado por fetch_stratz_synergies.py).
3. Faz merge: LOCAL sobrescreve onde houver (mais confiavel pra pro scene),
   Stratz preenche o vazio (4x cobertura, inclui pares classicos que faltam no pro local).

Sem Stratz: comportamento original (so local). Com Stratz: cobertura ~4x maior.
"""
import csv
import argparse
import subprocess
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from features import normalize_hero_name, split_heroes


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OUTPUT_FILE = BASE_DIR / "learned_synergies.csv"
STRATZ_FILE = BASE_DIR / "learned_synergies_stratz.csv"

SMOOTHING_PRIOR = 16  # matches sinteticos a 50% wr para Bayesian smoothing.
                       # tunado 03/06/26: era 20, reduzido pra 16 pra preservar
                       # mais sinal em sinergias de volume medio (40-300 jogos).
                       # Limiares: 40 jogos = 71% do raw, 85 = 83%, 300 = 95%, 1500 = 99%.
MIN_GAMES = 3  # min jogos pra par entrar no learned (corta ruido extremo)


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def compute_local_synergies(pro_matches_file):
    """Computa synergies a partir de pro_matches.csv (Bayesian smoothing).
    Retorna dict (hero_a, hero_b) sorted -> {games, wins, winrate, synergy_score, ...}.
    """
    pair_stats = defaultdict(lambda: {"games": 0, "wins": 0})
    with pro_matches_file.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            radiant_picks = sorted(set(split_heroes(row.get("radiant_picks", ""))))
            dire_picks = sorted(set(split_heroes(row.get("dire_picks", ""))))
            if len(radiant_picks) < 5 or len(dire_picks) < 5:
                continue
            radiant_win = int(as_float(row.get("radiant_win"), 0)) == 1
            for pair in combinations(radiant_picks, 2):
                pair_stats[pair]["games"] += 1
                pair_stats[pair]["wins"] += 1 if radiant_win else 0
            for pair in combinations(dire_picks, 2):
                pair_stats[pair]["games"] += 1
                pair_stats[pair]["wins"] += 0 if radiant_win else 1

    out = {}
    for (hero_a, hero_b), stats in pair_stats.items():
        games = stats["games"]
        if games < MIN_GAMES:
            continue
        wins = stats["wins"]
        winrate = wins / games * 100
        smoothed = (wins - 0.5 * games) / (games + SMOOTHING_PRIOR) * 10
        key = tuple(sorted([normalize_hero_name(hero_a), normalize_hero_name(hero_b)]))
        out[key] = {
            "hero_a": key[0],
            "hero_b": key[1],
            "games": games,
            "wins": wins,
            "winrate": round(winrate, 2),
            "synergy_score": round(max(min(smoothed, 2.0), -2.0), 3),
            "source": "OpenDota pro_matches local",
            "method": "same-team co-pick winrate + Bayesian smoothing",
        }
    return out


def load_stratz_synergies(path):
    """Carrega learned_synergies_stratz.csv (formato gerado por fetch_stratz_synergies.py).
    Retorna dict (hero_a, hero_b) sorted -> row dict.
    """
    if not path.exists():
        return {}
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            a = normalize_hero_name(row.get("hero_a", ""))
            b = normalize_hero_name(row.get("hero_b", ""))
            if not a or not b:
                continue
            key = tuple(sorted([a, b]))
            try:
                games = int(row.get("games") or 0)
                wins = int(row.get("wins") or 0)
                wr = float(row.get("winrate") or 0)
                score = float(row.get("synergy_score") or 0)
            except (TypeError, ValueError):
                continue
            out[key] = {
                "hero_a": key[0],
                "hero_b": key[1],
                "games": games,
                "wins": wins,
                "winrate": wr,
                "synergy_score": score,
                "source": row.get("source", "Stratz heroVsHeroMatchup.with"),
                "method": "Stratz aggregated synergy / scaled to [-2, 2]",
            }
    return out


def main():
    parser = argparse.ArgumentParser(description="Atualiza sinergias hero+hero (local pro + Stratz merge).")
    parser.add_argument("--fetch-opendota", action="store_true",
                        help="Baixa mais partidas da OpenDota antes de recalcular.")
    parser.add_argument("--limit", type=int, default=160,
                        help="Limite de partidas ao usar --fetch-opendota.")
    parser.add_argument("--no-stratz", action="store_true",
                        help="Ignora learned_synergies_stratz.csv (volta ao comportamento antigo, so local).")
    parser.add_argument("--fetch-stratz", action="store_true",
                        help="Roda fetch_stratz_synergies.py antes do merge (atualiza dados Stratz).")
    args = parser.parse_args()

    if args.fetch_opendota:
        subprocess.run(
            [sys.executable, str(BASE_DIR / "fetch_pro_matches.py"), "--limit", str(args.limit)],
            cwd=BASE_DIR,
            check=True,
        )

    if args.fetch_stratz:
        print("Atualizando dados Stratz...", flush=True)
        subprocess.run(
            [sys.executable, str(BASE_DIR / "fetch_stratz_synergies.py")],
            cwd=BASE_DIR,
            check=True,
        )

    if not PRO_MATCHES_FILE.exists():
        raise SystemExit("pro_matches.csv nao encontrado.")

    # 1. Computa synergies LOCAL (Bayesian smoothing em pro_matches.csv)
    local = compute_local_synergies(PRO_MATCHES_FILE)
    print(f"Sinergias LOCAL (pro_matches.csv):  {len(local)} pares.")

    # 2. Carrega Stratz
    stratz = {} if args.no_stratz else load_stratz_synergies(STRATZ_FILE)
    if not args.no_stratz:
        if stratz:
            print(f"Sinergias STRATZ (cache):           {len(stratz)} pares.")
        else:
            print("Sinergias STRATZ:                   AUSENTE — rode fetch_stratz_synergies.py")

    # 3. Merge: LOCAL sobrescreve onde houver (pro-scene confiavel),
    #          Stratz preenche o vazio (cobertura maior pra pares classicos).
    merged = dict(stratz)
    merged.update(local)
    n_only_local = len(local) - len(local.keys() & stratz.keys()) if stratz else len(local)
    n_only_stratz = len(stratz) - len(local.keys() & stratz.keys()) if stratz else 0
    n_overlap = len(local.keys() & stratz.keys()) if stratz else 0
    print(f"Merge: {len(merged)} pares totais  "
          f"(local-only {n_only_local}, stratz-only {n_only_stratz}, overlap {n_overlap} usa local)")

    # 4. Salva no learned_synergies.csv (formato original mantido)
    rows = sorted(merged.values(), key=lambda r: (r["synergy_score"], r["games"]), reverse=True)
    fieldnames = ["hero_a", "hero_b", "games", "wins", "winrate", "synergy_score", "source", "method"]
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})

    print(f"Salvei {len(rows)} sinergias em {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
