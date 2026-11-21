import csv
import math
from pathlib import Path

import requests

from utils import canonical_team_name


BASE_DIR = Path(__file__).parent
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
TEAM_RATINGS_FILE = BASE_DIR / "team_ratings.csv"
OPENDOTA_TEAMS_URL = "https://api.opendota.com/api/teams"

# Times sem rating OpenDota e fora de CURATED_BASELINES caem no default 1400
# (ver base_rating). Esse default ficava ACIMA do rating real de varios tops
# (OpenDota comprime: NaVi ~1327, Spirit ~1399), entao no-names eram cotados
# favoritos contra times de elite — invertendo previsoes em qualifiers.
# Penalidade no rating FINAL desses desconhecidos. Validado em backtest (2369
# partidas): +2.5pp em qualifiers, premium intocado (todos tem OpenDota), e
# calibracao no topo melhora (60-70%: 73->76%, 70+: 87->90%). Penalidade=200
# captura o ganho sem ser agressiva (satura ~250). Ver memoria do projeto.
UNKNOWN_TEAM_PENALTY = 200.0

# Cap da inflacao do Elo local acima da base OpenDota (assimetrico, so pra cima).
# Tier-2 que farma times fracos localmente subia demais (Estar Backs 1234->1400,
# Nemiga 1098->1478). Queda e' livre.
#
# DINAMICO (15/07): cap fixo de +100 segurava ascensao GENUINA de time novo com
# muito jogo local — caso Team Yandex: 91% WR/30d, 4-0 H2H vs Spirit, mas rating
# travado em base+100 (nota "cap local +100") enquanto Spirit (Elo vitalicio
# OpenDota alto) ficava acima. Agora o teto cresce com o volume local:
#   cap(team) = min(BASE + PER_GAME * local_games, MAX)
# Time com 5 jogos: +110 (protecao ~igual); 42 jogos (Yandex): +184; satura +250
# (mesmo teto da nota historica "satura ~250" do UNKNOWN_TEAM_PENALTY).
LOCAL_ELO_UP_CAP = 100.0
LOCAL_ELO_UP_CAP_PER_GAME = 2.0
LOCAL_ELO_UP_CAP_MAX = 250.0

CURATED_BASELINES = {
    "Team Falcons": 1810,
    "Team Liquid": 1790,
    "Team Spirit": 1785,
    "PARIVISION": 1765,
    "Team Yandex": 1760,  # top-tier atual ao lado de PARIVISION (user 15/07): 91% WR/30d, H2H 4-0 vs Spirit; OpenDota subestima (org nova, base ~1516)
    "REKONIX": 1490,  # meio termo (user 16/07): DLTV #17 mundial, mas nao top — base OpenDota 1168 subestimava demais
    "ZEDI Esports": 1480,  # =_PowerRangers/Poor Rangers. meio termo (user 16/07): DLTV #20; base OpenDota ~1130 subestimava
    "BetBoom Team": 1755,
    "Gaimin Gladiators": 1745,
    "Tundra Esports": 1735,
    "Xtreme Gaming": 1725,
    "Virtus.pro": 1690,
    "Vici Gaming": 1680,
    "GamerLegion": 1660,
    "ex-HEROIC": 1660,
    "LGD Gaming": 1700,  # = ex-HEROIC (mesmo time renomeado); 2o lugar em tier-1 recente eliminando tier-1 (user 22/06). OpenDota subestima (base ~1430)
    "Nigma Galaxy": 1655,
    "Aurora": 1650,
    "Aurora Gaming": 1650,
    "Team Tidebound": 1645,
    "PlayTime": 1630,  # tier-1 SA (TI qualifier); OpenDota subestima (base ~1336)
    "Shopify Rebellion": 1640,
    "Natus Vincere": 1635,
    "Yakutou Brothers": 1630,  # grafia nos dados (=Yakult Brothers); 3-2 vs Vici Gaming justifica tier-1 (antes era "Yakult Brothers", nome morto sem match)
    "OG": 1625,
    "Ninjas in Pyjamas": 1620,
    "MOUZ": 1450,  # decaiu muito do top tier-1 (user 22/06); era 1615
    "BOOM Esports": 1610,
    "HEROIC": 1605,
}

