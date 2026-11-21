"""
Backfill de LANE OUTCOMES via Stratz GraphQL — recupera matches HISTORICOS que a
OpenDota nunca parseou (replay expirado la; Stratz parseou quando era fresco).

Escreve nos MESMOS arquivos do fetch_lane_outcomes.py (schema identico):
  - lane_outcomes_by_match.csv  (confrontos de lane, vencedor por networth@10)
  - player_lanes_by_match.csv   (lane real por player)
E adicionalmente (exclusivo Stratz — resolve pos1 vs pos5):
  - player_roles_stratz.csv     (roleBasic: CORE/LIGHT_SUPPORT/HARD_SUPPORT)

Rate limit free tier: 10k/dia, ~250/min. Sleep 0.35s + backoff em 429.
Resumivel: lane_stratz_processed.txt.

Uso:
    python fetch_lane_outcomes_stratz.py --limit 3000
"""
import argparse, csv, json, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
PM = ROOT / "pro_matches.csv"
BY_MATCH = ROOT / "lane_outcomes_by_match.csv"
PLAYER_LANES = ROOT / "player_lanes_by_match.csv"
PLAYER_ROLES_STRATZ = ROOT / "player_roles_stratz.csv"
PROCESSED = ROOT / "lane_stratz_processed.txt"
HERO_ID_MAP = ROOT / "hero_id_map.csv"
CONFIG = ROOT / "config.local.json"

STRATZ_URL = "https://api.stratz.com/graphql"
LANE_MAP = {"SAFE_LANE": 1, "MID_LANE": 2, "OFF_LANE": 3, "JUNGLE": 4, "ROAMING": 4}
QUERY = """query($id: Long!) { match(id: $id) {
  id startDateTime parsedDateTime
  players { heroId isRadiant lane roleBasic steamAccountId
            steamAccount { name proSteamAccount { name } }
            stats { networthPerMinute } }
} }"""


def load_hero_map():
    m = {}
    with HERO_ID_MAP.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                m[int(r["hero_id"])] = r["hero"].strip()
            except (ValueError, KeyError):
                pass
    return m


class QuotaExhausted(Exception):
    pass


