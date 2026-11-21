import csv
import math
from pathlib import Path

from utils import HERO_ALIASES, as_float, canonical_team_name, normalize_hero_name, sigmoid, team_key


# Rating de time DESCONHECIDO (nao esta em team_ratings.csv). Distribuicao real
# (838 times): min=1050, mediana=1187, max=1831. O antigo fallback 1400 ficava no
# PERCENTIL 96 -> um time novo/sem historico (ex.: Aion, 2 jogos, 2 derrotas) era
# tratado como mais forte que 96% dos times, gerando rating_diff POSITIVO absurdo.
# 1050 = fundo da escala (mais fraco conhecido): sem dados => nao ganha rating.
UNKNOWN_TEAM_RATING = 1050


FEATURE_NAMES = [
    "hero_meta_diff",
    "player_comfort_diff",
    "role_fit_diff",
    "draft_balance_diff",
    "lane_matchup_diff",
    "synergy_diff",
    "counter_diff",
    "pre_match_block",
    "patch_meta_diff",
    # Adicionados em 31/05 — antes estavam embolados no pre_match_block,
    # agora cada um tem seu proprio coef (rating_diff=0.20, etc).
    # Sem isso, predict_probability ignorava esses coefs do model_coefficients.csv.
    "rating_diff",
    "player_form_diff",
    "recent_form_diff",
    "head_to_head_diff",
    # Adicionado 05/07: dias-parado (radiant-dire). Time descansado overperforma o modelo
    # (adaptacao entre torneios). Ver _layoff_diff_value.
    "layoff_diff",
]

MODEL_PROBABILITY_FLOOR = 20.0
MODEL_PROBABILITY_CEILING = 80.0
PRE_MATCH_BLOCK_CAP_PP = 3.0
PRE_MATCH_BLOCK_CAP_LOGIT = PRE_MATCH_BLOCK_CAP_PP / 25.0
PRE_MATCH_COMPONENT_WEIGHTS = {
    "rating_diff": 0.050,
    "head_to_head_diff": 0.010,
    "recent_form_diff": 0.050,
    "player_form_diff": 0.060,
}

HERO_ROLES_FILE = Path(__file__).parent / "hero_roles.csv"
PLAYER_ALIASES_FILE = Path(__file__).parent / "player_aliases.csv"

FEATURE_DESCRIPTIONS = {
    "hero_meta_diff": {
        "fonte": "hero_stats.csv",
        "updater": "update_hero_stats.py",
        "descricao": "Diferenca de meta dos 5 picks usando patch WR, immortal WR, pro WR, contest rate e comfort do heroi.",
    },
    "player_comfort_diff": {
        "fonte": "player_hero_external.csv + player_hero_stats.csv + player_hero_team_stats.csv",
        "updater": "update_player_hero_year_stats.py / update_player_hero_stats.py",
        "descricao": "Diferenca de comfort especifico dos jogadores nos herois escolhidos, com reducao por amostra pequena.",
    },
    "role_fit_diff": {
        "fonte": "player_roles.csv + tags dos herois",
        "updater": "update_player_roles.py",
        "descricao": "Diferenca de encaixe entre funcao do player e arquetipo do heroi.",
    },
    "draft_balance_diff": {
        "fonte": "tags dos herois",
        "updater": "manual no features.py",
        "descricao": "Diferenca de equilibrio do draft: controle, teamfight, catch, push, Roshan, save e scaling.",
    },
    "lane_matchup_diff": {
        "fonte": "player_roles.csv + counters temporais + hero_stats.csv",
        "updater": "update_player_roles.py / pro_matches.csv",
        "descricao": "Diferenca estimada das lanes usando role dos players e counters temporais com prior externo fraco.",
    },
    "synergy_diff": {
        "fonte": "historico temporal + learned_synergies_stratz.csv fraco",
        "updater": "pro_matches.csv / update_synergies.py",
        "descricao": "Diferenca de sinergias entre pares de herois do mesmo time usando apenas partidas anteriores e prior externo reduzido.",
    },
    "counter_diff": {
        "fonte": "historico temporal + OpenDota/Stratz fraco",
        "updater": "pro_matches.csv / update_matchups.py",
        "descricao": "Diferenca de counters hero vs hero usando apenas partidas anteriores, com prior externo reduzido e smoothing por amostra.",
    },
    "pre_match_block": {
        "fonte": "team_ratings.csv + head_to_head.csv + team_form.csv + player_form.csv",
        "updater": "update_team_ratings.py / update_head_to_head.py / update_team_form.py / update_player_form.py",
        "descricao": "Bloco pre-match unico: rating, confronto direto, forma do time e forma dos jogadores, capado em +/-3pp para nao travar underdogs com draft bom.",
    },
    "patch_meta_diff": {
        "fonte": "patch_meta.csv (fallback: hero_stats.csv)",
        "updater": "update_patch_meta.py",
        "descricao": "Diferenca de qualidade dos herois no patch atual usando score derivado de winrate publico + immortal.",
    },
    # Adicionadas em 31/05/26: antes embolavam dentro do pre_match_block (cap +/-3pp).
    # Hoje pre_match_block=0 e estas 4 tem coef proprio no model_coefficients.csv.
    "rating_diff": {
        "fonte": "team_ratings.csv",
        "updater": "update_team_ratings.py",
        "descricao": "Diferenca de rating do time (Elo OpenDota), normalizada e capada em +/- 1.5.",
    },
    "player_form_diff": {
        "fonte": "player_form.csv",
        "updater": "update_player_form.py",
        "descricao": "Diferenca de winrate recente dos jogadores escalados (janela ~30 partidas), pesada por confianca.",
    },
    "recent_form_diff": {
        "fonte": "team_form.csv",
        "updater": "update_team_form.py",
        "descricao": "Diferenca de winrate recente do time (janela ~10 partidas), com Bayesian smoothing (prior 50%/5 jogos).",
    },
    "head_to_head_diff": {
        "fonte": "head_to_head.csv",
        "updater": "update_head_to_head.py",
        "descricao": "Diferenca de winrate em confrontos diretos entre os dois times. Coef=0 hoje (suspeita de vies por times repetidos).",
    },
}

