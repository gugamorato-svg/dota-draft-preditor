"""Resolve incrementalmente o backlog de players nao-resolvidos.

Problema: times tier-baixo chegam via Steam Live com account_id cru
(account_320017600) que nao esta em player_accounts.csv -> comfort=0 +
penalidade -0.35/heroi (injusto, e' buraco de dados, nao skill). O
unresolved_players.csv acumula esses (milhares).

Este job, agendado, resolve um lote por execucao (cap pela cota OpenDota,
~2 calls/player): account_id -> personaname (/players/{id}) + comfort 365d
(/players/{id}/matches via rows_for_player). DESAMBIGUA nomes genericos que
colidem com player ja existente (M -> M#<account_id>) pra nao misturar dois
jogadores diferentes no lookup de comfort (que e' keyed por NOME).

Marca account_ids sem perfil publico em players_unresolvable.json pra nao
re-tentar (desperdicio de cota).

Uso:
    python resolve_players.py                 # default: ate 200 players
    python resolve_players.py --max 500       # lote maior
"""
import argparse
import csv
import json
import time
from pathlib import Path

import update_player_hero_year_stats as u
from utils import opendota_get_json

# Throttle entre players pra nao estourar o limite OpenDota de 60/min. Sao ~2
# calls/player; a 1.5s davam ~80/min (estourava). 3.0s + latencia de rede =
# ~2 calls / ~4s = ~30/min, com folga. ~600 players => ~40min/run (background OK).
THROTTLE_SECONDS = 3.0

ROOT = Path(__file__).resolve().parent
PLAYER_ACCOUNTS_FILE = ROOT / "player_accounts.csv"
PLAYER_HERO_EXTERNAL_FILE = ROOT / "player_hero_external.csv"
PLAYER_HERO_STATS_FILE = ROOT / "player_hero_stats.csv"
UNRESOLVED_FILE = ROOT / "unresolved_players.csv"
UNRESOLVABLE_FILE = ROOT / "players_unresolvable.json"

DAYS, LIMIT, MIN_GAMES = 365, 300, 5
PA_FIELDS = ["player", "account_id", "source", "notes"]
PH_FIELDS = ["player", "hero", "games", "winrate", "comfort", "source", "url"]


def _names_from(path, col="player"):
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig") as f:
        return {(r.get(col) or "").strip() for r in csv.DictReader(f) if (r.get(col) or "").strip()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=200, help="Maximo de players por execucao (cota OpenDota ~2 calls cada).")
    args = parser.parse_args()

    # ja resolvidos = account_ids em player_accounts.csv
    resolved = set()
    if PLAYER_ACCOUNTS_FILE.exists():
        with PLAYER_ACCOUNTS_FILE.open("r", encoding="utf-8-sig") as f:
            resolved = {(r.get("account_id") or "").strip() for r in csv.DictReader(f)}
    # sem perfil publico (nao re-tentar)
    try:
        unresolvable = set(json.loads(UNRESOLVABLE_FILE.read_text(encoding="utf-8")))
    except Exception:
        unresolvable = set()
    # nomes existentes (colisao): player_accounts + pro local + external
    existing_names = (_names_from(PLAYER_ACCOUNTS_FILE) | _names_from(PLAYER_HERO_STATS_FILE)
                      | _names_from(PLAYER_HERO_EXTERNAL_FILE))

    # backlog unico (preserva ordem de chegada)
    backlog, seen = [], set()
    if UNRESOLVED_FILE.exists():
        with UNRESOLVED_FILE.open("r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                aid = str(r.get("account_id") or "").strip()
                if aid and aid not in resolved and aid not in unresolvable and aid not in seen:
                    seen.add(aid)
                    backlog.append(aid)
    print(f"Backlog unico pendente: {len(backlog)} | lote desta execucao: {min(args.max, len(backlog))}")
    if not backlog:
        return

    hero_names = u.load_hero_names()
    new_pa, new_ph = [], []
    done = failed = 0
    # Writes sao APOS o loop; qualquer 'break' preserva o progresso, 'raise' perde
    # tudo. Politica de erro: rate_limit (sistemico) -> para; erro pontual de 1
    # player -> pula (continue); 3 erros SEGUIDOS -> API instavel (ex.: 521) -> para.
    consec_err = 0
    for aid in backlog[: args.max]:
        # 1) Perfil (nome).
        try:
            prof = opendota_get_json(f"/players/{aid}", timeout=30)
        except Exception as e:
            consec_err += 1
            print(f"  erro no perfil de {aid}: {e} (skip {consec_err}/3)")
            if consec_err >= 3:
                print("  3 erros seguidos — API instavel, parando (progresso salvo).")
                break
            continue
        if isinstance(prof, dict) and prof.get("error") == "rate_limit":
            print("  [429] rate limit no perfil — parando (continua no proximo run).")
            break
        consec_err = 0
        name = ((prof.get("profile") or {}).get("personaname") or "").strip() if isinstance(prof, dict) else ""
        if not name:
            unresolvable.add(aid)
            failed += 1
            continue
        # desambigua nome colidente
        if name in existing_names:
            name = f"{name}#{aid}"
        # 2) Comfort (partidas). fetch_player_matches faz raise apos 3 retries em
        #    429 -> ANTES crashava o job e perdia o lote. Agora trata: rate_limit
        #    para; outro erro pula (player re-tentado no proximo run).
        try:
            rows = u.rows_for_player(name, aid, hero_names, DAYS, LIMIT, MIN_GAMES)
        except Exception as e:
            if "rate limit" in str(e).lower() or "429" in str(e):
                print(f"  [429] rate limit no comfort de {aid} — parando, progresso salvo.")
                break
            consec_err += 1
            print(f"  erro no comfort de {aid}: {e} (skip {consec_err}/3)")
            if consec_err >= 3:
                print("  3 erros seguidos — API instavel, parando (progresso salvo).")
                break
            continue
        consec_err = 0
        new_pa.append({"player": name, "account_id": aid, "source": "opendota player profile", "notes": "resolve_players backlog"})
        new_ph.extend(rows)
        existing_names.add(name)
        done += 1
        time.sleep(THROTTLE_SECONDS)  # throttle anti rate-limit

    if new_pa:
        with PLAYER_ACCOUNTS_FILE.open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=PA_FIELDS).writerows(new_pa)
    if new_ph:
        with PLAYER_HERO_EXTERNAL_FILE.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PH_FIELDS)
            for r in new_ph:
                w.writerow({k: r.get(k, "") for k in PH_FIELDS})
    UNRESOLVABLE_FILE.write_text(json.dumps(sorted(unresolvable)), encoding="utf-8")

    print(f"Resolvidos {done} players (+{len(new_ph)} linhas de comfort), {failed} sem perfil publico.")
    print(f"Backlog restante estimado: ~{len(backlog) - done - failed}.")


if __name__ == "__main__":
    main()
