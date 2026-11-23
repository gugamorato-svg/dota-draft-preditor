"""Textos visiveis do app em portugues e ingles.

Cada chave tem o mesmo valor nos dois idiomas; `t()` devolve o texto do idioma
pedido e cai no portugues quando a chave ou o idioma nao existem. As chaves com
`{placeholder}` sao formatadas com `t("chave", lang, **valores)`.
"""

DEFAULT_LANG = "pt"
LANGUAGE_LABELS = {
    "pt": "Portugues",
    "en": "English",
}

TEXTS = {
    "pt": {
        "page_title": "Dota Draft Preditor",
        "title": "Dota Draft Preditor",
        "caption": "Chance de vitoria de Radiant e Dire a partir do draft, dos times e dos jogadores.",
        "language_label": "Idioma",
        "warning_example_model": (
            "Usando pesos de **exemplo**, nao calibrados. Rode a coleta e `python train_model.py` "
            "para treinar os seus (veja o README)."
        ),
        "info_missing_data": "Sem dados coletados ainda: {files}. As features ligadas a eles ficam em zero.",
        "error_load_match": "Nao consegui carregar a partida {match_id}: {error}",
        "error_load_match_short": "Nao consegui carregar: {error}",
        "match_not_found": "partida nao encontrada ou sem draft no OpenDota",
        "expander_load_match": "Carregar uma partida do OpenDota",
        "label_match_id": "Match ID",
        "placeholder_match_id": "ex.: 8123456789",
        "button_load": "Carregar",
        "label_team": "Time",
        "placeholder_optional": "opcional",
        "label_pick": "Pick {n}",
        "label_player": "Jogador {n}",
        "label_bans": "Bans",
        "info_choose_heroes": "Escolha os herois de cada lado para ver a previsao.",
        "caption_real_result": "Resultado real da partida carregada: {winner} venceu.",
        "subtitle_what_weighed": "O que pesou na previsao",
        "col_value": "valor (Radiant - Dire)",
        "col_weight": "peso",
        "col_contribution": "contribuicao",
        "col_what_it_measures": "o que mede",
        "caption_probability_range": (
            "A probabilidade fica limitada entre {floor:.0f}% e {ceiling:.0f}%. "
            "Ferramenta de estudo: nao trate a previsao como garantia."
        ),
    },
    "en": {
        "page_title": "Dota Draft Predictor",
        "title": "Dota Draft Predictor",
        "caption": "Win probability for Radiant and Dire from the draft, the teams and the players.",
        "language_label": "Language",
        "warning_example_model": (
            "Using **example** weights, not calibrated. Run the data collection and `python train_model.py` "
            "to train your own (see the README)."
        ),
        "info_missing_data": "No collected data yet: {files}. The features tied to them stay at zero.",
        "error_load_match": "Could not load match {match_id}: {error}",
        "error_load_match_short": "Could not load: {error}",
        "match_not_found": "match not found or without a draft on OpenDota",
        "expander_load_match": "Load a match from OpenDota",
        "label_match_id": "Match ID",
        "placeholder_match_id": "e.g.: 8123456789",
        "button_load": "Load",
        "label_team": "Team",
        "placeholder_optional": "optional",
        "label_pick": "Pick {n}",
        "label_player": "Player {n}",
        "label_bans": "Bans",
        "info_choose_heroes": "Pick the heroes for each side to see the prediction.",
        "caption_real_result": "Actual result of the loaded match: {winner} won.",
        "subtitle_what_weighed": "What drove the prediction",
        "col_value": "value (Radiant - Dire)",
        "col_weight": "weight",
        "col_contribution": "contribution",
        "col_what_it_measures": "what it measures",
        "caption_probability_range": (
            "The probability is bounded between {floor:.0f}% and {ceiling:.0f}%. "
            "Study tool: do not treat the prediction as a guarantee."
        ),
    },
}


def t(key, lang=DEFAULT_LANG, **kwargs):
    """Texto da `key` no `lang` (fallback: portugues; se faltar a chave, a propria chave)."""
    table = TEXTS.get(lang) or TEXTS[DEFAULT_LANG]
    if key in table:
        text = table[key]
    elif key in TEXTS[DEFAULT_LANG]:
        text = TEXTS[DEFAULT_LANG][key]
    else:
        text = key
    if kwargs:
        return text.format(**kwargs)
    return text
