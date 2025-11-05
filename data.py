# data.py
from __future__ import annotations
import os, json, time, ssl, traceback
from typing import Any, Dict, List, Tuple, Optional
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ============================= ENV =============================
PLAYERS_SEASON = os.getenv("PLAYERS_SEASON", "2025-26").strip()

# 1) Основной источник (твой воркер / любой прокси). Можно выключить целиком.
PLAYERS_USE_CUSTOM = os.getenv("PLAYERS_USE_CUSTOM", "1") == "1"
PLAYERS_CUSTOM_URL = os.getenv("PLAYERS_CUSTOM_URL", "").strip() or \
    f"https://nba-players-proxy.znamteam-903.workers.dev/?season={PLAYERS_SEASON}"

# Таймауты/ретраи для custom
CUSTOM_ATTEMPTS = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS", "3"))
CUSTOM_BASE_TIMEOUT = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT", "30"))  # сек: 30, 40, 50...

# 2) Legacy (data.nba.net) по умолчанию ВЫКЛ — он мёртв/ломает SSL/даёт 400
ALLOW_LEGACY_FALLBACK = os.getenv("PLAYERS_ALLOW_LEGACY", "0") == "1"
ALLOW_INSECURE_SSL_LEGACY = os.getenv("PLAYERS_INSECURE_SSL", "0") == "1"
LEGACY_URL = os.getenv("PLAYERS_LEGACY_URL",
    "https://data.nba.net/data/10s/prod/v1/2025/players.json")

# 3) Удалённые снапшоты (список URL через запятую): raw GitHub/Gist/S3 и т.п.
# Формат: ЛИБО NBA resultSets, ЛИБО {"league":{"standard":[...]}}
PLAYERS_SNAPSHOT_URLS = [u.strip() for u in os.getenv("PLAYERS_SNAPSHOT_URLS", "").split(",") if u.strip()]

# 4) Локальный снапшот как «последняя страховка»
DISABLE_LOCAL_SNAPSHOT = os.getenv("PLAYERS_DISABLE_LOCAL", "0") == "1"

# 5) Прочее
MIN_EXPECTED = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))  # минимально «адекватная» выборка
CACHE_TTL_SEC = int(os.getenv("PLAYERS_CACHE_TTL", "43200"))  # 12h
PHOTO_FMT = os.getenv("PLAYERS_PHOTO_FMT",
    "https://cdn.nba.com/headshots/nba/latest/1040x760/{personId}.png")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
LOCAL_SNAPSHOT = os.path.join(ASSETS_DIR, "players.json")
CACHE_PATH = os.path.join("/tmp", "players_cache.json")

_CACHED: Dict[str, Any] = {"ts": 0.0, "players": None, "index": None}

# ============================= utils =============================
def _log(*args: Any) -> None:
    try:
        print("[players]", *args, flush=True)
    except Exception:
        pass

def _with_query(url: str, **extra: str) -> str:
    pr = urlparse(url)
    q = dict(parse_qsl(pr.query))
    q.update({k: v for k, v in extra.items() if v is not None})
    return urlunparse(pr._replace(query=urlencode(q)))

def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def _read_json_file(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def _http_get_json(url: str, timeout: int = 30, verify_ssl: bool = True) -> Any:
    ctx = None
    if not verify_ssl:
        ctx = ssl._create_unverified_context()
    # Важные заголовки (некоторые прокси/серверы тупят на gzip/chunked)
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (players-fetch; like Gecko)",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Encoding": "identity",   # без gzip/deflate
        "Connection": "close",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
    })
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _get_json_with_retries(url: str, attempts: int, base_timeout: int, verify_ssl: bool = True) -> Any:
    last_err: Optional[BaseException] = None
    for i in range(attempts):
        try:
            j = _http_get_json(url, timeout=base_timeout + i * 10, verify_ssl=verify_ssl)
            return j
        except BaseException as e:
            last_err = e
            _log(f"fetch try {i+1}/{attempts} failed:", repr(e))
            time.sleep(1.5 * (i + 1))
    if last_err:
        raise last_err

