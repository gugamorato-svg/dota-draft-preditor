import os
from itertools import combinations

from features import as_float, split_heroes, split_players, team_key


# Mesmas constantes do update_synergies.py (Bayesian smoothing).
PAIR_SMOOTHING_PRIOR = float(os.getenv("SYNERGY_PRIOR", "20"))  # env p/ sweep
PAIR_MIN_GAMES = int(os.getenv("PAIR_MIN_GAMES", "3"))
PAIR_PRIOR_WEIGHT = 0.18
PAIR_PRIOR_FULL_GAMES = 120
PAIR_LOCAL_FULL_GAMES = 30
# Cap do score de sinergia temporal. Configurável via env pra sweep do cap.
SYNERGY_CAP = float(os.getenv("SYNERGY_TEMPORAL_CAP", "2.0"))

# Decay temporal de sinergias/counters (half-life em DIAS). 0 = SEM decay.
# Peso de cada jogo = 0.5^(idade_dias/half_life), aplicado como soma exponencialmente
# decaída ao acumular jogos. Serve pra o modelo se auto-curar após patches (jogos
# novos dominam; matchups que inverteram decaem).
# Precedência: env SYNERGY_COUNTER_HALFLIFE_DAYS (pra sweep) > config.local.json
# "synergy_counter_halflife_days" (produção) > 0 (default sem decay).
def _read_decay_halflife():
    v = os.getenv("SYNERGY_COUNTER_HALFLIFE_DAYS")
    if v is not None:
        try:
            return float(v)
        except ValueError:
            pass
    try:
        import json
        from pathlib import Path
        cfg = json.loads((Path(__file__).parent / "config.local.json").read_text(encoding="utf-8-sig"))
        return float(cfg.get("synergy_counter_halflife_days", 0) or 0)
    except Exception:
        return 0.0


DECAY_HALFLIFE_DAYS = _read_decay_halflife()


def set_decay_halflife(value):
    """Troca o half-life EM RUNTIME (06/09).

    `_decayed_add` le DECAY_HALFLIFE_DAYS no momento da chamada, entao mudar o global
    aqui vale pro resto do walk. Usado por amateur_context.decide(): half-life 180 SO
    quando o dado amador esta presente e fresco; senao volta pra 90 (config de hoje).
    Motivo: 180 sozinho, com pro-only, e' PIOR que 90 na validacao — ele so
    ganha porque preserva a cobertura de pares que o amador traz.
    """
    global DECAY_HALFLIFE_DAYS
    DECAY_HALFLIFE_DAYS = float(value)
    return DECAY_HALFLIFE_DAYS


def _decayed_add(stats, won, ts):
    """Soma 1 jogo (games) e win a `stats`, aplicando decay exponencial ao acúmulo
    anterior baseado no tempo decorrido desde o último jogo do par. ts em segundos.
    Se DECAY_HALFLIFE_DAYS<=0 ou sem ts, vira contagem simples (+1)."""
    if DECAY_HALFLIFE_DAYS > 0 and ts is not None:
        last = stats.get("last_ts")
        if last is not None and ts > last:
            dt_days = (ts - last) / 86400.0
            factor = 0.5 ** (dt_days / DECAY_HALFLIFE_DAYS)
            stats["games"] *= factor
            stats["wins"] *= factor
        stats["last_ts"] = ts
    stats["games"] += 1
    if won:
        stats["wins"] += 1

# 26/08: prior baixado 30->10. Counter aguenta prior menor
# (ao contrario da sinergia): no holdout, prior 10 foi melhor que 30
# em todos os cortes, ainda que com intervalo largo; barato/reversivel.
# Reverter: COUNTER_PRIOR=30 no env, ou trocar o default de volta. SINERGIA fica em 20 (baixar
# la' PIORA). Lido no import.
COUNTER_SMOOTHING_PRIOR = float(os.getenv("COUNTER_PRIOR", "10"))  # env p/ sweep
COUNTER_CAP = 2.5
COUNTER_PRIOR_WEIGHT = 0.20
COUNTER_PRIOR_FULL_GAMES = 200
COUNTER_PRIOR_EFFECTIVE_GAMES = 6
COUNTER_LOCAL_FULL_GAMES = 30