HERO_TAGS = {
    "Ancient Apparition": {"anti_heal", "teamfight"},
    "Axe": {"stun", "catch", "teamfight"},
    "Bane": {"disable", "save"},
    "Batrider": {"catch", "tempo"},
    "Beastmaster": {"push", "vision", "tempo"},
    "Chen": {"push", "save", "tempo"},
    "Clockwerk": {"catch", "stun"},
    "Dawnbreaker": {"teamfight", "save", "tempo"},
    "Dazzle": {"save", "sustain"},
    "Disruptor": {"catch", "teamfight"},
    "Doom": {"disable", "catch"},
    "Earthshaker": {"stun", "teamfight", "waveclear"},
    "Elder Titan": {"teamfight", "scaling"},
    "Enigma": {"teamfight", "push"},
    "Faceless Void": {"teamfight", "scaling"},
    "Grimstroke": {"teamfight", "disable"},
    "Hoodwink": {"catch", "burst"},
    "Io": {"save", "sustain"},
    "Keeper of the Light": {"waveclear", "tempo"},
    "Kez": {"burst"},  # scaling removido 03/07: dado (n_late 37) diz que NAO escala (-1.9)
    "Leshrac": {"push", "roshan", "tempo"},
    "Lich": {"teamfight", "save"},
    "Luna": {"push", "roshan"},  # scaling removido 03/07: dado (n_late 22) diz -2.9 (cai no late)
    "Magnus": {"teamfight", "scaling"},
    "Mars": {"teamfight", "stun"},
    "Medusa": {"scaling", "teamfight"},
    "Muerta": {"burst", "scaling"},
    "Naga Siren": {"scaling", "waveclear"},
    "Nature's Prophet": {"push", "tempo"},
    "Pangolier": {"teamfight", "stun"},
    "Phantom Lancer": {"scaling"},
    "Phoenix": {"teamfight", "sustain"},
    "Puck": {"catch", "teamfight", "waveclear"},
    "Rubick": {"disable", "teamfight"},
    "Sand King": {"teamfight", "stun", "waveclear"},
    "Shadow Demon": {"save", "disable"},
    "Shadow Shaman": {"disable", "push"},
    "Silencer": {"teamfight", "disable"},
    "Storm Spirit": {"catch", "burst", "tempo"},
    "Sven": {"stun", "scaling"},
    "Techies": {"burst", "waveclear"},
    "Templar Assassin": {"roshan", "tempo"},
    "Terrorblade": {"push", "roshan", "scaling"},
    "Tidehunter": {"teamfight", "stun"},
    "Tiny": {"stun", "burst", "push"},
    "Treant Protector": {"teamfight", "save"},
    "Tusk": {"save", "catch", "stun"},
    "Underlord": {"waveclear", "teamfight"},
    "Ursa": {"roshan", "burst"},
    "Vengeful Spirit": {"save", "stun"},
    "Viper": {"tempo", "anti_heal"},
    "Warlock": {"teamfight", "sustain"},
    "Windranger": {"catch", "burst"},
    "Winter Wyvern": {"save", "teamfight"},
    "Witch Doctor": {"sustain", "teamfight"},
    "Zeus": {"burst", "vision"},
    # Scaling adicionado 03/07 via analyze_lategame.py — carries genuinos confirmados
    # pelo dado (WR_late-WR_early). Supports com delta alto (Lina/AA/Lich/Ogre) NAO
    # entram: viés de selecao (jogo foi longo => utility persistiu, nao "escala").
    "Spectre": {"scaling"},           # +22.0 (n_late 14)
    "Lifestealer": {"scaling"},       # +20.9 (n_late 35)
    "Phantom Assassin": {"scaling"},  # +25.7 (n_late 6)
    "Weaver": {"scaling"},            # +23.8 (n_late 6)
}

# ─── Scaling DATA-DRIVEN (03/07, decisao do user) ────────────────────────────
# Alem do seed manual acima, adiciona 'scaling' a QUALQUER heroi com delta de
# late-game alto medido em hero_lategame.csv (analyze_lategame.py) — inclusive
# supports (user: incluir todos com delta alto, nao so carries). Auto-atualiza
# conforme o backfill de duracao cresce (mesmo padrao dos red/green flags).
# Ressalva: supports com delta alto tem vies de selecao (jogo longo => utility
# persiste); mas e' a escolha do user. Fallback: sem CSV, fica so o seed manual.
SCALING_DELTA_MIN = 15.0
SCALING_MIN_LATE_GAMES = 10


def _augment_scaling_from_data():
    path = Path(__file__).resolve().parent / "hero_lategame.csv"
    if not path.exists():
        return 0
    added = 0
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("reliable") != "1":
                    continue
                try:
                    delta = float(r.get("scaling_delta") or 0)
                    nlate = int(r.get("n_late") or 0)
                except (ValueError, TypeError):
                    continue
                if delta >= SCALING_DELTA_MIN and nlate >= SCALING_MIN_LATE_GAMES:
                    tags = HERO_TAGS.setdefault(r["hero"], set())
                    if "scaling" not in tags:
                        tags.add("scaling")
                        added += 1
    except Exception:
        pass
    return added


_augment_scaling_from_data()

ROLE_ARCHETYPES = {
    "carry": {"scaling", "roshan", "push", "burst"},
    "mid": {"tempo", "burst", "waveclear", "catch"},
    "offlane": {"teamfight", "stun", "catch", "disable"},
    "support": {"save", "disable", "catch", "teamfight", "vision"},
    "hard support": {"save", "sustain", "disable", "teamfight", "vision"},
}

DRAFT_PROFILE_WEIGHTS = {
    # "teamfight" NEUTRALIZADO 04/07 (peso era 1.1, O MAIOR): arquetipo MORTO — corr
    # -0.036 c/ vitoria, coef data-driven -0.046. Zerar melhora o composto (Δlog loss
    # -0.0022 out-of-sample). Tag hand cobria so 25 herois e nao previa nada. Versao
    # data-driven via performance (WR em jogos high-kill) exigiria backfill de kills e,
    # pelo padrao de redundancia (counter_diff/synergy_diff ja capturam draft), provavel
    # que nao pague. Penalidade "sem teamfight" tambem removida abaixo.
    # Pesos RE-CALIBRADOS 04/07 (data-driven: corr univariada c/ vitoria + coef
    # logistico, holdout temporal). catch = maior sinal (+0.091) -> subiu p/ 1.5;
    # tempo/save positivos; stun MORTO (+0.014) -> 0.1; roshan (-0.045) e burst (~0)
    # neutralizados -> 0. Δlog loss do composto -0.0027 vs pesos antigos a mao. E'
    # escala RELATIVA (o coef final do draft_balance_diff absorve a magnitude).
    "stun": 0.1,
    "disable": 0.5,
    "catch": 1.5,
    "push": 0.4,
    "roshan": 0.0,
    # "scaling" REMOVIDO 04/07: sinal medido NEGATIVO neste meta pro (corr -0.18 c/
    # vitoria, coef data-driven -0.295). Recompensar comp de scaling calibrava o
    # modelo AO CONTRARIO. Confirmado por 3 estudos independentes (lategame feature,
    # green-flags fadeiam no late, decomposicao dos arquetipos). A tag 'scaling' ainda
    # vive p/ role_fit; so saiu do draft_balance (+ removida a penalidade abaixo).
    "save": 0.6,
    "waveclear": 0.3,
    "burst": 0.0,
    "tempo": 0.6,
}