# ============================= parsing =============================
def _extract_players(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not j:
        return []

    # Вариант 1: NBA stats (resultSets)
    if "resultSets" in j:
        try:
            rs = j["resultSets"][0]
            headers = rs["headers"]; rows = rs["rowSet"]
            def idx(name: str) -> Optional[int]:
                try:
                    return headers.index(name)
                except ValueError:
                    return None
            i_pid = idx("PERSON_ID"); i_tid = idx("TEAM_ID"); i_act = idx("ROSTERSTATUS")
            i_fn  = idx("FIRST_NAME"); i_ln  = idx("LAST_NAME")
            i_dfl = idx("DISPLAY_FIRST_LAST"); i_lcf = idx("DISPLAY_LAST_COMMA_FIRST")
            out: List[Dict[str, Any]] = []
            for r in rows:
                pid = _safe_str(r[i_pid]) if i_pid is not None else ""
                tid = _safe_str(r[i_tid]) if i_tid is not None else "0"
                active = True
                if i_act is not None:
                    try:
                        active = bool(int(r[i_act]))
                    except Exception:
                        active = bool(r[i_act])
                if i_fn is not None and i_ln is not None:
                    fn = _safe_str(r[i_fn]).strip(); ln = _safe_str(r[i_ln]).strip()
                elif i_dfl is not None:
                    disp = _safe_str(r[i_dfl]).strip()
                    parts = disp.split()
                    fn = parts[0] if parts else ""; ln = " ".join(parts[1:]) if len(parts) > 1 else ""
                elif i_lcf is not None and "," in _safe_str(r[i_lcf]):
                    ln, fn = [s.strip() for s in _safe_str(r[i_lcf]).split(",", 1)]
                else:
                    fn, ln = "", ""
                out.append({
                    "personId": pid, "firstName": fn, "lastName": ln,
                    "teamId": tid or "0", "isActive": active,
                })
            return out
        except Exception as e:
            _log("stats extract error:", e)
            _log(traceback.format_exc())
            return []

    # Вариант 2: legacy data.nba.net
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

    # Вариант 3: список уже нормализован
    if isinstance(j, list) and j and isinstance(j[0], dict) and "personId" in j[0]:
        return j

    return []

# ============================= sources =============================
def _fetch_from_custom(url: Optional[str] = None) -> List[Dict[str, Any]]:
    if not PLAYERS_USE_CUSTOM:
        return []
    u = (url or PLAYERS_CUSTOM_URL or "").strip()
    if not u:
        return []
    if "season=" not in u:
        u = _with_query(u, season=PLAYERS_SEASON)
    try:
        j = _get_json_with_retries(u, attempts=CUSTOM_ATTEMPTS, base_timeout=CUSTOM_BASE_TIMEOUT, verify_ssl=True)
        players = _extract_players(j)
        _log(f"custom parsed {len(players)} from {u}")
        return players
    except BaseException as e:
        _log("custom fetch error:", e)
        _log(traceback.format_exc())
        return []

def _fetch_from_legacy() -> List[Dict[str, Any]]:
    if not ALLOW_LEGACY_FALLBACK:
        return []
    url = LEGACY_URL
    try:
        j = _get_json_with_retries(url, attempts=2, base_timeout=15, verify_ssl=True)
        p = _extract_players(j)
        _log(f"legacy parsed {len(p)} from {url}")
        return p
    except BaseException as e:
        msg = repr(e)
        _log("legacy fetch error:", msg)
        # попытка http/insecure — только если явно разрешили
        if "CERTIFICATE_VERIFY_FAILED" in msg or isinstance(e, URLError):
            http_url = "http://" + url[len("https://"):] if url.startswith("https://") else url
            try:
                j2 = _get_json_with_retries(http_url, attempts=2, base_timeout=15, verify_ssl=ALLOW_INSECURE_SSL_LEGACY is False)
                p2 = _extract_players(j2)
                _log(f"legacy (http{' insecure' if not ALLOW_INSECURE_SSL_LEGACY else ''}) parsed {len(p2)} from {http_url}")
                return p2
            except BaseException as e2:
                _log("legacy http fallback failed:", repr(e2))
        return []

def _fetch_remote_snapshots() -> List[Dict[str, Any]]:
    best: List[Dict[str, Any]] = []
    for u in PLAYERS_SNAPSHOT_URLS:
        try:
            j = _get_json_with_retries(u, attempts=2, base_timeout=20, verify_ssl=True)
            p = _extract_players(j)
            _log(f"remote snapshot parsed {len(p)} from {u}")
            if len(p) > len(best):
                best = p
            if len(p) >= MIN_EXPECTED:
                return p
        except BaseException as e:
            _log("remote snapshot error:", u, repr(e))
    return best

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

# ============================= overrides =============================
OVERRIDES_FILE = os.path.join(ASSETS_DIR, "players_overrides.json")

def _load_overrides() -> Dict[str, Dict[str, Any]]:
    env_raw = os.getenv("PLAYERS_OVERRIDES_JSON", "").strip()
    if env_raw:
        try:
            d = json.loads(env_raw)
            if isinstance(d, dict):
                return {str(k): v for k, v in d.items() if isinstance(v, dict)}
        except Exception as e:
            _log("overrides env parse error:", e)
    j = _read_json_file(OVERRIDES_FILE)
    if isinstance(j, dict):
        return {str(k): v for k, v in j.items() if isinstance(v, dict)}
    return {}

def _apply_overrides(players: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ov = _load_overrides()
    by_id = {p["personId"]: dict(p) for p in (players or [])}
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
        fn, ln = p.get("firstName","").strip(), p.get("lastName","").strip()
        p["displayName"] = (fn + " " + ln).strip() if (fn or ln) else p.get("displayName","")
        out.append(p)
    return out

# ============================= cache/build =============================
def _load_cache_from_disk() -> Optional[Tuple[List[Dict[str, Any]], float]]:
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
        return players, ts
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

def _build_from_sources() -> Tuple[List[Dict[str, Any]], str]:
    """
    Порядок (новый): custom → remote snapshots → (опционально) legacy → local
    """
    # 1) custom
    custom = _fetch_from_custom()
    if custom and len(custom) >= MIN_EXPECTED:
        return custom, "custom"

    # 2) удалённые снапшоты
    rem = _fetch_remote_snapshots()
    if rem and len(rem) >= MIN_EXPECTED:
        return rem, "remote_snapshot"

    # 3) legacy — только если явно включён
    leg = _fetch_from_legacy()
    if leg and len(leg) >= MIN_EXPECTED:
        return leg, "legacy"

    # 4) локальный снапшот
    loc = _fetch_local_snapshot()
    if loc and len(loc) > 0:
        return loc, "local"

    # 5) хоть что-то из того, что удалось частично
    best = custom if len(custom) >= len(rem) else rem
    if len(leg) > len(best): best = leg
    if len(loc) > len(best): best = loc
    tag = "partial"
    return (best or []), tag

# ============================= public API =============================
def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    if not force_refresh and _CACHED["players"] and _valid_cache(_CACHED["ts"]):
        return _CACHED["players"]

    if not force_refresh:
        disk = _load_cache_from_disk()
        if disk:
            players, ts = disk
            if _valid_cache(ts) and players:
                _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": ts})
                return players

    raw, source = _build_from_sources()
    players = _apply_overrides(raw)
    if players:
        _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": time.time()})
        _save_cache_to_disk(players)
        _log(f"final players count: {len(players)} (source={source})")
        return players

    disk = _load_cache_from_disk()
    if disk:
        players, ts = disk
        if players:
            _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": ts})
            _log(f"using stale disk cache: {len(players)} players")
            return players

    _log("no players parsed from any source (after all fallbacks)")
    return []

def get_players_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    ps = get_players(force_refresh=force_refresh)
    idx = {p["personId"]: p for p in ps}
    _CACHED["index"] = idx
    return idx

def refresh_players(drop_cache: bool = False) -> Tuple[int, Dict[str, Any]]:
    """
    Не чистим кэш заранее. Если новый фетч удачный — заменяем.
    """
    prev = _CACHED.get("players") or []
    try:
        raw, source = _build_from_sources()
        players = _apply_overrides(raw)
        if players and len(players) >= MIN_EXPECTED:
            _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": time.time()})
            _save_cache_to_disk(players)
            return len(players), {"ok": True, "players_indexed": len(players), "source": source}
        else:
            if prev:
                return len(prev), {"ok": True, "players_indexed": len(prev), "source": "cache_preserved"}
            return 0, {"ok": False, "error": "no_source_available"}
    except Exception as e:
        _log("refresh error:", e)
        if prev:
            return len(prev), {"ok": True, "players_indexed": len(prev), "source": "cache_preserved"}
        return 0, {"ok": False, "error": repr(e)}

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

# совместимость
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
