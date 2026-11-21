"""Atualiza hero_matchups.csv (counter scores hero vs hero).

ESTRATEGIA ATUAL (25/05/2026): Local pro + OpenDota fallback.

Justificativa:
- Local pro only deu o melhor test acc (90.7%) no experimento.
- Mas nossa amostra pro eh pequena (803 partidas) — muitos pares NAO existem ainda.
- Solucao: usar local quando houver, OpenDota all-ranks como fallback pra cobertura.
- Trade-off: 88.2% test acc (vs 90.7% local-only) em troca de cobertura ~3x maior.
- Quando pro_matches.csv crescer pra ~2000+, vale revisitar --source local-only.

Pipeline:
1. Compute counters LOCAL de pro_matches.csv (Bayesian smoothing, prior=30).
2. Carrega cache OpenDota de hero_matchups_opendota_cache.csv (se existir).
3. Merge: LOCAL sobrescreve onde houver, OpenDota preenche o vazio.

Uso:
    python update_matchups.py                        # default: local + OpenDota fallback
    python update_matchups.py --source local-only    # 90.7% test acc, cobertura menor
    python update_matchups.py --source opendota-only # all-ranks puro (volta ao antigo)
    python update_matchups.py --refresh-opendota     # rebaixa OpenDota cache (3 min)
"""
import argparse
import csv
import time
from collections import defaultdict
from pathlib import Path

import requests

from features import normalize_hero_name, split_heroes


BASE_DIR = Path(__file__).parent
HERO_STATS_FILE = BASE_DIR / "hero_stats.csv"
HERO_MATCHUPS_FILE = BASE_DIR / "hero_matchups.csv"
OPENDOTA_CACHE_FILE = BASE_DIR / "hero_matchups_opendota_cache.csv"
STRATZ_COUNTERS_FILE = BASE_DIR / "hero_counters_stratz.csv"
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
OPENDOTA_BASE = "https://api.opendota.com/api"

# Sample size minimo pra considerar local pro_matches confiavel.
# Abaixo disso (mas >= MIN_GAMES_LOCAL), preferimos Stratz se tiver mais jogos.
# Sem isso, counters com 5-6 jogos pro produzem ruido e levam o modelo a errar
# favoritos (caso Doom vs Razor: local 6 games +1.11, Stratz 147 games -0.43).
LOCAL_HIGH_CONFIDENCE = 30
STRATZ_MIN_GAMES = 20

HERO_ALIASES = {
    "Outworld Destroyer": "Outworld Devourer",
    "Ringmaster": "Ring Master",
}

SMOOTHING_PRIOR = 30
COUNTER_CAP = 2.5
MIN_GAMES_LOCAL = 3


def smoothed_counter_score(wins, games, prior=SMOOTHING_PRIOR, cap=COUNTER_CAP):
    if games <= 0:
        return 0.0
    raw = (wins - 0.5 * games) / (games + prior) * 20
    return round(max(min(raw, cap), -cap), 3)


def compute_local_counters():
    """Counters do pro_matches.csv com Bayesian smoothing."""
    if not PRO_MATCHES_FILE.exists():
        raise SystemExit("pro_matches.csv nao encontrado.")

    pair_stats = defaultdict(lambda: {"games": 0, "wins": 0})
    n = 0
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rad = split_heroes(row.get("radiant_picks", ""))
            dir_ = split_heroes(row.get("dire_picks", ""))
            if len(rad) != 5 or len(dir_) != 5:
                continue
            rw = row.get("radiant_win") == "1"
            n += 1
            for own in rad:
                for enemy in dir_:
                    k = (normalize_hero_name(own), normalize_hero_name(enemy))
                    pair_stats[k]["games"] += 1
                    if rw: pair_stats[k]["wins"] += 1
            for own in dir_:
                for enemy in rad:
                    k = (normalize_hero_name(own), normalize_hero_name(enemy))
                    pair_stats[k]["games"] += 1
                    if not rw: pair_stats[k]["wins"] += 1

    out = {}
    for (own, enemy), s in pair_stats.items():
        if s["games"] < MIN_GAMES_LOCAL:
            continue
        out[(own, enemy)] = {
            "own_hero": own, "enemy_hero": enemy,
            "games_played": s["games"], "wins": s["wins"],
            "winrate": round(s["wins"] / s["games"] * 100, 2),
            "counter_score": smoothed_counter_score(s["wins"], s["games"]),
            "source": "local pro_matches.csv",
        }
    return out, n


