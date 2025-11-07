# data.py — загрузка списка игроков, поиск, кеш, фото и логотипы
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import os, io, json, time, re, unicodedata, traceback
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# --- CONFIG из окружения ---
PLAYERS_CUSTOM_URLS = os.getenv("PLAYERS_CUSTOM_URLS","").strip()
PLAYERS_CUSTOM_URL  = os.getenv("PLAYERS_CUSTOM_URL","").strip()
PLAYERS_URL         = os.getenv("PLAYERS_URL","").strip()
PLAYERS_SEASON      = os.getenv("PLAYERS_SEASON","").strip() or "2025-26"

PLAYERS_MIN_EXPECTED = int(os.getenv("PLAYERS_MIN_EXPECTED","300") or "300")
PLAYERS_CUSTOM_TIMEOUT = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT","25") or "25")
PLAYERS_CUSTOM_ATTEMPTS= int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS","2") or "2")

# Overrides (в рантайме может прилетать через env PLAYERS_OVERRIDES_JSON)
OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","assets/players_overrides.json").strip()

IMAGE_PROXY_URLS = [u.strip() for u in os.getenv("IMAGE_PROXY_URLS","").split(",") if u.strip()]

# --- INTERNAL STATE ---
_PLAYERS_IDX: Dict[str, Dict[str,Any]] = {}
_PLAYERS_LIST: List[Dict[str,Any]] = []
_LAST_REFRESH_META: Dict[str,Any] = {}

def _log(*args: Any) -> None:
    try: print("[players]", *args, flush=True)
    except Exception: pass

def _http_json(url: str, timeout: int = 25) -> Any:
    req = Request(url, headers={"User-Agent":"vm-plashki-news/1.0"})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def _sources() -> List[str]:
    urls: List[str] = []
    if PLAYERS_CUSTOM_URLS:
        urls.extend([u.strip() for u in PLAYERS_CUSTOM_URLS.split(",") if u.strip()])
    if PLAYERS_CUSTOM_URL:
        urls.extend([u.strip() for u in PLAYERS_CUSTOM_URL.split(",") if u.strip()])
    if PLAYERS_URL:
        urls.append(PLAYERS_URL.strip())
    # уникализируем с сохранением порядка
    out: List[str] = []
    seen = set()
    for u in urls:
        if u not in seen:
            out.append(u); seen.add(u)
    return out

def _coerce_player(obj: Dict[str,Any]) -> Optional[Dict[str,Any]]:
    """
    Приводим элемент к виду:
      personId(str), firstName, lastName, displayName, teamId(str), isActive(bool)
    """
    if not isinstance(obj, dict): return None
    pid = obj.get("personId") or obj.get("id") or obj.get("playerId")
    if not pid: return None
    pid = str(pid)
    first = obj.get("firstName") or obj.get("first_name") or ""
    last  = obj.get("lastName")  or obj.get("last_name")  or ""
    disp  = obj.get("displayName") or (first + " " + last).strip()
    team  = str(obj.get("teamId") or obj.get("team_id") or obj.get("team") or "0")
    act   = bool(obj.get("isActive", True))
    # допускаем, что прокси мог вернуть nested поля
    if not first and isinstance(obj.get("player"), dict):
        p2 = obj["player"]
        first = p2.get("firstName",""); last = p2.get("lastName","")
        disp = p2.get("displayName") or (first + " " + last).strip()
    return {
        "personId": pid,
        "firstName": first,
        "lastName": last,
        "displayName": disp,
        "teamId": team,
        "isActive": act
    }

def _parse_players(doc: Any) -> List[Dict[str,Any]]:
    """
    Поддержка разных форматов: list, {"players":[...]}, {"result":[...]} и т.п.
    """
    arr: List[Any] = []
    if isinstance(doc, list):
        arr = doc
    elif isinstance(doc, dict):
        for k in ("players","result","data","items"):
            if isinstance(doc.get(k), list):
                arr = doc.get(k)  # type: ignore
                break
    out: List[Dict[str,Any]] = []
    for it in arr:
        p = _coerce_player(it)
        if p: out.append(p)
    return out

