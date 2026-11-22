"""Dota Draft Preditor: monte um draft (ou carregue uma partida do OpenDota) e veja
a chance de vitoria de Radiant e Dire, com o peso de cada feature.

Rodar: streamlit run app.py
"""

import csv
from pathlib import Path

import pandas as pd
import streamlit as st

import features as model_features
from i18n import DEFAULT_LANG, LANGUAGE_LABELS, t
from utils import normalize_hero_name, opendota_get_json

BASE_DIR = Path(__file__).parent
HERO_ID_MAP_FILE = BASE_DIR / "hero_id_map.csv"
MODEL_FILE = BASE_DIR / "model_coefficients.csv"
EXAMPLE_MODEL_FILE = BASE_DIR / "model_coefficients.example.csv"
# Arquivos que a coleta gera. Sem eles o modelo roda, mas quase todas as features dao 0.
DATA_FILES = [
    "hero_stats.csv",
    "team_ratings.csv",
    "hero_matchups.csv",
    "learned_synergies.csv",
    "team_form.csv",
    "player_form.csv",
    "head_to_head.csv",
    "patch_meta.csv",
]
SLOTS = 5

st.set_page_config(page_title=t("page_title", st.session_state.get("lang", DEFAULT_LANG)), layout="wide")


@st.cache_data
def load_heroes():
    """hero_id -> nome, e a lista de nomes em ordem alfabetica."""
    by_id = {}
    with HERO_ID_MAP_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            hero_id = row.get("hero_id", "").strip()
            hero = normalize_hero_name(row.get("hero", ""))
            if hero_id and hero:
                by_id[int(hero_id)] = hero
    return by_id, sorted(set(by_id.values()))


@st.cache_data
def load_model():
    """Pesos treinados, ou os de exemplo (nao calibrados) quando ainda nao ha treino."""
    path = MODEL_FILE if MODEL_FILE.exists() else EXAMPLE_MODEL_FILE
    model = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            try:
                model[row["feature"]] = float(row["coefficient"])
            except (KeyError, ValueError):
                continue
    return model, path == MODEL_FILE


@st.cache_data(ttl=600)
def load_context():
    return model_features.load_feature_context(BASE_DIR)


def match_from_opendota(match_id, heroes_by_id, lang=DEFAULT_LANG):
    """Picks, bans, times e jogadores de uma partida do OpenDota."""
    data = opendota_get_json(f"/matches/{int(match_id)}")
    if not isinstance(data, dict) or "picks_bans" not in data:
        raise ValueError(t("match_not_found", lang))

    draft = {"radiant": {"picks": [], "bans": []}, "dire": {"picks": [], "bans": []}}
    for entry in data.get("picks_bans") or []:
        side = "radiant" if entry.get("team") == 0 else "dire"
        hero = heroes_by_id.get(entry.get("hero_id"))
        if hero:
            draft[side]["picks" if entry.get("is_pick") else "bans"].append(hero)

    players = {"radiant": {}, "dire": {}}
    for player in data.get("players") or []:
        side = "radiant" if player.get("isRadiant") else "dire"
        hero = heroes_by_id.get(player.get("hero_id"))
        name = player.get("name") or player.get("personaname") or ""
        if hero and name:
            players[side][hero] = name

    return {
        "radiant_team": data.get("radiant_name") or "Radiant",
        "dire_team": data.get("dire_name") or "Dire",
        "draft": draft,
        "players": players,
        "radiant_win": data.get("radiant_win"),
    }


def apply_loaded_match(match):
    """Coloca a partida carregada nos campos da tela."""
    st.session_state["radiant_team"] = match["radiant_team"]
    st.session_state["dire_team"] = match["dire_team"]
    for side in ("radiant", "dire"):
        picks = match["draft"][side]["picks"][:SLOTS]
        for i in range(SLOTS):
            hero = picks[i] if i < len(picks) else ""
            st.session_state[f"{side}_pick_{i}"] = hero
            st.session_state[f"{side}_player_{i}"] = match["players"][side].get(hero, "")
        st.session_state[f"{side}_bans"] = match["draft"][side]["bans"]


def side_inputs(side, label, hero_options, lang):
    st.subheader(label)
    st.text_input(t("label_team", lang), key=f"{side}_team", placeholder=t("placeholder_optional", lang))
    picks, players = [], []
    for i in range(SLOTS):
        col_hero, col_player = st.columns([3, 2])
        with col_hero:
            hero = st.selectbox(t("label_pick", lang, n=i + 1), [""] + hero_options, key=f"{side}_pick_{i}")
        with col_player:
            player = st.text_input(
                t("label_player", lang, n=i + 1),
                key=f"{side}_player_{i}",
                placeholder=t("placeholder_optional", lang),
            )
        if hero:
            picks.append(hero)
            players.append(player.strip())
    bans = st.multiselect(t("label_bans", lang), hero_options, key=f"{side}_bans")
    return picks, players, bans