def gql(key, mid, retries=3):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": "STRATZ_API"}
    for attempt in range(retries):
        r = requests.post(STRATZ_URL, json={"query": QUERY, "variables": {"id": int(mid)}},
                          headers=headers, timeout=25)
        if r.status_code == 429:
            wait = 10 * (attempt + 1)
            print(f"    [429] esperando {wait}s...", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        d = r.json()
        if "errors" in d:
            raise RuntimeError(str(d["errors"])[:150])
        return (d.get("data") or {}).get("match")
    # 429 em todas as tentativas = cota (minuto/hora) esgotada — NAO e' "sem parse".
    raise QuotaExhausted(mid)


def nw_at10(p):
    st = p.get("stats") or {}
    nw = st.get("networthPerMinute") or []
    if len(nw) > 10:
        return nw[10]
    return nw[-1] if nw else None


def process(m, hero_map):
    """Retorna (lane_rows, player_rows, role_rows) ou (None,)*3 se sem parse."""
    if not m or not m.get("parsedDateTime"):
        return None, None, None
    mid = m.get("id", "")
    ts = m.get("startDateTime", "")
    lanes = {0: {1: [], 2: [], 3: []}, 1: {1: [], 2: [], 3: []}}
    player_rows, role_rows = [], []
    any_nw = False
    for p in m.get("players") or []:
        lr = LANE_MAP.get(p.get("lane"))
        hero = hero_map.get(p.get("heroId"))
        aid = p.get("steamAccountId")
        sa = p.get("steamAccount") or {}
        pro = sa.get("proSteamAccount") or {}
        name = (pro.get("name") or sa.get("name") or "").strip()
        is_rad = bool(p.get("isRadiant"))
        if hero and aid and lr:
            player_rows.append([mid, ts, aid, name, hero, lr, int(is_rad)])
        if hero and aid and p.get("roleBasic"):
            role_rows.append([mid, ts, aid, name, hero, p.get("lane") or "", p.get("roleBasic")])
        if lr not in (1, 2, 3) or not hero:
            continue
        nw = nw_at10(p)
        if nw is None:
            continue
        any_nw = True
        lanes[0 if is_rad else 1][lr].append((hero, nw))
    if not any_nw:
        return None, player_rows or None, role_rows or None

    lane_rows = []
    for rad_lane, dire_lane in [(1, 3), (2, 2), (3, 1)]:
        rad, dire = lanes[0][rad_lane], lanes[1][dire_lane]
        if not rad or not dire:
            continue
        rg, dg = sum(g for _, g in rad), sum(g for _, g in dire)
        if rg == dg:
            continue
        lane_rows.append([mid, ts, f"{rad_lane}v{dire_lane}", int(rg > dg),
                          "|".join(h for h, _ in rad), "|".join(h for h, _ in dire)])
    return lane_rows or None, player_rows or None, role_rows or None


def append_csv(path, header, rows):
    if not rows:
        return
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--sleep", type=float, default=0.35)
    args = ap.parse_args()

    key = json.loads(CONFIG.read_text(encoding="utf-8-sig")).get("stratz_api_key", "").strip()
    if not key:
        raise SystemExit("stratz_api_key vazia em config.local.json")
    hero_map = load_hero_map()

    have = set()
    if BY_MATCH.exists():
        with BY_MATCH.open(encoding="utf-8-sig") as f:
            have = {r["match_id"] for r in csv.DictReader(f)}
    done = set(PROCESSED.read_text(encoding="utf-8").split()) if PROCESSED.exists() else set()

    with PM.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: float(r.get("start_time") or 0), reverse=True)
    todo = [r["match_id"] for r in rows
            if r.get("match_id") and r["match_id"] not in have and r["match_id"] not in done]
    todo = todo[:args.limit]
    print(f"ja com lane outcome: {len(have)} | stratz processados: {len(done)} | fila: {len(todo)}")

    ok = noparse = err = 0
    for i, mid in enumerate(todo, 1):
        while True:  # re-tenta o MESMO match apos cooldown de cota
            try:
                m = gql(key, mid)
                break
            except QuotaExhausted:
                print(f"  [{i}/{len(todo)}] cota esgotada — cooldown 5min e re-tenta o mesmo match...", flush=True)
                PROCESSED.write_text(" ".join(sorted(done)), encoding="utf-8")
                time.sleep(300)
            except Exception as e:
                err += 1
                if err <= 10 or err % 50 == 0:
                    print(f"  [{i}/{len(todo)}] {mid} erro: {str(e)[:80]}")
                done.add(mid)
                m = "SKIP"
                break
        if m == "SKIP":
            time.sleep(args.sleep)
            continue
        lane_rows, player_rows, role_rows = process(m, hero_map)
        if lane_rows:
            append_csv(BY_MATCH, ["match_id", "start_time", "lane", "rad_won", "rad_heroes", "dire_heroes"], lane_rows)
            ok += 1
        else:
            noparse += 1
        append_csv(PLAYER_LANES, ["match_id", "start_time", "account_id", "name", "hero", "lane_role", "is_radiant"], player_rows)
        append_csv(PLAYER_ROLES_STRATZ, ["match_id", "start_time", "account_id", "name", "hero", "lane", "role_basic"], role_rows)
        done.add(mid)
        if i % 50 == 0:
            print(f"  [{i}/{len(todo)}] lane_ok={ok} sem_parse={noparse} err={err}", flush=True)
            PROCESSED.write_text(" ".join(sorted(done)), encoding="utf-8")
        time.sleep(args.sleep)

    PROCESSED.write_text(" ".join(sorted(done)), encoding="utf-8")
    print(f"\nOK. lane outcomes novos={ok} | sem parse na Stratz={noparse} | erros={err}")
    print("Rode: python rebuild_lane_matrix.py  (ou o proximo fetch_lane_outcomes do scheduler regenera a matriz)")


if __name__ == "__main__":
    main()
