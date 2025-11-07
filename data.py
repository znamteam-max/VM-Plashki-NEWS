# data.py — merge normalized+passthrough, search, headshots, team logos, RU-overrides (with force_refresh)
from __future__ import annotations
import os, json, time, io, re, base64, threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from PIL import Image

# опционально используем team_brand для пути логотипа
try:
    from team_brand import get_team_logo_path  # optional
except Exception:
    get_team_logo_path = None  # type: ignore

# -------------------------- ENV --------------------------
ENV = os.environ.get
PLAYERS_USE_CUSTOM      = (ENV("PLAYERS_USE_CUSTOM", "1") == "1")
PLAYERS_CUSTOM_URLS_RAW = ENV("PLAYERS_CUSTOM_URLS", "") or ENV("PLAYERS_CUSTOM_URL", "")
PLAYERS_URL_FALLBACK    = ENV("PLAYERS_URL", "")
PLAYERS_SEASON          = ENV("PLAYERS_SEASON", "2025-26")
PLAYERS_MIN_EXPECTED    = int(ENV("PLAYERS_MIN_EXPECTED", "350") or 350)
PLAYERS_CUSTOM_TIMEOUT  = int(ENV("PLAYERS_CUSTOM_TIMEOUT", "25") or 25)
PLAYERS_CUSTOM_ATTEMPTS = int(ENV("PLAYERS_CUSTOM_ATTEMPTS", "3") or 3)

IMAGE_PROXY_URLS_RAW    = ENV("IMAGE_PROXY_URLS", "")
IMG_CACHE_TTL_SEC       = int(ENV("IMG_CACHE_TTL_SEC", "604800") or 604800)  # неделя

# GitHub overrides (опционально)
OV_GH_TOKEN  = (ENV("OVERRIDES_GH_TOKEN") or "").strip()
OV_GH_REPO   = (ENV("OVERRIDES_GH_REPO")  or "").strip()
OV_GH_BRANCH = (ENV("OVERRIDES_GH_BRANCH") or "main").strip()
OV_GH_PATH   = (ENV("OVERRIDES_GH_PATH")   or "assets/players_overrides.json").strip()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
OV_LOCAL_DEFAULT = os.path.join(ASSETS_DIR, "players_overrides_default.json")
OV_LOCAL_TMP     = "/tmp/players_overrides.json"

# где искать логотипы локально
TEAM_LOGO_DIRS = [
    os.path.join(ASSETS_DIR, "cache"),
    os.path.join(ASSETS_DIR, "teams"),
]

# -------------------------- STATE --------------------------
_LOCK = threading.RLock()
_PLAYERS: List[Dict[str, Any]] = []
_INDEX_BY_ID: Dict[str, Dict[str, Any]] = {}
_LAST_REFRESH_TS: float = 0.0
_LAST_SOURCE_URL: str = "none"
_OVERRIDES: Dict[str, Any] = {}

# -------------------------- UTILS --------------------------
def _log(*a: Any) -> None:
    try: print("[data]", *a, flush=True)
    except: pass

def _http_json(url: str, timeout: int = PLAYERS_CUSTOM_TIMEOUT) -> Any:
    req = Request(url, headers={"User-Agent": "VM-Plashki/1.0"})
    with urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(text)
    except Exception:
        # если вдруг вернулся не-JSON — не роняемся
        _log("json decode error:", url, "payload head:", text[:120])
        return None

def _http_bytes(url: str, timeout: int = 20) -> Optional[bytes]:
    try:
        req = Request(url, headers={"User-Agent": "VM-Plashki/1.0"})
        with urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        _log("img get error", url, repr(e))
        return None

def _now() -> float: return time.time()

def _norm_id(v: Any) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    m = re.search(r"(\d+)", s)
    return m.group(1) if m else s

def _split_urls(raw: str) -> List[str]:
    out: List[str] = []
    for part in (raw or "").split(","):
        u = part.strip()
        if u: out.append(u)
    return out

