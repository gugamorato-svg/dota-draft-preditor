import csv
import json
import math
import time
from pathlib import Path

HERO_ALIASES = {
    "Ring Master": "Ringmaster",
    "Furion": "Nature's Prophet",
    "Outworld Devourer": "Outworld Destroyer",
    "Skeleton King": "Wraith King",
    "Windrunner": "Windranger",
}

HERO_IMAGE_SLUGS = {
    "Anti-Mage": "antimage",
    "Centaur Warrunner": "centaur",
    "Clockwerk": "rattletrap",
    "Doom": "doom_bringer",
    "Dragon Knight": "dragon_knight",
    "Drow Ranger": "drow_ranger",
    "Io": "wisp",
    "Kunkka": "kunkka",
    "Lifestealer": "life_stealer",
    "Magnus": "magnataur",
    "Nature's Prophet": "furion",
    "Necrophos": "necrolyte",
    "Outworld Destroyer": "obsidian_destroyer",
    "Queen of Pain": "queenofpain",
    "Shadow Fiend": "nevermore",
    "Timbersaw": "shredder",
    "Treant Protector": "treant",
    "Underlord": "abyssal_underlord",
    "Vengeful Spirit": "vengefulspirit",
    "Windranger": "windrunner",
    "Wraith King": "skeleton_king",
    "Zeus": "zuus",
}


def hero_image_url(hero):
    slug = HERO_IMAGE_SLUGS.get(hero)
    if not slug:
        slug = (
            hero.lower()
            .replace("'", "")
            .replace("-", "")
            .replace(" ", "_")
        )
    return f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{slug}.png"


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def normalize_hero_name(hero):
    return HERO_ALIASES.get(hero, hero).strip()


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def _team_key_raw(name):
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


def team_key(name):
    """Chave de time p/ indexar Elo/form/h2h. CANONICALIZA via aliases (20/07).

    Bug descoberto na final do EWC: team_key era so normalizacao textual —
    'PVISION' e 'PARIVISION' viravam chaves DIFERENTES e o walk temporal
    fragmentava Elo/form/h2h entre os nomes do mesmo time. Pior: a consolidacao
    do team_ratings.csv (16/07) removeu as linhas nao-canonicas, entao o SEED
    dos nomes alternativos sumiu — os 2 finalistas viraram 'times novos' ~1400
    no walk e o modelo errou a final com 65% no lado errado.
    _team_key_raw fica para o proprio lookup de alias (evita recursao)."""
    return _team_key_raw(canonical_team_name(name))


_TEAM_ALIASES_CACHE = None


def load_team_aliases(base_dir=None):
    """Le team_aliases.csv. Retorna dict {alias_key: canonical_name}.

    Cacheado pra reaproveitar entre chamadas no mesmo processo. Pra recarregar
    apos editar o CSV, passe force_reload=True ou reinicie o processo.
    """
    global _TEAM_ALIASES_CACHE
    if _TEAM_ALIASES_CACHE is not None:
        return _TEAM_ALIASES_CACHE

    if base_dir is None:
        base_dir = Path(__file__).resolve().parent
    csv_path = Path(base_dir) / "team_aliases.csv"
    aliases = {}
    if not csv_path.exists():
        _TEAM_ALIASES_CACHE = aliases
        return aliases

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            alias = _team_key_raw(row.get("alias", ""))
            canonical = (row.get("canonical") or "").strip()
            if alias and canonical:
                aliases[alias] = canonical
    _TEAM_ALIASES_CACHE = aliases
    return aliases


def canonical_team_name(name, aliases=None):
    """Resolve nome de time pra forma canonica (definida em team_aliases.csv).
    Se nao houver alias, retorna o nome original (com strip)."""
    name = str(name or "").strip()
    if not name:
        return ""
    if aliases is None:
        aliases = load_team_aliases()
    return aliases.get(_team_key_raw(name), name)


# ─── OpenDota helper ────────────────────────────────────────────────────────
# Centraliza acesso a OpenDota com API key + retry em 429.
# Tier anonimo: 60 calls/min, 2000 calls/dia. Com key: 50000/dia.
# Cadastro gratuito em https://www.opendota.com/api-keys.