SYNERGIES = {
    ("Faceless Void", "Phoenix"): 2.8,
    ("Faceless Void", "Invoker"): 2.2,
    ("Magnus", "Sven"): 2.0,
    ("Magnus", "Phantom Assassin"): 2.2,
    ("Tiny", "Io"): 2.4,
    ("Luna", "Shadow Demon"): 2.1,
    ("Naga Siren", "Disruptor"): 1.8,
    ("Mars", "Phoenix"): 1.7,
    ("Dawnbreaker", "Phoenix"): 1.8,
    ("Dark Seer", "Spirit Breaker"): 1.7,
    ("Grimstroke", "Doom"): 1.6,
    ("Grimstroke", "Lion"): 1.5,
}

MANUAL_COUNTERS = {
    ("Ancient Apparition", "Huskar"): 2.5,
    ("Ancient Apparition", "Alchemist"): 2.0,
    ("Shadow Demon", "Luna"): 1.8,
    ("Shadow Demon", "Terrorblade"): 1.8,
    ("Doom", "Storm Spirit"): 2.0,
    ("Doom", "Puck"): 1.8,
    ("Silencer", "Enigma"): 2.3,
    ("Rubick", "Enigma"): 1.8,
    ("Nyx Assassin", "Storm Spirit"): 1.6,
    ("Bloodseeker", "Pangolier"): 2.0,
    ("Axe", "Phantom Lancer"): 1.7,
    ("Earthshaker", "Phantom Lancer"): 2.3,
    ("Underlord", "Phantom Lancer"): 1.5,
}


def split_heroes(value):
    return [normalize_hero_name(hero) for hero in (value or "").split("|") if hero]


def split_players(value):
    return [player for player in (value or "").split("|") if player]


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_player_aliases(base_dir=None):
    path = Path(base_dir) / "player_aliases.csv" if base_dir else PLAYER_ALIASES_FILE
    aliases = {}
    for row in read_csv(path):
        alias = (row.get("alias") or "").strip().lower()
        player = (row.get("player") or "").strip()
        if alias and player:
            aliases[alias] = player
    return aliases


def canonical_player_name(player, aliases=None):
    name = (player or "").strip()
    if not name:
        return ""
    alias_map = aliases if aliases is not None else load_player_aliases()
    return alias_map.get(name.lower(), name)


def player_key(player, aliases=None):
    return canonical_player_name(player, aliases).strip().lower()


def load_hero_roles(base_dir):
    roles = {}
    base_dir = Path(base_dir)
    role_file = base_dir / "hero_roles.csv"
    if not role_file.exists():
        role_file = HERO_ROLES_FILE
    if not role_file.exists():
        return roles
    with role_file.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            hero = normalize_hero_name(row.get("hero", ""))
            primary = (row.get("primary_role") or "").strip().lower()
            secondary = (row.get("secondary_role") or "").strip().lower()
            if hero:
                roles[hero] = {
                    "primary_role": primary,
                    "secondary_role": secondary,
                    "source": (row.get("source") or "").strip(),
                }
    return roles


