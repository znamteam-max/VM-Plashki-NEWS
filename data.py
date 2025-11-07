# data.py — источники игроков + кэш + headshots
from __future__ import annotations
import os, json, time, re, io
from typing import Any, Dict, List, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode

# ---------- ENV ----------
SEASON              = os.getenv("PLAYERS_SEASON", "").strip() or "2025-26"
USE_CUSTOM          = os.getenv("PLAYERS_USE_CUSTOM", "1").strip() == "1"
CUSTOM_URLS_RAW     = os.getenv("PLAYERS_CUSTOM_URLS", "").strip()
CUSTOM_URL_RAW      = os.getenv("PLAYERS_CUSTOM_URL", "").strip()
FALLBACK_URL        = os.getenv("PLAYERS_URL", "").strip()
IMAGE_PROXY_URLS    = [u.strip() for u in os.getenv("IMAGE_PROXY_URLS", "").split(",") if u.strip()]
CUSTOM_TIMEOUT      = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT", "20"))
CUSTOM_ATTEMPTS     = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS", "3"))
CACHE_TTL           = int(os.getenv("PLAYERS_CACHE_TTL", os.getenv("CACHE_TTL_SEC", "43200")))  # сек
MIN_EXPECTED        = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))
DISABLE_LOCAL       = os.getenv("PLAYERS_DISABLE_LOCAL", "0").strip() == "1"
INSECURE_SSL        = os.getenv("PLAYERS_INSECURE_SSL", "0").strip() == "1"  # не используется, но оставим для совместимости

# ---------- PATHS ----------
ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
TMP_DIR    = "/tmp"
PLAYERS_CACHE_PATH = os.path.join(TMP_DIR, "players_cache.json")

# ---------- STATE ----------
_PLAYERS: List[Dict[str, Any]] = []
_LAST_REFRESH_INFO: Dict[str, Any] = {}

# ---------- LOG ----------
def _log(*a: Any) -> None:
    try: print("[players]", *a, flush=True)
    except: pass

