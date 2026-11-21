"""
Deriva role REAL de cada player pela moda do lane_role observado nos matches
parseados (player_lanes_by_match.csv). Dado observado > wiki.

lane_role OpenDota: 1=safe(carry), 2=mid, 3=off, 4=jungle/roam.
Posicao 1-5 do projeto: 1=carry(safe), 2=mid, 3=off, 4/5=support.
Suportes compartilham lane com o core: distinguimos por gold? Nao temos aqui —
aproximacao: quem tem lane_role 1/2/3 e NAO e o top-gold da lane pode ser sup.
Simples e robusto: role = moda do lane_role; confidence = freq da moda.

Saida: player_lanes_derived.csv (account_id, name, n_matches, modal_lane, confidence)
Merge opcional em player_roles.csv com --apply (so adiciona quem nao existe la).
"""
import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "player_lanes_by_match.csv"
OUT = ROOT / "player_lanes_derived.csv"
ROLES = ROOT / "player_roles.csv"

LANE_LABEL = {1: "carry/safe", 2: "mid", 3: "offlane", 4: "jungle/roam"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-matches", type=int, default=3, help="minimo de matches parseados")
    ap.add_argument("--apply", action="store_true", help="mescla em player_roles.csv (so novos)")
    args = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"{SRC.name} nao existe — rode fetch_lane_outcomes.py primeiro")

    by_acct = defaultdict(lambda: {"lanes": Counter(), "names": Counter()})
    with SRC.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            aid = r["account_id"]
            try:
                lr = int(r["lane_role"])
            except ValueError:
                continue
            by_acct[aid]["lanes"][lr] += 1
            nm = (r.get("name") or "").strip()
            if nm:
                by_acct[aid]["names"][nm] += 1

    rows = []
    for aid, d in by_acct.items():
        n = sum(d["lanes"].values())
        if n < args.min_matches:
            continue
        modal, cnt = d["lanes"].most_common(1)[0]
        name = d["names"].most_common(1)[0][0] if d["names"] else f"account_{aid}"
        rows.append({
            "account_id": aid, "name": name, "n_matches": n,
            "modal_lane": modal, "modal_label": LANE_LABEL.get(modal, "?"),
            "confidence": round(cnt / n, 2),
        })
    rows.sort(key=lambda r: -r["n_matches"])

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["account_id", "name", "n_matches", "modal_lane", "modal_label", "confidence"])
        w.writeheader(); w.writerows(rows)
    print(f"{OUT.name}: {len(rows)} players (>= {args.min_matches} matches parseados)")
    solid = [r for r in rows if r["confidence"] >= 0.7]
    print(f"  com lane consistente (conf >= 0.7): {len(solid)}")
    for r in rows[:12]:
        print(f"  {r['name']:24s} n={r['n_matches']:>3} lane={r['modal_label']:12s} conf={r['confidence']}")

    if args.apply:
        existing = set()
        if ROLES.exists():
            with ROLES.open(encoding="utf-8-sig") as f:
                existing = {r["player"].strip().lower() for r in csv.DictReader(f)}
        added = 0
        with ROLES.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            for r in rows:
                if r["confidence"] < 0.7 or r["name"].lower() in existing:
                    continue
                # player_roles.csv usa pos 1-5; mapeia lane->pos core (sups indistinguiveis aqui)
                w.writerow([r["name"], r["modal_lane"], f"derived_from_parse n={r['n_matches']} conf={r['confidence']}"])
                added += 1
        print(f"player_roles.csv: +{added} players derivados (conf>=0.7, so novos)")


if __name__ == "__main__":
    main()