def _apply_overrides(players: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    # 1) overrides из переменной окружения (рантайм)
    env_json = os.getenv("PLAYERS_OVERRIDES_JSON","").strip()
    ovr: Dict[str,Any] = {}
    if env_json:
        try:
            ovr = json.loads(env_json)
            if not isinstance(ovr, dict): ovr = {}
        except Exception:
            ovr = {}
    # 2) попытка подтянуть GitHub overrides (не критично, ошибки игнорим)
    if not ovr and OV_GH_REPO and OV_GH_PATH:
        try:
            raw_url = f"https://raw.githubusercontent.com/{OV_GH_REPO}/{OV_GH_BRANCH}/{OV_GH_PATH}"
            req = Request(raw_url, headers={
                "User-Agent":"vm-plashki-news/1.0",
                **({"Authorization": f"token {OV_GH_TOKEN}"} if OV_GH_TOKEN else {})
            })
            with urlopen(req, timeout=10) as r:
                txt = r.read().decode("utf-8")
            ovr = json.loads(txt) if txt else {}
            if not isinstance(ovr, dict): ovr = {}
        except Exception as e:
            _log("github get error:", repr(e))
    if not ovr:
        return players

    # применим patch по personId
    by_id = {p["personId"]: p for p in players}
    for pid, patch in ovr.items():
        p = by_id.get(str(pid))
        if not p: continue
        if isinstance(patch, dict):
            # поддерживаем поля: firstName, lastName, displayName, teamId
            for k in ("firstName","lastName","displayName","teamId","isActive"):
                if k in patch:
                    p[k] = patch[k]
    return list(by_id.values())

def refresh_players() -> Tuple[int, Dict[str,Any]]:
    """
    Обновляет кеш игроков, возвращает (count, meta)
    """
    global _PLAYERS_IDX, _PLAYERS_LIST, _LAST_REFRESH_META
    urls = _sources()
    if not urls:
        raise RuntimeError("no_source_available")

    last_err = None
    parsed: List[Dict[str,Any]] = []
    used_url = None
    for url in urls:
        try:
            doc = _http_json(url, timeout=PLAYERS_CUSTOM_TIMEOUT)
            items = _parse_players(doc)
            _log("custom parsed", len(items), "from", url)
            if len(items) >= PLAYERS_MIN_EXPECTED:
                parsed = items
                used_url = url
                break
        except Exception as e:
            last_err = e
            _log("custom get error:", repr(e))

    if not parsed:
        if last_err:
            raise last_err
        raise RuntimeError("no_source_parsed")

    parsed = _apply_overrides(parsed)
    _PLAYERS_LIST = parsed
    _PLAYERS_IDX = {p["personId"]: p for p in parsed}
    _LAST_REFRESH_META = {"ts": int(time.time()), "source": "custom", "url": used_url or ""}
    return (len(_PLAYERS_LIST), dict(_LAST_REFRESH_META))

def drop_players_cache() -> None:
    global _PLAYERS_IDX, _PLAYERS_LIST, _LAST_REFRESH_META
    _PLAYERS_IDX = {}
    _PLAYERS_LIST = []
    _LAST_REFRESH_META = {}

def get_players_index() -> Dict[str,Dict[str,Any]]:
    return _PLAYERS_IDX

def _find_candidates(q: str, limit: int = 10) -> List[Dict[str,Any]]:
    qn = _norm(q)
    out: List[Tuple[int,Dict[str,Any]]] = []
    for p in _PLAYERS_LIST:
        disp = (p.get("displayName") or (p.get("firstName","")+" "+p.get("lastName",""))).strip()
        nm = _norm(disp)
        score = 0
        if nm == qn: score = 100
        elif nm.startswith(qn): score = 90
        elif qn in nm: score = 75
        elif _norm(p.get("lastName","")).startswith(qn): score = 60
        if score>0:
            out.append((score,p))
    out.sort(key=lambda x: -x[0])
    return [p for _,p in out[:limit]]

def find_player_by_name(q: str) -> List[Dict[str,Any]]:
    if not _PLAYERS_LIST:
        try: refresh_players()
        except Exception: pass
    return _find_candidates(q, limit=5)

# --- IMAGES ---
def _http_bytes(url: str, timeout: int = 20) -> bytes:
    req = Request(url, headers={"User-Agent":"vm-plashki-news/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def ensure_headshot_png(person_id: str) -> bytes:
    """
    Порядок:
    1) локальный кеш: assets/cache/head_<pid>.png
    2) CDN NBA: https://cdn.nba.com/headshots/nba/latest/1040x760/<pid>.png
    3) IMAGE_PROXY_URLS: {base}/img?u=<pid>
    """
    pid = str(person_id)
    local = os.path.join("assets","cache", f"head_{pid}.png")
    if os.path.exists(local):
        with open(local,"rb") as f: return f.read()
    # CDN
    cdn = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
    try:
        return _http_bytes(cdn, timeout=20)
    except Exception as e:
        _log("img get error", cdn, repr(e))
    # Proxy chain
    for base in IMAGE_PROXY_URLS:
        u = base.rstrip("/") + f"/img?u={pid}"
        try:
            return _http_bytes(u, timeout=20)
        except Exception as e:
            _log("img get error", u, repr(e))
    raise RuntimeError("headshot_not_found")

def ensure_team_logo_png(team_id: str) -> bytes:
    """
    1) локальный кеш: assets/cache/logo_<teamId>.png  (вы сказали, что «реальные логотипы лежат тут»)
    2) иначе — поднимаем placeholder 1x1 прозрачный
    """
    tid = str(team_id)
    local = os.path.join("assets","cache", f"logo_{tid}.png")
    if os.path.exists(local):
        with open(local,"rb") as f: return f.read()
    # прозрачный пиксель
    try:
        from PIL import Image
        im = Image.new("RGBA", (1,1), (0,0,0,0))
        bio = io.BytesIO()
        im.save(bio, format="PNG")
        return bio.getvalue()
    except Exception:
        return b"\x89PNG\r\n\x1a\n"  # minimal header fallback (хак)