OPENDOTA_BASE = "https://api.opendota.com/api"
_OPENDOTA_KEY_CACHE = None

# Cache local pra endpoints estaveis. Reduz consumo da cota OpenDota (3000/dia tier gratuito).
# Cada entrada: {ttl_seconds: int}. Chamadas que casam o path sao servidas do disco.
OPENDOTA_CACHE_TTL = {
    "/constants/heroes":  7 * 24 * 3600,  # pool de herois muda raramente (patch)
    "/constants/patch":   30 * 24 * 3600, # patches definidos
    "/teams":             24 * 3600,      # rosters mudam mas raramente em 24h
    "/heroStats":         12 * 3600,      # meta muda devagar
}
_OPENDOTA_CACHE_DIR = Path(__file__).resolve().parent / ".opendota_cache"

# Contador de calls/dia. Tier gratuito = 3000/dia. Warn em 70%, error em 90%.
_OPENDOTA_USAGE_FILE = Path(__file__).resolve().parent / "opendota_usage.json"
OPENDOTA_DAILY_LIMIT = 3000


def _opendota_usage_read():
    if not _OPENDOTA_USAGE_FILE.exists():
        return {}
    try:
        return json.loads(_OPENDOTA_USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _opendota_usage_increment():
    """Incrementa contador do dia atual. Loga warning em thresholds."""
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    data = _opendota_usage_read()
    # Mantem apenas ultimos 7 dias
    cutoff = (_dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=7)).isoformat()
    data = {k: v for k, v in data.items() if k >= cutoff}
    prev = int(data.get(today, 0))
    data[today] = prev + 1
    try:
        _OPENDOTA_USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        return
    # Loga em thresholds (uma unica vez por dia por threshold)
    if prev < int(OPENDOTA_DAILY_LIMIT * 0.7) <= data[today]:
        import logging
        logging.getLogger("opendota").warning(
            f"OpenDota uso 70%: {data[today]}/{OPENDOTA_DAILY_LIMIT} calls hoje"
        )
    elif prev < int(OPENDOTA_DAILY_LIMIT * 0.9) <= data[today]:
        import logging
        logging.getLogger("opendota").error(
            f"OpenDota uso 90%: {data[today]}/{OPENDOTA_DAILY_LIMIT} calls hoje. "
            "Iminente bater limite — apos isso, fallback pra cache + skip de updates nao-criticos."
        )


def opendota_usage_today():
    """Retorna (calls_hoje, limite_diario). Util pra dashboard mostrar."""
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    data = _opendota_usage_read()
    return int(data.get(today, 0)), OPENDOTA_DAILY_LIMIT


# ─── Single-instance lock pra workers ──────────────────────────────────────
# Usado por processos de longa duracao pra prevenir empilhamento
# de processos (causa raiz dos 71 pythonw rodando em 2026-05-26).

