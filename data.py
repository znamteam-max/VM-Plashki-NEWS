# data.py — поиск по PASSTHROUGH (имена/команды), normalized только для headshot
from __future__ import annotations
import os, io, json, time, re
from typing import Any, Dict, List, Tuple, Optional
from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError
from PIL import Image

# =========================
# ENV / Конфиг
# =========================
ENV_URLS          = os.getenv("PLAYERS_CUSTOM_URLS", "").strip()
ENV_URL_LEGACY    = os.getenv("PLAYERS_CUSTOM_URL", "").strip()
ENV_SEASON        = os.getenv("PLAYERS_SEASON", "2025-26").strip()
TIMEOUT           = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT", "30"))
ATTEMPTS          = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS", "3"))
ACTIVE_ONLY       = os.getenv("PLAYERS_ACTIVE_ONLY", "true").lower() in ("1","true","yes")
MIN_EXPECTED      = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))
PLAYERS_JSON_URL  = os.getenv("PLAYERS_JSON_URL", "").strip()  # опциональный бэкап (HTTP)

IMAGE_PROXY_URLS  = [u.strip() for u in os.getenv("IMAGE_PROXY_URLS","").split(",") if u.strip()]

# overrides имён
OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()          # "owner/repo"
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","assets/players_overrides.json").strip()

NAMES_LOCAL_PATH = "/tmp/names_ru.json"

# Лого команд
ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
LOGO_DIRS  = [os.path.join(ASSETS_DIR, "cache"), os.path.join(ASSETS_DIR, "teams")]

DEFAULT_PT   = f"https://nba-players-proxy.znamteam-903.workers.dev/players?season={ENV_SEASON}&format=passthrough"
DEFAULT_NORM = f"https://nba-players-proxy.znamteam-903.workers.dev/players?season={ENV_SEASON}&format=normalized"

# =========================
# Состояние
# =========================
_PLAYERS: List[Dict[str, Any]] = []
_LAST_SOURCE: str = "none"
_LAST_REFRESH_AT: float = 0.0

_NAMES_RU: Dict[str, str] = {}  # personId -> "Имя Фамилия"

def _log(*a):
    try: print("[data]", *a, flush=True)
    except: pass