def load_opendota_cache():
    """Carrega cache OpenDota all-ranks (gerado por --refresh-opendota)."""
    if not OPENDOTA_CACHE_FILE.exists():
        return {}
    out = {}
    with OPENDOTA_CACHE_FILE.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            own = normalize_hero_name(r.get("own_hero", ""))
            enemy = normalize_hero_name(r.get("enemy_hero", ""))
            try:
                games = int(r.get("games_played", 0) or 0)
                wins = int(r.get("wins", 0) or 0)
                wr = float(r.get("winrate", 0) or 0)
                score = float(r.get("counter_score", 0) or 0)
            except (TypeError, ValueError):
                continue
            if own and enemy:
                out[(own, enemy)] = {
                    "own_hero": own, "enemy_hero": enemy,
                    "games_played": games, "wins": wins, "winrate": wr,
                    "counter_score": score,
                    "source": "OpenDota /heroes/X/matchups (all-ranks)",
                }
    return out


def refresh_opendota_cache():
    """Baixa todos counters da OpenDota e salva no cache."""
    if not HERO_STATS_FILE.exists():
        raise SystemExit("hero_stats.csv nao encontrado.")
    with HERO_STATS_FILE.open("r", encoding="utf-8-sig") as f:
        heroes = [r["hero"] for r in csv.DictReader(f) if r.get("hero")]

    from utils import opendota_get_json
    heroes_const = opendota_get_json("/constants/heroes", timeout=30)
    if isinstance(heroes_const, dict) and heroes_const.get("error") == "rate_limit":
        raise SystemExit("OpenDota rate limit (429). Configure opendota_api_key em config.local.json.")
    by_name = {data["localized_name"]: int(hid) for hid, data in heroes_const.items()}
    by_id = {int(hid): data["localized_name"] for hid, data in heroes_const.items()}

    rows = []
    for i, hero in enumerate(heroes, 1):
        on = HERO_ALIASES.get(hero, hero)
        hid = by_name.get(on)
        if not hid:
            continue
        print(f"  [{i}/{len(heroes)}] {hero}", flush=True)
        matchups = opendota_get_json(f"/heroes/{hid}/matchups", timeout=30,
                                     max_retries=5, retry_wait=10)
        if isinstance(matchups, dict) and matchups.get("error") == "rate_limit":
            print(f"    [429] desistindo de {hero} (rate limit persistente)")
            continue
        for item in matchups:
            enemy = by_id.get(int(item.get("hero_id", 0)))
            games = int(item.get("games_played", 0))
            wins = int(item.get("wins", 0))
            if not enemy or games < 20:
                continue
            rows.append({
                "own_hero": hero, "enemy_hero": enemy,
                "games_played": games, "wins": wins,
                "winrate": round(wins/games*100, 2),
                "counter_score": smoothed_counter_score(wins, games),
            })
        time.sleep(1.1)

    rows.sort(key=lambda r: -r["counter_score"])
    fields = ["own_hero", "enemy_hero", "games_played", "wins", "winrate", "counter_score"]
    with OPENDOTA_CACHE_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f"Cache OpenDota atualizado: {len(rows)} entries em {OPENDOTA_CACHE_FILE}")
    return rows


def load_stratz_counters():
    """Carrega hero_counters_stratz.csv (gerado por fetch_stratz_counters.py).
    Dados de bracket DIVINE_IMMORTAL — sample grande (100-300 jogos por par tipico).
    """
    if not STRATZ_COUNTERS_FILE.exists():
        return {}
    out = {}
    with STRATZ_COUNTERS_FILE.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            own = normalize_hero_name(r.get("own_hero", ""))
            enemy = normalize_hero_name(r.get("enemy_hero", ""))
            if not own or not enemy:
                continue
            out[(own, enemy)] = {
                "own_hero": own, "enemy_hero": enemy,
                "games_played": int(r.get("games_played", 0)),
                "wins": int(r.get("wins", 0)),
                "winrate": float(r.get("winrate", 0)),
                "counter_score": float(r.get("counter_score", 0)),
            }
    return out


def smart_merge(local, stratz, opendota):
    """Prioridade:
      1. Local com sample alto (>= LOCAL_HIGH_CONFIDENCE 30 jogos) — meta pro mais recente
      2. Stratz com sample minimo (>= STRATZ_MIN_GAMES 20 jogos) — DIVINE+ data, meta atual
      3. Local com sample baixo (3-29) — algum sinal mas vulneravel a ruido
      4. OpenDota — all-ranks, mais antigo

    Resolve o problema de counters como Doom vs Razor onde local tinha 6 jogos
    (+1.11 favorecendo Doom artificialmente) vs Stratz 147 jogos (Razor counter Doom
    no meta atual).
    """
    merged = {}
    stats = {"local_high": 0, "stratz_mid": 0, "local_low": 0, "opendota": 0}
    all_keys = set(local) | set(stratz) | set(opendota)
    for k in all_keys:
        l = local.get(k)
        s = stratz.get(k)
        o = opendota.get(k)
        l_games = l["games_played"] if l else 0
        s_games = s["games_played"] if s else 0

        if l and l_games >= LOCAL_HIGH_CONFIDENCE:
            merged[k] = l; stats["local_high"] += 1
        elif s and s_games >= STRATZ_MIN_GAMES:
            merged[k] = s; stats["stratz_mid"] += 1
        elif l:
            merged[k] = l; stats["local_low"] += 1
        elif o:
            merged[k] = o; stats["opendota"] += 1
        elif s:
            # Stratz com < STRATZ_MIN_GAMES — usa como ultimo recurso
            merged[k] = s; stats["stratz_mid"] += 1
    return merged, stats


