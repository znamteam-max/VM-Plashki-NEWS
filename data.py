# data.py — надёжная загрузка/кеш игроков, фоллбэки, головы/логотипы
from __future__ import annotations
import os, io, json, time, re, threading, tempfile
from typing import Any, Dict, List, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from PIL import Image

# -------------------- ENV / Константы --------------------
ENV = os.environ.get

PLAYERS_USE_CUSTOM     = (ENV("PLAYERS_USE_CUSTOM", "1").strip() == "1")
PLAYERS_CUSTOM_URLS    = [u.strip() for u in ENV("PLAYERS_CUSTOM_URLS", "").split(",") if u.strip()]
# Поддержим старый одиночный ключ тоже (добавим его в список):
if ENV("PLAYERS_CUSTOM_URL", "").strip():
    PLAYERS_CUSTOM_URLS.extend([u.strip() for u in ENV("PLAYERS_CUSTOM_URL", "").split(",") if u.strip()])

PLAYERS_URL_FALLBACK   = ENV("PLAYERS_URL", "").strip()  # на всякий случай
PLAYERS_JSON_PUBLIC    = ENV("PLAYERS_JSON_URL", "").strip()  # например, https://<host>/players.json

PLAYERS_MIN_EXPECTED   = int(ENV("PLAYERS_MIN_EXPECTED", "350"))
PLAYERS_REFRESH_SECONDS= int(ENV("PLAYERS_REFRESH_SECONDS", "21600"))  # 6 часов по умолчанию
PLAYERS_CUSTOM_TIMEOUT = int(ENV("PLAYERS_CUSTOM_TIMEOUT", "20"))
PLAYERS_CUSTOM_ATTEMPTS= int(ENV("PLAYERS_CUSTOM_ATTEMPTS", "3"))
PLAYERS_SEASON         = ENV("PLAYERS_SEASON", "2025-26")

IMAGE_PROXY_URLS       = [u.strip() for u in ENV("IMAGE_PROXY_URLS", "").split(",") if u.strip()]

CACHE_TTL_SEC          = int(ENV("CACHE_TTL_SEC", "43200"))
IMG_CACHE_TTL_SEC      = int(ENV("IMG_CACHE_TTL_SEC", "604800"))  # неделя

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
PUBLIC_DIR = os.path.join(ROOT_DIR, "public")  # вдруг положишь players.json сюда
TMP_DIR   = "/tmp"

PLAYERS_CACHE_PATH     = os.path.join(TMP_DIR, "players_cache.json")
IDX_CACHE_PATH         = os.path.join(TMP_DIR, "players_index.json")

HEAD_CACHE_PATH        = os.path.join(TMP_DIR, "headshots")
os.makedirs(HEAD_CACHE_PATH, exist_ok=True)

# -------------------- Глобальное состояние --------------------
_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "players": [],            # унифицированные игроки
    "index": {},              # индекс для поиска
    "last_source": None,
    "last_url": None,
    "last_ts": 0.0,
}

def _log(*a: Any) -> None:
    try:
        print("[players]", *a, flush=True)
    except:
        pass

# -------------------- HTTP helpers --------------------
def _http_get_json(url: str, timeout: int) -> Any:
    req = Request(url, headers={"User-Agent": "vm-plashki/1.0"})
    with urlopen(req, timeout=timeout) as r:
        data = r.read()
    try:
        return json.loads(data.decode("utf-8", "ignore"))
    except Exception as e:
        _log("json parse error for", url, e)
        return None