# ---------- HTTP ----------
_UA = "vm-plashki-news/1.0 (+https://vercel.app)"
def _http_get(url: str, timeout: int = CUSTOM_TIMEOUT) -> bytes:
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def _http_get_img(url: str, timeout: int = 15) -> bytes:
    req = Request(url, headers={"User-Agent": _UA, "Accept": "image/png,image/*;q=0.9,*/*;q=0.8"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

# ---------- UTILS ----------
def _now() -> int:
    return int(time.time())

def _save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        _log("cache save error:", e)

def _load_json(path: str) -> Any:
    if not os.path.exists(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log("cache load error:", e)
        return None

def _norm_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-zа-яё0-9\-'\s]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s

# ---------- URL CANDIDATES ----------
def _candidate_sources() -> List[str]:
    cands: List[str] = []
    if USE_CUSTOM:
        if CUSTOM_URLS_RAW:
            # может быть список URL с разными форматами
            cands.extend([u.strip() for u in CUSTOM_URLS_RAW.split(",") if u.strip()])
        if CUSTOM_URL_RAW:
            cands.extend([u.strip() for u in CUSTOM_URL_RAW.split(",") if u.strip()])
    if FALLBACK_URL:
        cands.append(FALLBACK_URL)
    # уникализируем порядок
    seen = set()
    out: List[str] = []
    for u in cands:
        if u not in seen:
            out.append(u); seen.add(u)
    return out

# ---------- PARSERS ----------
def _extract_team_id(p: Dict[str, Any]) -> str:
    # разные форматы
    tid = p.get("teamId")
    if isinstance(tid, (int, float)): return str(int(tid))
    if isinstance(tid, str) and tid.strip(): return tid.strip()
    team = p.get("team") or {}
    if isinstance(team, dict):
        t2 = team.get("teamId") or team.get("id")
        if isinstance(t2, (int, float)): return str(int(t2))
        if isinstance(t2, str) and t2.strip(): return t2.strip()
    return "0"

def _extract_person_id(p: Dict[str, Any]) -> Optional[str]:
    for k in ("personId","person_id","playerId","id"):
        v = p.get(k)
        if isinstance(v, (int, float)):
            return str(int(v))
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def _extract_names(p: Dict[str, Any]) -> Tuple[str, str]:
    fn = p.get("firstName") or p.get("first_name") or p.get("first") or ""
    ln = p.get("lastName") or p.get("last_name") or p.get("last") or ""
    return str(fn or "").strip(), str(ln or "").strip()

def _extract_photo(p: Dict[str, Any], pid: str) -> str:
    # у прокси может быть уже прямая ссылка
    ph = p.get("photo") or p.get("image") or ""
    if isinstance(ph, str) and ph.startswith(("http://","https://")):
        return ph
    # формируем CDN
    return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"

def _normalize_player(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pid = _extract_person_id(p)
    if not pid: return None
    fn, ln = _extract_names(p)
    team_id = _extract_team_id(p)
    img = _extract_photo(p, pid)
    is_active = bool(p.get("isActive", True))
    return {
        "personId": pid,
        "firstName": fn,
        "lastName": ln,
        "teamId": team_id,
        "isActive": is_active,
        "photo": img,
    }

def _parse_payload(raw: Any) -> List[Dict[str, Any]]:
    # 1) Уже список
    if isinstance(raw, list):
        out: List[Dict[str, Any]] = []
        for p in raw:
            if isinstance(p, dict):
                q = _normalize_player(p)
                if q: out.append(q)
        return out

    # 2) В dict где ключи разные: league/standard, resultSets, data, players
    if isinstance(raw, dict):
        # прямой список в dict
        if isinstance(raw.get("players"), list):
            return _parse_payload(raw["players"])
        if isinstance(raw.get("data"), list):
            return _parse_payload(raw["data"])
        # nba json style
        league = raw.get("league") or raw.get("League") or {}
        if isinstance(league, dict):
            for key in ("standard","vegas","africa","sacramento","utah"):
                arr = league.get(key)
                if isinstance(arr, list) and arr:
                    return _parse_payload(arr)
        # last fallback — пройтись по значениям
        for k, v in raw.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                tmp = _parse_payload(v)
                if tmp: return tmp
    return []

# ---------- CACHE ----------
def _cache_ok(meta: Dict[str, Any]) -> bool:
    if not isinstance(meta, dict): return False
    ts = int(meta.get("ts", 0))
    if ( _now() - ts ) > CACHE_TTL: return False
    n = int(meta.get("count", 0))
    return n >= MIN_EXPECTED

def _load_cache() -> Optional[List[Dict[str, Any]]]:
    meta = _load_json(PLAYERS_CACHE_PATH)
    if not meta or not _cache_ok(meta): return None
    arr = meta.get("players")
    if isinstance(arr, list): return arr
    return None

def _save_cache(players: List[Dict[str, Any]], info: Dict[str, Any]) -> None:
    data = {
        "ts": _now(),
        "count": len(players),
        "players": players,
        "info": info or {},
    }
    _save_json(PLAYERS_CACHE_PATH, data)

# ---------- PUBLIC: refresh ----------
def refresh_players(drop_cache: bool = False) -> Tuple[int, Dict[str, Any]]:
    """
    Возвращает: (кол-во, info)
    info: { ok, source, source_url }
    """
    global _PLAYERS, _LAST_REFRESH_INFO

    if not drop_cache:
        cached = _load_cache()
        if cached:
            _PLAYERS = cached
            _LAST_REFRESH_INFO = {"ok": True, "source": "cache", "source_url": None}
            _log("loaded from cache:", len(_PLAYERS))
            return len(_PLAYERS), dict(_LAST_REFRESH_INFO)

    # иначе — тянем из сети
    sources = _candidate_sources()
    if not sources:
        _log("no sources configured")
        _PLAYERS = []
        _LAST_REFRESH_INFO = {"ok": False, "source": None, "source_url": None}
        return 0, dict(_LAST_REFRESH_INFO)

    last_err = None
    for url in sources:
        # дозаполняем сезон, если в URL нет явного query
        try_url = url
        if "{season}" in try_url:
            try_url = try_url.replace("{season}", quote(SEASON))
        # попытки
        for attempt in range(1, max(1, CUSTOM_ATTEMPTS) + 1):
            try:
                raw = _http_get(try_url, timeout=CUSTOM_TIMEOUT)
                js = json.loads(raw.decode("utf-8", errors="ignore"))
                arr = _parse_payload(js)
                _log("custom parsed", len(arr), "from", try_url)
                if len(arr) >= MIN_EXPECTED:
                    _PLAYERS = arr
                    info = {"ok": True, "source": "custom", "source_url": try_url}
                    _LAST_REFRESH_INFO = info
                    _save_cache(_PLAYERS, info)
                    return len(_PLAYERS), dict(info)
            except Exception as e:
                last_err = e
                _log("custom get error:", repr(e))
    # если дошли сюда — не смогли
    _PLAYERS = []
    _LAST_REFRESH_INFO = {"ok": False, "source": "none", "source_url": None, "error": repr(last_err) if last_err else None}
    return 0, dict(_LAST_REFRESH_INFO)

# ---------- PUBLIC: counts/index ----------
def players_count() -> int:
    return len(_PLAYERS)

def get_players_index() -> List[Dict[str, Any]]:
    return _PLAYERS

# ---------- PUBLIC: find ----------
def _score_candidate(qn: str, p: Dict[str, Any]) -> int:
    # Очень простой скорер: точные совпадения/начало строки — больше вес
    fn = _norm_name(p.get("firstName",""))
    ln = _norm_name(p.get("lastName",""))
    full = (fn + " " + ln).strip()
    score = 0
    if qn == fn or qn == ln or qn == full: score += 100
    if fn and qn.startswith(fn): score += 30
    if ln and qn.startswith(ln): score += 30
    if fn and fn in qn: score += 10
    if ln and ln in qn: score += 10
    if full and qn in full: score += 10
    return score

def find_player_by_name(q: str, limit: int = 6) -> List[Dict[str, Any]]:
    """
    Поиск по части имени/фамилии (англ) или по ID.
    Возвращает top-N кандидатов.
    """
    if not q: return []
    if not _PLAYERS:
        # Автоподгрузка, если пусто — не падать
        try:
            refresh_players(drop_cache=False)
        except Exception:
            pass
    # Если ID
    qq = q.strip()
    if re.fullmatch(r"\d{2,}", qq):
        for p in _PLAYERS:
            if str(p.get("personId")) == qq:
                return [p]
        return []

    qn = _norm_name(qq)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for p in _PLAYERS:
        sc = _score_candidate(qn, p)
        if sc > 0:
            scored.append((sc, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in scored[:limit]]

# ---------- PUBLIC: headshot ----------
def _head_tmp_path(pid: str) -> str:
    return os.path.join(TMP_DIR, f"head_{pid}.png")

def _head_local_assets(pid: str) -> Optional[str]:
    # если заранее положили
    for d in ("cache", "heads"):
        p = os.path.join(ASSETS_DIR, d, f"{pid}.png")
        if os.path.exists(p):
            return p
    return None

def _possible_head_urls(pid: str, players_idx: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    urls: List[str] = []
    # из индекса возьмём photo если есть
    if players_idx:
        for p in players_idx:
            if str(p.get("personId")) == str(pid):
                if isinstance(p.get("photo"), str) and p["photo"].startswith(("http://","https://")):
                    urls.append(p["photo"])
                break
    # CDN крупный и мелкий
    urls.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png")
    urls.append(f"https://cdn.nba.com/headshots/nba/latest/260x190/{pid}.png")
    # прокси
    if IMAGE_PROXY_URLS:
        for base in IMAGE_PROXY_URLS:
            base = base.rstrip("/")
            # 1) проксируем id (если ваш воркер поддерживает /img?u=<id>)
            urls.append(f"{base}/img?u={quote(str(pid))}")
            # 2) проксируем оригинальный cdn урл
            orig = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
            urls.append(f"{base}/img?u={quote(orig, safe='')}")
    # уникализируем
    seen = set(); out = []
    for u in urls:
        if u not in seen:
            out.append(u); seen.add(u)
    return out

def ensure_headshot_png(player_or_id: Any, timeout: int = 15, **_kwargs) -> Optional[str]:
    """
    Возвращает локальный путь к PNG в /tmp.
    Принимает personId (str/int) или dict игрока.
    Игнорирует лишние именованные аргументы.
    """
    try:
        if isinstance(player_or_id, dict):
            pid = str(player_or_id.get("personId","")).strip()
        else:
            pid = str(player_or_id).strip()
        if not pid:
            return None

        # уже есть в /tmp?
        tp = _head_tmp_path(pid)
        if os.path.exists(tp) and os.path.getsize(tp) > 0:
            return tp

        # ассеты
        aset = _head_local_assets(pid)
        if aset:
            try:
                # скопируем в /tmp для единообразия
                with open(aset, "rb") as f:
                    data = f.read()
                with open(tp, "wb") as f:
                    f.write(data)
                return tp
            except Exception as e:
                _log("img copy error", pid, e)

        # индексация могла ещё не прогрузиться
        idx = _PLAYERS if _PLAYERS else None
        urls = _possible_head_urls(pid, idx)

        last_err = None
        for u in urls:
            try:
                data = _http_get_img(u, timeout=timeout)
                # очень короткий ответ — отбросим
                if not data or len(data) < 128:
                    continue
                with open(tp, "wb") as f:
                    f.write(data)
                _log("img ok", pid, u)
                return tp
            except Exception as e:
                last_err = e
                _log("img get error", u, repr(e))
        # не смогли — возвращаем None
        return None
    except Exception as e:
        _log("headshot ensure err", repr(e))
        return None
