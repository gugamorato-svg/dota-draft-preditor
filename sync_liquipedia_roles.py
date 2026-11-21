"""Sincroniza os lane roles do Liquipedia (liquipedia_roles.csv) pro player_roles.csv.

IDEMPOTENTE + AUTO-RESOLVEDOR: toda vez que player_accounts.csv cresce (novos jogos
ingeridos), mais jogadores do EWC passam a ter account_id/nome canonico conhecido.
Este job re-casa liquipedia_roles.csv contra player_accounts + player_aliases e
promove a role (posicao 1-5 = autoritativa) pro player_roles.csv. Pode virar job
diario (rodar depois do fetch_league_matches).

liquipedia_roles.csv (fonte): liquipedia_id, team, position, role, our_player, source
Posicao -> role: 1=carry 2=mid 3=offlane 4=support 5=hard support.

Uso: python sync_liquipedia_roles.py
"""
import csv, shutil, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIQ = ROOT / "liquipedia_roles.csv"
ACCOUNTS = ROOT / "player_accounts.csv"
ALIASES = ROOT / "player_aliases.csv"
ROLES = ROOT / "player_roles.csv"
POS_ROLE = {1: "carry", 2: "mid", 3: "offlane", 4: "support", 5: "hard support"}


def _load_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _match_name(name, canon, alias):
    k = (name or "").strip().lower()
    if not k:
        return None
    if k in canon:
        return canon[k]
    if k in alias:
        return alias[k]
    ks = k.rstrip("~`-_ ").strip()          # tira sufixos de tag comuns
    if ks in canon:
        return canon[ks]
    return None


def main():
    if not LIQ.exists():
        raise SystemExit("liquipedia_roles.csv nao existe. Rode a coleta do Liquipedia primeiro.")

    canon = {(r.get("player") or "").strip().lower(): (r.get("player") or "").strip()
             for r in _load_csv(ACCOUNTS) if r.get("player")}
    alias = {(r.get("alias") or "").strip().lower(): (r.get("player") or "").strip()
             for r in _load_csv(ALIASES) if r.get("alias") and r.get("player")}

    liq = _load_csv(LIQ)
    liq_fields = ["liquipedia_id", "team", "position", "role", "our_player", "source"]

    # 1) re-resolve our_player em liquipedia_roles.csv
    newly = []
    for r in liq:
        prev = (r.get("our_player") or "").strip()
        our = _match_name(r.get("liquipedia_id"), canon, alias)
        r["our_player"] = our or ""
        if our and not prev:
            newly.append((r["liquipedia_id"], our))
    with LIQ.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=liq_fields)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in liq_fields} for r in liq)

    # 2) upsert player_roles.csv (autoritativo p/ resolvidos)
    roles = _load_csv(ROLES)
    role_fields = roles[0].keys() if roles else ["player", "role", "notes"]
    by = {(r.get("player") or "").strip().lower(): r for r in roles}
    changed, added = [], []
    for r in liq:
        our = (r.get("our_player") or "").strip()
        if not our:
            continue
        try:
            pos = int(float(r.get("position")))
        except (TypeError, ValueError):
            continue
        role = POS_ROLE.get(pos)
        if not role:
            continue
        note = f"Liquipedia EWC2026 pos{pos} ({r.get('team')}); confianca 1.0"
        ex = by.get(our.lower())
        if ex is None:
            nr = {"player": our, "role": role, "notes": note}
            roles.append(nr); by[our.lower()] = nr; added.append((our, role))
        else:
            old = (ex.get("role") or "").strip()
            if old != role:
                changed.append((our, old, role))
            ex["role"] = role; ex["notes"] = note

    if changed or added:
        bak = ROOT / f"player_roles.csv.bak_sync_{datetime.datetime.now():%Y%m%d_%H%M%S}"
        shutil.copy2(ROLES, bak)
        with ROLES.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(role_fields))
            w.writeheader(); w.writerows(roles)
        print(f"player_roles.csv atualizado (backup: {bak.name})")
    else:
        print("player_roles.csv ja estava em dia (nada a mudar).")

    resolved = sum(1 for r in liq if (r.get("our_player") or "").strip())
    print(f"Liquipedia: {resolved}/{len(liq)} jogadores resolvidos | "
          f"novos resolvidos agora: {len(newly)} | corrigidos: {len(changed)} | adicionados: {len(added)}")
    for lid, our in newly:
        print(f"   + resolvido: {lid} -> {our}")
    for p, o, n in changed:
        print(f"   ~ corrigido: {p}: {o or '(vazio)'} -> {n}")


if __name__ == "__main__":
    main()
