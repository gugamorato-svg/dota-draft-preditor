"""Coleta LANE-OUTCOME (quem ganhou a lane) de matches parseados e agrega numa
matriz hero-vs-hero por lane -> hero_lane_matchups.csv.

Diferente do hero_matchups.csv (counter = quem ganha o JOGO), esta matriz mede
quem ganha a LANE aos ~10min (gold@10). Sinal ORTOGONAL ao counter — captura
"perdeu as lanes apesar do draft" que o counter nao ve. Base pra REVIVER o
lane_matchup (hoje morto por re-misturar counter/meta/comfort).

Fonte: OpenDota /matches/{id} (parseado) -> players[].lane_role + gold_t[10].
Lane grouping: lane_role 1=safe, 2=mid, 3=off (4=jungle ignorado).
Confronto de lane: radiant safe vs dire off, mid vs mid, radiant off vs dire safe.
Lane vencida = maior soma de gold@10. Registra os 2 sentidos (A venceu B / B perdeu A).

RESUMIVEL: pula match_ids em lane_processed.txt. Rate-limited.

Uso:
    python fetch_lane_outcomes.py --limit 100      # 100 matches novos
    python fetch_lane_outcomes.py --limit 100 --prior 10
"""
import argparse, csv, time
from collections import defaultdict
from pathlib import Path

from utils import opendota_get_json, normalize_hero_name

ROOT = Path(__file__).resolve().parent
PM = ROOT / "pro_matches.csv"
OUT = ROOT / "hero_lane_matchups.csv"
BY_MATCH = ROOT / "lane_outcomes_by_match.csv"  # log por partida (walk-forward futuro)
PLAYER_LANES = ROOT / "player_lanes_by_match.csv"  # lane REAL jogada por player/match
PROCESSED = ROOT / "lane_processed.txt"
HERO_ID_MAP = ROOT / "hero_id_map.csv"
GOLD_MINUTE = 10


def load_hero_map():
    m = {}
    with HERO_ID_MAP.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                m[int(r["hero_id"])] = normalize_hero_name(r["hero"])
            except (ValueError, KeyError):
                pass
    return m


def rebuild_agg_from_by_match():
    """Reconstrói (own,enemy) -> [games, wins] REPLAYANDO lane_outcomes_by_match.csv.

    Idempotente: re-processar um match já registrado não duplica contagem (o
    load antigo somava por cima do CSV agregado — dupla contagem em re-runs).
    Retorna (agg, match_ids que já têm lane outcome).
    """
    agg = defaultdict(lambda: [0, 0])
    have = set()
    if BY_MATCH.exists():
        with BY_MATCH.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                have.add(r["match_id"])
                rw = r.get("rad_won") == "1"
                rh = [h for h in (r.get("rad_heroes") or "").split("|") if h]
                dh = [h for h in (r.get("dire_heroes") or "").split("|") if h]
                for oh in rh:
                    for eh in dh:
                        agg[(oh, eh)][0] += 1; agg[(oh, eh)][1] += 1 if rw else 0
                        agg[(eh, oh)][0] += 1; agg[(eh, oh)][1] += 1 if not rw else 0
    return agg, have


def load_player_lanes_done():
    if not PLAYER_LANES.exists():
        return set()
    with PLAYER_LANES.open(encoding="utf-8-sig") as f:
        return {r["match_id"] for r in csv.DictReader(f)}


def load_processed():
    if PROCESSED.exists():
        return set(PROCESSED.read_text(encoding="utf-8").split())
    return set()


def gold_at(player, minute):
    gt = player.get("gold_t") or []
    if not gt:
        return None
    return gt[minute] if len(gt) > minute else gt[-1]


def extract_player_lanes(match, hero_map):
    """Extrai (account_id, name, hero, lane_role, is_radiant) — lane REAL jogada.
    Base pra derivar role por player via moda (melhor que wiki: dado observado)."""
    rows = []
    mid = match.get("match_id", "")
    ts = match.get("start_time", "")
    for p in match.get("players") or []:
        lr = p.get("lane_role")
        aid = p.get("account_id")
        hero = hero_map.get(p.get("hero_id"))
        if lr not in (1, 2, 3, 4) or not aid or not hero:
            continue
        name = (p.get("name") or p.get("personaname") or "").strip()
        is_rad = p.get("isRadiant") if "isRadiant" in p else (p.get("player_slot", 0) < 128)
        rows.append([mid, ts, aid, name, hero, lr, int(bool(is_rad))])
    if rows:
        new_file = not PLAYER_LANES.exists()
        with PLAYER_LANES.open("a", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["match_id", "start_time", "account_id", "name", "hero", "lane_role", "is_radiant"])
            w.writerows(rows)
    return bool(rows)