# -------------------------- OVERRIDES --------------------------
def _read_json(path: str) -> Any:
    if not os.path.exists(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return None

def _write_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log("write json error:", e)

def _load_overrides_from_github() -> Dict[str, Any]:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return {}
    try:
        req = Request(
            f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}?ref={OV_GH_BRANCH}",
            headers={
                "User-Agent": "VM-Plashki/1.0",
                "Authorization": f"Bearer {OV_GH_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8"))
        if not isinstance(j, dict): return {}
        content_b64 = j.get("content")
        if not content_b64: return {}
        raw = base64.b64decode(content_b64).decode("utf-8", "ignore")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log("github get overrides error:", repr(e))
        return {}

def _load_overrides() -> None:
    global _OVERRIDES
    merged: Dict[str, Any] = {}
    loc = _read_json(OV_LOCAL_DEFAULT)
    if isinstance(loc, dict): merged.update(loc)
    tmp = _read_json(OV_LOCAL_TMP)
    if isinstance(tmp, dict): merged.update(tmp)
    gh = _load_overrides_from_github()
    if isinstance(gh, dict): merged.update(gh)
    _OVERRIDES = merged
    _log("overrides loaded:", len(_OVERRIDES))

def _save_overrides_local() -> None:
    _write_json(OV_LOCAL_TMP, _OVERRIDES)

def overrides_get_name_ru(person_id: str) -> Optional[str]:
    pid = _norm_id(person_id)
    v = (_OVERRIDES.get(pid) or {}).get("ru_name")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None

def overrides_set_name_ru(person_id: str, ru_name: str) -> bool:
    try:
        pid = _norm_id(person_id)
        ru_name = (ru_name or "").strip()
        if not ru_name: return False
        entry = _OVERRIDES.get(pid) or {}
        entry["ru_name"] = ru_name
        _OVERRIDES[pid] = entry
        _save_overrides_local()
        _log("override ru_name saved:", pid, ru_name)
        return True
    except Exception as e:
        _log("override set ru_name error:", e)
        return False

# алиас под старые импорты
def overrides_save_name_ru(person_id: str, ru_name: str) -> bool:
    return overrides_set_name_ru(person_id, ru_name)

def overrides_get_team(person_id: str) -> Optional[str]:
    pid = _norm_id(person_id)
    tid = (_OVERRIDES.get(pid) or {}).get("teamId")
    if tid is None: return None
    return str(tid)

def overrides_set_team(person_id: str, team_id: str) -> bool:
    try:
        pid = _norm_id(person_id)
        tid = _norm_id(team_id)
        entry = _OVERRIDES.get(pid) or {}
        if tid == "0":
            if "teamId" in entry: del entry["teamId"]
        else:
            entry["teamId"] = tid
        _OVERRIDES[pid] = entry
        _save_overrides_local()
        _log("override team saved:", pid, tid)
        return True
    except Exception as e:
        _log("override set team error:", e)
        return False

# алиас под старые импорты
def overrides_save_team(person_id: str, team_id: str) -> bool:
    return overrides_set_team(person_id, team_id)

