# data.py — источники игроков: passthrough (имена) + normalized (аватарки), мердж по personId

from __future__ import annotations
import os, io, json, time, re
from typing import Any, Dict, List, Tuple, Optional
from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError
from PIL import Image

# -----------------------
# Конфиг из ENV
# -----------------------
ENV_URLS = os.getenv("PLAYERS_CUSTOM_URLS", "").strip()
ENV_URL_LEGACY = os.getenv("PLAYERS_CUSTOM_URL", "").strip()
ENV_PLAYERS_SEASON = os.getenv("PLAYERS_SEASON", "").strip()

# Примеры по умолчанию (если ничего не задано)
DEFAULT_PASSTHROUGH = f"https://nba-players-proxy.znamteam-903.workers.dev/players?season={ENV_PLAYERS_SEASON or '2025-26'}&format=passthrough"
DEFAULT_NORMALIZED   = f"https://nba-players-proxy.znamteam-903.workers.dev/players?season={ENV_PLAYERS_SEASON or '2025-26'}&format=normalized"

TIMEOUT = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT", "30"))
ATTEMPTS = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS", "3"))
ACTIVE_ONLY = os.getenv("PLAYERS_ACTIVE_ONLY", "true").lower() in ("1","true","yes")
MIN_EXPECTED = int(os.getenv("PLAYERS_MIN_EXPECTED","350"))

IMAGE_PROXY_URLS = [u.strip() for u in os.getenv("IMAGE_PROXY_URLS","").split(",") if u.strip()]

# overrides для имён (локально + опционально GitHub)
OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()          # формата "owner/repo"
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","assets/players_overrides.json").strip()

NAMES_LOCAL_PATH = "/tmp/names_ru.json"

# Лого команд локальные
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
LOGO_DIRS = [os.path.join(ASSETS_DIR, "cache"), os.path.join(ASSETS_DIR, "teams")]

# -----------------------
# Внутренние состояния
# -----------------------
_PLAYERS: List[Dict[str, Any]] = []
_LAST_SOURCE: str = "none"
_LAST_REFRESH_AT: float = 0.0

_NAMES_RU: Dict[str, str] = {}  # personId -> "Имя Фамилия"

def _log(*a):
    try: print("[data]", *a, flush=True)
    except: pass