def load_feature_context(base_dir):
    base_dir = Path(base_dir)
    player_aliases = load_player_aliases(base_dir)
    hero_stats = {}
    for row in read_csv(base_dir / "hero_stats.csv"):
        hero = normalize_hero_name(row.get("hero", ""))
        if hero:
            hero_stats[hero] = {
                "patch_wr": as_float(row.get("patch_wr"), 50),
                "immortal_wr": as_float(row.get("immortal_wr"), 50),
                "pro_pickrate": as_float(row.get("pro_pickrate"), 0),
                "pro_banrate": as_float(row.get("pro_banrate"), 0),
                "pro_winrate": as_float(row.get("pro_winrate"), 50),
                "comfort": as_float(row.get("comfort"), 0),
            }

    team_ratings = {}
    for row in read_csv(base_dir / "team_ratings.csv"):
        team = row.get("team", "").strip()
        if team:
            team_ratings[team_key(team)] = as_float(row.get("rating"), UNKNOWN_TEAM_RATING)

    player_roles = {}
    for row in read_csv(base_dir / "player_roles.csv"):
        player = player_key(row.get("player", ""), player_aliases)
        role = row.get("role", "").strip().lower()
        if player and role:
            player_roles[player] = role

    player_hero_stats = {}
    player_hero_layers = {}
    for source_file, layer in [("player_hero_external.csv", "general_365d"), ("player_hero_stats.csv", "pro_local")]:
        for row in read_csv(base_dir / source_file):
            player = player_key(row.get("player", ""), player_aliases)
            hero = normalize_hero_name(row.get("hero", ""))
            if player and hero:
                stats = {
                    "games": as_float(row.get("games"), 0),
                    "winrate": as_float(row.get("winrate"), 50),
                    "comfort": as_float(row.get("comfort"), 0),
                }
                player_hero_layers[(layer, player, hero)] = stats
                if (player, hero) not in player_hero_stats or stats["games"] >= player_hero_stats[(player, hero)].get("games", 0):
                    player_hero_stats[(player, hero)] = stats

    player_hero_team_stats = {}
    for row in read_csv(base_dir / "player_hero_team_stats.csv"):
        team = team_key(row.get("team", ""))
        player = player_key(row.get("player", ""), player_aliases)
        hero = normalize_hero_name(row.get("hero", ""))
        if team and player and hero:
            player_hero_team_stats[(team, player, hero)] = {
                "games": as_float(row.get("games"), 0),
                "winrate": as_float(row.get("winrate"), 50),
                "comfort": as_float(row.get("comfort"), 0),
            }

    matchup_counters = {}
    matchup_games = {}  # (own,enemy) -> games_played (pra confidence smoothing)
    for row in read_csv(base_dir / "hero_matchups.csv"):
        own = normalize_hero_name(row.get("own_hero", ""))
        enemy = normalize_hero_name(row.get("enemy_hero", ""))
        if own and enemy:
            matchup_counters[(own, enemy)] = as_float(row.get("counter_score"), 0)
            matchup_games[(own, enemy)] = int(as_float(row.get("games_played"), 0))

    external_matchup_counters = {}
    external_matchup_games = {}
    for source_file, source_weight in [
        ("hero_matchups_opendota_cache.csv", 1.0),
        ("hero_counters_stratz.csv", 0.65),
    ]:
        for row in read_csv(base_dir / source_file):
            own = normalize_hero_name(row.get("own_hero", ""))
            enemy = normalize_hero_name(row.get("enemy_hero", ""))
            if not own or not enemy:
                continue
            games = int(as_float(row.get("games_played"), 0))
            score = as_float(row.get("counter_score"), 0) * source_weight
            key = (own, enemy)
            if key not in external_matchup_games or games > external_matchup_games[key]:
                external_matchup_counters[key] = score
                external_matchup_games[key] = games

    learned_synergies = {}
    for row in read_csv(base_dir / "learned_synergies.csv"):
        hero_a = normalize_hero_name(row.get("hero_a", ""))
        hero_b = normalize_hero_name(row.get("hero_b", ""))
        if hero_a and hero_b:
            learned_synergies[tuple(sorted((hero_a, hero_b)))] = as_float(row.get("synergy_score"), 0)

    external_synergies = {}
    external_synergy_games = {}
    for row in read_csv(base_dir / "learned_synergies_stratz.csv"):
        hero_a = normalize_hero_name(row.get("hero_a", ""))
        hero_b = normalize_hero_name(row.get("hero_b", ""))
        if not hero_a or not hero_b:
            continue
        key = tuple(sorted((hero_a, hero_b)))
        games = int(as_float(row.get("games"), 0))
        score = as_float(row.get("synergy_score"), 0)
        if key not in external_synergy_games or games > external_synergy_games[key]:
            external_synergies[key] = score
            external_synergy_games[key] = games

    team_form = {}
    team_form_games = {}
    for row in read_csv(base_dir / "team_form.csv"):
        team = row.get("team", "").strip()
        if team:
            team_form[team_key(team)] = as_float(row.get("recent_form"), 50)
            team_form_games[team_key(team)] = as_float(row.get("recent_games"), 0)

    player_form = {}
    for row in read_csv(base_dir / "player_form.csv"):
        raw_player = (row.get("player") or "").strip()
        player = player_key(raw_player, player_aliases)
        if player:
            if player in player_form and raw_player.lower() != player:
                continue
            player_form[player] = as_float(row.get("recent_form"), 50)

    head_to_head = {}
    for row in read_csv(base_dir / "head_to_head.csv"):
        team_a = team_key(row.get("team_a", ""))
        team_b = team_key(row.get("team_b", ""))
        if team_a and team_b:
            head_to_head[(team_a, team_b)] = as_float(row.get("team_a_winrate"), 50)
            head_to_head[(team_b, team_a)] = 100 - as_float(row.get("team_a_winrate"), 50)

    patch_meta = {}
    for row in read_csv(base_dir / "patch_meta.csv"):
        hero = normalize_hero_name(row.get("hero", ""))
        if hero:
            patch_meta[hero] = as_float(row.get("patch_meta_score"), 0)

    # LANE-OUTCOME matchups (hero_lane_matchups.csv): quem ganha a LANE (gold@10),
    # sinal ORTOGONAL ao counter de resultado de jogo. Alimenta o lane_unit_score
    # reescrito. Vazio-safe: se o arquivo nao existir, lane_matchup_diff fica neutro
    # ate fetch_lane_outcomes.py preencher a matriz.
    lane_matchups = {}
    lane_matchup_games = {}
    for row in read_csv(base_dir / "hero_lane_matchups.csv"):
        own = normalize_hero_name(row.get("own_hero", ""))
        enemy = normalize_hero_name(row.get("enemy_hero", ""))
        if own and enemy:
            lane_matchups[(own, enemy)] = as_float(row.get("lane_score"), 0.0)
            lane_matchup_games[(own, enemy)] = int(as_float(row.get("games"), 0))

    return {
        "hero_stats": hero_stats,
        "team_ratings": team_ratings,
        "player_roles": player_roles,
        "player_hero_stats": player_hero_stats,
        "player_hero_layers": player_hero_layers,
        "player_hero_team_stats": player_hero_team_stats,
        "matchup_counters": matchup_counters,
        "matchup_games": matchup_games,  # pra confidence smoothing por sample size
        "external_matchup_counters": external_matchup_counters,
        "external_matchup_games": external_matchup_games,
        "learned_synergies": learned_synergies,
        "external_synergies": external_synergies,
        "external_synergy_games": external_synergy_games,
        "team_form": team_form,
        "team_form_games": team_form_games,
        "player_form": player_form,
        "player_aliases": player_aliases,
        "head_to_head": head_to_head,
        "patch_meta": patch_meta,
        "lane_matchups": lane_matchups,
        "lane_matchup_games": lane_matchup_games,
        "hero_roles": load_hero_roles(base_dir),
    }


def decimal_to_probability(odd):
    return 100 / odd if odd and odd > 1 else 0


def pro_win_confidence(stats):
    pickrate = max(0.0, stats.get("pro_pickrate", 0.0))
    return pickrate / (pickrate + 10.0) if pickrate else 0.0


def player_hero_games(player, hero, context, team=""):
    if not player:
        return 0.0
    p_key = player_key(player, context.get("player_aliases", {}))
    games = 0.0
    for layer in ["general_365d", "pro_local"]:
        games += context.get("player_hero_layers", {}).get((layer, p_key, hero), {}).get("games", 0.0)
    if team:
        games += context.get("player_hero_team_stats", {}).get((team_key(team), p_key, hero), {}).get("games", 0.0)
    return games


def hero_meta_components(hero, context):
    stats = context["hero_stats"].get(hero)
    if not stats:
        return {}
    pro_contest = stats["pro_pickrate"] + stats["pro_banrate"]
    # META ENXUTO (22/06): so dados PRO. Ablacao holdout mostrou que patch (WR pub
    # all-ranks) adicionava RUIDO, immortal era neutro e comfort (knob manual 0.50)
    # era REDUNDANTE. Removidos -> +0.7pp acc / log loss melhor + formula 100%
    # data-driven do nosso pro. pro_win (nosso WR pro x confianca por amostra) e o
    # sinal real; contest (pick/ban pro) carrega um pouco. Zerados (nao removidos)
    # pra reversibilidade. Ver features.py.bak_pre_lean_meta_*.
    return {
        "patch": 0.0,         # era (patch_wr-50)*0.20 — pub meta != pro, ruido
        "immortal": 0.0,      # era (immortal_wr-50)*0.10 — neutro
        "pro_win": (stats["pro_winrate"] - 50) * 0.20 * pro_win_confidence(stats),
        "contest": (pro_contest - 10) * 0.015,
        "hero_comfort": 0.0,  # era comfort*0.50 — manual/subjetivo, redundante
    }