def is_process_alive(pid) -> bool:
    """Retorna True se um processo com este PID existe e responde.
    Funciona em Windows e Unix. Robusto contra PermissionError
    (processo existe mas pertence a outro usuario)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    import os as _os
    if _os.name == "nt":
        # Windows: usa ctypes pra abrir handle com PROCESS_QUERY_LIMITED_INFORMATION
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        # Checa se processo ainda esta ativo (STILL_ACTIVE = 259)
        exit_code = ctypes.c_ulong(0)
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == 259
    else:
        # Unix: signal 0 nao envia nada, so checa permissao/existencia
        try:
            _os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def _is_our_python_process(pid, script_name) -> bool:
    """True se o PID corresponde a um processo Python rodando o script especifico.
    Defesa contra reuso de PID por outro processo qualquer.
    Em Windows usa WMI/CIM via subprocess (powershell). Em outros sistemas
    retorna True por compat (assume que is_process_alive ja confirmou)."""
    import os as _os
    if _os.name != "nt":
        return True
    try:
        import subprocess as _sp
        cmd = (
            f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" "
            f"-ErrorAction SilentlyContinue; "
            f"if ($p) {{ $p.CommandLine }}"
        )
        r = _sp.run(["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True, text=True, timeout=5)
        out = (r.stdout or "").lower()
        return script_name.lower() in out
    except Exception:
        # Em caso de erro, assume que eh nosso (conservador — nao da false negative)
        return True


def acquire_single_instance_lock(pid_file_path, worker_name="worker", logger=None,
                                  script_name=None):
    """Verifica se ja existe um worker rodando via PID file. Se sim, retorna False.
    Se nao, escreve o PID atual no arquivo e retorna True.

    Args:
        pid_file_path: Path ou string apontando pro PID file desse worker.
        worker_name: nome amigavel pra log (ex.: "coletor").
        logger: logger opcional (logging.Logger). Se omitido, usa print.
        script_name: nome do script (.py) pra validar que o PID antigo eh REALMENTE
                     nosso processo (defesa contra reuso de PID). Se None, pula check.

    Returns:
        True se conseguiu o lock (deve continuar). False se outro processo ja roda
        (chamador deve sys.exit(1)).
    """
    import os as _os
    pid_file = Path(pid_file_path)
    log_func = (logger.error if logger else (lambda m: print(m, flush=True)))
    log_info = (logger.info if logger else (lambda m: print(m, flush=True)))

    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and old_pid != _os.getpid() and is_process_alive(old_pid):
            # Endurece: verifica que o PID antigo eh REALMENTE nosso script
            # (defesa contra reuso de PID por outro processo qualquer).
            if script_name and not _is_our_python_process(old_pid, script_name):
                log_info(
                    f"[{worker_name}] PID file aponta {old_pid} mas processo "
                    f"nao roda {script_name} (PID reusado). Limpando lock orfao."
                )
                try: pid_file.unlink()
                except OSError: pass
            else:
                log_func(
                    f"[{worker_name}] Ja existe instancia rodando (PID {old_pid}). "
                    f"Saindo. Use stop_bot_dashboard.bat pra parar a anterior."
                )
                return False
        else:
            # PID file existe mas processo morreu (orfao) — sobrescreve
            try:
                pid_file.unlink()
            except OSError:
                pass

    try:
        pid_file.write_text(str(_os.getpid()))
    except OSError as e:
        log_func(f"[{worker_name}] Falha ao escrever PID file: {e}")
        # Nao bloqueia inicializacao por falha de I/O do PID file
    return True


def _load_opendota_key():
    """Le opendota_api_key do config.local.json. Cached entre chamadas."""
    global _OPENDOTA_KEY_CACHE
    if _OPENDOTA_KEY_CACHE is not None:
        return _OPENDOTA_KEY_CACHE
    cfg_path = Path(__file__).resolve().parent / "config.local.json"
    if not cfg_path.exists():
        _OPENDOTA_KEY_CACHE = ""
        return ""
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except Exception:
        _OPENDOTA_KEY_CACHE = ""
        return ""
    key = (cfg.get("opendota_api_key") or cfg.get("OPENDOTA_API_KEY") or "").strip()
    _OPENDOTA_KEY_CACHE = key
    return key


def _opendota_cache_path(path):
    """Resolve caminho do arquivo de cache local pra um path da API."""
    safe = path.lstrip("/").replace("/", "_") or "root"
    return _OPENDOTA_CACHE_DIR / f"{safe}.json"


def _opendota_cache_read(path):
    """Le cache local se ainda valido (dentro do TTL). Retorna None se miss."""
    ttl = OPENDOTA_CACHE_TTL.get(path)
    if not ttl:
        return None
    cache_file = _opendota_cache_path(path)
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if time.time() - payload.get("ts", 0) > ttl:
            return None
        return payload.get("data")
    except Exception:
        return None


def _opendota_cache_write(path, data):
    """Persiste resposta no cache local. Silencioso em erros de I/O."""
    if path not in OPENDOTA_CACHE_TTL:
        return
    try:
        _OPENDOTA_CACHE_DIR.mkdir(exist_ok=True)
        cache_file = _opendota_cache_path(path)
        cache_file.write_text(
            json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def opendota_get_json(path_or_url, params=None, timeout=30, max_retries=2,
                     retry_wait=60.0, raise_on_429=False, skip_cache=False):
    """GET em OpenDota com api_key automatica, cache local em endpoints estaveis,
    e retry em 429.

    Args:
        path_or_url: caminho relativo (ex.: "/proMatches") ou URL completa.
        params: dict de query params extras.
        timeout: timeout do request em segundos.
        max_retries: tentativas extras apos 429 (default 2 = 1 inicial + 1 retry).
        retry_wait: segundos pra esperar entre tentativas em 429.
        raise_on_429: se True, propaga HTTPError em 429 mesmo apos retries.
        skip_cache: ignora cache local (forca refresh).

    Returns:
        JSON parseado. Lanca requests.HTTPError em outros erros HTTP.
    """
    import requests
    if path_or_url.startswith("http"):
        url = path_or_url
        cache_key = url[len(OPENDOTA_BASE):] if url.startswith(OPENDOTA_BASE) else None
    else:
        cache_key = "/" + path_or_url.lstrip("/")
        url = OPENDOTA_BASE + cache_key

    # Cache hit?
    if not skip_cache and cache_key and not params:
        cached = _opendota_cache_read(cache_key)
        if cached is not None:
            return cached

    query = dict(params or {})
    key = _load_opendota_key()
    if key:
        query.setdefault("api_key", key)

    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=query, timeout=timeout)
            if r.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(retry_wait)
                    continue
                if not raise_on_429:
                    return {"error": "rate_limit", "status_code": 429}
            r.raise_for_status()
            data = r.json()
            _opendota_usage_increment()
            # Persiste no cache se for endpoint estavel
            if cache_key:
                _opendota_cache_write(cache_key, data)
            return data
        except requests.HTTPError as e:
            last_exc = e
            status = e.response.status_code if e.response is not None else None
            if status == 429 and attempt < max_retries - 1:
                time.sleep(retry_wait)
                continue
            # 5xx (502/503/504/520/521/522/524) = OpenDota/Cloudflare instável.
            # Backoff exponencial curto — transitório, recupera em segundos/minutos.
            if status is not None and 500 <= status < 600 and attempt < max_retries - 1:
                time.sleep(min(retry_wait, 5.0 * (2 ** attempt)))
                continue
            raise
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(retry_wait / 4)
                continue
            raise
    if last_exc:
        raise last_exc
    return None


def validate_csv_schema(file_path, required_columns):
    """Check if a CSV file has the required columns.

    Returns (is_valid, missing_columns, error).
    - is_valid: True if file exists and has all required columns
    - missing_columns: list of columns expected but absent
    - error: string describing the problem ("missing_file", "empty_file", "missing_columns") or None
    """
    path = Path(file_path)
    if not path.exists():
        return False, list(required_columns), "missing_file"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames or []
    except (OSError, csv.Error):
        return False, list(required_columns), "empty_file"
    if not fieldnames:
        return False, list(required_columns), "empty_file"
    missing = [col for col in required_columns if col not in fieldnames]
    if missing:
        return False, missing, "missing_columns"
    return True, [], None


# ─── Escrita atomica + mutex entre processos (16/07, auditoria) ──────────────
# Arquivos JSON compartilhados por mais de um processo podem ser lidos e escritos
# ao mesmo tempo; sem coordenacao, uma race pode perder dados. Estes helpers dao:
#
#   atomic_write_json: escreve em .tmp e os.replace (atomico no mesmo volume;
#     leitor nunca ve arquivo pela metade)
#   file_mutex: lock cooperativo via criacao exclusiva de .lock com retry e
#     quebra de lock velho (>stale_secs = dono morreu)
import os as _os
import time as _time
from contextlib import contextmanager


def atomic_write_json(path, data, indent=2):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    _os.replace(tmp, path)


@contextmanager
def file_mutex(lock_path, timeout=10.0, stale_secs=30.0):
    """Mutex cooperativo entre processos. Uso: with file_mutex(x): ...
    Se nao conseguir em `timeout`s, segue SEM lock (log implicito via best-effort:
    preferimos uma race rara a travar o processo)."""
    lock_path = Path(lock_path)
    deadline = _time.time() + timeout
    acquired = False
    while _time.time() < deadline:
        try:
            fd = _os.open(str(lock_path), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
            _os.write(fd, str(_os.getpid()).encode())
            _os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                if _time.time() - lock_path.stat().st_mtime > stale_secs:
                    lock_path.unlink()  # dono provavelmente morreu
                    continue
            except OSError:
                pass
            _time.sleep(0.1)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock_path.unlink()
            except OSError:
                pass