# Espelha update_team_ratings.py. Time sem opendota_team_id (default 1400) e' penalizado.
UNKNOWN_TEAM_PENALTY = 200.0


def load_opendota_rating_prior(base_dir):
    """Seed do Elo temporal: usa o opendota_rating de team_ratings.csv como ponto de
    partida (prior externo slow-moving) em vez do cold-start 1400 — que comecava todos
    iguais e fazia o Elo temporal inverter elite vs B-team no backtest.

    Leak BOUNDED e da mesma classe ja aceita (counters/synergies externos): usa SO o
    opendota_rating (prior de forca do time), NAO o `rating` cheio que carrega Elo local
    de jogos futuros. Conhecido -> opendota_rating; curado (sem oid, od!=1400) -> od;
    desconhecido (sem oid, od==1400) -> 1400 - penalidade.
    """
    import csv as _csv
    from pathlib import Path as _Path
    seed = {}
    p = _Path(base_dir) / "team_ratings.csv"
    if not p.exists():
        return seed
    with p.open("r", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            team = (r.get("team") or "").strip()
            if not team or team in ("Radiant", "Dire"):
                continue
            od = as_float(r.get("opendota_rating"), 1400.0)
            oid = (r.get("opendota_team_id") or "").strip()
            is_unknown = (not oid) and abs(od - 1400.0) < 0.5
            seed[team_key(team)] = (od - UNKNOWN_TEAM_PENALTY) if is_unknown else od
    return seed


_DEFAULT_SEED_CACHE = {"key": None, "seed": {}}


def _default_rating_seed():
    """Prior OpenDota de team_ratings.csv (ao lado deste modulo), cacheado por mtime."""
    from pathlib import Path as _Path
    p = _Path(__file__).resolve().parent / "team_ratings.csv"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if _DEFAULT_SEED_CACHE["key"] != mtime:
        _DEFAULT_SEED_CACHE["seed"] = load_opendota_rating_prior(p.parent)
        _DEFAULT_SEED_CACHE["key"] = mtime
    return _DEFAULT_SEED_CACHE["seed"]


def initial_temporal_state(seed_ratings=None):
    # seed_ratings: None = auto-carrega o prior OpenDota (padrao, consistente em todos os
    # callers); False = sem seed (cold-start 1400 antigo, opt-out); dict = usa o passado.
    if seed_ratings is None:
        seed_ratings = _default_rating_seed()
    elif seed_ratings is False:
        seed_ratings = {}
    return {
        "prior_matches": 0,
        "hero_stats": {},
        "player_hero_stats": {},
        "player_form": {},
        "team_ratings": {},
        # Seed externo (opendota prior penalizado). Usado como fallback inicial do Elo
        # temporal — ver update_team_ratings/context_for_match. {} = cold-start antigo.
        "_rating_seed": dict(seed_ratings) if seed_ratings else {},
        "team_form": {},   # {team_key: [1,0,1,...]}  ultimos N resultados
        "head_to_head": {},
        "pair_stats": {},  # {(hero_a, hero_b) sorted: {"games": n, "wins": n}}
        "counter_stats": {},  # {(own_hero, enemy_hero): {"games": n, "wins": n}}
    }


TEAM_FORM_WINDOW = 10  # ultimas 10 partidas pra forma recente


def update_team_form_state(state, radiant_team, dire_team, radiant_win):
    """Mantem lista rolling de resultados (W=1, L=0) por time, janela de 10 jogos."""
    rad_key = team_key(radiant_team)
    dir_key = team_key(dire_team)
    state["team_form"].setdefault(rad_key, [])
    state["team_form"].setdefault(dir_key, [])
    state["team_form"][rad_key].append(1 if radiant_win else 0)
    state["team_form"][dir_key].append(0 if radiant_win else 1)
    state["team_form"][rad_key] = state["team_form"][rad_key][-TEAM_FORM_WINDOW:]
    state["team_form"][dir_key] = state["team_form"][dir_key][-TEAM_FORM_WINDOW:]


def temporal_team_form(state):
    """Retorna {team_key: winrate_pct} apenas pra times com >=2 jogos no estado."""
    out = {}
    for tk, results in state.get("team_form", {}).items():
        if not results or len(results) < 2:
            continue
        out[tk] = round(sum(results) / len(results) * 100, 2)
    return out


def temporal_team_form_games(state):
    return {tk: len(results) for tk, results in state.get("team_form", {}).items() if results}


def clamp(value, low, high):
    return max(low, min(high, value))


def temporal_hero_stats(base_context, state):
    prior_matches = state["prior_matches"]
    rows = {}
    for hero, base_stats in base_context.get("hero_stats", {}).items():
        observed = state["hero_stats"].get(hero, {"picks": 0, "bans": 0, "wins": 0})
        picks = observed["picks"]
        bans = observed["bans"]
        wins = observed["wins"]
        pro_pickrate = picks / prior_matches * 100 if prior_matches else 0.0
        pro_banrate = bans / prior_matches * 100 if prior_matches else 0.0
        pro_winrate = wins / picks * 100 if picks else 50.0
        rows[hero] = {
            **base_stats,
            "pro_pickrate": pro_pickrate,
            "pro_banrate": pro_banrate,
            "pro_winrate": pro_winrate,
            "comfort": 0.0,
        }
    return rows


def temporal_player_hero_stats(state):
    rows = {}
    for key, value in state["player_hero_stats"].items():
        games = value["games"]
        wins = value["wins"]
        winrate = wins / games * 100 if games else 50.0
        rows[key] = {
            "games": games,
            "winrate": winrate,
            "comfort": clamp((winrate - 50) / 10, -1.5, 1.5),
        }
    return rows


def temporal_player_form(state):
    rows = {}
    for player, values in state["player_form"].items():
        rows[player] = sum(values) / len(values) * 100 if values else 50.0
    return rows


def temporal_head_to_head(state):
    rows = {}
    for key, values in state["head_to_head"].items():
        rows[key] = sum(values) / len(values) * 100 if values else 50.0
    return rows


def temporal_learned_synergies(state):
    """Deriva learned_synergies do pair_stats atual (mesma fórmula do update_synergies.py).

    Smoothed = (wins - 0.5*games) / (games + SMOOTHING_PRIOR) * 10, clamp em [-2, 2].
    Pares com games < MIN_GAMES ficam fora (sem sinal confiável).
    """
    rows = {}
    for pair, stats in state["pair_stats"].items():
        games = stats["games"]
        if games < PAIR_MIN_GAMES:
            continue
        wins = stats["wins"]
        smoothed = (wins - 0.5 * games) / (games + PAIR_SMOOTHING_PRIOR) * 10
        rows[pair] = clamp(smoothed, -SYNERGY_CAP, SYNERGY_CAP)
    return rows


def temporal_learned_synergy_games(state):
    rows = {}
    for pair, stats in state["pair_stats"].items():
        games = stats["games"]
        if games >= PAIR_MIN_GAMES:
            rows[pair] = games
    return rows


def temporal_matchup_counters(state):
    rows = {}
    for pair, stats in state["counter_stats"].items():
        games = stats["games"]
        if games <= 0:
            continue
        wins = stats["wins"]
        smoothed = (wins - 0.5 * games) / (games + COUNTER_SMOOTHING_PRIOR) * 20
        rows[pair] = clamp(smoothed, -COUNTER_CAP, COUNTER_CAP)
    return rows


def temporal_matchup_games(state):
    return {pair: stats["games"] for pair, stats in state["counter_stats"].items() if stats["games"] > 0}


def blend_synergy_priors(local_scores, local_games, external_scores, external_games):
    """Combina sinergia temporal local com prior externo fraco.

    O prior externo entra como empurrao pequeno, sem substituir o historico
    temporal. Conforme o par ganha jogos locais, o prior perde peso.
    """
    blended = dict(local_scores)
    for pair, ext_score in (external_scores or {}).items():
        ext_games = (external_games or {}).get(pair, 0)
        ext_conf = min(1.0, ext_games / PAIR_PRIOR_FULL_GAMES) if ext_games else 0.0
        local_conf = min(1.0, local_games.get(pair, 0) / PAIR_LOCAL_FULL_GAMES)
        prior = ext_score * PAIR_PRIOR_WEIGHT * ext_conf * (1.0 - local_conf)
        if abs(prior) < 0.001 and pair not in blended:
            continue
        blended[pair] = clamp(blended.get(pair, 0.0) + prior, -SYNERGY_CAP, SYNERGY_CAP)
    return blended


def blend_counter_priors(local_scores, local_games, external_scores, external_games):
    """Combina counters temporais locais com prior externo fraco.

    O effective_games deixa o prior passar pelo smoothing de sample size em
    features.counter_bonus, mas com peso baixo.
    """
    blended = dict(local_scores)
    games = dict(local_games)
    for pair, ext_score in (external_scores or {}).items():
        ext_games = (external_games or {}).get(pair, 0)
        ext_conf = min(1.0, ext_games / COUNTER_PRIOR_FULL_GAMES) if ext_games else 0.0
        local_conf = min(1.0, games.get(pair, 0) / COUNTER_LOCAL_FULL_GAMES)
        prior = ext_score * COUNTER_PRIOR_WEIGHT * ext_conf * (1.0 - local_conf)
        if abs(prior) < 0.001 and pair not in blended:
            continue
        blended[pair] = clamp(blended.get(pair, 0.0) + prior, -COUNTER_CAP, COUNTER_CAP)
        if games.get(pair, 0) < COUNTER_PRIOR_EFFECTIVE_GAMES:
            games[pair] = COUNTER_PRIOR_EFFECTIVE_GAMES
    return blended, games


def temporal_patch_meta(hero_stats):
    rows = {}
    for hero, stats in hero_stats.items():
        pro_contest = stats["pro_pickrate"] + stats["pro_banrate"]
        rows[hero] = (
            (stats["patch_wr"] - 50) * 0.45
            + (stats["immortal_wr"] - 50) * 0.20
            + (stats["pro_winrate"] - 50) * 0.25
            + (pro_contest - 10) * 0.025
        )
    return rows


def context_for_match(base_context, state, include_external_comfort=False):
    hero_stats = temporal_hero_stats(base_context, state)
    player_stats = temporal_player_hero_stats(state)
    synergy_scores = temporal_learned_synergies(state)
    synergy_games = temporal_learned_synergy_games(state)
    matchup_scores = temporal_matchup_counters(state)
    matchup_games = temporal_matchup_games(state)
    synergy_scores = blend_synergy_priors(
        synergy_scores,
        synergy_games,
        base_context.get("external_synergies", {}),
        base_context.get("external_synergy_games", {}),
    )
    matchup_scores, matchup_games = blend_counter_priors(
        matchup_scores,
        matchup_games,
        base_context.get("external_matchup_counters", {}),
        base_context.get("external_matchup_games", {}),
    )
    player_layers = {
        ("pro_local", player, hero): stats
        for (player, hero), stats in player_stats.items()
    }
    if include_external_comfort:
        # Reintroduz o prior externo de 365d (player_hero_external.csv -> layer
        # "general_365d") que normalmente e descartado no modo temporal. Esse
        # snapshot e ATUAL: valido no LIVE (a partida e agora, sem leakage), mas
        # NAO deve entrar no backtest historico (vazaria o futuro). Por isso fica
        # atras de uma flag que so o build_temporal_context do live ativa.
        # O player_hero_comfort ja blenda essa layer com peso menor (quality 1.0
        # vs 1.5 do pro_local) + smoothing por sample size, entao entra como prior
        # fraco — resgata jogadores cuja base LOCAL esta incompleta sem dominar.
        for key, stats in base_context.get("player_hero_layers", {}).items():
            if key[0] == "general_365d":
                player_layers[key] = stats
    # RATING: no LIVE (include_external_comfort=True) usa o team_ratings.csv
    # estatico (OpenDota + Elo local + penalidade de desconhecido). Validado em
    # 2369 partidas: +2.5pp em qualifiers, premium intocado, calibracao melhor no
    # topo — o Elo temporal cold-start (todos comecam 1400, so jogos do dataset)
    # cotava no-names como favoritos contra elite. A partida live e' "agora",
    # entao o snapshot atual NAO vaza futuro. No BACKTEST mantem o Elo temporal
    # (leak-free): usar rating estatico em partida historica reintroduziria o
    # Leakage #2 (team_ratings pos-jogo) removido de proposito. Times ausentes do
    # CSV caem no Elo temporal como fallback. Ver team_ratings.csv.bak_pre_penalty.
    # Seed (opendota prior) como BASE: cobre time que ainda nao jogou no walk (state
    # so tem quem ja apareceu). Elo temporal acumulado sobrescreve o seed.
    seed = state.get("_rating_seed", {})
    if include_external_comfort:
        # LIVE: estatico de team_ratings.csv ainda vence no topo (path validado +2pp).
        team_ratings_field = {**seed, **dict(state["team_ratings"]), **base_context.get("team_ratings", {})}
    else:
        team_ratings_field = {**seed, **dict(state["team_ratings"])}
    return {
        **base_context,
        "hero_stats": hero_stats,
        "team_ratings": team_ratings_field,
        "player_hero_stats": player_stats,
        "player_hero_layers": player_layers,
        "player_hero_team_stats": {},
        "matchup_counters": matchup_scores,
        "matchup_games": matchup_games,
        "player_form": temporal_player_form(state),
        "head_to_head": temporal_head_to_head(state),
        "learned_synergies": synergy_scores,
        "learned_synergy_games": synergy_games,
        "team_form": temporal_team_form(state),  # antes vazio: agora computado igual h2h
        "team_form_games": temporal_team_form_games(state),
        "patch_meta": temporal_patch_meta(hero_stats),
    }


# K ADAPTATIVO (experimento 17/07, caso Vici/Yandex — ascensao lenta com K fixo):
# rastreia a "surpresa" acumulada por time (resultado − esperado, com decay 0.85).
# Time surpreendendo consistentemente (transformacao real) tem K ampliado ate
# ~(1+GAIN*1.5)x — move rapido; time estavel mantem K base. GAIN=0 desliga (default).
ELO_ADAPTIVE_GAIN = float(os.getenv("ELO_ADAPTIVE_GAIN", "0"))


def _adaptive_k(state, team_key_, k_factor, result_minus_expected):
    if ELO_ADAPTIVE_GAIN <= 0:
        return k_factor
    surp = state.setdefault("elo_surprise", {})
    s = surp.get(team_key_, 0.0)
    k_eff = k_factor * (1.0 + ELO_ADAPTIVE_GAIN * min(abs(s), 1.5))
    surp[team_key_] = 0.85 * s + result_minus_expected
    return k_eff


def update_team_ratings(state, radiant_team, dire_team, radiant_win, k_factor=32):
    radiant_key = team_key(radiant_team)
    dire_key = team_key(dire_team)
    seed = state.get("_rating_seed", {})
    # Primeira aparicao do time no walk: parte do prior OpenDota (penalizado), nao 1400.
    radiant_rating = state["team_ratings"].get(radiant_key)
    if radiant_rating is None:
        radiant_rating = seed.get(radiant_key, 1400.0)
    dire_rating = state["team_ratings"].get(dire_key)
    if dire_rating is None:
        dire_rating = seed.get(dire_key, 1400.0)
    expected_radiant = 1 / (1 + 10 ** ((dire_rating - radiant_rating) / 400))
    score_radiant = 1.0 if radiant_win else 0.0
    delta_rad = score_radiant - expected_radiant
    k_rad = _adaptive_k(state, radiant_key, k_factor, delta_rad)
    k_dire = _adaptive_k(state, dire_key, k_factor, -delta_rad)
    state["team_ratings"][radiant_key] = radiant_rating + k_rad * delta_rad
    state["team_ratings"][dire_key] = dire_rating + k_dire * (-delta_rad)


def update_hero_state(state, picks, bans, won):
    for hero in picks:
        stats = state["hero_stats"].setdefault(hero, {"picks": 0, "bans": 0, "wins": 0})
        stats["picks"] += 1
        stats["wins"] += 1 if won else 0
    for hero in bans:
        stats = state["hero_stats"].setdefault(hero, {"picks": 0, "bans": 0, "wins": 0})
        stats["bans"] += 1


def update_player_state(state, picks, players, won):
    for hero, player in zip(picks, players):
        if not player:
            continue
        key = (player.strip().lower(), hero)
        stats = state["player_hero_stats"].setdefault(key, {"games": 0, "wins": 0})
        stats["games"] += 1
        stats["wins"] += 1 if won else 0
        player_key = player.strip().lower()
        state["player_form"].setdefault(player_key, [])
        state["player_form"][player_key].append(1 if won else 0)
        state["player_form"][player_key] = state["player_form"][player_key][-10:]


def update_pair_state(state, picks, won, ts=None):
    """Incrementa games/wins de cada par (hero_a, hero_b) do mesmo time (com decay)."""
    unique_picks = sorted(set(picks))
    if len(unique_picks) < 2:
        return
    for pair in combinations(unique_picks, 2):
        stats = state["pair_stats"].setdefault(pair, {"games": 0, "wins": 0})
        _decayed_add(stats, won, ts)


def update_counter_state(state, own_picks, enemy_picks, won, ts=None):
    for own in own_picks:
        for enemy in enemy_picks:
            stats = state["counter_stats"].setdefault((own, enemy), {"games": 0, "wins": 0})
            _decayed_add(stats, won, ts)


def update_head_to_head_state(state, radiant_team, dire_team, radiant_win):
    radiant_key = team_key(radiant_team)
    dire_key = team_key(dire_team)
    state["head_to_head"].setdefault((radiant_key, dire_key), [])
    state["head_to_head"].setdefault((dire_key, radiant_key), [])
    state["head_to_head"][(radiant_key, dire_key)].append(1 if radiant_win else 0)
    state["head_to_head"][(dire_key, radiant_key)].append(0 if radiant_win else 1)
    state["head_to_head"][(radiant_key, dire_key)] = state["head_to_head"][(radiant_key, dire_key)][-8:]
    state["head_to_head"][(dire_key, radiant_key)] = state["head_to_head"][(dire_key, radiant_key)][-8:]


def update_temporal_state(state, match):
    radiant_team = match.get("radiant_team", "Radiant")
    dire_team = match.get("dire_team", "Dire")
    radiant_win = int(as_float(match.get("radiant_win"), 0)) == 1
    match_ts = as_float(match.get("start_time"), 0) or None
    radiant_picks = split_heroes(match.get("radiant_picks", ""))
    dire_picks = split_heroes(match.get("dire_picks", ""))
    radiant_bans = split_heroes(match.get("radiant_bans", ""))
    dire_bans = split_heroes(match.get("dire_bans", ""))
    radiant_players = split_players(match.get("radiant_players", ""))
    dire_players = split_players(match.get("dire_players", ""))

    update_team_ratings(state, radiant_team, dire_team, radiant_win)
    update_head_to_head_state(state, radiant_team, dire_team, radiant_win)
    update_team_form_state(state, radiant_team, dire_team, radiant_win)
    update_hero_state(state, radiant_picks, radiant_bans, radiant_win)
    update_hero_state(state, dire_picks, dire_bans, not radiant_win)
    update_player_state(state, radiant_picks, radiant_players, radiant_win)
    update_player_state(state, dire_picks, dire_players, not radiant_win)
    update_pair_state(state, radiant_picks, radiant_win, match_ts)
    update_pair_state(state, dire_picks, not radiant_win, match_ts)
    update_counter_state(state, radiant_picks, dire_picks, radiant_win, match_ts)
    update_counter_state(state, dire_picks, radiant_picks, not radiant_win, match_ts)
    state["prior_matches"] += 1