def hero_meta_score(hero, context):
    return sum(hero_meta_components(hero, context).values())


def player_pick_score(hero, context, player="", team=""):
    if not player:
        return 0.0
    player_comfort = player_hero_comfort(player, hero, context, team)
    # Decaimento exponencial: 0 games = -0.35, 1 game = -0.25, 3 = -0.13, 10 = -0.013.
    # Substitui o cliff binario "0 vs >=1" por curva mais natural.
    games = player_hero_games(player, hero, context, team)
    familiarity_penalty = -0.35 * math.exp(-games / 3.0)
    return player_comfort * 2.00 + familiarity_penalty


def hero_role_archetypes(hero):
    hero_role = {}
    try:
        hero_role = HERO_ROLES.get(hero, {})
    except NameError:
        hero_role = {}
    primary = (hero_role.get("primary_role") or "").strip()
    secondary = (hero_role.get("secondary_role") or "").strip()
    if primary or secondary:
        roles = [role for role in [primary, secondary] if role]
        return roles or ["flex"]
    tags = HERO_TAGS.get(hero, set())
    scores = []
    for role, role_tags in ROLE_ARCHETYPES.items():
        score = len(tags & role_tags)
        if score:
            scores.append((score, role))
    if not scores:
        return ["flex"]
    scores.sort(reverse=True)
    return [role for _, role in scores[:2]]


COMPATIBLE_ROLES = {
    "support": {"hard support"},
    "hard support": {"support"},
    "carry": {"mid"},
    "mid": {"carry", "offlane"},
    "offlane": {"mid"},
}


def role_fit_value(player, hero, context):
    if not player:
        return 0.0
    role = context.get("player_roles", {}).get(player_key(player, context.get("player_aliases", {})), "")
    if not role:
        return 0.0
    hero_role_info = {}
    try:
        hero_role_info = HERO_ROLES.get(hero, {})
    except NameError:
        hero_role_info = {}
    primary = (hero_role_info.get("primary_role") or "").strip().lower()
    secondary = (hero_role_info.get("secondary_role") or "").strip().lower()
    hero_roles = hero_role_archetypes(hero)
    # Score continuo:
    # primary hit = 0.60, secondary = 0.35, compativel (sup<->hard sup) = 0.10,
    # role aparece em archetypes (caso flex sem primary/secondary CSV) = 0.20,
    # caso contrario penaliza -0.30 (antes -0.40).
    if role == primary:
        return 0.60
    if role == secondary:
        return 0.35
    if role in COMPATIBLE_ROLES.get(primary, set()) or role in COMPATIBLE_ROLES.get(secondary, set()):
        return 0.10
    if role in hero_roles:
        return 0.20
    return -0.30


def hero_score_components(hero, context, player="", team=""):
    meta_components = hero_meta_components(hero, context)
    if not meta_components:
        return {}
    return {
        **meta_components,
        "player_comfort": player_pick_score(hero, context, player, team),
        "role_fit": role_fit_value(player, hero, context),
    }


def hero_score(hero, context, player="", team=""):
    return sum(hero_score_components(hero, context, player, team).values())


def comfort_confidence(games, prior_games):
    games = max(0.0, games)
    return games / (games + prior_games) if games else 0.0


def adjusted_comfort(stats, prior_games):
    games = stats.get("games", 0.0)
    return stats.get("comfort", 0.0) * comfort_confidence(games, prior_games)


def player_hero_comfort(player, hero, context, team=""):
    if not player:
        return 0.0
    p_key = player_key(player, context.get("player_aliases", {}))
    layers = [
        (context.get("player_hero_layers", {}).get(("general_365d", p_key, hero)), 1.0, 60, 12),
        (context.get("player_hero_layers", {}).get(("pro_local", p_key, hero)), 1.5, 25, 8),
    ]
    if team:
        layers.append((context.get("player_hero_team_stats", {}).get((team_key(team), p_key, hero)), 2.0, 15, 6))

    weighted_sum = 0.0
    total_weight = 0.0
    for stats, quality_weight, cap, prior_games in layers:
        if not stats:
            continue
        games = stats.get("games", 0.0)
        confidence = comfort_confidence(games, prior_games)
        sample_weight = min(games, cap) * quality_weight * confidence
        if sample_weight <= 0:
            continue
        weighted_sum += adjusted_comfort(stats, prior_games) * sample_weight
        total_weight += sample_weight
    return round(weighted_sum / total_weight, 3) if total_weight else 0.0


def draft_score(picks, context, players=None, team=""):
    players = players or []
    incomplete_penalty = -2.0 if len(picks) < 5 else 0.0
    return (
        sum(hero_score(hero, context, players[index] if index < len(players) else "", team) for index, hero in enumerate(picks))
        + incomplete_penalty
    )


def hero_meta_total(picks, context):
    return sum(hero_meta_score(hero, context) for hero in picks)


def player_pick_total(picks, players, context, team=""):
    players = players or []
    return sum(
        player_pick_score(hero, context, players[index] if index < len(players) else "", team)
        for index, hero in enumerate(picks)
    )


def role_fit_total(picks, players, context):
    players = players or []
    return sum(
        role_fit_value(players[index] if index < len(players) else "", hero, context)
        for index, hero in enumerate(picks)
    )


def draft_profile(picks):
    profile = {tag: 0 for tag in DRAFT_PROFILE_WEIGHTS}
    for hero in picks:
        for tag in HERO_TAGS.get(hero, set()):
            if tag in profile:
                profile[tag] += 1
    return profile