def overrides_push_to_github(commit_msg: str = "update overrides", author: str = "bot <bot@example.com>") -> bool:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return False
    try:
        # получить текущий sha
        req = Request(
            f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}?ref={OV_GH_BRANCH}",
            headers={
                "User-Agent": "VM-Plashki/1.0",
                "Authorization": f"Bearer {OV_GH_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(req, timeout=15) as r:
            meta = json.loads(r.read().decode("utf-8"))
        sha = meta.get("sha") if isinstance(meta, dict) else None

        # новый контент
        raw = json.dumps(_OVERRIDES, ensure_ascii=False, indent=2).encode("utf-8")
        content_b64 = base64.b64encode(raw).decode("utf-8")

        payload = json.dumps({
            "message": commit_msg,
            "content": content_b64,
            "branch": OV_GH_BRANCH,
            **({"sha": sha} if sha else {}),
            "committer": {
                "name": author.split("<")[0].strip(),
                "email": (author.split("<")[-1].strip(" >") if "<" in author else "bot@example.com")
            },
        }).encode("utf-8")

        put = Request(
            f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}",
            data=payload,
            headers={
                "User-Agent": "VM-Plashki/1.0",
                "Authorization": f"Bearer {OV_GH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        with urlopen(put, timeout=20) as r:
            _ = r.read()
        _log("overrides pushed to GitHub")
        return True
    except Exception as e:
        _log("github put overrides error:", repr(e))
        return False

# -------------------------- PARSERS --------------------------
def _parse_normalized(payload: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(payload, (list, dict)):
        return out
    items: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("players"), list):
        items = payload["players"]

    for it in items:
        if not isinstance(it, dict): continue
        pid = _norm_id(it.get("id") or it.get("personId"))
        if not pid: continue
        photo = it.get("photo") or it.get("headshot") or it.get("headshot_url")
        d = out.get(pid) or {"personId": pid}
        if photo: d["headshot_url"] = str(photo)
        out[pid] = d
    return out

def _parse_passthrough(payload: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(payload, (list, dict)):
        return out
    items: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("players"), list):
        items = payload["players"]

    for it in items:
        if not isinstance(it, dict): continue
        pid = _norm_id(it.get("personId") or it.get("id"))
        if not pid: continue
        first = (it.get("firstName") or "").strip()
        last  = (it.get("lastName") or "").strip()
        disp  = (it.get("displayName") or f"{first} {last}").strip()
        team  = _norm_id(it.get("teamId") or "0")
        active = bool(it.get("isActive")) if it.get("isActive") is not None else True
        photo = it.get("photo") or it.get("headshot") or it.get("headshot_url")
        out[pid] = {
            "personId": pid,
            "firstName": first,
            "lastName":  last,
            "displayName": disp,
            "teamId": team,
            "isActive": active,
            "headshot_url": str(photo) if photo else None,
        }
    return out

def _merge_players(nz: Dict[str, Dict[str, Any]], pt: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    ids = set(nz.keys()) | set(pt.keys())
    out: List[Dict[str, Any]] = []
    for pid in ids:
        a = pt.get(pid) or {"personId": pid}
        b = nz.get(pid) or {}
        person = dict(a)  # имена/команды — из passthrough
        headshot = b.get("headshot_url") or a.get("headshot_url")
        if not headshot:
            headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
        person["headshot_url"] = headshot
        disp = (person.get("displayName") or f"{person.get('firstName','').strip()} {person.get('lastName','').strip()}").strip()
        person["displayName"] = disp or pid
        # override teamId из overrides (если есть)
        ov_team = overrides_get_team(pid)
        if ov_team:
            person["teamId"] = ov_team
        out.append(person)
    out.sort(key=lambda p: (not p.get("isActive", True), (p.get("lastName") or p.get("displayName") or "")))
    return out

def _fetch_custom_players() -> Tuple[List[Dict[str, Any]], str]:
    urls = _split_urls(PLAYERS_CUSTOM_URLS_RAW)
    if not urls and PLAYERS_URL_FALLBACK:
        urls = [PLAYERS_URL_FALLBACK]
    if not urls:
        return [], "none"

    normalized_acc: Dict[str, Dict[str, Any]] = {}
    passthrough_acc: Dict[str, Dict[str, Any]] = {}
    last_ok = "none"

    for attempt in range(max(1, PLAYERS_CUSTOM_ATTEMPTS)):
        for u in urls:
            try:
                j = _http_json(u, timeout=PLAYERS_CUSTOM_TIMEOUT)
                if j is None:
                    continue
                lo = u.lower()
                if "format=normalized" in lo or "normalized" in lo:
                    part = _parse_normalized(j)
                    if part: normalized_acc.update(part); last_ok = u
                elif "format=passthrough" in lo or "passthrough" in lo:
                    part = _parse_passthrough(j)
                    if part: passthrough_acc.update(part); last_ok = u
                else:
                    # попытка угадать
                    pt = _parse_passthrough(j)
                    nz = _parse_normalized(j)
                    if pt: passthrough_acc.update(pt); last_ok = u
                    elif nz: normalized_acc.update(nz); last_ok = u
                    else: _log("unknown schema:", u)
            except (HTTPError, URLError) as e:
                _log("custom get error:", e)
            except Exception as e:
                _log("custom get error:", repr(e))
        if passthrough_acc or normalized_acc:
            break
        time.sleep(0.2)

    players = _merge_players(normalized_acc, passthrough_acc)
    return players, last_ok

# -------------------------- PUBLIC API --------------------------
def refresh_players() -> Tuple[int, str]:
    """Обновляет пул игроков; возвращает (count, source_url)."""
    global _PLAYERS, _INDEX_BY_ID, _LAST_REFRESH_TS, _LAST_SOURCE_URL
    with _LOCK:
        if PLAYERS_USE_CUSTOM:
            players, src = _fetch_custom_players()
            _PLAYERS = players if isinstance(players, list) else []
            _INDEX_BY_ID = {str(p.get("personId")): p for p in _PLAYERS if isinstance(p, dict)}
            _LAST_REFRESH_TS = _now()
            _LAST_SOURCE_URL = src if isinstance(src, str) else "custom"
            _log("final players count:", len(_PLAYERS), "(source=custom)", "url:", _LAST_SOURCE_URL)
            return len(_PLAYERS), _LAST_SOURCE_URL
        else:
            _PLAYERS, _INDEX_BY_ID = [], {}
            _LAST_REFRESH_TS = _now()
            _LAST_SOURCE_URL = "none"
            return 0, "none"

def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Вернёт список игроков; при force_refresh=True сделает refresh перед возвратом."""
    if force_refresh:
        try:
            refresh_players()
        except Exception as e:
            _log("refresh on get_players failed:", repr(e))
    with _LOCK:
        return list(_PLAYERS)

def ensure_players(min_count: int = 1) -> Tuple[bool, int]:
    """Гарантирует наличие игроков (минимум min_count). Возвращает (ready, count)."""
    with _LOCK:
        cnt = len(_PLAYERS)
    if cnt >= min_count:
        return True, cnt
    try:
        refresh_players()
    except Exception as e:
        _log("ensure failed:", repr(e))
    with _LOCK:
        cnt2 = len(_PLAYERS)
    return (cnt2 >= min_count), cnt2

def players_ready() -> bool:
    with _LOCK:
        return len(_PLAYERS) >= PLAYERS_MIN_EXPECTED or len(_PLAYERS) > 0

def get_player_by_id(person_id: str) -> Optional[Dict[str, Any]]:
    pid = _norm_id(person_id)
    with _LOCK:
        p = _INDEX_BY_ID.get(pid)
    return p if isinstance(p, dict) else None

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("ё", "е")
    return re.sub(r"\s+", " ", s)

def _player_name_variants(p: Dict[str, Any]) -> List[str]:
    pid = str(p.get("personId"))
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName") or "").strip()
    disp  = (p.get("displayName") or "").strip()
    ru    = overrides_get_name_ru(pid) or ""
    vs = set()
    for v in (first, last, f"{first} {last}", disp, ru):
        n = _norm(v)
        if n: vs.add(n)
    return list(vs)

def find_player_by_name(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = _norm(query)
    if not q:
        return []
    with _LOCK:
        pool = list(_PLAYERS)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for p in pool:
        if not isinstance(p, dict):  # защита от случайных типов
            continue
        score = 0
        for v in _player_name_variants(p):
            if v == q: score = max(score, 3)
            elif v.startswith(q) or q in v: score = max(score, 2)
        if score == 0 and " " not in q:
            if (p.get("lastName") or "").lower().startswith(q):
                score = 1
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda t: (-t[0], (t[1].get("lastName") or t[1].get("displayName") or "")))
    return [p for _, p in scored[:limit]]

def display_name_for(p: Dict[str, Any]) -> str:
    pid = str(p.get("personId") or "")
    ru = overrides_get_name_ru(pid)
    if ru: return ru
    disp = (p.get("displayName") or "").strip()
    if disp: return disp
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName") or "").strip()
    full = f"{first} {last}".strip()
    return full or pid

# -------------------------- IMAGES: HEADSHOTS + TEAM LOGOS --------------------------
_IMG_CACHE_DIR = "/tmp/img_cache"
os.makedirs(_IMG_CACHE_DIR, exist_ok=True)

def _img_cache_path(key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", key)
    return os.path.join(_IMG_CACHE_DIR, safe)

def _load_image_from_bytes(raw: Optional[bytes]) -> Optional[Image.Image]:
    if not raw: return None
    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None

def ensure_headshot_png(player_or_id: Any, timeout: int = 20) -> Optional[Image.Image]:
    """Возвращает PIL.Image (RGBA) головы игрока с кэшем в /tmp."""
    if isinstance(player_or_id, dict):
        pid = _norm_id(player_or_id.get("personId"))
        primary_url = (player_or_id.get("headshot_url") or "").strip()
    else:
        pid = _norm_id(player_or_id)
        primary_url = ""

    candidates: List[str] = []
    if primary_url:
        candidates.append(primary_url)

    # Прокси
    for proxy in _split_urls(IMAGE_PROXY_URLS_RAW):
        if "{id}" in proxy:
            candidates.append(proxy.replace("{id}", pid))
        else:
            candidates.append(f"{proxy.rstrip('/')}/img?u={pid}")
            candidates.append(f"{proxy.rstrip('/')}/{pid}.png")

    # CDN fallback
    candidates.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png")

    cache_path = _img_cache_path(f"head_{pid}.png")
    if os.path.exists(cache_path):
        try:
            if _now() - os.path.getmtime(cache_path) <= IMG_CACHE_TTL_SEC:
                return Image.open(cache_path).convert("RGBA")
        except Exception:
            pass

    for u in candidates:
        raw = _http_bytes(u, timeout=timeout)
        im = _load_image_from_bytes(raw)
        if im:
            try: im.save(cache_path, "PNG")
            except Exception: pass
            return im
    return None

def _find_logo_file(team_id: str) -> Optional[str]:
    tid = _norm_id(team_id)
    # 1) если есть team_brand.get_team_logo_path — используем
    if callable(get_team_logo_path):
        try:
            p = get_team_logo_path(tid)
            if p and os.path.exists(p):
                return p
        except Exception:
            pass
    # 2) прямой поиск по папкам
    for d in TEAM_LOGO_DIRS:
        p = os.path.join(d, f"{tid}.png")
        if os.path.exists(p): return p
    for d in TEAM_LOGO_DIRS:
        try:
            for fn in os.listdir(d):
                if fn.lower().endswith(".png") and tid in fn:
                    return os.path.join(d, fn)
        except FileNotFoundError:
            continue
    # 3) generic
    for d in TEAM_LOGO_DIRS:
        gp = os.path.join(d, "generic.png")
        if os.path.exists(gp): return gp
    return None

def ensure_team_logo_png(team_id: Any, timeout: int = 10) -> Optional[Image.Image]:
    """
    Возвращает PIL.Image (RGBA) логотипа *локально*.
    Сначала ищет файл в assets/cache и assets/teams, кэширует копию в /tmp.
    Никаких сетевых загрузок здесь не делаем.
    """
    tid = _norm_id(team_id)
    if not tid or tid == "0":
        return None

    cache_path = _img_cache_path(f"logo_{tid}.png")
    if os.path.exists(cache_path):
        try:
            if _now() - os.path.getmtime(cache_path) <= IMG_CACHE_TTL_SEC:
                return Image.open(cache_path).convert("RGBA")
        except Exception:
            pass

    src = _find_logo_file(tid)
    if not src:
        return None
    try:
        im = Image.open(src).convert("RGBA")
        try: im.save(cache_path, "PNG")
        except Exception: pass
        return im
    except Exception:
        return None

# -------------------------- INIT --------------------------
try:
    _load_overrides()
except Exception as e:
    _log("overrides load error at init:", repr(e))