# -------------------- Нормализация форматов --------------------
def _normalize_from_list(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    passthrough-подобный список: [{personId, firstName, lastName, teamId, isActive, photo?}, ...]
    Приводим к единому виду.
    """
    out: List[Dict[str, Any]] = []
    for it in items or []:
        pid = str(it.get("personId") or it.get("id") or it.get("playerId") or "").strip()
        if not pid:
            continue
        fn = (it.get("firstName") or it.get("first_name") or "").strip()
        ln = (it.get("lastName")  or it.get("last_name")  or "").strip()
        tid = str(it.get("teamId") or it.get("team_id") or it.get("team") or "0").strip()
        active = bool(it.get("isActive") or it.get("active") or False)
        photo = it.get("photo") or it.get("headshot") or ""
        # cdn по id (на всякий случай)
        if not photo and pid.isdigit():
            photo = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"

        out.append({
            "personId": pid,
            "firstName": fn,
            "lastName": ln,
            "teamId": tid,
            "isActive": active,
            "photo": photo,
            "fullName": (fn + " " + ln).strip(),
        })
    return out

def _normalize_from_dict(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    normalized-вариант может быть в разных обёртках: {"players": [...]}, {"data":[...]} и т.д.
    Пробуем самые типовые ключи.
    """
    for key in ("players", "data", "result", "items"):
        if isinstance(obj.get(key), list):
            return _normalize_from_list(obj[key])
    # бывает, что сам obj — это список игроков, но прилетел как dict-обёртка
    return []

def _unify_players(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return _normalize_from_list(payload)
    if isinstance(payload, dict):
        return _normalize_from_dict(payload)
    return []

# -------------------- Кеш --------------------
def _atomic_write_json(path: str, data: Any) -> None:
    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as e:
        _log("atomic write error:", e)

def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# -------------------- Загрузка игроков со стеком фоллбэков --------------------
def _fetch_from_custom(timeout: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Пробуем последовательно все URL из PLAYERS_CUSTOM_URLS.
    Возвращаем (players, used_url) или ([], None) если ни один не сработал.
    """
    if not PLAYERS_CUSTOM_URLS:
        return [], None

    for attempt in range(1, PLAYERS_CUSTOM_ATTEMPTS + 1):
        for u in PLAYERS_CUSTOM_URLS:
            try:
                payload = _http_get_json(u, timeout=timeout)
                players = _unify_players(payload)
                _log(f"custom parsed {len(players)} from {u}")
                if len(players) >= PLAYERS_MIN_EXPECTED:
                    return players, u
            except HTTPError as e:
                _log("custom get error:", e)
            except URLError as e:
                _log("custom get error:", e)
            except OSError as e:
                # Частая ошибка: OSError(16, 'Device or resource busy') — считаем транзиентной
                _log("custom get error:", e)
        time.sleep(0.25)  # маленький бэк-офф между попытками
    return [], None

def _fetch_from_public() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Если в проекте лежит public/players.json — используем.
    Или есть внешний URL в PLAYERS_JSON_URL.
    """
    # локальный public
    local_path = os.path.join(PUBLIC_DIR, "players.json")
    if os.path.exists(local_path):
        obj = _load_json(local_path)
        players = _unify_players(obj)
        if len(players) >= PLAYERS_MIN_EXPECTED:
            return players, local_path

    # внешний URL
    if PLAYERS_JSON_PUBLIC:
        try:
            obj = _http_get_json(PLAYERS_JSON_PUBLIC, timeout=10)
            players = _unify_players(obj)
            if len(players) >= PLAYERS_MIN_EXPECTED:
                return players, PLAYERS_JSON_PUBLIC
        except Exception as e:
            _log("public json load error:", e)

    return [], None

def _load_cache_if_fresh(max_age_sec: int = PLAYERS_REFRESH_SECONDS) -> Tuple[List[Dict[str, Any]], Optional[str], float]:
    obj = _load_json(PLAYERS_CACHE_PATH)
    if not isinstance(obj, dict):
        return [], None, 0.0
    players = _unify_players(obj.get("players"))
    ts = float(obj.get("ts") or 0.0)
    src = obj.get("source")
    if players and (time.time() - ts) <= max_age_sec:
        return players, src, ts
    return [], src, ts

def _save_cache(players: List[Dict[str, Any]], source: str) -> None:
    data = {"players": players, "ts": time.time(), "source": source}
    _atomic_write_json(PLAYERS_CACHE_PATH, data)

# -------------------- Индекс/поиск --------------------
def _build_index(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Простой индекс: по полной строке и по токенам фамилии/имени.
    """
    idx = {"by_id": {}, "by_token": {}}
    def put_token(tok: str, pid: str):
        if not tok: return
        tok = tok.lower()
        idx["by_token"].setdefault(tok, []).append(pid)

    for p in players:
        pid = str(p.get("personId") or "").strip()
        if not pid: 
            continue
        idx["by_id"][pid] = p
        fn = (p.get("firstName") or "").strip()
        ln = (p.get("lastName")  or "").strip()
        full = (fn + " " + ln).strip()

        # базовые токены
        for t in (fn, ln, full, pid):
            if t: put_token(t)

        # упрощённые токены: без апострофов/диакритик — только базовая чистка
        simple_full = re.sub(r"[^a-zA-Z0-9]+", "", full.lower())
        if simple_full:
            put_token(simple_full)

    return idx

def _ensure_loaded_locked(refresh: bool = False) -> None:
    """
    Загружает игроков по стеку источников. Не перетирает рабочий список,
    если новый источник отдал мусор (len < PLAYERS_MIN_EXPECTED) или ошибка сети.
    """
    # 1) если кеш свежий и refresh=False — просто читаем кеш
    if not refresh:
        cached, cached_src, cached_ts = _load_cache_if_fresh(PLAYERS_REFRESH_SECONDS)
        if cached:
            _state["players"] = cached
            _state["index"] = _build_index(cached)
            _state["last_source"] = "cache"
            _state["last_url"] = cached_src
            _state["last_ts"] = cached_ts
            _log(f"cache hit: {len(cached)} players (src={cached_src})")
            return

    # 2) пробуем custom-URL (passthrough/normalized)
    players, used = ([], None)
    if PLAYERS_USE_CUSTOM:
        players, used = _fetch_from_custom(timeout=PLAYERS_CUSTOM_TIMEOUT)

    # 3) если не вышло — public/players.json
    if len(players) < PLAYERS_MIN_EXPECTED:
        pub, pub_src = _fetch_from_public()
        if len(pub) >= PLAYERS_MIN_EXPECTED:
            players, used = pub, pub_src

    # 4) если совсем пусто — пробуем устаревший кеш (лучше старое, чем 0)
    if len(players) < PLAYERS_MIN_EXPECTED:
        old_obj = _load_json(PLAYERS_CACHE_PATH)
        if isinstance(old_obj, dict):
            old_players = _unify_players(old_obj.get("players"))
            if len(old_players) >= PLAYERS_MIN_EXPECTED:
                _state["players"] = old_players
                _state["index"]   = _build_index(old_players)
                _state["last_source"] = old_obj.get("source") or "cache(old)"
                _state["last_url"]    = old_obj.get("source")
                _state["last_ts"]     = float(old_obj.get("ts") or time.time())
                _log(f"fallback to stale cache: {len(old_players)} players")
                return

    # 5) если получили валидный список — сохраняем и индексируем
    if len(players) >= PLAYERS_MIN_EXPECTED:
        _state["players"] = players
        _state["index"]   = _build_index(players)
        _state["last_source"] = "custom" if used else "public"
        _state["last_url"]    = used
        _state["last_ts"]     = time.time()
        _save_cache(players, _state["last_url"] or _state["last_source"])
        _log(f"players ready: {len(players)} (source={_state['last_source']} url={_state['last_url']})")
        return

    # 6) вообще ничего не получилось — НЕ ПЕРЕТИРАЕМ текущее состояние
    _log("all sources failed; keeping previous in-memory players ("
         f"{len(_state.get('players') or [])})")

def ensure_players(refresh: bool = False) -> int:
    with _state_lock:
        _ensure_loaded_locked(refresh=refresh)
        return len(_state.get("players") or [])

def players_count() -> int:
    return len(_state.get("players") or [])

def players_search(q: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return []
    players = _state.get("players") or []
    idx = _state.get("index") or {}
    by_token = idx.get("by_token") or {}
    by_id = idx.get("by_id") or {}

    # прямое совпадение по токенам
    hits: List[str] = []
    qtok = q.lower()
    if qtok in by_token:
        hits.extend(by_token[qtok])

    # упрощённый токен
    simp = re.sub(r"[^a-zA-Z0-9]+", "", qtok)
    if simp and simp in by_token:
        hits.extend(by_token[simp])

    # contains по полному имени
    if len(hits) < limit:
        qnorm = q.lower()
        for p in players:
            fullname = (p.get("fullName") or "").lower()
            if qnorm in fullname:
                pid = str(p.get("personId"))
                if pid not in hits:
                    hits.append(pid)
                if len(hits) >= limit:
                    break

    # собираем ответ
    out: List[Dict[str, Any]] = []
    for pid in hits:
        if pid in by_id:
            out.append(by_id[pid])
            if len(out) >= limit:
                break
    return out

def get_player_by_query(q: str) -> Optional[Dict[str, Any]]:
    res = players_search(q, limit=1)
    return res[0] if res else None

# -------------------- Головы/логотипы --------------------
def _is_fresh(path: str, ttl_sec: int) -> bool:
    try:
        st = os.stat(path)
        return (time.time() - st.st_mtime) <= ttl_sec
    except Exception:
        return False

def _download_to(path: str, url: str, timeout: int = 15) -> bool:
    try:
        req = Request(url, headers={"User-Agent": "vm-plashki/1.0"})
        with urlopen(req, timeout=timeout) as r:
            data = r.read()
        # атомарная запись
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_", suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        _log("img get error", url, e)
        return False

def ensure_headshot_png(player_or_id: Any, timeout: int = 20, **kwargs) -> Optional[str]:
    """
    Возвращает локальный путь к PNG с головой игрока.
    Принимает либо дикт игрока, либо personId (str/int), либо URL.
    """
    # если прилетел URL напрямую
    if isinstance(player_or_id, str) and player_or_id.startswith(("http://", "https://")):
        pid = re.findall(r"/(\d+)\.png", player_or_id)
        pid = pid[0] if pid else "custom"
        dst = os.path.join(HEAD_CACHE_PATH, f"head_{pid}.png")
        if _is_fresh(dst, IMG_CACHE_TTL_SEC):
            return dst
        return dst if _download_to(dst, player_or_id, timeout=timeout) else None

    # если прилетел dict игрока
    if isinstance(player_or_id, dict):
        pid = str(player_or_id.get("personId") or "").strip()
        photo = (player_or_id.get("photo") or "").strip()
    else:
        # предполагаем personId
        pid = str(player_or_id).strip()
        photo = ""

    if not pid:
        return None

    dst = os.path.join(HEAD_CACHE_PATH, f"head_{pid}.png")
    if _is_fresh(dst, IMG_CACHE_TTL_SEC):
        return dst

    # приоритет: прокси → явная ссылка из данных → cdn
    urls: List[str] = []
    for base in IMAGE_PROXY_URLS:
        if base.endswith("/"):
            base = base[:-1]
        urls.append(f"{base}/img?u={pid}")

    if photo:
        urls.append(photo)

    urls.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png")

    for u in urls:
        if _download_to(dst, u, timeout=timeout):
            return dst
    return None

def ensure_team_logo_png(team_id: Any) -> Optional[str]:
    """
    Возвращает путь к PNG логотипа (из assets/cache или assets/teams, если есть).
    """
    from team_brand import get_team_logo_path
    tid = str(team_id or "0")
    p = get_team_logo_path(tid)
    return p if (p and os.path.exists(p)) else None
