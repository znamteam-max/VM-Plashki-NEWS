# data.py
# Надёжный источник списка игроков NBA с жёсткими фоллбэками и понятным логгингом.

from __future__ import annotations
import os
import json
import time
import traceback
from typing import Any, Dict, List, Tuple, Optional
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse
from urllib.request import Request, urlopen

# ========== Конфиги через ENV ==========
PLAYERS_SEASON            = os.getenv("PLAYERS_SEASON", "2025-26").strip()
# Если PLAYERS_CUSTOM_URL не задан — используем твой Cloudflare Worker с сезоном 2025-26
FALLBACK_CUSTOM_URL       = f"https://nba-players-proxy.znamteam-903.workers.dev/?season={PLAYERS_SEASON}"
PLAYERS_CUSTOM_URL        = (os.getenv("PLAYERS_CUSTOM_URL", "").strip() or FALLBACK_CUSTOM_URL)

PLAYERS_ENFORCE_SOURCE    = os.getenv("PLAYERS_ENFORCE_SOURCE", "").strip().lower()  # "stats" | "legacy" | ""
ALLOW_LEGACY_FALLBACK_ENV = os.getenv("PLAYERS_ALLOW_LEGACY", "0") == "1"
MIN_EXPECTED              = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))
DISABLE_LOCAL_SNAPSHOT    = os.getenv("PLAYERS_DISABLE_LOCAL", "1") == "1"
CACHE_TTL_SEC             = int(os.getenv("PLAYERS_CACHE_TTL", "3600"))
PHOTO_FMT                 = os.getenv("PLAYERS_PHOTO_FMT", "https://cdn.nba.com/headshots/nba/latest/1040x760/{personId}.png")
LEGACY_URL                = os.getenv("PLAYERS_LEGACY_URL", "https://data.nba.net/data/10s/prod/v1/2025/players.json")

# Пути
ROOT_DIR        = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR      = os.path.join(ROOT_DIR, "assets")
LOCAL_SNAPSHOT  = os.path.join(ASSETS_DIR, "players.json")
OVERRIDES_FILE  = os.path.join(ASSETS_DIR, "players_overrides.json")
CACHE_PATH      = os.path.join("/tmp", "players_cache.json")

# Память кэша
_CACHED: Dict[str, Any] = {"ts": 0.0, "players": None, "index": None}

# ========== Утилиты ==========
def _log(*args: Any) -> None:
    try:
        print("[players]", *args, flush=True)
    except Exception:
        pass

def _http_get_json(url: str, timeout: int = 15) -> Any:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (players-fetch)",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _read_json_file(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def _with_query(url: str, **extra: str) -> str:
    """Добавить/заменить query-параметры в URL."""
    pr = urlparse(url)
    q = dict(parse_qsl(pr.query))
    q.update({k: v for k, v in extra.items() if v is not None})
    return urlunparse(pr._replace(query=urlencode(q)))

