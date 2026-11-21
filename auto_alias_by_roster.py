"""
AUTO-ALIAS POR ROSTER (03/08): quando uma org troca de nome (ou usa nome
tournament-specific) mas mantem o elenco, o rating deveria continuar valendo.
Este job detecta isso SOZINHO: nome de time nao-ratado cujo elenco (top-5
account_ids) sobrepoe >=MIN_OVERLAP com um time RATADO -> adiciona o alias.

Prova de conceito (03/08): 1w x Tundra Esports = 5/5, BoomBoys x BetBoom = 5/5,
Team Liquid x Vici = 0/5 (controle). Corte 4/5 pega rename e rejeita times diferentes.

SEGURANCA: so casa contra times RATADOS (team_ratings.csv), exige >=4/5 overlap E
>=MIN_GAMES jogos de cada lado (evita stand-in/fluke). Nao sobrescreve alias existente.

Uso:
  python auto_alias_by_roster.py            # DRY-RUN: so mostra o que adicionaria
  python auto_alias_by_roster.py --apply    # grava em team_aliases.csv (p/ scheduler diario)
"""
import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from utils import canonical_team_name, team_key

ROOT = Path(__file__).resolve().parent
PRO = ROOT / "pro_matches.csv"
RATINGS = ROOT / "team_ratings.csv"
ALIASES = ROOT / "team_aliases.csv"

MIN_OVERLAP = 4     # dos 5 top players tem que bater
MIN_GAMES = 2       # cada nome de time precisa de >= tantos jogos (anti-fluke/stand-in)


def _split_players(s):
    return [p.strip() for p in (s or "").split("|") if p.strip()]


def build_rosters():
    """{nome_cru_de_time: (top5_account_ids:set, n_jogos:int)}"""
    counts = defaultdict(Counter)
    games = defaultdict(int)
    with PRO.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            for tm, pl in [(r.get("radiant_team"), r.get("radiant_players")),
                           (r.get("dire_team"), r.get("dire_players"))]:
                tm = (tm or "").strip()
                if not tm:
                    continue
                games[tm] += 1
                for p in _split_players(pl):
                    counts[tm][p] += 1
    return {tm: (set(p for p, _ in c.most_common(5)), games[tm]) for tm, c in counts.items()}


def rated_canonicals():
    out = set()
    if RATINGS.exists():
        with RATINGS.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = (r.get("team") or "").strip()
                if t:
                    out.add(t)
    return out


def existing_alias_keys():
    keys = set()
    if ALIASES.exists():
        with ALIASES.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                keys.add((r.get("alias") or "").strip().lower())
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="grava os aliases achados em team_aliases.csv")
    args = ap.parse_args()

    rosters = build_rosters()
    rated = rated_canonicals()
    rated_key_to_name = {team_key(t): t for t in rated}
    alias_keys = existing_alias_keys()

    # rosters dos times RATADOS (pelo nome canonico)
    rated_rosters = {}
    for tm, (roster, n) in rosters.items():
        canon = canonical_team_name(tm)
        if canon in rated and n >= MIN_GAMES:
            # acumula: pega o roster do nome que tem mais jogos p/ esse canonico
            prev = rated_rosters.get(canon)
            if prev is None or n > prev[1]:
                rated_rosters[canon] = (roster, n)

    proposals = []
    for tm, (roster, n) in rosters.items():
        if n < MIN_GAMES or len(roster) < 5:
            continue
        canon = canonical_team_name(tm)
        if canon in rated:
            continue  # ja resolve p/ um time ratado (alias existente ou nome direto)
        if tm.strip().lower() in alias_keys:
            continue  # ja tem alias
        # procura o time ratado com maior overlap
        best = None
        for rcanon, (rroster, rn) in rated_rosters.items():
            ov = len(roster & rroster)
            if ov >= MIN_OVERLAP and (best is None or ov > best[1]):
                best = (rcanon, ov)
        if best:
            proposals.append((tm, best[0], best[1], n))

    if not proposals:
        print("Nenhum rename novo detectado (todos os times ja resolvem p/ um ratado ou sem overlap>=4).")
        return

    print(f"{len(proposals)} alias(es) por roster detectado(s) (overlap>={MIN_OVERLAP}/5):")
    for tm, canon, ov, n in proposals:
        print(f"  '{tm}' -> '{canon}'  (overlap {ov}/5, {n} jogos)")

    if args.apply:
        with ALIASES.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            for tm, canon, ov, n in proposals:
                w.writerow([tm, canon])
        print(f"\nGRAVADO em team_aliases.csv ({len(proposals)} linhas). Worker pega no proximo reload.")
    else:
        print("\n(DRY-RUN — rode com --apply p/ gravar. Recomendado no scheduler diario.)")


if __name__ == "__main__":
    main()
