import csv
import argparse
import math
from pathlib import Path

from features import (
    FEATURE_NAMES,
    build_feature_dict,
    feature_vector,
    load_feature_context,
    recent_form,
    split_players,
    split_heroes,
    update_recent_form,
)
from temporal_context import context_for_match, initial_temporal_state, update_temporal_state


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
MODEL_FILE = BASE_DIR / "model_coefficients.csv"
REPORT_FILE = BASE_DIR / "model_report.txt"
POSITIVE_FEATURES = set(FEATURE_NAMES)
MIN_FEATURE_WEIGHTS = {
    "hero_meta_diff": 0.05,
    "player_comfort_diff": 0.10,
    "pre_match_block": 1.0,
}
MAX_FEATURE_WEIGHTS = {
    "player_comfort_diff": 0.40,
    "role_fit_diff": 0.04,
    "draft_balance_diff": 0.26,
    "lane_matchup_diff": 0.12,
    "synergy_diff": 0.25,
    "counter_diff": 0.25,
    "pre_match_block": 1.0,
    "patch_meta_diff": 0.20,
}


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def sigmoid(value):
    return 1 / (1 + math.exp(-value))


def train_logistic_regression(features, labels, epochs=4500, learning_rate=0.02, l2=0.12):
    weights = [0.0 for _ in features[0]]
    intercept = 0.0
    n = len(labels)

    for _ in range(epochs):
        grad_w = [0.0 for _ in weights]
        grad_b = 0.0

        for x, y in zip(features, labels):
            prediction = sigmoid(intercept + sum(w * v for w, v in zip(weights, x)))
            error = prediction - y
            grad_b += error
            for i, value in enumerate(x):
                grad_w[i] += error * value

        intercept -= learning_rate * grad_b / n
        for i in range(len(weights)):
            weights[i] -= learning_rate * ((grad_w[i] / n) + l2 * weights[i])
            floor = MIN_FEATURE_WEIGHTS.get(FEATURE_NAMES[i], 0.0)
            if FEATURE_NAMES[i] in POSITIVE_FEATURES and weights[i] < floor:
                weights[i] = floor
            ceiling = MAX_FEATURE_WEIGHTS.get(FEATURE_NAMES[i])
            if ceiling is not None and weights[i] > ceiling:
                weights[i] = ceiling

    return intercept, weights


def log_loss(features, labels, intercept, weights):
    total = 0.0
    for x, y in zip(features, labels):
        prediction = sigmoid(intercept + sum(w * v for w, v in zip(weights, x)))
        prediction = min(max(prediction, 0.001), 0.999)
        total += -(y * math.log(prediction) + (1 - y) * math.log(1 - prediction))
    return total / len(labels)


def accuracy(features, labels, intercept, weights):
    if not labels:
        return 0.0
    hits = 0
    for x, y in zip(features, labels):
        prediction = sigmoid(intercept + sum(w * v for w, v in zip(weights, x)))
        hits += int((prediction >= 0.5) == bool(y))
    return hits / len(labels) * 100


def load_matches():
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        return sorted(list(csv.DictReader(file)), key=lambda row: as_float(row.get("start_time"), 0))


def main():
    parser = argparse.ArgumentParser(description="Treina o modelo do Dota Draft Analyzer.")
    parser.add_argument("--patch", default="", help="Treina apenas um patch especifico, ex: 60.")
    args = parser.parse_args()

    if not PRO_MATCHES_FILE.exists():
        raise SystemExit("pro_matches.csv nao existe. Rode primeiro: python fetch_pro_matches.py")

    base_context = load_feature_context(BASE_DIR)
    features = []
    labels = []
    form_state = {}
    temporal_state = initial_temporal_state()

    for row in load_matches():
        if args.patch and str(row.get("patch", "")) != str(args.patch):
            continue
        radiant_picks = split_heroes(row.get("radiant_picks", ""))
        dire_picks = split_heroes(row.get("dire_picks", ""))
        radiant_bans = split_heroes(row.get("radiant_bans", ""))
        dire_bans = split_heroes(row.get("dire_bans", ""))

        if len(radiant_picks) != 5 or len(dire_picks) != 5:
            continue

        radiant_team = row.get("radiant_team", "Radiant")
        dire_team = row.get("dire_team", "Dire")
        radiant_win = row.get("radiant_win") == "1"
        context = context_for_match(base_context, temporal_state)
        feature_dict = build_feature_dict(
            radiant_team,
            dire_team,
            radiant_picks,
            dire_picks,
            radiant_bans=radiant_bans,
            dire_bans=dire_bans,
            radiant_players=split_players(row.get("radiant_players", "")),
            dire_players=split_players(row.get("dire_players", "")),
            recent_form_radiant=recent_form(form_state, radiant_team),
            recent_form_dire=recent_form(form_state, dire_team),
            context=context,
        )
        features.append(feature_vector(feature_dict))
        labels.append(1 if radiant_win else 0)

        update_recent_form(form_state, radiant_team, radiant_win)
        update_recent_form(form_state, dire_team, not radiant_win)
        update_temporal_state(temporal_state, row)

    if len(labels) < 20:
        raise SystemExit("Poucas partidas para treinar. Colete pelo menos 20 jogos profissionais.")

    split_at = max(1, int(len(labels) * 0.8))
    train_x = features[:split_at]
    train_y = labels[:split_at]
    test_x = features[split_at:]
    test_y = labels[split_at:]

    intercept, weights = train_logistic_regression(train_x, train_y)
    train_loss = log_loss(train_x, train_y, intercept, weights)
    test_loss = log_loss(test_x, test_y, intercept, weights) if test_y else train_loss
    train_acc = accuracy(train_x, train_y, intercept, weights)
    test_acc = accuracy(test_x, test_y, intercept, weights) if test_y else train_acc

    coefficients = [{"feature": "intercept", "coefficient": round(intercept, 8)}]
    coefficients.extend(
        {"feature": name, "coefficient": round(weight, 8)}
        for name, weight in zip(FEATURE_NAMES, weights)
    )

    with MODEL_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["feature", "coefficient"])
        writer.writeheader()
        writer.writerows(coefficients)

    REPORT_FILE.write_text(
        "\n".join(
            [
                "Dota Draft Analyzer - model report",
                f"Partidas usadas: {len(labels)}",
                f"Patch filtrado: {args.patch or 'todos'}",
                f"Features: {', '.join(FEATURE_NAMES)}",
                f"Treino: {len(train_y)}",
                f"Teste: {len(test_y)}",
                f"Train log loss: {train_loss:.4f}",
                f"Test log loss: {test_loss:.4f}",
                f"Train accuracy: {train_acc:.1f}%",
                f"Test accuracy: {test_acc:.1f}%",
                "",
                "Coeficientes salvos em model_coefficients.csv",
            ]
        ),
        encoding="utf-8",
    )

    print("Modelo treinado e salvo em model_coefficients.csv.")
    print(REPORT_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
