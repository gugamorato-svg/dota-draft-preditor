"""Comportamento basico do motor de features, sem dados coletados (contexto vazio)."""

import math

import pytest

import features as model_features

EMPTY_CONTEXT = model_features.load_feature_context("diretorio-que-nao-existe")
RADIANT = ["Anti-Mage", "Crystal Maiden", "Axe", "Lion", "Invoker"]
DIRE = ["Pudge", "Juggernaut", "Lina", "Tidehunter", "Witch Doctor"]


def build(radiant, dire):
    return model_features.build_feature_dict(
        radiant_team="",
        dire_team="",
        radiant_picks=radiant,
        dire_picks=dire,
        context=EMPTY_CONTEXT,
    )


def test_every_feature_is_present_and_finite_without_data():
    feature_dict = build(RADIANT, DIRE)
    for name in model_features.FEATURE_NAMES:
        assert name in feature_dict
        assert math.isfinite(feature_dict[name])


@pytest.mark.parametrize("name", model_features.FEATURE_NAMES)
def test_swapping_sides_flips_the_sign(name):
    forward = build(RADIANT, DIRE)[name]
    backward = build(DIRE, RADIANT)[name]
    assert forward == pytest.approx(-backward, abs=1e-9)


def test_probability_stays_inside_the_floor_and_ceiling():
    huge = {name: 10.0 for name in model_features.FEATURE_NAMES}
    model = {name: 1.0 for name in model_features.FEATURE_NAMES}
    assert model_features.predict_probability(model, huge) == model_features.MODEL_PROBABILITY_CEILING
    tiny = {name: -10.0 for name in model_features.FEATURE_NAMES}
    assert model_features.predict_probability(model, tiny) == model_features.MODEL_PROBABILITY_FLOOR


def test_zero_features_and_zero_intercept_give_fifty_percent():
    zeros = {name: 0.0 for name in model_features.FEATURE_NAMES}
    assert model_features.predict_probability({"intercept": 0.0}, zeros) == pytest.approx(50.0)


def test_every_feature_has_pt_and_en_descriptions():
    for name in model_features.FEATURE_NAMES:
        assert name in model_features.FEATURE_DESCRIPTIONS, name
        details = model_features.FEATURE_DESCRIPTIONS[name]
        assert details["descricao"].strip(), name
        assert details["descricao_en"].strip(), name