TEAM_ALIASES = {
    "BB Team": "BetBoom Team",
    "BetBoom": "BetBoom Team",
    "TSpirit": "Team Spirit",
    "Liquid": "Team Liquid",
    "FLCN": "Team Falcons",
    "PV": "PARIVISION",
    "XG": "Xtreme Gaming",
    "VG": "Vici Gaming",
    "NAVI": "Natus Vincere",
    "GG": "Gaimin Gladiators",
}


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def expected_score(rating_a, rating_b):
    return 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))


def load_matches():
    if not PRO_MATCHES_FILE.exists():
        return []
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def normalize_name(name):
    return (
        (name or "")
        .lower()
        .replace("team ", "")
        .replace("esports", "")
        .replace("-", " ")
        .replace("_", " ")
        .replace(".", " ")
        .strip()
    )


def fetch_opendota_teams():
    from utils import opendota_get_json
    teams = opendota_get_json("/teams", timeout=30)
    if isinstance(teams, dict) and teams.get("error") == "rate_limit":
        raise SystemExit("OpenDota rate limit (429). Configure opendota_api_key em config.local.json.")
    by_name = {}
    for team in teams:
        name = (team.get("name") or "").strip()
        tag = (team.get("tag") or "").strip()
        rating = as_float(team.get("rating"), 0)
        if not name or rating <= 0:
            continue
        team_info = {
            "team_id": team.get("team_id", ""),
            "name": name,
            "tag": tag,
            "opendota_rating": rating,
            "wins": int(as_float(team.get("wins"), 0)),
            "losses": int(as_float(team.get("losses"), 0)),
            "last_match_time": team.get("last_match_time", ""),
        }
        for key in {normalize_name(name), normalize_name(tag)}:
            if not key:
                continue
            existing = by_name.get(key)
            if not existing or team_info["opendota_rating"] > existing["opendota_rating"]:
                by_name[key] = team_info
    return by_name


def find_opendota_team(team_name, opendota_teams):
    canonical_name = TEAM_ALIASES.get(team_name, team_name)
    key = normalize_name(canonical_name)
    if key in opendota_teams:
        return opendota_teams[key]
    for candidate_key, info in opendota_teams.items():
        if key and len(key) >= 6 and len(candidate_key) >= 6 and (key in candidate_key or candidate_key in key):
            return info
    return None


def base_rating(team_name, opendota_teams):
    # Base = max(OpenDota, curado). A Elo da OpenDota comprime os tops (NaVi ~1327,
    # Spirit ~1399); os CURATED_BASELINES sao um tier-list manual mais fiel pra elite
    # (NaVi 1635, Spirit 1785). max() da' separacao correta aos tops sem rebaixar quem
    # a OpenDota ja' avalia alto. O Elo local (k=28) ainda ajusta por cima — corrige
    # parcialmente baseline curado que envelheceu (time que decaiu perde Elo local).
    opendota_team = find_opendota_team(team_name, opendota_teams)
    curated = CURATED_BASELINES.get(team_name)
    if opendota_team:
        od = opendota_team["opendota_rating"]
        return (max(od, curated) if curated is not None else od), opendota_team
    if curated is not None:
        return curated, None
    return 1400.0, None