# ========== Парсинг разных форматов ==========
def _extract_players(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Поддержка двух форматов:
      1) stats.nba.com (proxied CommonAllPlayers): resultSets[0].headers/rowSet
      2) legacy data.nba.net: league.standard
    Приводим к:
      {personId, firstName, lastName, teamId, isActive}
    """
    if not j:
        return []

    # Формат stats.nba.com proxied
    if "resultSets" in j:
        try:
            rs = j["resultSets"][0]
            headers = rs["headers"]
            rowset = rs["rowSet"]

            pid_i  = headers.index("PERSON_ID")
            tid_i  = headers.index("TEAM_ID")
            act_i  = headers.index("ROSTERSTATUS") if "ROSTERSTATUS" in headers else None

            fn_i   = headers.index("FIRST_NAME") if "FIRST_NAME" in headers else None
            ln_i   = headers.index("LAST_NAME")  if "LAST_NAME"  in headers else None
            dfl_i  = headers.index("DISPLAY_FIRST_LAST") if "DISPLAY_FIRST_LAST" in headers else None
            dlcf_i = headers.index("DISPLAY_LAST_COMMA_FIRST") if "DISPLAY_LAST_COMMA_FIRST" in headers else None

            out: List[Dict[str, Any]] = []
            for row in rowset:
                personId = _safe_str(row[pid_i])
                teamId   = _safe_str(row[tid_i] or "0")
                # Активность
                isActive = True
                if act_i is not None:
                    try:
                        isActive = bool(int(row[act_i]))
                    except Exception:
                        isActive = bool(row[act_i])

                # Имя/фамилия
                if fn_i is not None and ln_i is not None:
                    firstName = _safe_str(row[fn_i]).strip()
                    lastName  = _safe_str(row[ln_i]).strip()
                elif dfl_i is not None:
                    disp = _safe_str(row[dfl_i]).strip()
                    parts = disp.split()
                    firstName = parts[0] if parts else ""
                    lastName  = " ".join(parts[1:]) if len(parts) > 1 else ""
                elif dlcf_i is not None:
                    disp2 = _safe_str(row[dlcf_i]).strip()
                    if "," in disp2:
                        lastName, firstName = [s.strip() for s in disp2.split(",", 1)]
                    else:
                        firstName, lastName = disp2, ""
                else:
                    firstName, lastName = "", ""

                out.append({
                    "personId": personId,
                    "firstName": firstName,
                    "lastName": lastName,
                    "teamId": teamId,
                    "isActive": isActive,
                })
            return out
        except Exception as e:
            _log("stats extract error:", e)
            _log(traceback.format_exc())
            return []

    # Формат data.nba.net
    league_std = j.get("league", {}).get("standard")
    if isinstance(league_std, list):
        out: List[Dict[str, Any]] = []
        for p in league_std:
            try:
                out.append({
                    "personId": _safe_str(p.get("personId") or ""),
                    "firstName": _safe_str(p.get("firstName") or "").strip(),
                    "lastName":  _safe_str(p.get("lastName")  or "").strip(),
                    "teamId":    _safe_str(p.get("teamId")    or "0"),
                    "isActive":  bool(p.get("isActive", True)),
                })
            except Exception:
                continue
        return out

    return []

# ========== Источники ==========
def _fetch_from_custom(url: Optional[str] = None) -> List[Dict[str, Any]]:
    u = (url or PLAYERS_CUSTOM_URL or "").strip()
    if not u:
        _log("custom url is empty")
        return []
    # гарантируем наличие season в query
    if "season=" not in u:
        u = _with_query(u, season=PLAYERS_SEASON)
    try:
        j = _http_get_json(u)
        players = _extract_players(j)
        _log(f"custom parsed {len(players)} from {u}")
        return players
    except Exception as e:
        _log("custom fetch error:", e)
        _log(traceback.format_exc())
        return []

def _fetch_from_legacy() -> List[Dict[str, Any]]:
    try:
        j = _http_get_json(LEGACY_URL)
        players = _extract_players(j)
        _log(f"legacy parsed {len(players)} from {LEGACY_URL}")
        return players
    except Exception as e:
        _log("legacy fetch error:", e)
        _log(traceback.format_exc())
        return []

def _fetch_local_snapshot() -> List[Dict[str, Any]]:
    if DISABLE_LOCAL_SNAPSHOT:
        return []
    try:
        j = _read_json_file(LOCAL_SNAPSHOT)
        players = _extract_players(j) if isinstance(j, dict) else (j or [])
        _log(f"local snapshot parsed {len(players)} from {LOCAL_SNAPSHOT}")
        return players
    except Exception as e:
        _log("local snapshot error:", e)
        return []

# ========== Оверрайды ==========
def _load_overrides() -> Dict[str, Dict[str, Any]]:
    """
    Формат overrides:
      {"1627783": {"teamId":"1610612754","photo":"...","firstName":"...","lastName":"...","isActive":true}}
    """
    env_raw = os.getenv("PLAYERS_OVERRIDES_JSON", "").strip()
    if env_raw:
        try:
            data = json.loads(env_raw)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            _log("overrides env parse error:", e)

    j = _read_json_file(OVERRIDES_FILE)
    if isinstance(j, dict):
        return {str(k): v for k, v in j.items() if isinstance(v, dict)}
    return {}

def _apply_overrides(players: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ov = _load_overrides()
    by_id: Dict[str, Dict[str, Any]] = {p["personId"]: dict(p) for p in (players or [])}

    if ov:
        for pid, patch in ov.items():
            if pid in by_id:
                base = by_id[pid]
                if "firstName" in patch: base["firstName"] = _safe_str(patch["firstName"]).strip()
                if "lastName"  in patch: base["lastName"]  = _safe_str(patch["lastName"]).strip()
                if "teamId"    in patch: base["teamId"]    = _safe_str(patch["teamId"]).strip() or "0"
                if "isActive"  in patch: base["isActive"]  = bool(patch["isActive"])
                if "photo"     in patch: base["photo"]     = _safe_str(patch["photo"]).strip()
            else:
                add = {
                    "personId": pid,
                    "firstName": _safe_str(patch.get("firstName", "")),
                    "lastName":  _safe_str(patch.get("lastName", "")),
                    "teamId":    _safe_str(patch.get("teamId", "0")),
                    "isActive":  bool(patch.get("isActive", True)),
                }
                if "photo" in patch:
                    add["photo"] = _safe_str(patch["photo"])
                by_id[pid] = add

    out: List[Dict[str, Any]] = []
    for p in by_id.values():
        if not p.get("photo"):
            p["photo"] = PHOTO_FMT.format(personId=p["personId"])
        fn = p.get("firstName", "").strip()
        ln = p.get("lastName", "").strip()
        p["displayName"] = (fn + " " + ln).strip() if (fn or ln) else p.get("displayName", "")
        out.append(p)
    return out

# ========== Сборка со «строгими» фоллбэками ==========
def _build_from_sources() -> List[Dict[str, Any]]:
    """
    Стратегия:
      1) Если явно ENFORCE=stats — сначала custom.
      2) Если ENFORCE=legacy — сразу legacy.
      3) Иначе: custom → (если 0 или <MIN_EXPECTED и ALLOW_LEGACY_FALLBACK_ENV) legacy → local.
      4) Если всё равно 0 — принудительно legacy → local (игнорируя флаги), чтобы не вернуть ноль.
    """
    # Первичный выбор
    if PLAYERS_ENFORCE_SOURCE == "legacy":
        primary = _fetch_from_legacy()
        if primary:
            return primary
        # аварийно добираем
        secondary = _fetch_from_custom()
        if secondary:
            return secondary

    # По умолчанию/ENFORCE=stats
    players = _fetch_from_custom()

    # Доп. фоллбеки по env
    if (not players) or (len(players) < MIN_EXPECTED and ALLOW_LEGACY_FALLBACK_ENV):
        _log(f"custom insufficient ({len(players) if players else 0}), trying legacy (env-allowed={ALLOW_LEGACY_FALLBACK_ENV})")
        leg = _fetch_from_legacy()
        if leg:
            players = leg
        elif not DISABLE_LOCAL_SNAPSHOT:
            loc = _fetch_local_snapshot()
            if loc:
                players = loc

    # Жёсткий аварийный фоллбэк: никогда не отдаём 0
    if not players:
        _log("hard fallback engaged: legacy → local regardless of env toggles")
        leg2 = _fetch_from_legacy()
        if leg2:
            players = leg2
        elif not DISABLE_LOCAL_SNAPSHOT:
            loc2 = _fetch_local_snapshot()
            if loc2:
                players = loc2

    return players or []

def _build_index() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    base = _build_from_sources()
    if not base:
        _log("no players parsed from any source (after all fallbacks)")
        return [], {}

    players = _apply_overrides(base)
    index = {p["personId"]: p for p in players}
    _log(f"final players count (after overrides): {len(players)}")
    return players, index

# ========== Кэш ==========
def _load_cache_from_disk() -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], float]]:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        j = _read_json_file(CACHE_PATH)
        if not isinstance(j, dict):
            return None
        ts = float(j.get("ts", 0))
        players = j.get("players") or []
        if not isinstance(players, list):
            players = []
        index = {p["personId"]: p for p in players if isinstance(p, dict) and p.get("personId")}
        return players, index, ts
    except Exception as e:
        _log("cache read error:", e)
        return None

def _save_cache_to_disk(players: List[Dict[str, Any]]) -> None:
    try:
        _write_json_file(CACHE_PATH, {"ts": time.time(), "players": players})
    except Exception as e:
        _log("cache write error:", e)

def _valid_cache(ts: float) -> bool:
    return (time.time() - ts) < CACHE_TTL_SEC

# ========== Публичные API ==========
def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    if not force_refresh and _CACHED["players"] and _valid_cache(_CACHED["ts"]):
        return _CACHED["players"]

    if not force_refresh:
        disk = _load_cache_from_disk()
        if disk:
            players, index, ts = disk
            if _valid_cache(ts) and len(players) > 0:
                _CACHED.update({"players": players, "index": index, "ts": ts})
                return players

    players, index = _build_index()
    _CACHED.update({"players": players, "index": index, "ts": time.time()})
    if players:
        _save_cache_to_disk(players)
    return players

def get_players_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    if not force_refresh and _CACHED["index"] and _valid_cache(_CACHED["ts"]):
        return _CACHED["index"]
    players = get_players(force_refresh=force_refresh)
    index = {p["personId"]: p for p in players}
    _CACHED["index"] = index
    return index

def refresh_players(drop_cache: bool = False) -> Tuple[int, Dict[str, Any]]:
    if drop_cache:
        drop_players_cache()
    players = get_players(force_refresh=True)
    return len(players), {"ok": True, "players_indexed": len(players)}

def drop_players_cache() -> bool:
    try:
        _CACHED["ts"] = 0.0
        _CACHED["players"] = None
        _CACHED["index"] = None
        if os.path.exists(CACHE_PATH):
            try:
                os.remove(CACHE_PATH)
            except Exception:
                pass
        _log("players cache dropped")
        return True
    except Exception as e:
        _log("drop cache error:", e)
        return False

def find_player_by_name(query: str) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    res: List[Dict[str, Any]] = []
    for p in get_players():
        name = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if q in (name or "").lower():
            res.append(p)
    return res

# Совместимость со старыми импортами
def players_count(force_refresh: bool = False) -> int:
    return len(get_players(force_refresh=force_refresh))

def players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    return get_players(force_refresh=force_refresh)

def players_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    return get_players_index(force_refresh=force_refresh)

__all__ = [
    "get_players", "get_players_index", "refresh_players", "drop_players_cache",
    "find_player_by_name", "players_count", "players", "players_index"
]