def draft_balance_score(picks):
    profile = draft_profile(picks)
    if not picks:
        return 0.0
    score = 0.0
    for tag, weight in DRAFT_PROFILE_WEIGHTS.items():
        count = profile.get(tag, 0)
        if count:
            # Beneficio linear ate 3 picks (antes era cap em 2 + penalty -0.25).
            # Permite valorizar composicoes com 3 herois do mesmo tag (ex: 3 teamfight).
            score += min(count, 3) * weight
    # Penalty mais suave (-0.30 por categoria, antes -0.80). Composicoes
    # modernas podem ganhar sem stun explicito; -0.80 era cliff agressivo demais.
    # 'stun' REMOVIDO das penalidades 04/07: arquetipo morto (corr +0.014 c/ vitoria;
    # OpenDota Disabler idem, +0.041) e o hand-list so tem 11 herois -> penalizava
    # drafts cujos stunners nao estao na lista. Δlog loss da remocao = -0.0001 (neutro).
    # 'catch' mantido: e' o MAIOR sinal positivo (+0.091). 'teamfight' fica p/ a passada
    # dedicada (performance-based).
    # 'catch' = unica penalidade mantida: e' o MAIOR sinal positivo de draft (+0.091).
    # 'teamfight' e 'stun' removidos (arquetipos mortos, ver acima).
    for required in ["catch"]:
        if profile.get(required, 0) == 0:
            score -= 0.30
    if profile.get("push", 0) == 0 and profile.get("roshan", 0) == 0:
        score -= 0.25
    # Penalidade de "sem scaling" REMOVIDA 04/07 (ver DRAFT_PROFILE_WEIGHTS): comp sem
    # scaling nao e' pior neste meta — o dado diz o contrario. Penalizar era erro de sinal.
    return score


def player_role(player, context):
    if not player:
        return ""
    return context.get("player_roles", {}).get(player_key(player, context.get("player_aliases", {})), "")


def lane_groups(picks, players, context):
    lanes = {"safe": [], "mid": [], "off": [], "support_pool": []}
    players = players or []
    for index, hero in enumerate(picks):
        player = players[index] if index < len(players) else ""
        role = player_role(player, context)
        item = (hero, player)
        if role == "carry":
            lanes["safe"].append(item)
        elif role == "mid":
            lanes["mid"].append(item)
        elif role == "offlane":
            lanes["off"].append(item)
        elif role == "hard support":
            lanes["safe"].append(item)
        elif role == "support":
            lanes["off"].append(item)
        else:
            lanes["support_pool"].append(item)

    for item in lanes["support_pool"]:
        if len(lanes["safe"]) <= len(lanes["off"]):
            lanes["safe"].append(item)
        else:
            lanes["off"].append(item)
    return lanes


# Lane-outcome confidence (hero_lane_matchups.csv). Analogo ao counter, mas por lane.
LANE_CONFIDENCE_FULL = 10   # >= 10 jogos de lane: peso integral
LANE_CONFIDENCE_FLOOR = 3   # < 3: ruido, ignora


def lane_unit_score(lane, enemy_lane, context, team=""):
    """Vantagem de LANE dos herois desta lane contra a lane inimiga, a partir de
    hero_lane_matchups.csv (lane-outcome real = quem ganha a lane em gold@10).

    REESCRITO 08/07: antes re-misturava meta+comfort+counter -> multicolinear com
    features que o modelo JA tem (corr +0.78 c/ counter_diff) -> coef ~0 (morta).
    Agora usa SO o sinal de lane-outcome, ORTOGONAL ao counter de resultado de jogo.
    Confidence smoothing por sample size. Neutro (0) quando o par nao tem dado — o
    feature 'liga' conforme fetch_lane_outcomes.py preenche a matriz.
    """
    if not lane or not enemy_lane:
        return 0.0
    own_heroes = [hero for hero, _ in lane]
    enemy_heroes = [hero for hero, _ in enemy_lane]
    table = context.get("lane_matchups", {})
    games_table = context.get("lane_matchup_games", {})
    total = 0.0
    for own_hero in own_heroes:
        for enemy_hero in enemy_heroes:
            value = table.get((own_hero, enemy_hero))
            if value is None:
                continue
            games = games_table.get((own_hero, enemy_hero), 0)
            if games < LANE_CONFIDENCE_FLOOR:
                continue
            confidence = 1.0 if games >= LANE_CONFIDENCE_FULL else (
                (games - LANE_CONFIDENCE_FLOOR) / (LANE_CONFIDENCE_FULL - LANE_CONFIDENCE_FLOOR))
            total += value * confidence
    return total / (len(enemy_heroes) or 1)


def lane_matchup_score(own_picks, enemy_picks, own_players, enemy_players, context, own_team=""):
    own_lanes = lane_groups(own_picks, own_players, context)
    enemy_lanes = lane_groups(enemy_picks, enemy_players, context)
    safe = lane_unit_score(own_lanes["safe"], enemy_lanes["off"], context, own_team)
    mid = lane_unit_score(own_lanes["mid"], enemy_lanes["mid"], context, own_team)
    off = lane_unit_score(own_lanes["off"], enemy_lanes["safe"], context, own_team)
    return safe + mid + off


PLAYER_FORM_MIN_RECOGNIZED = 3
TEAM_FORM_PRIOR_GAMES = 5.0
TEAM_FORM_PRIOR_WINRATE = 50.0


def is_unresolved_player(player):
    player_key = (player or "").strip().lower()
    return not player_key or player_key.startswith("account_")


def player_form_profile(players, context):
    form_table = context.get("player_form", {})
    aliases = context.get("player_aliases", {})
    clean_players = [player_key(player, aliases) for player in (players or []) if player and not is_unresolved_player(player)]
    values = [form_table[player] for player in clean_players if player in form_table]
    total_slots = len([player for player in (players or []) if player])
    recognized = len(values)
    average = sum(values) / recognized if recognized else 50.0
    coverage = recognized / total_slots if total_slots else 0.0
    if recognized < PLAYER_FORM_MIN_RECOGNIZED:
        confidence = 0.0
    else:
        confidence = min(1.0, coverage)
    return {
        "average": average,
        "recognized": recognized,
        "total": total_slots,
        "coverage": coverage,
        "confidence": confidence,
    }


def average_player_form(players, context):
    return player_form_profile(players, context)["average"]


def player_form_diff_value(radiant_players, dire_players, context):
    radiant = player_form_profile(radiant_players, context)
    dire = player_form_profile(dire_players, context)
    confidence = min(radiant["confidence"], dire["confidence"])
    raw_diff = (radiant["average"] - dire["average"]) / 100
    return raw_diff * confidence


