# data.py — источники игроков, поиск, оверрайды имён, фото и логотипы
from __future__ import annotations
import os, io, re, json, time, unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

# ========= Конфиг из ENV =========
DEBUG = os.getenv("DEBUG", "1") in ("1", "true", "yes")

PLAYERS_SEASON          = os.getenv("PLAYERS_SEASON", "").strip() or "2025-26"
PLAYERS_MIN_EXPECTED    = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))
PLAYERS_CUSTOM_TIMEOUT  = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT", "15"))
PLAYERS_CUSTOM_ATTEMPTS = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS", "3"))
PLAYERS_CACHE_TTL       = int(os.getenv("PLAYERS_CACHE_TTL", "43200"))  # сек

# кандидаты URL из двух переменных (обе поддерживаем)
def _split_csv(v: str) -> List[str]:
    return [p.strip() for p in (v or "").split(",") if p.strip()]

PLAYERS_CUSTOM_URLS = _split_csv(os.getenv("PLAYERS_CUSTOM_URLS", ""))
PLAYERS_CUSTOM_URLS += _split_csv(os.getenv("PLAYERS_CUSTOM_URL", ""))

# Фолбэк, если вообще пусто
if not PLAYERS_CUSTOM_URLS:
    # твой Cloudflare Worker normalized + passthrough
    WORKER = "https://nba-players-proxy.znamteam-903.workers.dev"
    PLAYERS_CUSTOM_URLS = [
        f"{WORKER}/players?season={PLAYERS_SEASON}&format=normalized",
        f"{WORKER}/players?season={PLAYERS_SEASON}&format=passthrough",
    ]

IMAGE_PROXY_URLS = _split_csv(os.getenv("IMAGE_PROXY_URLS", ""))

# ========= Пути =========
ROOT_DIR    = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR  = os.path.join(ROOT_DIR, "assets")
TMP_PLAYERS = "/tmp/players.json"
TMP_OVR     = "/tmp/players_overrides.json"
ASSET_OVR   = os.path.join(ASSETS_DIR, "players_overrides.json")

# ========= Лог =========
def _log(*a: Any) -> None:
    if DEBUG:
        try: print(*a, flush=True)
        except: pass