# -----------------------
# HTTP helpers
# -----------------------
def _http_json(url: str, timeout: int = TIMEOUT) -> Any:
    req = UrlRequest(url, headers={"User-Agent": "vm-plashki/1.0"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None

def _try_fetch(url: str) -> Any:
    last_err = None
    for i in range(ATTEMPTS):
        try:
            return _http_json(url, timeout=TIMEOUT)
        except Exception as e:
            last_err = e
    if last_err:
        _log("fetch error:", url, repr(last_err))
    return None

# -----------------------
# Разбор источников
# -----------------------
def _as_list(x: Any) -> List[Any]:
    if isinstance(x, list): return x
    if isinstance(x, dict) and "players" in x and isinstance(x["players"], list): return x["players"]
    return []

def _to_str(x: Any) -> str:
    if x is None: return ""
    try:
        return str(x)
    except:
        return ""

def _extract_passthrough(obj: Any) -> List[Dict[str, Any]]:
    """Вытащить из passthrough: personId, firstName, lastName, displayName, teamId, isActive?"""
    out: List[Dict[str, Any]] = []
    for it in _as_list(obj):
        pid = _to_str(it.get("personId") or it.get("id") or it.get("playerId") or it.get("pid"))
        if not pid: continue
        first = (it.get("firstName") or it.get("first_name") or "").strip()
        last  = (it.get("lastName")  or it.get("last_name")  or "").strip()
        disp  = (it.get("displayName") or it.get("display_name") or (first + " " + last).strip()).strip()
        team  = _to_str(it.get("teamId") or it.get("tid") or it.get("team_id") or "")
        active = it.get("isActive")
        if active is None:
            # иногда бывает 'teamId'==0/None => считаем неактивным; иначе активен
            active = bool(team and team != "0")
        p = {
            "personId": pid,
            "firstName": first,
            "lastName":  last,
            "displayName": disp or (first + " " + last).strip(),
            "teamId": team,
            "isActive": bool(active),
        }
        out.append(p)
    return out

def _extract_normalized(obj: Any) -> List[Dict[str, Any]]:
    """
    Из normalized вытаскиваем хотя бы personId + headshot/url + teamId (если есть).
    Схемы бывают разные: id / personId / pid, headshot / img / image / photo / u
    """
    out: List[Dict[str, Any]] = []
    for it in _as_list(obj):
        pid = _to_str(it.get("personId") or it.get("id") or it.get("playerId") or it.get("pid"))
        if not pid: continue
        team = _to_str(it.get("teamId") or it.get("tid") or it.get("team_id") or "")
        url  = (
            it.get("headshot") or it.get("img") or it.get("image") or it.get("photo") or it.get("u") or ""
        )
        url = _to_str(url)
        out.append({
            "personId": pid,
            "teamId": team,
            "headshot": url,
        })
    return out

def _index_by_pid(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    m: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = _to_str(r.get("personId"))
        if not pid: continue
        m[pid] = r
    return m

def _merge_players(base_names: List[Dict[str, Any]], norm_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    base_names — из passthrough (есть имена),
    norm_rows — из normalized (есть headshot).
    """
    if not base_names and not norm_rows:
        return []
    names_ix = _index_by_pid(base_names)
    norm_ix  = _index_by_pid(norm_rows)
    # Берём всех из names; добавляем headshot, если есть
    merged: List[Dict[str, Any]] = []
    for pid, row in names_ix.items():
        r = dict(row)
        n = norm_ix.get(pid)
        if n:
            if (not r.get("teamId")) and n.get("teamId"):
                r["teamId"] = _to_str(n.get("teamId"))
            if n.get("headshot"):  # приклеим ссылку на фото
                r["headshot"] = _to_str(n.get("headshot"))
        merged.append(r)
    # Добавим тех, кого не было в names (на всякий случай)
    for pid, n in norm_ix.items():
        if pid not in names_ix:
            merged.append({
                "personId": pid,
                "firstName": "",
                "lastName": "",
                "displayName": pid,
                "teamId": _to_str(n.get("teamId")),
                "isActive": bool(n.get("teamId")),
                "headshot": _to_str(n.get("headshot")),
            })
    # Фильтр активных
    if ACTIVE_ONLY:
        merged = [p for p in merged if p.get("isActive", True)]
    return merged

def _classify_urls() -> Tuple[List[str], List[str]]:
    """
    Возвращает (passthrough_urls, normalized_urls)
    """
    urls: List[str] = []
    if ENV_URLS:
        urls += [u.strip() for u in ENV_URLS.split(",") if u.strip()]
    if ENV_URL_LEGACY:
        urls += [u.strip() for u in ENV_URL_LEGACY.split(",") if u.strip()]
    if not urls:
        urls = [DEFAULT_PASSTHROUGH, DEFAULT_NORMALIZED]

    pt, norm = [], []
    for u in urls:
        us = u.lower()
        if "format=passthrough" in us:
            pt.append(u)
        elif "format=normalized" in us:
            norm.append(u)
        else:
            # эвристика
            if "passthrough" in us:
                pt.append(u)
            elif "normalized" in us:
                norm.append(u)
            else:
                # неизвестно — подстрахуемся, считаем это passthrough
                pt.append(u)
    if not pt:   pt = [DEFAULT_PASSTHROUGH]
    if not norm: norm = [DEFAULT_NORMALIZED]
    return pt, norm

# -----------------------
# Публичные API для telegram.py
# -----------------------
def refresh_players() -> Tuple[int, str]:
    """
    Качаем passthrough + normalized, мерджим, сохраняем кэш в памяти.
    Возвращаем (кол-во, строка-источник).
    """
    global _PLAYERS, _LAST_SOURCE, _LAST_REFRESH_AT

    pt_urls, norm_urls = _classify_urls()
    pt_rows: List[Dict[str, Any]] = []
    norm_rows: List[Dict[str, Any]] = []

    # Сначала пытаемся passthrough
    src_used = None
    for u in pt_urls:
        j = _try_fetch(u)
        if not j: 
            continue
        rows = _extract_passthrough(j)
        if rows and len(rows) >= MIN_EXPECTED:
            pt_rows = rows
            src_used = u
            break
        # если мало, всё равно примем, но попробуем следующий
        if rows:
            pt_rows = rows
            src_used = u

    # Теперь normalized
    for u in norm_urls:
        j = _try_fetch(u)
        if not j:
            continue
        rows = _extract_normalized(j)
        if rows and len(rows) >= MIN_EXPECTED:
            norm_rows = rows
            if not src_used:  # если не было нормального passthrough
                src_used = u
            break
        if rows and not norm_rows:
            norm_rows = rows
            if not src_used:
                src_used = u

    merged = _merge_players(pt_rows, norm_rows)
    _PLAYERS = merged or []
    _LAST_SOURCE = ("merged" if (pt_rows and norm_rows) else (src_used or "none"))
    _LAST_REFRESH_AT = time.time()

    _log(f"final players count: {len(_PLAYERS)} (source={_LAST_SOURCE}) url: {src_used}")
    return (len(_PLAYERS), _LAST_SOURCE)

def get_players(force_refresh: Optional[bool] = None) -> List[Dict[str, Any]]:
    """
    Возвращает кэш игроков. Если пусто — дергает refresh_players().
    Аргумент force_refresh допускается (для совместимости), но внутри не обязателен.
    """
    global _PLAYERS
    if not _PLAYERS:
        refresh_players()
    return _PLAYERS

def find_player_by_name(q: str) -> List[Dict[str, Any]]:
    """
    Гибкий поиск по displayName / first+last (без диакритики не делаем здесь, пусть telegram.py нормализует).
    Возвращаем первые 10 совпадений.
    """
    if not q:
        return []
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
    # Апгрейд: если пусто и поиск был однословным — попробуем по отдельности first/last
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
    if d:
        return d
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName")  or "").strip()
    nm = (first + " " + last).strip()
    return nm or str(p.get("personId") or "")

# -----------------------
# Русские имена (overrides)
# -----------------------
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
    # только чтение raw, без записи
    url = f"https://raw.githubusercontent.com/{OV_GH_REPO}/{OV_GH_BRANCH}/{OV_GH_PATH}"
    try:
        j = _http_json(url, timeout=10)
        if isinstance(j, dict) and "names_ru" in j and isinstance(j["names_ru"], dict):
            out = {}
            for k,v in j["names_ru"].items():
                out[str(k)] = str(v)
            return out
        # если лежит просто dict без ключа names_ru
        if isinstance(j, dict):
            return {str(k): str(v) for k,v in j.items()}
    except Exception as e:
        _log("github overrides read err:", repr(e))
    return {}

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

# -----------------------
# Картинки: headshot + лого
# -----------------------
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
    # прокси /img?u=
    for base in IMAGE_PROXY_URLS:
        base = base.rstrip("/")
        urls.append(f"{base}/img?u={person_id}")
    # известные CDN (на всякий случай)
    # 260x190 (старый)
    urls.append(f"https://ak-static.cms.nba.com/wp-content/uploads/headshots/nba/latest/260x190/{person_id}.png")
    # 1040x760 (часто 404, но пусть будет)
    urls.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{person_id}.png")
    return urls

def ensure_headshot_png(p: Dict[str, Any]) -> Optional[bytes]:
    """
    Возвращает PNG (bytes) с портретом игрока.
    Источники: p['headshot'] -> IMAGE_PROXY_URLS -> CDN-фоллбеки.
    """
    pid = str(p.get("personId") or "")
    # 1) явная ссылка в игроке
    for key in ("headshot","img","image","photo","u"):
        url = p.get(key)
        if isinstance(url, str) and url.startswith(("http://","https://")):
            raw = _fetch_image(url)
            if raw:
                return raw
    # 2) прокси/фоллбеки
    for url in _fallback_headshot_urls(pid):
        raw = _fetch_image(url)
        if raw:
            return raw
    return None

def ensure_team_logo_png(team_id: str) -> Optional[str]:
    """
    Возвращает путь к PNG логотипу команды, если он есть локально.
    Поиск в assets/cache и assets/teams.
    """
    tid = str(team_id or "0")
    for d in LOGO_DIRS:
        p = os.path.join(d, f"{tid}.png")
        if os.path.exists(p):
            return p
    # generic?
    for d in LOGO_DIRS:
        p = os.path.join(d, "generic.png")
        if os.path.exists(p):
            return p
    return None