def team_form_profile(team, context, raw_form=None):
    team_form = context.get("team_form", {})
    team_games = context.get("team_form_games", {})
    key = team_key(team)
    raw = raw_form if raw_form is not None else team_form.get(key, 50.0)
    games = float(team_games.get(key, 0.0))
    smoothed = ((raw / 100.0 * games) + (TEAM_FORM_PRIOR_WINRATE / 100.0 * TEAM_FORM_PRIOR_GAMES)) / (
        games + TEAM_FORM_PRIOR_GAMES
    ) * 100.0 if games > 0 else 50.0
    confidence = min(1.0, games / (games + TEAM_FORM_PRIOR_GAMES)) if games > 0 else 0.0
    return {
        "raw": raw,
        "games": games,
        "smoothed": smoothed,
        "confidence": confidence,
    }


def team_form_diff_value(radiant_team, dire_team, context, radiant_form=None, dire_form=None):
    radiant = team_form_profile(radiant_team, context, radiant_form)
    dire = team_form_profile(dire_team, context, dire_form)
    return (radiant["smoothed"] - dire["smoothed"]) / 100


def pre_match_block_value(rating_diff, head_to_head_diff, recent_form_diff, player_form_diff):
    raw = (
        PRE_MATCH_COMPONENT_WEIGHTS["rating_diff"] * rating_diff
        + PRE_MATCH_COMPONENT_WEIGHTS["head_to_head_diff"] * head_to_head_diff
        + PRE_MATCH_COMPONENT_WEIGHTS["recent_form_diff"] * recent_form_diff
        + PRE_MATCH_COMPONENT_WEIGHTS["player_form_diff"] * player_form_diff
    )
    return max(-PRE_MATCH_BLOCK_CAP_LOGIT, min(PRE_MATCH_BLOCK_CAP_LOGIT, raw))


def head_to_head_value(team_a, team_b, context):
    return context.get("head_to_head", {}).get((team_key(team_a), team_key(team_b)), 50.0)


def denied_player_comfort(hero, opponent_players, context, opponent_team=""):
    return max(
        [player_hero_comfort(player, hero, context, opponent_team) for player in (opponent_players or [])] or [0.0]
    )


def ban_score(bans, context, opponent_players=None, opponent_team="", own_picks=None):
    own_picks = own_picks or []
    total = 0.0
    for hero in bans:
        meta_denied = hero_meta_score(hero, context) * 0.25
        comfort_denied = denied_player_comfort(hero, opponent_players, context, opponent_team) * 1.20
        counter_denied = counter_bonus([hero], own_picks, context) * 0.60 if own_picks else 0.0
        total += meta_denied + comfort_denied + counter_denied
    return total


def pair_bonus(picks, context=None):
    picked = set(picks)
    manual = sum(value for (a, b), value in SYNERGIES.items() if a in picked and b in picked)
    learned = 0.0
    learned_synergies = (context or {}).get("learned_synergies", {})
    for index, hero_a in enumerate(picks):
        for hero_b in picks[index + 1:]:
            learned += learned_synergies.get(tuple(sorted((hero_a, hero_b))), 0.0)
    return manual + learned


COUNTER_CONFIDENCE_FULL = 15   # >= 15 jogos: peso integral (tunado em 27/05: 10/15/20 todos
                                # dao 77.8% no 26/05; 25/30 caem pra 66.7%)
COUNTER_CONFIDENCE_FLOOR = 3   # < 3 jogos: ignora (ruido puro)


def counter_bonus(own_picks, enemy_picks, context):
    """Soma de counter scores hero-vs-hero, com SMOOTHING por sample size.

    Cada par hero counter eh pesado por uma confidence factor baseada em quantos
    jogos ele tem no dataset:
      games < 5         : ignora (ruido puro)
      games >= 30       : peso integral (1.0)
      games entre 5-30  : peso proporcional ((games-5) / 25)

    Sem isso, counters com 5-6 jogos viram dados de +1.1 que dominam a decisao
    (foi a causa raiz dos erros 60-72% de favoritos perdendo em 26/05 e 27/05).
    """
    own = set(own_picks)
    enemy = set(enemy_picks)
    manual = sum(value for (counter, target), value in MANUAL_COUNTERS.items() if counter in own and target in enemy)
    matchup_games = context.get("matchup_games", {})
    learned = 0.0
    for own_hero in own_picks:
        for enemy_hero in enemy_picks:
            value = context["matchup_counters"].get((own_hero, enemy_hero))
            if value is None:
                continue
            games = matchup_games.get((own_hero, enemy_hero), 0)
            if games < COUNTER_CONFIDENCE_FLOOR:
                continue  # ruido — descarta
            if games >= COUNTER_CONFIDENCE_FULL:
                confidence = 1.0
            else:
                # Linear de 0 a 1 entre FLOOR e FULL
                confidence = (games - COUNTER_CONFIDENCE_FLOOR) / (COUNTER_CONFIDENCE_FULL - COUNTER_CONFIDENCE_FLOOR)
            learned += value * confidence
    # Divide pelo numero de inimigos (sempre 5 num draft completo).
    # Mantem escala consistente entre full draft, last pick e ban_score.
    enemy_size = len(enemy_picks) or 1
    learned = learned / enemy_size
    return manual + learned


def last_pick_counter_bonus(own_picks, enemy_picks, context):
    if not own_picks or not enemy_picks:
        return 0.0
    return counter_bonus([own_picks[-1]], enemy_picks, context)


def counter_coverage_quality(radiant_picks, dire_picks, context):
    """Avalia quao confiavel sao os counters dessa partida.

    Pra cada par (own, enemy) nos 2 sentidos, classifica:
      - high     : games >= COUNTER_CONFIDENCE_FULL (peso integral)
      - mid      : games entre FLOOR e FULL (peso parcial)
      - low/none : games < FLOOR ou ausente (ignorado)

    Total possivel: 5 * 5 * 2 = 50 interacoes (radiant_counter_dire + dire_counter_radiant).

    Retorna dict {'high': n, 'mid': n, 'low': n, 'total_possible': 50, 'quality_pct': X}.
    quality_pct = (high + 0.5*mid) / total_possible * 100. >= 60% = bom, 30-60% = medio, <30% = ruim.
    """
    counters_table = context.get("matchup_counters", {})
    games_table = context.get("matchup_games", {})
    high = mid = low = 0
    for own_picks, enemy_picks in [(radiant_picks, dire_picks), (dire_picks, radiant_picks)]:
        for own in own_picks:
            for enemy in enemy_picks:
                games = games_table.get((own, enemy), 0)
                if counters_table.get((own, enemy)) is None:
                    low += 1
                elif games >= COUNTER_CONFIDENCE_FULL:
                    high += 1
                elif games >= COUNTER_CONFIDENCE_FLOOR:
                    mid += 1
                else:
                    low += 1
    total_possible = 50
    quality_pct = round((high + 0.5 * mid) / total_possible * 100, 1)
    if quality_pct >= 60:
        label = "Boa"
    elif quality_pct >= 30:
        label = "Media"
    else:
        label = "Baixa"
    return {
        "high": high,
        "mid": mid,
        "low": low,
        "total_possible": total_possible,
        "quality_pct": quality_pct,
        "label": label,
    }