# ========= Утилиты HTTP =========
def _http_json(url: str, timeout: int) -> Any:
    req = UrlRequest(url, headers={"User-Agent": "vm-plashki-news/1.0"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "ignore")
        try:
            return json.loads(raw)
        except Exception:
            return None

def _http_bytes(url: str, timeout: int) -> Optional[bytes]:
    req = UrlRequest(url, headers={"User-Agent": "vm-plashki-news/1.0"})
    with http_urlopen(req, timeout=timeout) as r:
        return r.read()

# ========= Кэш игроков =========
_PLAYERS_CACHE: List[Dict[str, Any]] = []
_PLAYERS_TS: float = 0.0

def _cache_valid() -> bool:
    return bool(_PLAYERS_CACHE) and (time.time() - _PLAYERS_TS) < PLAYERS_CACHE_TTL

def _save_tmp_players(players: List[Dict[str, Any]]) -> None:
    try:
        with open(TMP_PLAYERS, "w", encoding="utf-8") as f:
            json.dump(players, f, ensure_ascii=False)
    except Exception as e:
        _log("[data] save tmp error:", e)

def _load_tmp_players() -> List[Dict[str, Any]]:
    if not os.path.exists(TMP_PLAYERS): return []
    try:
        with open(TMP_PLAYERS, "r", encoding="utf-8") as f:
            arr = json.load(f)
        return arr if isinstance(arr, list) else []
    except Exception:
        return []

def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    global _PLAYERS_CACHE, _PLAYERS_TS
    if not force_refresh and _cache_valid():
        return _PLAYERS_CACHE
    # пробуем загрузить из /tmp, если глобальный кэш пуст
    if not _PLAYERS_CACHE:
        arr = _load_tmp_players()
        if arr:
            _PLAYERS_CACHE = arr
            _PLAYERS_TS = time.time()
            return _PLAYERS_CACHE
    return _PLAYERS_CACHE

# ========= Парсинг форматов =========
def _norm_player(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Приводим к единому виду:
    {
      personId: str,
      firstName: str,
      lastName: str,
      displayName: str,
      teamId: str,
      isActive: bool,
      photo: str | None
    }
    """
    try:
        pid = str(obj.get("personId") or obj.get("id") or "").strip()
        if not pid: return None
        first = (obj.get("firstName") or "").strip()
        last  = (obj.get("lastName") or "").strip()
        disp  = (obj.get("displayName") or f"{first} {last}").strip()
        team  = str(obj.get("teamId") or obj.get("team", {}).get("teamId") or "0")
        active = bool(obj.get("isActive", True))
        photo  = obj.get("photo") or obj.get("headshot") or None
        return {
            "personId": pid,
            "firstName": first,
            "lastName": last,
            "displayName": disp if disp.strip() else f"{first} {last}".strip(),
            "teamId": team,
            "isActive": active,
            "photo": photo
        }
    except Exception:
        return None

def _parse_players_payload(j: Any) -> List[Dict[str, Any]]:
    if not j: return []
    out: List[Dict[str, Any]] = []
    # normalized: list[dict]
    if isinstance(j, list):
        for it in j:
            if isinstance(it, dict):
                n = _norm_player(it)
                if n: out.append(n)
        return out
    # passthrough-like: dict with "players" or "sample" or "league.standard"
    if isinstance(j, dict):
        if isinstance(j.get("players"), list):
            for it in j["players"]:
                if isinstance(it, dict):
                    n = _norm_player(it)
                    if n: out.append(n)
            return out
        if isinstance(j.get("sample"), list):  # как было в selftest
            for it in j["sample"]:
                if isinstance(it, dict):
                    n = _norm_player(it)
                    if n: out.append(n)
            return out
        # старый NBA json
        league = j.get("league")
        if isinstance(league, dict) and isinstance(league.get("standard"), list):
            for it in league["standard"]:
                if isinstance(it, dict):
                    n = _norm_player(it)
                    if n: out.append(n)
            return out
    return out

# ========= Обновление игроков =========
def refresh_players() -> Tuple[int, Dict[str, Any]]:
    """
    Возвращает (count, info). info: {"source": "...", "url": "..."} или {"error": "..."}
    """
    global _PLAYERS_CACHE, _PLAYERS_TS
    candidates = [u for u in PLAYERS_CUSTOM_URLS if u]
    _log("[players] custom candidates:", len(candidates))
    last_err = None
    for url in candidates:
        for attempt in range(1, PLAYERS_CUSTOM_ATTEMPTS + 1):
            try:
                j = _http_json(url, timeout=PLAYERS_CUSTOM_TIMEOUT)
                arr = _parse_players_payload(j)
                _log(f"[players] custom parsed {len(arr)} from {url}")
                if len(arr) >= PLAYERS_MIN_EXPECTED:
                    _PLAYERS_CACHE = arr
                    _PLAYERS_TS = time.time()
                    _save_tmp_players(arr)
                    return len(arr), {"source": "custom", "url": url}
            except (HTTPError, URLError) as e:
                last_err = repr(e)
                _log("[players] custom get error:", e)
            except Exception as e:
                last_err = repr(e)
                _log("[players] custom parse error:", e)
    # не нашли валидный
    return 0, {"error": last_err or "no_source_available"}

# ========= Поиск игроков =========
def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

def find_player_by_name(query: str) -> List[Dict[str, Any]]:
    q = _normalize(query)
    if not q: return []
    arr = get_players(False)
    out: List[Dict[str, Any]] = []
    for p in arr:
        disp = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if not disp: continue
        if q in _normalize(disp):
            out.append(p)
            if len(out) >= 10:
                break
    return out

def display_name_for(p: Dict[str, Any]) -> str:
    disp = p.get("displayName") or ""
    if disp.strip():
        return disp.strip()
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName") or "").strip()
    return f"{first} {last}".strip()

# ========= Русские имена (овверрайды) =========
def _load_overrides() -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for path in (ASSET_OVR, TMP_OVR):
        if not os.path.exists(path): continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
            if isinstance(j, dict):
                merged.update(j)
        except Exception:
            pass
    return merged

def _save_overrides(data: Dict[str, Any]) -> None:
    try:
        with open(TMP_OVR, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        _log("[overrides] save error:", e)

def overrides_get_name_ru(person_id: str) -> Optional[str]:
    ov = _load_overrides()
    rec = ov.get(str(person_id)) if isinstance(ov, dict) else None
    if isinstance(rec, dict):
        ru = rec.get("name_ru")
        if isinstance(ru, str) and ru.strip():
            return ru.strip()
    return None

def overrides_save_name_ru(person_id: str, name_ru: str) -> bool:
    try:
        ov = _load_overrides()
        pid = str(person_id)
        rec = ov.get(pid) or {}
        rec["name_ru"] = name_ru.strip()
        ov[pid] = rec
        _save_overrides(ov)
        _log("[overrides] saved name_ru for", pid, "->", name_ru)
        return True
    except Exception as e:
        _log("[overrides] save name err:", e)
        return False

# ========= Фото игрока =========
def _try_headshot_urls(person_id: str) -> List[str]:
    pid = str(person_id).strip()
    urls: List[str] = []
    # Прокси (если есть)
    for base in IMAGE_PROXY_URLS:
        b = base.rstrip("/")
        # стандартный роут твоего воркера
        urls.append(f"{b}/img?u={pid}")
        urls.append(f"{b}/headshots/{pid}.png")
    # Прямые пути NBA CDN (часто блокирует, но пробуем)
    urls.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png")
    urls.append(f"https://cdn.nba.com/headshots/nba/latest/260x190/{pid}.png")
    return urls

def ensure_headshot_png(player: Dict[str, Any], timeout: int = 10):
    """
    Возвращает BYTES PNG (чтобы telegram мог слать сразу).
    telegram.py сам распарсит в Image при необходимости.
    """
    # если в источнике уже есть photo-URL — попробуем сначала его
    photo = player.get("photo")
    tried: List[str] = []
    if isinstance(photo, str) and photo.startswith("http"):
        tried.append(photo)

    for u in tried + _try_headshot_urls(player.get("personId") or player.get("id") or ""):
        try:
            b = _http_bytes(u, timeout=timeout)
            if b and len(b) > 256:
                return b
        except Exception:
            continue
    return None

# ========= Логотип команды =========
def ensure_team_logo_png(team_id: str) -> Optional[str]:
    """
    Ищем реальные логотипы в assets/cache/, затем любые в assets/teams/.
    Возвращаем путь к файлу PNG или None.
    """
    team_id = str(team_id or "0")
    candidates = [
        os.path.join(ASSETS_DIR, "cache", f"{team_id}.png"),
        os.path.join(ASSETS_DIR, "cache", f"logo_{team_id}.png"),
        os.path.join(ASSETS_DIR, "teams", f"{team_id}.png"),
        os.path.join(ASSETS_DIR, "teams", f"logo_{team_id}.png"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None