def main():
    ratings = {}
    games = {}
    k_factor = 28
    opendota_teams = fetch_opendota_teams()
    opendota_info_by_team = {}

    matches = sorted(load_matches(), key=lambda row: as_float(row.get("start_time"), 0))
    if not matches:
        raise SystemExit("pro_matches.csv esta vazio ou nao existe.")

    for match in matches:
        # FIX 16/07: canonicaliza ANTES de agregar. Sem isso, aliases fragmentavam
        # o Elo local ("1w" separado de "Tundra Esports" — 13 jogos bons do EWC nao
        # contavam; "_PowerRangers"/"Poor Rangers" separados de "ZEDI Esports").
        # O consumidor (features.team_key) ja canonicalizava — producer nao. Alias
        # novo em team_aliases.csv passa a consolidar historico automaticamente.
        radiant = canonical_team_name(match.get("radiant_team", "").strip()) or "Radiant"
        dire = canonical_team_name(match.get("dire_team", "").strip()) or "Dire"
        radiant_base, radiant_opendota = base_rating(radiant, opendota_teams)
        dire_base, dire_opendota = base_rating(dire, opendota_teams)
        ratings.setdefault(radiant, float(radiant_base))
        ratings.setdefault(dire, float(dire_base))
        opendota_info_by_team.setdefault(radiant, radiant_opendota)
        opendota_info_by_team.setdefault(dire, dire_opendota)
        games[radiant] = games.get(radiant, 0) + 1
        games[dire] = games.get(dire, 0) + 1

        radiant_win = 1.0 if int(as_float(match.get("radiant_win"), 0)) == 1 else 0.0
        dire_win = 1.0 - radiant_win
        radiant_expected = expected_score(ratings[radiant], ratings[dire])
        dire_expected = 1.0 - radiant_expected

        ratings[radiant] += k_factor * (radiant_win - radiant_expected)
        ratings[dire] += k_factor * (dire_win - dire_expected)

    base_by_team = {team: base_rating(team, opendota_teams)[0] for team in ratings}

    def is_unknown(team):
        # Time sem match na OpenDota e fora dos baselines curados: pegou default 1400.
        return opendota_info_by_team.get(team) is None and team not in CURATED_BASELINES

    def team_up_cap(team):
        # Cap dinamico: cresce com o volume de jogos locais (ascensao com muita
        # evidencia e' mais crivel), satura em LOCAL_ELO_UP_CAP_MAX.
        return min(LOCAL_ELO_UP_CAP + LOCAL_ELO_UP_CAP_PER_GAME * games.get(team, 0),
                   LOCAL_ELO_UP_CAP_MAX)

    def final_rating(team, rating):
        # Cap ASSIMETRICO da inflacao do Elo local: o time nao pode subir mais que
        # +team_up_cap(team) acima da base OpenDota (evita tier-2 que farma fracos
        # localmente parecer tier-1 — ex.: Estar Backs base 1234 inflava pra 1400).
        # Pra baixo e' livre (time em queda DEVE cair). Penalidade de desconhecido
        # aplicada por cima.
        capped = min(rating, base_by_team[team] + team_up_cap(team))
        return capped - UNKNOWN_TEAM_PENALTY if is_unknown(team) else capped

    def was_capped(team, rating):
        return rating > base_by_team[team] + team_up_cap(team)

    rows = [
        {
            "team": team,
            "rating": round(final_rating(team, rating), 2),
            "opendota_rating": round(base_by_team[team], 2),
            "local_games": games.get(team, 0),
            "opendota_team_id": (opendota_info_by_team.get(team) or {}).get("team_id", ""),
            "opendota_wins": (opendota_info_by_team.get(team) or {}).get("wins", ""),
            "opendota_losses": (opendota_info_by_team.get(team) or {}).get("losses", ""),
            "last_match_time": (opendota_info_by_team.get(team) or {}).get("last_match_time", ""),
            "notes": (
                f"OpenDota rating + ajuste Elo local por {games.get(team, 0)} partidas"
                + (f" | cap local +{team_up_cap(team):.0f} (dinamico)" if was_capped(team, rating) else "")
                + (f" | -{UNKNOWN_TEAM_PENALTY:.0f} penalidade desconhecido" if is_unknown(team) else "")
            ),
        }
        for team, rating in sorted(
            ratings.items(),
            key=lambda item: final_rating(item[0], item[1]),
            reverse=True,
        )
    ]
    rows.extend(
        [
            {
                "team": "Radiant",
                "rating": 1400,
                "opendota_rating": "",
                "local_games": "",
                "opendota_team_id": "",
                "opendota_wins": "",
                "opendota_losses": "",
                "last_match_time": "",
                "notes": "fallback",
            },
            {
                "team": "Dire",
                "rating": 1400,
                "opendota_rating": "",
                "local_games": "",
                "opendota_team_id": "",
                "opendota_wins": "",
                "opendota_losses": "",
                "last_match_time": "",
                "notes": "fallback",
            },
        ]
    )

    with TEAM_RATINGS_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "team",
                "rating",
                "opendota_rating",
                "local_games",
                "opendota_team_id",
                "opendota_wins",
                "opendota_losses",
                "last_match_time",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Atualizei {TEAM_RATINGS_FILE} com {len(rows)} times.")
    print("Fonte principal: https://api.opendota.com/api/teams")


if __name__ == "__main__":
    main()