def process_match(match, hero_map, agg):
    """Extrai lane outcomes de um match parseado. Retorna True se usavel."""
    players = match.get("players") or []
    lanes = {0: {1: [], 2: [], 3: []}, 1: {1: [], 2: [], 3: []}}  # team -> lane_role -> [(hero, gold10)]
    any_gold = False
    for p in players:
        lr = p.get("lane_role")
        if lr not in (1, 2, 3):
            continue
        hero = hero_map.get(p.get("hero_id"))
        if not hero:
            continue
        g10 = gold_at(p, GOLD_MINUTE)
        if g10 is None:
            continue
        any_gold = True
        team = 0 if (p.get("isRadiant") if "isRadiant" in p else (p.get("player_slot", 0) < 128)) else 1
        lanes[team][lr].append((hero, g10))
    if not any_gold:
        return False

    # confrontos: (rad_lane, dire_lane) — safe(1) vs off(3), mid vs mid, off(3) vs safe(1)
    per_match = []  # (lane_label, rad_won, rad_heroes, dire_heroes) — auditoria/walk-forward
    for rad_lane, dire_lane in [(1, 3), (2, 2), (3, 1)]:
        rad = lanes[0][rad_lane]
        dire = lanes[1][dire_lane]
        if not rad or not dire:
            continue
        rad_gold = sum(g for _, g in rad)
        dire_gold = sum(g for _, g in dire)
        if rad_gold == dire_gold:
            continue
        rad_won = rad_gold > dire_gold
        per_match.append((f"{rad_lane}v{dire_lane}", rad_won,
                          "|".join(h for h, _ in rad), "|".join(h for h, _ in dire)))
        for oh, _ in rad:
            for eh, _ in dire:
                agg[(oh, eh)][0] += 1; agg[(oh, eh)][1] += 1 if rad_won else 0
                agg[(eh, oh)][0] += 1; agg[(eh, oh)][1] += 1 if not rad_won else 0
    # Log POR PARTIDA (append) — habilita walk-forward temporal SEM re-fetch no
    # futuro (a matriz agregada perde o timestamp; este arquivo preserva).
    mid = match.get("match_id", "")
    ts = match.get("start_time", "")
    if per_match and mid:
        new_file = not BY_MATCH.exists()
        with BY_MATCH.open("a", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["match_id", "start_time", "lane", "rad_won", "rad_heroes", "dire_heroes"])
            for lane_label, rw, rh, dh in per_match:
                w.writerow([mid, ts, lane_label, int(rw), rh, dh])
    return True


def write_out(agg, prior):
    rows = []
    for (o, e), (g, w) in agg.items():
        if g <= 0:
            continue
        wr = 100.0 * w / g
        # score suavizado (Bayes, puxa low-sample p/ 0): (-1..+1), + = ganha a lane
        smoothed = ((w + prior * 0.5) / (g + prior) - 0.5) * 2
        rows.append({
            "own_hero": o, "enemy_hero": e, "games": g, "lane_wins": w,
            "lane_wr": round(wr, 1), "lane_score": round(smoothed, 4),
        })
    rows.sort(key=lambda x: (-x["games"], x["own_hero"]))
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["own_hero", "enemy_hero", "games", "lane_wins", "lane_wr", "lane_score"])
        w.writeheader(); w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100, help="matches NOVOS a buscar")
    ap.add_argument("--prior", type=int, default=10, help="smoothing bayesiano (puxa p/ 50pct)")
    ap.add_argument("--sleep", type=float, default=1.0, help="pausa entre calls (rate limit)")
    ap.add_argument("--recheck-days", type=int, default=0,
                    help="re-checa matches 'sem parse' dos ultimos N dias (podem ter parseado depois)")
    args = ap.parse_args()

    hero_map = load_hero_map()
    agg, have_lanes = rebuild_agg_from_by_match()
    player_done = load_player_lanes_done()
    processed = load_processed()

    with PM.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: float(r.get("start_time") or 0), reverse=True)
    start_by_mid = {r["match_id"]: float(r.get("start_time") or 0) for r in rows if r.get("match_id")}

    # Re-check: tira do processed os 'sem parse' recentes (podem ter parseado depois).
    if args.recheck_days > 0:
        cutoff = time.time() - args.recheck_days * 86400
        recheck = {m for m in processed
                   if m not in have_lanes and start_by_mid.get(m, 0) >= cutoff}
        processed -= recheck
        print(f"re-check: {len(recheck)} matches recentes sem parse voltam pra fila")

    # Fila: (a) nunca processados; (b) parseados que ainda nao tem player lanes.
    todo = [r["match_id"] for r in rows if r.get("match_id") and (
        r["match_id"] not in processed
        or (r["match_id"] in have_lanes and r["match_id"] not in player_done)
    )]
    todo = todo[:args.limit]
    print(f"processados: {len(processed)} | com lane-outcome: {len(have_lanes)} | "
          f"com player-lanes: {len(player_done)} | fila: {len(todo)}")

    used = unparsed = err = plades = 0
    for i, mid in enumerate(todo, 1):
        try:
            m = opendota_get_json(f"/matches/{mid}")
        except Exception as e:
            err += 1
            print(f"  [{i}/{len(todo)}] {mid} erro: {e}")
            time.sleep(args.sleep); continue
        if isinstance(m, dict):
            # lane outcomes: SO se ainda nao tem (evita duplicar by_match/agg)
            if mid not in have_lanes:
                if process_match(m, hero_map, agg):
                    used += 1
                    have_lanes.add(mid)
                else:
                    unparsed += 1
            # player lanes: SO se ainda nao tem
            if mid not in player_done:
                if extract_player_lanes(m, hero_map):
                    plades += 1
                    player_done.add(mid)
        else:
            unparsed += 1
        processed.add(mid)
        if i % 20 == 0:
            print(f"  [{i}/{len(todo)}] lane_out={used} player_lanes={plades} sem_parse={unparsed} err={err}", flush=True)
        if i % 100 == 0:
            write_out(agg, args.prior)
            PROCESSED.write_text(" ".join(sorted(processed)), encoding="utf-8")
        time.sleep(args.sleep)

    n = write_out(agg, args.prior)
    PROCESSED.write_text(" ".join(sorted(processed)), encoding="utf-8")
    print(f"\nOK. lane_outcomes novos={used} | player_lanes novos={plades} | sem parse={unparsed} | erros={err}")
    print(f"hero_lane_matchups.csv: {n} pares | processados: {len(processed)}")


if __name__ == "__main__":
    main()