def feature_table(feature_dict, model, lang):
    rows = []
    contribution_key = t("col_contribution", lang)
    for name in model_features.FEATURE_NAMES:
        value = feature_dict.get(name, 0.0)
        coef = model.get(name, 0.0)
        info = model_features.FEATURE_DESCRIPTIONS.get(name, {})
        description = info.get("descricao_en" if lang == "en" else "descricao") or info.get("descricao", "")
        rows.append({
            "feature": name,
            t("col_value", lang): round(value, 3),
            t("col_weight", lang): round(coef, 3),
            contribution_key: round(value * coef, 3),
            t("col_what_it_measures", lang): description,
        })
    return pd.DataFrame(rows).sort_values(contribution_key, key=abs, ascending=False)


def main():
    lang = st.session_state.get("lang", DEFAULT_LANG)
    with st.sidebar:
        st.selectbox(
            t("language_label", lang),
            options=list(LANGUAGE_LABELS),
            format_func=LANGUAGE_LABELS.__getitem__,
            key="lang",
        )

    heroes_by_id, hero_options = load_heroes()
    model, trained = load_model()
    context = load_context()

    st.title(t("title", lang))
    st.caption(t("caption", lang))

    missing = [name for name in DATA_FILES if not (BASE_DIR / name).exists()]
    if not trained:
        st.warning(t("warning_example_model", lang))
    if missing:
        st.info(t("info_missing_data", lang, files=", ".join(missing)))

    # Um link com ?match_id=... abre a partida direto, para compartilhar uma previsao.
    linked_id = str(st.query_params.get("match_id", "")).strip()
    if linked_id.isdigit() and st.session_state.get("linked_match_id") != linked_id:
        st.session_state["linked_match_id"] = linked_id
        try:
            match = match_from_opendota(linked_id, heroes_by_id, lang)
            apply_loaded_match(match)
            st.session_state["loaded_result"] = match["radiant_win"]
        except Exception as error:  # rede, rate limit ou partida sem draft
            st.error(t("error_load_match", lang, match_id=linked_id, error=error))

    with st.expander(t("expander_load_match", lang)):
        col_id, col_button = st.columns([3, 1])
        with col_id:
            match_id = st.text_input(t("label_match_id", lang), placeholder=t("placeholder_match_id", lang))
        with col_button:
            st.write("")
            if st.button(t("button_load", lang)) and match_id.strip().isdigit():
                try:
                    match = match_from_opendota(match_id.strip(), heroes_by_id, lang)
                    apply_loaded_match(match)
                    st.session_state["loaded_result"] = match["radiant_win"]
                    st.session_state["linked_match_id"] = match_id.strip()
                    st.query_params["match_id"] = match_id.strip()
                    st.rerun()
                except Exception as error:  # rede, rate limit ou partida sem draft
                    st.error(t("error_load_match_short", lang, error=error))

    col_radiant, col_dire = st.columns(2)
    with col_radiant:
        radiant_picks, radiant_players, radiant_bans = side_inputs("radiant", "Radiant", hero_options, lang)
    with col_dire:
        dire_picks, dire_players, dire_bans = side_inputs("dire", "Dire", hero_options, lang)

    if not radiant_picks and not dire_picks:
        st.info(t("info_choose_heroes", lang))
        return

    feature_dict = model_features.build_feature_dict(
        radiant_team=st.session_state.get("radiant_team", "").strip(),
        dire_team=st.session_state.get("dire_team", "").strip(),
        radiant_picks=radiant_picks,
        dire_picks=dire_picks,
        radiant_bans=radiant_bans,
        dire_bans=dire_bans,
        radiant_players=radiant_players,
        dire_players=dire_players,
        context=context,
    )
    radiant_prob = model_features.predict_probability(model, feature_dict)

    st.divider()
    col_r, col_d = st.columns(2)
    col_r.metric("Radiant", f"{radiant_prob:.1f}%")
    col_d.metric("Dire", f"{100 - radiant_prob:.1f}%")
    st.progress(radiant_prob / 100)

    loaded_result = st.session_state.get("loaded_result")
    if loaded_result is not None:
        winner = "Radiant" if loaded_result else "Dire"
        st.caption(t("caption_real_result", lang, winner=winner))

    st.subheader(t("subtitle_what_weighed", lang))
    st.dataframe(feature_table(feature_dict, model, lang), hide_index=True, use_container_width=True)
    st.caption(
        t(
            "caption_probability_range",
            lang,
            floor=model_features.MODEL_PROBABILITY_FLOOR,
            ceiling=model_features.MODEL_PROBABILITY_CEILING,
        )
    )


main()
