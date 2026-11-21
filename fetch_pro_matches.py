import argparse
import csv
import time
from pathlib import Path

import requests

from utils import opendota_get_json, OPENDOTA_BASE


BASE_DIR = Path(__file__).parent
HERO_STATS_FILE = BASE_DIR / "hero_stats.csv"
PRO_MATCHES_FILE = BASE_DIR / "pro_matches.csv"
TIER1_LEAGUES_FILE = BASE_DIR / "tier1_leagues.csv"

REQUEST_DELAY_SECONDS = 1.2
FIELDNAMES = [
    "match_id",
    "start_time",
    "patch",
    "leagueid",
    "radiant_team_id",
    "dire_team_id",
    "radiant_team",
    "dire_team",
    "radiant_win",
    "radiant_picks",
    "dire_picks",
    "radiant_bans",
    "dire_bans",
    "radiant_players",
    "dire_players",
    "duration",  # segundos (de /matches/{id}). Habilita analise de late-game (29/06 add).
]
HERO_ALIASES = {
    "Ring Master": "Ringmaster",
    "Outworld Devourer": "Outworld Destroyer",
}


def get_json(url):
    """Wrapper compat. Usa opendota_get_json se for OpenDota, requests direto se nao."""
    if url.startswith(OPENDOTA_BASE) or url.startswith("/"):
        # path absoluto ou OpenDota URL completa
        path = url[len(OPENDOTA_BASE):] if url.startswith(OPENDOTA_BASE) else url
        data = opendota_get_json(path)
        if isinstance(data, dict) and data.get("error") == "rate_limit":
            raise requests.HTTPError(f"OpenDota rate limit (429): adicione opendota_api_key em config.local.json")
        return data
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def load_hero_names():
    heroes = get_json(f"{OPENDOTA_BASE}/constants/heroes")
    return {
        int(hero_id): HERO_ALIASES.get(data["localized_name"], data["localized_name"])
        for hero_id, data in heroes.items()
    }


def load_team_names():
    teams = get_json(f"{OPENDOTA_BASE}/teams")
    names = {}
    for team in teams:
        team_id = team.get("team_id")
        name = (team.get("name") or "").strip()
        if team_id and name:
            names[int(team_id)] = name
    return names


def load_tier1_leagues():
    if not TIER1_LEAGUES_FILE.exists():
        return []
    with TIER1_LEAGUES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        return [
            row.get("league_id", "").strip()
            for row in csv.DictReader(file)
            if row.get("enabled", "1").strip() in {"1", "true", "True", "sim", "yes"}
        ]


def read_existing_rows():
    if not PRO_MATCHES_FILE.exists():
        return []
    with PRO_MATCHES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_existing_match_ids():
    return {row.get("match_id", "") for row in read_existing_rows()}


EXCLUDED_LEAGUES_FILE = BASE_DIR / "excluded_leagues.csv"


def load_excluded_leagues():
    """Ligas T4/baixa-qualidade que NÃO devem entrar no pro_matches.csv.
    Removidas na escrita (auto-limpante) e nunca re-adicionadas."""
    if not EXCLUDED_LEAGUES_FILE.exists():
        return set()
    with EXCLUDED_LEAGUES_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        return {str(r.get("league_id", "")).strip() for r in csv.DictReader(f) if r.get("league_id", "").strip()}


def write_all_rows(rows):
    excluded = load_excluded_leagues()
    deduped = {}
    skipped = 0
    for row in rows:
        match_id = str(row.get("match_id", "")).strip()
        if str(row.get("leagueid", "")).strip() in excluded:
            skipped += 1
            continue  # filtra ligas excluídas (T4) — purga existentes e bloqueia novas
        if match_id:
            deduped[match_id] = {field: row.get(field, "") for field in FIELDNAMES}
    if skipped:
        print(f"  [excluded_leagues] {skipped} partidas de ligas T4 removidas/ignoradas.")

    sorted_rows = sorted(deduped.values(), key=lambda row: int(row.get("start_time") or 0), reverse=True)
    with PRO_MATCHES_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(sorted_rows)


def picks_for_team(picks_bans, team_id, hero_names):
    picks = []
    bans = []
    for item in sorted(picks_bans or [], key=lambda value: value.get("order", 0)):
        hero_name = hero_names.get(item.get("hero_id"))
        if not hero_name or item.get("team") != team_id:
            continue
        if item.get("is_pick"):
            picks.append(hero_name)
        else:
            bans.append(hero_name)
    return picks[:5], bans[:7]


def player_display_name(player):
    name = player.get("name") or player.get("personaname")
    if name:
        return str(name).strip()
    if player.get("account_id"):
        return f"account_{player.get('account_id')}"
    return "Player"


