"""Busca partidas ao vivo via STRATZ GraphQL.

STRATZ tem menos delay que OpenDota para draft ao vivo.

Pre-requisitos:
1. Crie conta em https://stratz.com/api e gere um JWT.
2. Adicione em config.local.json:
       "stratz_api_key": "SEU_JWT_AQUI"

Uso CLI:
    python fetch_stratz_live.py
    python fetch_stratz_live.py --tier1-only

Como modulo:
    from fetch_stratz_live import fetch_stratz_live_matches
    matches = fetch_stratz_live_matches(api_key, tier1_only=True)

Retorna a mesma estrutura de fetch_live_league_games (Steam Live API):
    {
      "match_id", "league_id", "league_name", "league_tier",
      "radiant_team", "dire_team",
      "radiant_picks", "dire_picks",
      "radiant_bans", "dire_bans",
      "radiant_players", "dire_players",
    }
"""
import argparse
import json
from pathlib import Path

import requests

from utils import normalize_hero_name


BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.local.json"
HERO_ID_CACHE_FILE = BASE_DIR / "hero_id_map.cache.json"

STRATZ_GRAPHQL = "https://api.stratz.com/graphql"

LIVE_MATCHES_QUERY = """
query LiveMatches {
  live {
    matches(request: {isLeague: true, take: 50}) {
      matchId
      leagueId
      gameState
      gameMinute
      league {
        displayName
        tier
      }
      radiantTeam {
        name
      }
      direTeam {
        name
      }
      players {
        steamAccount {
          name
          id
        }
        heroId
        isRadiant
      }
      playbackData {
        pickBans {
          isPick
          heroId
          bannedHeroId
          isRadiant
          order
        }
      }
    }
  }
}
"""


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def load_hero_id_map():
    """Carrega cache de hero_id -> nome canonico. Reaproveita o cache do app principal."""
    if not HERO_ID_CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(HERO_ID_CACHE_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return {int(hero_id): normalize_hero_name(name) for hero_id, name in raw.items()}


def fetch_stratz_live_matches(api_key, tier1_only=True, tier1_leagues=None, timeout=30):
    """Busca matches ao vivo na STRATZ. Retorna lista de dicts no shape do app."""
    if not api_key:
        raise ValueError("STRATZ api_key vazia. Configure stratz_api_key em config.local.json.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API",
    }
    response = requests.post(
        STRATZ_GRAPHQL,
        json={"query": LIVE_MATCHES_QUERY},
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()

    if "errors" in payload:
        raise RuntimeError(f"STRATZ GraphQL erro: {payload['errors']}")

    raw_matches = ((payload.get("data") or {}).get("live") or {}).get("matches", []) or []
    hero_names = load_hero_id_map()
    tier1_leagues = tier1_leagues or {}

    matches = []
    for raw in raw_matches:
        match = _convert_stratz_match(raw, hero_names)
        if tier1_only and not _is_tier1(match, tier1_leagues):
            continue
        if match["radiant_picks"] or match["dire_picks"]:
            matches.append(match)
    return matches


def _convert_stratz_match(raw, hero_names):
    """STRATZ traz draft em playbackData.pickBans (com ordem + flag pick/ban).
    Fallback: usa players[].heroId quando playbackData ainda nao chegou."""
    radiant_picks, dire_picks = [], []
    radiant_bans, dire_bans = [], []
    radiant_player_by_hero = {}
    dire_player_by_hero = {}

    # 1. Tenta pickBans do playbackData (fonte primaria, em ordem do draft)
    pickbans = ((raw.get("playbackData") or {}).get("pickBans")) or []
    pickbans_sorted = sorted(pickbans, key=lambda pb: pb.get("order") or 0)
    for pb in pickbans_sorted:
        is_pick = bool(pb.get("isPick"))
        is_radiant = bool(pb.get("isRadiant"))
        hero_id = pb.get("heroId") if is_pick else pb.get("bannedHeroId")
        if not hero_id:
            continue
        hero_name = normalize_hero_name(hero_names.get(int(hero_id), ""))
        if not hero_name:
            continue
        if is_pick and is_radiant:
            radiant_picks.append(hero_name)
        elif is_pick and not is_radiant:
            dire_picks.append(hero_name)
        elif not is_pick and is_radiant:
            radiant_bans.append(hero_name)
        else:
            dire_bans.append(hero_name)

    # 2. Players + fallback de picks pelo heroId atual
    players = raw.get("players") or []
    for player in players:
        hero_id = player.get("heroId") or 0
        hero_name = normalize_hero_name(hero_names.get(int(hero_id), "")) if hero_id else ""
        is_radiant = bool(player.get("isRadiant"))
        steam_account = player.get("steamAccount") or {}
        display_name = steam_account.get("name") or f"account_{steam_account.get('id', '')}"

        if hero_name and is_radiant and hero_name not in radiant_picks:
            radiant_picks.append(hero_name)
        elif hero_name and not is_radiant and hero_name not in dire_picks:
            dire_picks.append(hero_name)

        if hero_name:
            target = radiant_player_by_hero if is_radiant else dire_player_by_hero
            target[hero_name] = display_name

    league = raw.get("league") or {}
    radiant_team_obj = raw.get("radiantTeam") or {}
    dire_team_obj = raw.get("direTeam") or {}

    def players_in_pick_order(picks, player_by_hero):
        return [player_by_hero.get(hero, "") for hero in picks if player_by_hero.get(hero)]

    return {
        "match_id": str(raw.get("matchId") or ""),
        "start_time": "",
        "league_id": str(raw.get("leagueId") or ""),
        "league_name": league.get("displayName", "") or "",
        "league_tier": str(league.get("tier", "") or "").lower(),
        "radiant_team": radiant_team_obj.get("name") or "Radiant",
        "dire_team": dire_team_obj.get("name") or "Dire",
        "radiant_picks": "|".join(radiant_picks[:5]),
        "dire_picks": "|".join(dire_picks[:5]),
        "radiant_bans": "|".join(radiant_bans[:7]),
        "dire_bans": "|".join(dire_bans[:7]),
        "radiant_players": "|".join(players_in_pick_order(radiant_picks[:5], radiant_player_by_hero)),
        "dire_players": "|".join(players_in_pick_order(dire_picks[:5], dire_player_by_hero)),
    }


def _is_tier1(match, tier1_leagues):
    league_id = str(match.get("league_id", ""))
    league_tier = str(match.get("league_tier", "")).lower()
    return league_id in tier1_leagues or league_tier in {"premium", "professional", "dpc_qualifier"}


def main():
    parser = argparse.ArgumentParser(description="Busca partidas ao vivo via STRATZ GraphQL.")
    parser.add_argument("--tier1-only", action="store_true", help="Filtra apenas torneios T1.")
    parser.add_argument("--limit", type=int, default=20, help="Maximo de matches a imprimir.")
    args = parser.parse_args()

    config = load_config()
    api_key = config.get("stratz_api_key", "").strip()
    if not api_key:
        print("ERRO: stratz_api_key nao configurada em config.local.json.")
        print("Crie conta em https://stratz.com/api e adicione o JWT como 'stratz_api_key'.")
        return

    try:
        matches = fetch_stratz_live_matches(api_key, tier1_only=args.tier1_only)
    except requests.RequestException as error:
        print(f"Erro de rede STRATZ: {error}")
        return
    except (RuntimeError, ValueError) as error:
        print(f"Erro STRATZ: {error}")
        return

    print(f"Partidas ao vivo encontradas: {len(matches)}")
    for match in matches[:args.limit]:
        print(
            f"  {match['match_id']} | {match['radiant_team']} vs {match['dire_team']} | "
            f"R:{len(match['radiant_picks'].split('|')) if match['radiant_picks'] else 0}/5 "
            f"D:{len(match['dire_picks'].split('|')) if match['dire_picks'] else 0}/5 | "
            f"liga={match['league_name'] or match['league_id']}"
        )


if __name__ == "__main__":
    main()