def patch_meta_score(picks, context):
    values = []
    for hero in picks:
        if hero in context.get("patch_meta", {}):
            values.append(context["patch_meta"][hero])
            continue
        stats = context["hero_stats"].get(hero)
        if stats:
            values.append((stats["patch_wr"] - 50) + (stats["immortal_wr"] - 50) * 0.5)
    return sum(values) / len(values) if values else 0.0


def team_rating(team, context, fallback=UNKNOWN_TEAM_RATING):
    return context["team_ratings"].get(team_key(team), fallback)


def _layoff_diff_value(radiant_team, dire_team, context):
    """Feature LAYOFF (05/07): dias-parado (radiant - dire), normalizado /30, cap +/-2.
    + = radiant mais descansado. Time descansado OVERPERFORMA o modelo (adaptacao entre
    torneios; rating/form sao pre-pausa -> subestima). Validado: +0.12 a +0.18 de residuo
    p/ layoff 7+ dias; feature melhora log loss -0.0015 (walk-forward). Neutro se sem dado."""
    tl = (context or {}).get("_team_last_match") or {}
    ts = (context or {}).get("_context_target_start", 0) or 0
    if not tl or not ts:
        return 0.0
    def _lay(team):
        last = tl.get(team_key(team))
        if not last or last >= ts:
            return None
        return (ts - last) / 86400.0
    lr, ld = _lay(radiant_team), _lay(dire_team)
    if lr is None or ld is None:
        return 0.0
    return max(-2.0, min(2.0, (min(lr, 60.0) - min(ld, 60.0)) / 30.0))


def build_feature_dict(
    radiant_team,
    dire_team,
    radiant_picks,
    dire_picks,
    radiant_bans=None,
    dire_bans=None,
    radiant_players=None,
    dire_players=None,
    odd_radiant=0,
    odd_dire=0,
    recent_form_radiant=None,
    recent_form_dire=None,
    context=None,
):
    radiant_bans = radiant_bans or []
    dire_bans = dire_bans or []
    radiant_players = radiant_players or []
    dire_players = dire_players or []
    # Resolve alias -> nome canonico ANTES de qualquer lookup keyed por time
    # (rating, form, layoff, h2h). Sem isso, uma fonte que entrega "PVSION"/"PVSN"
    # nao casa com "PARIVISION" em team_ratings.csv e cai no fallback 1050.
    radiant_team = canonical_team_name(radiant_team)
    dire_team = canonical_team_name(dire_team)
    radiant_rating = team_rating(radiant_team, context)
    dire_rating = team_rating(dire_team, context)
    rating_diff = max(-1.5, min(1.5, (radiant_rating - dire_rating) / 250))
    if recent_form_radiant is None:
        recent_form_radiant = context.get("team_form", {}).get(team_key(radiant_team), 50)
    if recent_form_dire is None:
        recent_form_dire = context.get("team_form", {}).get(team_key(dire_team), 50)
    recent_form_diff = team_form_diff_value(
        radiant_team,
        dire_team,
        context,
        recent_form_radiant,
        recent_form_dire,
    )

    radiant_meta = hero_meta_total(radiant_picks, context)
    dire_meta = hero_meta_total(dire_picks, context)
    radiant_player = player_pick_total(radiant_picks, radiant_players, context, radiant_team)
    dire_player = player_pick_total(dire_picks, dire_players, context, dire_team)
    radiant_role = role_fit_total(radiant_picks, radiant_players, context)
    dire_role = role_fit_total(dire_picks, dire_players, context)
    radiant_balance = draft_balance_score(radiant_picks)
    dire_balance = draft_balance_score(dire_picks)
    radiant_lane = lane_matchup_score(radiant_picks, dire_picks, radiant_players, dire_players, context, radiant_team)
    dire_lane = lane_matchup_score(dire_picks, radiant_picks, dire_players, radiant_players, context, dire_team)
    player_form_diff = player_form_diff_value(radiant_players, dire_players, context)
    head_to_head_diff = (head_to_head_value(radiant_team, dire_team, context) - 50) / 50
    pre_match_block = pre_match_block_value(
        rating_diff,
        head_to_head_diff,
        recent_form_diff,
        player_form_diff,
    )
    layoff_diff = _layoff_diff_value(radiant_team, dire_team, context)

    return {
        "hero_meta_diff": (radiant_meta - dire_meta) / 10,
        "player_comfort_diff": (radiant_player - dire_player) / 5,
        "role_fit_diff": (radiant_role - dire_role) / 5,
        "draft_balance_diff": (radiant_balance - dire_balance) / 10,
        "lane_matchup_diff": (radiant_lane - dire_lane) / 10,
        "synergy_diff": (pair_bonus(radiant_picks, context) - pair_bonus(dire_picks, context)) / 5,
        "counter_diff": (counter_bonus(radiant_picks, dire_picks, context) - counter_bonus(dire_picks, radiant_picks, context)) / 5,
        "pre_match_block": pre_match_block,
        "rating_diff": rating_diff,
        "player_form_diff": player_form_diff,
        "head_to_head_diff": head_to_head_diff,
        "recent_form_diff": recent_form_diff,
        "patch_meta_diff": (patch_meta_score(radiant_picks, context) - patch_meta_score(dire_picks, context)) / 10,
        "layoff_diff": layoff_diff,
    }


def feature_vector(feature_dict):
    return [feature_dict.get(name, 0.0) for name in FEATURE_NAMES]


def predict_probability(model, feature_dict):
    value = model.get("intercept", 0.0)
    for name in FEATURE_NAMES:
        value += model.get(name, 0.0) * feature_dict.get(name, 0.0)
    return max(MODEL_PROBABILITY_FLOOR, min(MODEL_PROBABILITY_CEILING, sigmoid(value) * 100))


def update_recent_form(form_state, team, won, window=10):
    key = team_key(team)
    form_state.setdefault(key, [])
    form_state[key].append(1 if won else 0)
    form_state[key] = form_state[key][-window:]


def recent_form(form_state, team):
    values = form_state.get(team_key(team), [])
    if not values:
        return 50.0
    return sum(values) / len(values) * 100
