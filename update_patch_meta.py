import csv
from pathlib import Path


BASE_DIR = Path(__file__).parent
HERO_STATS_FILE = BASE_DIR / "hero_stats.csv"
OUTPUT_FILE = BASE_DIR / "patch_meta.csv"


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def main():
    if not HERO_STATS_FILE.exists():
        raise SystemExit("hero_stats.csv nao encontrado.")

    rows = []
    with HERO_STATS_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            hero = row.get("hero", "").strip()
            if not hero:
                continue
            patch_wr = as_float(row.get("patch_wr"), 50)
            immortal_wr = as_float(row.get("immortal_wr"), 50)
            pro_pick = as_float(row.get("pro_pickrate"), 0)
            pro_ban = as_float(row.get("pro_banrate"), 0)
            pro_wr = as_float(row.get("pro_winrate"), 50)
            patch_meta_score = (
                (patch_wr - 50) * 0.45
                + (immortal_wr - 50) * 0.20
                + (pro_wr - 50) * 0.25
                + ((pro_pick + pro_ban) - 10) * 0.025
            )
            rows.append(
                {
                    "hero": hero,
                    "patch_wr": patch_wr,
                    "immortal_wr": immortal_wr,
                    "pro_contest": round(pro_pick + pro_ban, 2),
                    "pro_winrate": pro_wr,
                    "patch_meta_score": round(patch_meta_score, 3),
                }
            )

    rows.sort(key=lambda row: row["patch_meta_score"], reverse=True)
    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["hero", "patch_wr", "immortal_wr", "pro_contest", "pro_winrate", "patch_meta_score"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salvei meta do patch para {len(rows)} herois em {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
