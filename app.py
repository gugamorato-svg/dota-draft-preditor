"""Dota Draft Preditor: monte um draft (ou carregue uma partida do OpenDota) e veja
a chance de vitoria de Radiant e Dire, com o peso de cada feature.

Rodar: streamlit run app.py
"""

import csv
from pathlib import Path

import pandas as pd
import streamlit as st

import features as model_features
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

st.set_page_config(page_title="Dota Draft Preditor", layout="wide")


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


def match_from_opendota(match_id, heroes_by_id):
    """Picks, bans, times e jogadores de uma partida do OpenDota."""
    data = opendota_get_json(f"/matches/{int(match_id)}")
    if not isinstance(data, dict) or "picks_bans" not in data:
        raise ValueError("partida nao encontrada ou sem draft no OpenDota")

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


def side_inputs(side, label, hero_options):
    st.subheader(label)
    st.text_input("Time", key=f"{side}_team", placeholder="opcional")
    picks, players = [], []
    for i in range(SLOTS):
        col_hero, col_player = st.columns([3, 2])
        with col_hero:
            hero = st.selectbox(f"Pick {i + 1}", [""] + hero_options, key=f"{side}_pick_{i}")
        with col_player:
            player = st.text_input(f"Jogador {i + 1}", key=f"{side}_player_{i}", placeholder="opcional")
        if hero:
            picks.append(hero)
            players.append(player.strip())
    bans = st.multiselect("Bans", hero_options, key=f"{side}_bans")
    return picks, players, bans


def feature_table(feature_dict, model):
    rows = []
    for name in model_features.FEATURE_NAMES:
        value = feature_dict.get(name, 0.0)
        coef = model.get(name, 0.0)
        info = model_features.FEATURE_DESCRIPTIONS.get(name, {})
        rows.append({
            "feature": name,
            "valor (Radiant - Dire)": round(value, 3),
            "peso": round(coef, 3),
            "contribuicao": round(value * coef, 3),
            "o que mede": info.get("descricao", ""),
        })
    return pd.DataFrame(rows).sort_values("contribuicao", key=abs, ascending=False)


def main():
    heroes_by_id, hero_options = load_heroes()
    model, trained = load_model()
    context = load_context()

    st.title("Dota Draft Preditor")
    st.caption("Chance de vitoria de Radiant e Dire a partir do draft, dos times e dos jogadores.")

    missing = [name for name in DATA_FILES if not (BASE_DIR / name).exists()]
    if not trained:
        st.warning(
            "Usando pesos de **exemplo**, nao calibrados. Rode a coleta e `python train_model.py` "
            "para treinar os seus (veja o README)."
        )
    if missing:
        st.info("Sem dados coletados ainda: " + ", ".join(missing) + ". As features ligadas a eles ficam em zero.")

    # Um link com ?match_id=... abre a partida direto, para compartilhar uma previsao.
    linked_id = str(st.query_params.get("match_id", "")).strip()
    if linked_id.isdigit() and st.session_state.get("linked_match_id") != linked_id:
        st.session_state["linked_match_id"] = linked_id
        try:
            match = match_from_opendota(linked_id, heroes_by_id)
            apply_loaded_match(match)
            st.session_state["loaded_result"] = match["radiant_win"]
        except Exception as error:  # rede, rate limit ou partida sem draft
            st.error(f"Nao consegui carregar a partida {linked_id}: {error}")

    with st.expander("Carregar uma partida do OpenDota"):
        col_id, col_button = st.columns([3, 1])
        with col_id:
            match_id = st.text_input("Match ID", placeholder="ex.: 8123456789")
        with col_button:
            st.write("")
            if st.button("Carregar") and match_id.strip().isdigit():
                try:
                    match = match_from_opendota(match_id.strip(), heroes_by_id)
                    apply_loaded_match(match)
                    st.session_state["loaded_result"] = match["radiant_win"]
                    st.session_state["linked_match_id"] = match_id.strip()
                    st.query_params["match_id"] = match_id.strip()
                    st.rerun()
                except Exception as error:  # rede, rate limit ou partida sem draft
                    st.error(f"Nao consegui carregar: {error}")

    col_radiant, col_dire = st.columns(2)
    with col_radiant:
        radiant_picks, radiant_players, radiant_bans = side_inputs("radiant", "Radiant", hero_options)
    with col_dire:
        dire_picks, dire_players, dire_bans = side_inputs("dire", "Dire", hero_options)

    if not radiant_picks and not dire_picks:
        st.info("Escolha os herois de cada lado para ver a previsao.")
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
        st.caption(f"Resultado real da partida carregada: {'Radiant' if loaded_result else 'Dire'} venceu.")

    st.subheader("O que pesou na previsao")
    st.dataframe(feature_table(feature_dict, model), hide_index=True, use_container_width=True)
    st.caption(
        f"A probabilidade fica limitada entre {model_features.MODEL_PROBABILITY_FLOOR:.0f}% e "
        f"{model_features.MODEL_PROBABILITY_CEILING:.0f}%. Ferramenta de estudo: nao trate a previsao como garantia."
    )


main()