def players_for_side(players, is_radiant, hero_names):
    side_players = []
    for player in players or []:
        if "isRadiant" in player and bool(player.get("isRadiant")) == is_radiant:
            side_players.append(player)
            continue
        slot = player.get("player_slot", 128)
        if is_radiant and slot < 128:
            side_players.append(player)
        elif not is_radiant and slot >= 128:
            side_players.append(player)
    side_players = sorted(side_players, key=lambda player: player.get("player_slot", 0))
    names = []
    heroes = []
    player_by_hero = {}
    for player in side_players[:5]:
        name = player_display_name(player)
        names.append(name)
        hero = hero_names.get(player.get("hero_id"))
        if hero:
            heroes.append(hero)
            player_by_hero[hero] = name
    return names, heroes, player_by_hero


def players_for_picks(picks, player_by_hero):
    return [player_by_hero.get(hero, "") for hero in picks]


def team_name_from_sources(details_value, match_value, team_id, team_names, fallback):
    if details_value:
        return details_value
    if match_value:
        return match_value
    if str(team_id).isdigit():
        return team_names.get(int(team_id), fallback)
    return fallback


def match_to_row(match, details, hero_names, team_names):
    picks_bans = details.get("picks_bans") or []
    radiant_picks, radiant_bans = picks_for_team(picks_bans, 0, hero_names)
    dire_picks, dire_bans = picks_for_team(picks_bans, 1, hero_names)
    radiant_player_names, player_radiant_heroes, radiant_player_by_hero = players_for_side(details.get("players", []), True, hero_names)
    dire_player_names, player_dire_heroes, dire_player_by_hero = players_for_side(details.get("players", []), False, hero_names)

    if len(radiant_picks) != 5 and len(player_radiant_heroes) == 5:
        radiant_picks = player_radiant_heroes
    if len(dire_picks) != 5 and len(player_dire_heroes) == 5:
        dire_picks = player_dire_heroes
    radiant_players = players_for_picks(radiant_picks, radiant_player_by_hero) or radiant_player_names
    dire_players = players_for_picks(dire_picks, dire_player_by_hero) or dire_player_names

    radiant_team_id = details.get("radiant_team_id") or match.get("radiant_team_id") or ""
    dire_team_id = details.get("dire_team_id") or match.get("dire_team_id") or ""
    radiant_team = team_name_from_sources(
        details.get("radiant_name"),
        match.get("radiant_team_name"),
        radiant_team_id,
        team_names,
        "Radiant",
    )
    dire_team = team_name_from_sources(
        details.get("dire_name"),
        match.get("dire_team_name"),
        dire_team_id,
        team_names,
        "Dire",
    )
    patch = details.get("patch") or next((player.get("patch") for player in details.get("players", []) if player.get("patch")), "")

    return {
        "match_id": str(details.get("match_id") or match.get("match_id")),
        "start_time": details.get("start_time") or match.get("start_time") or "",
        "patch": patch or "",
        "leagueid": details.get("leagueid") or match.get("leagueid") or "",
        "radiant_team_id": radiant_team_id,
        "dire_team_id": dire_team_id,
        "radiant_team": radiant_team or "Radiant",
        "dire_team": dire_team or "Dire",
        "radiant_win": 1 if details.get("radiant_win") else 0,
        "radiant_picks": "|".join(radiant_picks),
        "dire_picks": "|".join(dire_picks),
        "radiant_bans": "|".join(radiant_bans),
        "dire_bans": "|".join(dire_bans),
        "radiant_players": "|".join(radiant_players[:5]),
        "dire_players": "|".join(dire_players[:5]),
        "duration": details.get("duration") or "",
    }


def fetch_match_candidates(limit, league_ids, per_league_limit=None, existing_ids=None):
    """Coleta candidatos das ligas T1 + /proMatches.

    IMPORTANTE: filtra partidas JA existentes ANTES do corte [:limit]. Sem isso,
    uma liga antiga iterada primeiro (ex: DreamLeague encerrada) enchia o limite
    com partidas ja salvas e empurrava pra fora as NOVAS de uma liga ativa
    iterada depois (ex: BLAST). Resultado: 'nenhuma partida nova' mesmo havendo.
    """
    existing_ids = existing_ids or set()
    candidates = []
    seen = set()
    league_limit = per_league_limit or limit

    for league_id in league_ids:
        try:
            for row in get_json(f"{OPENDOTA_BASE}/leagues/{league_id}/matches")[:league_limit]:
                match_id = str(row.get("match_id"))
                if match_id not in seen and match_id not in existing_ids:
                    candidates.append(row)
                    seen.add(match_id)
        except requests.RequestException as error:
            print(f"Falha ao buscar liga {league_id}: {error}")

    for row in get_json(f"{OPENDOTA_BASE}/proMatches")[:limit]:
        match_id = str(row.get("match_id"))
        if match_id not in seen and match_id not in existing_ids:
            candidates.append(row)
            seen.add(match_id)

    return candidates[:limit]


