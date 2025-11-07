# data.py — объединение normalized + passthrough, поиск, хедшоты, RU-оверрайды
from __future__ import annotations
import os, json, time, io, re, threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from PIL import Image

# -------------------------- ENV --------------------------

ENV = os.environ.get
PLAYERS_USE_CUSTOM      = (ENV("PLAYERS_USE_CUSTOM", "1") == "1")
PLAYERS_CUSTOM_URLS_RAW = ENV("PLAYERS_CUSTOM_URLS", "") or ENV("PLAYERS_CUSTOM_URL", "")
PLAYERS_URL_FALLBACK    = ENV("PLAYERS_URL", "")  # если есть одиночный URL
PLAYERS_SEASON          = ENV("PLAYERS_SEASON", "2025-26")
PLAYERS_MIN_EXPECTED    = int(ENV("PLAYERS_MIN_EXPECTED", "350") or 350)
PLAYERS_CUSTOM_TIMEOUT  = int(ENV("PLAYERS_CUSTOM_TIMEOUT", "25") or 25)
PLAYERS_CUSTOM_ATTEMPTS = int(ENV("PLAYERS_CUSTOM_ATTEMPTS", "3") or 3)

IMAGE_PROXY_URLS_RAW    = ENV("IMAGE_PROXY_URLS", "")  # напр. https://.../img?u={id}
IMG_CACHE_TTL_SEC       = int(ENV("IMG_CACHE_TTL_SEC", "604800") or 604800)  # неделя

# GitHub overrides (опционально; если не настроено — всё всё равно работает)
OV_GH_TOKEN  = ENV("OVERRIDES_GH_TOKEN", "").strip()
OV_GH_REPO   = ENV("OVERRIDES_GH_REPO", "").strip()            # e.g. RNGN/vm-plashki-news
OV_GH_BRANCH = ENV("OVERRIDES_GH_BRANCH", "main").strip()
OV_GH_PATH   = ENV("OVERRIDES_GH_PATH", "assets/players_overrides.json").strip()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")

# локальные overrides (мерджим несколько источников)
OV_LOCAL_DEFAULT = os.path.join(ASSETS_DIR, "players_overrides_default.json")
OV_LOCAL_TMP     = "/tmp/players_overrides.json"

# -------------------------- STATE --------------------------

_LOCK = threading.RLock()
_PLAYERS: List[Dict[str, Any]] = []
_INDEX_BY_ID: Dict[str, Dict[str, Any]] = {}
_LAST_REFRESH_TS: float = 0.0
_OVERRIDES: Dict[str, Any] = {}  # структура: { personId: {"ru_name": "...", "teamId": "..."}, ... }

# -------------------------- UTILS --------------------------

def _log(*a: Any) -> None:
    try:
        print("[data]", *a, flush=True)
    except:
        pass