# =========================
# HTTP helpers
# =========================
def _http_json(url: str, timeout: int = TIMEOUT) -> Any:
    req = UrlRequest(url, headers={"User-Agent": "vm-plashki/1.0"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try: return json.loads(raw.decode("utf-8"))
    except: return None

def _try_fetch(url: str) -> Any:
    last = None
    for i in range(ATTEMPTS):
        try:
            j = _http_json(url, timeout=TIMEOUT)
            return j
        except Exception as e:
            last = e
            _log("fetch attempt", i+1, "failed:", url, repr(e))
    if last: _log("fetch err:", url, repr(last))
    return None

# =========================
# Utils
# =========================
def _as_list(x: Any) -> List[Any]:
    if isinstance(x, list): return x
    if isinstance(x, dict) and "players" in x and isinstance(x["players"], list): return x["players"]
    return []

def _s(x: Any) -> str:
    try:
        return "" if x is None else str(x)
    except:
        return ""

def _classify_urls() -> Tuple[List[str], List[str]]:
    urls: List[str] = []
    if ENV_URLS:
        urls += [u.strip() for u in ENV_URLS.split(",") if u.strip()]
    if ENV_URL_LEGACY:
        urls += [u.strip() for u in ENV_URL_LEGACY.split(",") if u.strip()]
    if not urls:
        urls = [DEFAULT_PT, DEFAULT_NORM]

    pt, norm = [], []
    for u in urls:
        lu = u.lower()
        if "format=passthrough" in lu or "passthrough" in lu:
            pt.append(u)
        elif "format=normalized" in lu or "normalized" in lu:
            norm.append(u)
        else:
            pt.append(u)

    if not pt and norm:
        # генерим pt-кандидаты из normalized
        for u in norm:
            if "format=normalized" in u:
                pt.append(u.replace("format=normalized", "format=passthrough"))
            elif "normalized" in u:
                pt.append(u.replace("normalized", "passthrough"))
    if not pt:   pt = [DEFAULT_PT]
    if not norm: norm = [DEFAULT_NORM]

    _log("pt_urls:", pt)
    _log("norm_urls:", norm)
    return pt, norm

def _extract_passthrough(obj: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in _as_list(obj):
        pid   = _s(it.get("personId") or it.get("id") or it.get("playerId") or it.get("pid"))
        if not pid: continue
        first = (it.get("firstName") or it.get("first_name") or "").strip()
        last  = (it.get("lastName")  or it.get("last_name")  or "").strip()
        disp  = (it.get("displayName") or it.get("display_name") or (first + " " + last).strip()).strip()
        team  = _s(it.get("teamId") or it.get("tid") or it.get("team_id") or "")
        active = it.get("isActive")
        if active is None:
            active = bool(team and team != "0")
        out.append({
            "personId":   pid,
            "firstName":  first,
            "lastName":   last,
            "displayName": disp or (first + " " + last).strip() or pid,
            "teamId":     team,
            "isActive":   bool(active),
        })
    return out

def _extract_normalized(obj: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in _as_list(obj):
        pid = _s(it.get("personId") or it.get("id") or it.get("playerId") or it.get("pid"))
        if not pid: continue
        team = _s(it.get("teamId") or it.get("tid") or it.get("team_id") or "")
        url  = _s(it.get("headshot") or it.get("img") or it.get("image") or it.get("photo") or it.get("u"))
        out.append({"personId": pid, "teamId": team, "headshot": url})
    return out

def _index_by_pid(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    m: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = _s(r.get("personId"))
        if pid: m[pid] = r
    return m

def _merge_passthrough_with_normalized(base_names: List[Dict[str, Any]], norm_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not base_names and not norm_rows: return []
    names_ix = _index_by_pid(base_names)
    norm_ix  = _index_by_pid(norm_rows)
    merged: List[Dict[str, Any]] = []
    for pid, row in names_ix.items():
        r = dict(row)
        n = norm_ix.get(pid)
        if n and n.get("headshot"):
            r["headshot"] = _s(n.get("headshot"))
        merged.append(r)
    if ACTIVE_ONLY:
        merged = [p for p in merged if p.get("isActive", True)]
    return merged

# ===== локальный бэкап (без сети) =====
def _load_local_players_json() -> List[Dict[str, Any]]:
    candidates = [
        os.path.join(ROOT_DIR, "public", "players.json"),
        os.path.join(ROOT_DIR, "players.json"),
        os.path.join(ASSETS_DIR, "players.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    j = json.load(f)
                rows = _extract_passthrough(j if isinstance(j, list) else {"players": j})
                if rows:
                    _log("local players.json used:", p, "count:", len(rows))
                    return rows
            except Exception as e:
                _log("local players.json read err:", p, repr(e))
    return []

# =========================
# Публичные API
# =========================
def refresh_players() -> Tuple[int, str]:
    """
    PASSTHROUGH — основной слой (имена/команды).
    NORMALIZED — только headshot.
    Жёсткие логи по каждому источнику + локальный бэкап.
    """
    global _PLAYERS, _LAST_SOURCE, _LAST_REFRESH_AT

    pt_urls, norm_urls = _classify_urls()
    src_used = "none"

    # 1) PASSTHROUGH
    pt_rows: List[Dict[str, Any]] = []
    for u in pt_urls:
        _log("try pt:", u)
        j = _try_fetch(u)
        if not j:
            _log("pt fetch empty:", u)
            continue
        rows = _extract_passthrough(j)
        _log("pt parsed:", len(rows), "from", u)
        if rows:
            pt_rows = rows
            src_used = "passthrough"
            if len(rows) >= MIN_EXPECTED:
                break

    # 1b) HTTP-бэкап
    if not pt_rows and PLAYERS_JSON_URL:
        _log("fallback PLAYERS_JSON_URL:", PLAYERS_JSON_URL)
        j = _try_fetch(PLAYERS_JSON_URL)
        rows = _extract_passthrough(j)
        _log("PLAYERS_JSON_URL parsed:", len(rows))
        if rows:
            pt_rows = rows
            src_used = "players_json"

    # 1c) ЛОКАЛЬНЫЙ бэкап
    if not pt_rows:
        loc = _load_local_players_json()
        if loc:
            pt_rows = loc
            src_used = "players_local"

    # 1d) абсолютно последний шанс — DEFAULT_PT
    if not pt_rows:
        _log("fallback DEFAULT_PT:", DEFAULT_PT)
        j = _try_fetch(DEFAULT_PT)
        rows = _extract_passthrough(j)
        _log("DEFAULT_PT parsed:", len(rows))
        if rows:
            pt_rows = rows
            src_used = "passthrough(default)"

    # 2) NORMALIZED — только headshot
    norm_rows: List[Dict[str, Any]] = []
    for u in norm_urls:
        _log("try norm:", u)
        j = _try_fetch(u)
        if not j:
            _log("norm fetch empty:", u)
            continue
        rows = _extract_normalized(j)
        _log("norm parsed:", len(rows), "from", u)
        if rows and len(rows) >= MIN_EXPECTED:
            norm_rows = rows
            break
        if rows and not norm_rows:
            norm_rows = rows

    merged = _merge_passthrough_with_normalized(pt_rows, norm_rows)
    _PLAYERS = merged or []
    _LAST_SOURCE = "merged(pt+norm)" if (pt_rows and norm_rows) else src_used
    _LAST_REFRESH_AT = time.time()

    _log(f"final players count: {len(_PLAYERS)} (source={_LAST_SOURCE})")
    return (len(_PLAYERS), _LAST_SOURCE)

def get_players(force_refresh: Optional[bool] = None) -> List[Dict[str, Any]]:
    global _PLAYERS
    if not _PLAYERS or (force_refresh is True and (time.time() - _LAST_REFRESH_AT > 60)):
        refresh_players()
    return _PLAYERS

def find_player_by_name(q: str) -> List[Dict[str, Any]]:
    if not q: return []
    qq = q.strip().lower()
    ps = get_players()
    out: List[Dict[str, Any]] = []
    for p in ps:
        dn = (p.get("displayName") or "").strip()
        fl = (f"{p.get('firstName','')} {p.get('lastName','')}".strip())
        hay = (dn or fl).lower()
        if qq in hay:
            out.append(p)
            if len(out) >= 10:
                break
    if not out and " " in qq:
        parts = [t for t in re.split(r"\s+", qq) if t]
        for p in ps:
            first = (p.get("firstName") or "").lower()
            last  = (p.get("lastName")  or "").lower()
            if any(t in first or t in last for t in parts):
                out.append(p)
                if len(out) >= 10:
                    break
    return out

def display_name_for(p: Dict[str, Any]) -> str:
    d = (p.get("displayName") or "").strip()
    if d: return d
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName")  or "").strip()
    nm = (first + " " + last).strip()
    return nm or str(p.get("personId") or "")

# =========================
# Русские имена (overrides)
# =========================
def _load_names_local() -> Dict[str, str]:
    try:
        if os.path.exists(NAMES_LOCAL_PATH):
            with open(NAMES_LOCAL_PATH, "r", encoding="utf-8") as f:
                j = json.load(f)
            if isinstance(j, dict):
                return {str(k): str(v) for k,v in j.items()}
    except Exception as e:
        _log("names local load err:", repr(e))
    return {}

def _save_names_local(data: Dict[str, str]) -> None:
    try:
        with open(NAMES_LOCAL_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        _log("names local save err:", repr(e))

def _maybe_load_from_github() -> Dict[str, str]:
    if not OV_GH_REPO or not OV_GH_PATH:
        return {}
    url = f"https://raw.githubusercontent.com/{OV_GH_REPO}/{OV_GH_BRANCH}/{OV_GH_PATH}"
    try:
        j = _http_json(url, timeout=10)
        if isinstance(j, dict) and "names_ru" in j and isinstance(j["names_ru"], dict):
            return {str(k): str(v) for k,v in j["names_ru"].items()}
        if isinstance(j, dict):
            return {str(k): str(v) for k,v in j.items()}
    except Exception as e:
        _log("github overrides read err:", repr(e))
    return {}

_NAMES_RU: Dict[str, str] = {}

def _ensure_names_loaded():
    global _NAMES_RU
    if _NAMES_RU:
        return
    base = _maybe_load_from_github()
    loc  = _load_names_local()
    base.update(loc)
    _NAMES_RU = base

def overrides_get_name_ru(person_id: str) -> Optional[str]:
    _ensure_names_loaded()
    return _NAMES_RU.get(str(person_id))

def overrides_save_name_ru(person_id: str, name_ru: str) -> bool:
    try:
        _ensure_names_loaded()
        _NAMES_RU[str(person_id)] = name_ru.strip()
        _save_names_local(_NAMES_RU)
        return True
    except Exception as e:
        _log("save name ru err:", repr(e))
        return False

# =========================
# Картинки
# =========================
def _fetch_image(url: str, timeout: int = 20) -> Optional[bytes]:
    if not url or not url.startswith(("http://","https://")):
        return None
    try:
        req = UrlRequest(url, headers={"User-Agent":"vm-plashki/1.0"})
        with http_urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None

def _fallback_headshot_urls(person_id: str) -> List[str]:
    urls: List[str] = []
    for base in IMAGE_PROXY_URLS:
        base = base.rstrip("/")
        urls.append(f"{base}/img?u={person_id}")
    urls.append(f"https://ak-static.cms.nba.com/wp-content/uploads/headshots/nba/latest/260x190/{person_id}.png")
    urls.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{person_id}.png")
    return urls

def ensure_headshot_png(p: Dict[str, Any]) -> Optional[bytes]:
    pid = str(p.get("personId") or "")
    for key in ("headshot","img","image","photo","u"):
        url = p.get(key)
        if isinstance(url, str) and url.startswith(("http://","https://")):
            raw = _fetch_image(url)
            if raw: return raw
    for url in _fallback_headshot_urls(pid):
        raw = _fetch_image(url)
        if raw: return raw
    return None

def ensure_team_logo_png(team_id: str) -> Optional[str]:
    tid = str(team_id or "0")
    for d in LOGO_DIRS:
        p = os.path.join(d, f"{tid}.png")
        if os.path.exists(p):
            return p
    for d in LOGO_DIRS:
        p = os.path.join(d, "generic.png")
        if os.path.exists(p):
            return p
    return None