def write_output(table, source_label):
    """Salva table no formato compativel com hero_matchups.csv."""
    fields = ["own_hero", "enemy_hero", "games_played", "wins", "winrate", "counter_score"]
    rows = sorted(table.values(), key=lambda r: -r["counter_score"])
    with HERO_MATCHUPS_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"Salvei {len(rows)} linhas em {HERO_MATCHUPS_FILE}.")
    print(f"Fonte: {source_label}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default="merged",
        choices=["merged", "local-only", "opendota-only"],
        help="merged (default, recomendado): local + OpenDota fallback. "
             "local-only: so pro_matches.csv (test acc 90.7pp mas menos cobertura). "
             "opendota-only: cache OpenDota all-ranks puro.",
    )
    parser.add_argument("--refresh-opendota", action="store_true",
                        help="Re-baixa cache OpenDota antes (3-5 min).")
    parser.add_argument("--use-stratz", action="store_true",
                        help="EXPERIMENTAL: usa Stratz como fonte primaria. Testado em "
                             "27/05 e PIOROU acuracia (Stratz = bracket DIVINE pub, "
                             "nao reflete coordenacao pro).")
    args = parser.parse_args()

    if args.refresh_opendota:
        print("Atualizando cache OpenDota...")
        refresh_opendota_cache()

    if args.source == "opendota-only":
        cache = load_opendota_cache()
        if not cache:
            raise SystemExit("Cache OpenDota vazio. Rode --refresh-opendota primeiro.")
        write_output(cache, f"OpenDota all-ranks cache ({len(cache)} entries)")
        return

    print("Computando counters LOCAL de pro_matches.csv...")
    local, n_matches = compute_local_counters()
    print(f"  {n_matches} partidas pro, {len(local)} pares com >= {MIN_GAMES_LOCAL} jogos")

    if args.source == "local-only":
        write_output(local, f"local pro_matches.csv ({n_matches} matches)")
        return

    # merged (DEFAULT): Local pro + OpenDota fallback (Stratz NAO usado como source
    # primario — testado em 27/05 e PIORO acuracia de 72.2% pra 55.6% no backtest
    # 26/05. Motivo: Stratz eh DIVINE_IMMORTAL bracket = meta pub, nao reflete
    # coordenacao pro. Stratz fica disponivel via --use-stratz pra experimentos.
    cache = load_opendota_cache()
    if not cache:
        print("WARN: cache OpenDota nao encontrado, salvando so local. "
              "Rode --refresh-opendota pra ativar fallback.")
        write_output(local, "local pro_matches.csv (sem OpenDota cache)")
        return

    if args.use_stratz:
        stratz = load_stratz_counters()
        merged, stats = smart_merge(local, stratz, cache)
        print(f"Smart merge (--use-stratz): {len(merged)} entries totais")
        print(f"  local high (>= {LOCAL_HIGH_CONFIDENCE}): {stats['local_high']}  "
              f"stratz (>= {STRATZ_MIN_GAMES}): {stats['stratz_mid']}  "
              f"local low: {stats['local_low']}  opendota: {stats['opendota']}")
        write_output(merged, f"smart merge: {n_matches} pro + Stratz + OpenDota fallback")
        return

    # Default merge: Local + OpenDota com THRESHOLD de confianca.
    # Local so sobrescreve OpenDota quando tem amostra confiavel (>= LOCAL_HIGH_CONFIDENCE
    # 30 jogos). Local de amostra pequena (<30j) NAO sobrescreve o alto volume da OpenDota
    # — eram ruido/inversoes (ex.: Axe vs Lifestealer 27j local invertia 243j OpenDota;
    # ~55% dos pares estavam assim). Neutro pro modelo (smoothing por amostra ja descontava)
    # mas corrige a VISUALIZACAO dos counters no app. Pares so-local (sem OpenDota) entram
    # mesmo com amostra baixa (melhor que nada).
    merged = dict(cache)
    local_used = local_low_kept_od = 0
    for k, v in local.items():
        if v["games_played"] >= LOCAL_HIGH_CONFIDENCE or k not in cache:
            merged[k] = v; local_used += 1
        else:
            local_low_kept_od += 1  # mantem OpenDota (mais volume)
    only_cache = len(cache) - len(local.keys() & cache.keys())
    print(f"Merge (threshold local>={LOCAL_HIGH_CONFIDENCE}j): {len(merged)} entries  "
          f"(local usado {local_used}, local-baixo->mantem OpenDota {local_low_kept_od}, "
          f"opendota-only {only_cache})")
    write_output(merged, f"merged threshold: {n_matches} pro + OpenDota fallback")


if __name__ == "__main__":
    main()