def _http_json(url: str, timeout: int = PLAYERS_CUSTOM_TIMEOUT) -> Any:
    req = Request(url, headers={"User-Agent": "VM-Plashki/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _now() -> float:
    return time.time()

def _norm_id(v: Any) -> str:
    s = str(v or "").strip()
    # иногда приходит "201142.0"
    if s.endswith(".0"):
        s = s[:-2]
    # только цифры
    m = re.search(r"(\d+)", s)
    return m.group(1) if m else s

# -------------------------- OVERRIDES --------------------------

def _read_json(path: str) -> Any:
    if not os.path.exists(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

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
        import base64
        # GET file
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

    _OVERRIDES = merged or {}
    _log("overrides loaded:", len(_OVERRIDES))

def _save_overrides_local() -> None:
    # сохраняем только в /tmp (без GitHub push'а; push делает телеграм-хэндлер, если нужно)
    _write_json(OV_LOCAL_TMP, _OVERRIDES)

def overrides_get_name_ru(person_id: str) -> Optional[str]:
    person_id = _norm_id(person_id)
    entry = _OVERRIDES.get(person_id) or {}
    v = entry.get("ru_name")
    return v.strip() if isinstance(v, str) and v.strip() else None

def overrides_set_name_ru(person_id: str, ru_name: str) -> bool:
    try:
        person_id = _norm_id(person_id)
        ru_name = (ru_name or "").strip()
        if not ru_name: return False
        entry = _OVERRIDES.get(person_id) or {}
        entry["ru_name"] = ru_name
        _OVERRIDES[person_id] = entry
        _save_overrides_local()
        _log("override ru_name saved:", person_id, ru_name)
        return True
    except Exception as e:
        _log("override set ru_name error:", e)
        return False

def overrides_get_team(person_id: str) -> Optional[str]:
    person_id = _norm_id(person_id)
    entry = _OVERRIDES.get(person_id) or {}
    tid = entry.get("teamId")
    if tid is None: return None
    return str(tid)

def overrides_set_team(person_id: str, team_id: str) -> bool:
    try:
        person_id = _norm_id(person_id)
        team_id = _norm_id(team_id)
        entry = _OVERRIDES.get(person_id) or {}
        if team_id == "0":
            # удаляем оверрайд
            if "teamId" in entry: del entry["teamId"]
        else:
            entry["teamId"] = team_id
        _OVERRIDES[person_id] = entry
        _save_overrides_local()
        _log("override team saved:", person_id, team_id)
        return True
    except Exception as e:
        _log("override set team error:", e)
        return False

# -------------------------- PLAYERS FETCH & MERGE --------------------------

def _parse_normalized(payload: Any) -> Dict[str, Dict[str, Any]]:
    """
    Ожидается структура ~ [{ "id": "201142", "photo": "https://..." }, ...]
    Возвращает dict[id] = {"personId": id, "headshot_url": "..."}
    """
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(payload, list):
        for it in payload:
            if not isinstance(it, dict): continue
            pid = _norm_id(it.get("id") or it.get("personId"))
            if not pid: continue
            photo = it.get("photo") or it.get("headshot") or it.get("headshot_url")
            d = out.get(pid) or {"personId": pid}
            if photo:
                d["headshot_url"] = str(photo)
            out[pid] = d
    elif isinstance(payload, dict) and "players" in payload and isinstance(payload["players"], list):
        # иногда приходит {"players":[...]}
        for it in payload["players"]:
            if not isinstance(it, dict): continue
            pid = _norm_id(it.get("id") or it.get("personId"))
            if not pid: continue
            photo = it.get("photo") or it.get("headshot") or it.get("headshot_url")
            d = out.get(pid) or {"personId": pid}
            if photo:
                d["headshot_url"] = str(photo)
            out[pid] = d
    return out

def _parse_passthrough(payload: Any) -> Dict[str, Dict[str, Any]]:
    """
    Ожидается структура ~ [{ "personId":"...", "firstName":"...", "lastName":"...", "teamId":"...", "isActive":bool, "photo":"..."}, ...]
    """
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and "players" in payload and isinstance(payload["players"], list):
        items = payload["players"]
    else:
        items = []

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
            "lastName": last,
            "displayName": disp,
            "teamId": team,
            "isActive": active,
            "headshot_url": str(photo) if photo else None,
        }
    return out

def _merge_players(nz: Dict[str, Dict[str, Any]], pt: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Слияние: приоритет по именам/командам у passthrough; headshot_url берём из normalized если есть,
    иначе оставляем из passthrough; добавляем CDN fallback.
    """
    ids = set(nz.keys()) | set(pt.keys())
    out: List[Dict[str, Any]] = []
    for pid in ids:
        a = pt.get(pid) or {"personId": pid}
        b = nz.get(pid) or {}
        person = dict(a)  # старт — passthrough

        # headshot: prefer normalized->passthrough->CDN
        headshot = b.get("headshot_url") or a.get("headshot_url")
        if not headshot:
            headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
        person["headshot_url"] = headshot

        # displayName базовый
        disp = person.get("displayName") or f"{person.get('firstName','').strip()} {person.get('lastName','').strip()}".strip()
        person["displayName"] = disp or pid

        # оверрайд teamId — если есть
        ov_team = overrides_get_team(pid)
        if ov_team:
            person["teamId"] = ov_team

        out.append(person)
    # сортировка: активные сверху, потом по фамилии
    out.sort(key=lambda p: (not p.get("isActive", True), (p.get("lastName") or p.get("displayName") or "")))
    return out

def _split_urls(raw: str) -> List[str]:
    urls: List[str] = []
    for part in (raw or "").split(","):
        u = part.strip()
        if u:
            urls.append(u)
    return urls

def _fetch_custom_players() -> Tuple[List[Dict[str, Any]], str]:
    """
    Тянем все URL, отдельно агрегируем normalized и passthrough, потом мержим.
    Возвращает (players_list, source_url_used_for_log)
    """
    urls = _split_urls(PLAYERS_CUSTOM_URLS_RAW)
    if not urls and PLAYERS_URL_FALLBACK:
        urls = [PLAYERS_URL_FALLBACK]

    if not urls:
        return [], "none"

    normalized_acc: Dict[str, Dict[str, Any]] = {}
    passthrough_acc: Dict[str, Dict[str, Any]] = {}

    last_ok_url = "none"

    for attempt in range(max(1, PLAYERS_CUSTOM_ATTEMPTS)):
        for u in urls:
            try:
                j = _http_json(u, timeout=PLAYERS_CUSTOM_TIMEOUT)
                # эвристика формата
                u_low = u.lower()
                if "format=normalized" in u_low or "normalized" in u_low:
                    part = _parse_normalized(j)
                    if part:
                        normalized_acc.update(part)
                        last_ok_url = u
                elif "format=passthrough" in u_low or "passthrough" in u_low:
                    part = _parse_passthrough(j)
                    if part:
                        passthrough_acc.update(part)
                        last_ok_url = u
                else:
                    # пробуем угадать формат
                    part_pt = _parse_passthrough(j)
                    part_nz = _parse_normalized(j)
                    if part_pt:
                        passthrough_acc.update(part_pt)
                        last_ok_url = u
                    elif part_nz:
                        normalized_acc.update(part_nz)
                        last_ok_url = u
                    else:
                        _log("custom parse unknown schema:", u)
            except HTTPError as e:
                _log("custom get error:", e)
            except URLError as e:
                _log("custom get error:", e)
            except Exception as e:
                _log("custom get error:", repr(e))

        # если что-то набрали — выходим
        if passthrough_acc or normalized_acc:
            break
        time.sleep(0.2)

    players = _merge_players(normalized_acc, passthrough_acc)
    return players, last_ok_url

def refresh_players() -> Tuple[int, str]:
    """
    Обновляет _PLAYERS из кастомных источников, мержит normalized+passthrough.
    Возвращает (count, source_url)
    """
    global _PLAYERS, _INDEX_BY_ID, _LAST_REFRESH_TS

    with _LOCK:
        if PLAYERS_USE_CUSTOM:
            players, src = _fetch_custom_players()
            if len(players) < PLAYERS_MIN_EXPECTED:
                _log("players fetched but too few:", len(players))
            # даже если мало, всё равно заменим (чтобы не залипало на пустом у других)
            _PLAYERS = players
            _INDEX_BY_ID = {str(p.get("personId")): p for p in _PLAYERS}
            _LAST_REFRESH_TS = _now()
            _log("final players count:", len(_PLAYERS), "(source=custom) url:", src)
            return len(_PLAYERS), src
        else:
            # Можно добавить другие источники (ESPN/старая NBA stats), если понадобится
            _PLAYERS = []
            _INDEX_BY_ID = {}
            _LAST_REFRESH_TS = _now()
            return 0, "none"

def get_players() -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_PLAYERS)

def players_ready() -> bool:
    with _LOCK:
        return len(_PLAYERS) >= PLAYERS_MIN_EXPECTED or len(_PLAYERS) > 0

def get_player_by_id(person_id: str) -> Optional[Dict[str, Any]]:
    person_id = _norm_id(person_id)
    with _LOCK:
        return _INDEX_BY_ID.get(person_id)

# -------------------------- SEARCH --------------------------

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
        if n:
            vs.add(n)
    return list(vs)

def find_player_by_name(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = _norm(query)
    if not q:
        return []
    with _LOCK:
        pool = list(_PLAYERS)
    # точные/частичные совпадения
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for p in pool:
        variants = _player_name_variants(p)
        score = 0
        for v in variants:
            if v == q:
                score = max(score, 3)
            elif v.startswith(q) or q in v:
                score = max(score, 2)
        # ещё один шанс на совпадение по одной фамилии/короткому куску
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
    if ru:
        return ru
    disp = (p.get("displayName") or "").strip()
    if disp:
        return disp
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName") or "").strip()
    full = f"{first} {last}".strip()
    return full or pid

# -------------------------- HEADSHOT FETCH --------------------------

_IMG_CACHE_DIR = "/tmp/img_cache"
os.makedirs(_IMG_CACHE_DIR, exist_ok=True)

def _img_cache_path(key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", key)
    return os.path.join(_IMG_CACHE_DIR, safe)

def _http_bytes(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        req = Request(url, headers={"User-Agent": "VM-Plashki/1.0"})
        with urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        _log("img get error", url, repr(e))
        return None

def _load_image_from_bytes(raw: Optional[bytes]) -> Optional[Image.Image]:
    if not raw:
        return None
    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None

def ensure_headshot_png(player_or_id: Any, timeout: int = 20) -> Optional[Image.Image]:
    """
    Возвращает PIL.Image (RGBA) головы игрока. Пытается:
    - headshot_url из игрока (normalized/passthrough);
    - прокси IMAGE_PROXY_URLS: "{proxy}/img?u={id}" или "{proxy}/{id}.png" (оба варианта);
    - прямой CDN NBA.
    С кэшем в /tmp.
    """
    if isinstance(player_or_id, dict):
        pid = _norm_id(player_or_id.get("personId"))
        primary_url = (player_or_id.get("headshot_url") or "").strip()
    else:
        pid = _norm_id(player_or_id)
        primary_url = ""

    # порядок URL-кандидатов
    candidates: List[str] = []
    if primary_url:
        candidates.append(primary_url)

    # прокси
    for proxy in _split_urls(IMAGE_PROXY_URLS_RAW):
        if "{id}" in proxy:
            candidates.append(proxy.replace("{id}", pid))
        else:
            # поддержим два популярных паттерна
            candidates.append(f"{proxy.rstrip('/')}/img?u={pid}")
            candidates.append(f"{proxy.rstrip('/')}/{pid}.png")

    # CDN NBA
    candidates.append(f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png")

    # кэшируем по pid
    cache_path = _img_cache_path(f"head_{pid}.png")
    if os.path.exists(cache_path):
        try:
            # TTL
            if _now() - os.path.getmtime(cache_path) <= IMG_CACHE_TTL_SEC:
                im = Image.open(cache_path).convert("RGBA")
                return im
        except Exception:
            pass

    for u in candidates:
        raw = _http_bytes(u, timeout=timeout)
        im = _load_image_from_bytes(raw)
        if im:
            try:
                im.save(cache_path, "PNG")
            except Exception:
                pass
            return im

    return None

# -------------------------- MODULE INIT --------------------------

# Загружаем overrides при импорте один раз
try:
    _load_overrides()
except Exception as e:
    _log("overrides load error at init:", repr(e))

# Не делаем авто-refresh, чтобы управлять через /api/telegram?action=refresh
