import csv
from pathlib import Path

from features import FEATURE_DESCRIPTIONS, FEATURE_NAMES


BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "feature_sources.csv"


def main():
    rows = []
    for feature in FEATURE_NAMES:
        details = FEATURE_DESCRIPTIONS[feature]
        source_files = [part for part in details["fonte"].replace("+", " ").replace("/", " ").split() if part.endswith(".csv")]
        exists = all((BASE_DIR / source_file).exists() for source_file in source_files) if source_files else True
        rows.append(
            {
                "feature": feature,
                "source": details["fonte"],
                "updater": details["updater"],
                "status": "OK" if exists else "faltando",
                "description": details["descricao"],
            }
        )

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["feature", "source", "updater", "status", "description"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salvei mapa de features em {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