def update_hero_stats_from_matches():
    if not PRO_MATCHES_FILE.exists() or not HERO_STATS_FILE.exists():
        return
    hero_rows = []
    with HERO_STATS_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        hero_rows = list(csv.DictReader(file))

    stats = {row["hero"]: {"games": 0, "wins": 0, "picks": 0, "bans": 0} for row in hero_rows}
    total_matches = 0
    for row in read_existing_rows():
        radiant_picks = [hero for hero in row.get("radiant_picks", "").split("|") if hero]
        dire_picks = [hero for hero in row.get("dire_picks", "").split("|") if hero]
        if len(radiant_picks) < 5 or len(dire_picks) < 5:
            continue
        total_matches += 1
        radiant_win = row.get("radiant_win") == "1"
        for hero in radiant_picks:
            if hero in stats:
                stats[hero]["games"] += 1
                stats[hero]["picks"] += 1
                stats[hero]["wins"] += int(radiant_win)
        for hero in dire_picks:
            if hero in stats:
                stats[hero]["games"] += 1
                stats[hero]["picks"] += 1
                stats[hero]["wins"] += int(not radiant_win)
        for hero in [h for h in row.get("radiant_bans", "").split("|") + row.get("dire_bans", "").split("|") if h]:
            if hero in stats:
                stats[hero]["bans"] += 1

    if total_matches == 0:
        return
    updated_rows = []
    for row in hero_rows:
        hero = row["hero"]
        hero_data = stats[hero]
        games = hero_data["games"]
        row["pro_pickrate"] = round(hero_data["picks"] / total_matches * 100, 2)
        row["pro_banrate"] = round(hero_data["bans"] / total_matches * 100, 2)
        row["pro_winrate"] = round(hero_data["wins"] / games * 100, 2) if games else 50.0
        updated_rows.append(row)

    with HERO_STATS_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=updated_rows[0].keys())
        writer.writeheader()
        writer.writerows(updated_rows)


def main():
    parser = argparse.ArgumentParser(description="Baixa partidas profissionais/T1 da OpenDota.")
    parser.add_argument("--limit", type=int, default=120, help="Numero maximo de candidatos para tentar baixar.")
    parser.add_argument("--per-league-limit", type=int, default=0, help="Numero maximo de partidas por liga T1 antes do limite geral.")
    parser.add_argument("--all-leagues", action="store_true", help="Ignora tier1_leagues.csv e usa apenas /proMatches.")
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Rebaixa partidas ja salvas para reparar draft, patch e players alinhados por hero_id.",
    )
    args = parser.parse_args()

    hero_names = load_hero_names()
    team_names = load_team_names()
    existing_rows = read_existing_rows()
    existing_ids = {row.get("match_id", "") for row in existing_rows}
    league_ids = [] if args.all_leagues else load_tier1_leagues()
    if args.refresh_existing:
        candidates = existing_rows[:args.limit]
    else:
        candidates = fetch_match_candidates(args.limit, league_ids, args.per_league_limit or None, existing_ids=existing_ids)

    rows = []
    for match in candidates:
        match_id = str(match["match_id"])
        if match_id in existing_ids and not args.refresh_existing:
            continue
        print(f"Baixando match {match_id}...")
        try:
            details = get_json(f"{OPENDOTA_BASE}/matches/{match_id}")
        except requests.RequestException as error:
            print(f"Ignorando {match_id}: erro {error}.")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        row = match_to_row(match, details, hero_names, team_names)
        if len(row["radiant_picks"].split("|")) != 5 or len(row["dire_picks"].split("|")) != 5:
            print(f"Ignorando {match_id}: draft incompleto.")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        rows.append(row)
        existing_ids.add(match_id)
        time.sleep(REQUEST_DELAY_SECONDS)

    write_all_rows(existing_rows + rows)
    if rows:
        update_hero_stats_from_matches()
        action = "Reparei" if args.refresh_existing else "Salvei"
        print(f"{action} {len(rows)} partidas em pro_matches.csv.")
        print("Atualizei schema com patch e players quando disponiveis.")
    else:
        print("Nenhuma partida nova salva.")


if __name__ == "__main__":
    main()
