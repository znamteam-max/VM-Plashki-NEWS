# data.py
from __future__ import annotations
import os, io, json, time, base64, unicodedata, mimetypes, re
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

# ------------------ ENV / CONFIG ------------------
DEBUG = os.getenv("DEBUG", "1") in ("1","true","yes")

ENV_URLS        = os.getenv("PLAYERS_CUSTOM_URLS","").strip()
ENV_URL_LEGACY  = os.getenv("PLAYERS_CUSTOM_URL","").strip()
PLAYERS_URL     = os.getenv("PLAYERS_URL","").strip()
PLAYERS_JSON_URL= os.getenv("PLAYERS_JSON_URL","").strip()

PLAYERS_SEASON  = os.getenv("PLAYERS_SEASON","2025-26").strip()
ATTEMPTS        = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS","3") or "3")
TIMEOUT         = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT","25") or "25")

DEFAULT_PT      = f"https://nba-players-proxy.znamteam-903.workers.dev/players?season={PLAYERS_SEASON}&format=passthrough"
DEFAULT_NORM    = f"https://nba-players-proxy.znamteam-903.workers.dev/players?season={PLAYERS_SEASON}&format=normalized"

# GH overrides (не обязательно)
OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()            # "owner/repo"
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","").strip()            # "assets/players_overrides.json"
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()

# Файловые пути
ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
CACHE_DIR  = "/tmp"
OV_LOCAL   = os.path.join(CACHE_DIR, "players_overrides.json")

LOGO_DIR_CACHED = os.path.join(ASSETS_DIR, "cache")
LOGO_DIR_TEAMS  = os.path.join(ASSETS_DIR, "teams")

HEADSHOT_TMP_FMT = os.path.join(CACHE_DIR, "headshot_{pid}.png")

# ------------------ LOG ------------------
def _log(*a: Any) -> None:
    if DEBUG:
        try: print("[data]", *a, flush=True)
        except: pass

# ------------------ HTTP UTILS ------------------
def _http_get(url: str, timeout: int = TIMEOUT) -> Optional[bytes]:
    req = UrlRequest(url, headers={
        "User-Agent": "vm-plashki/1.0 (+bot)",
        "Accept": "application/json, */*",
    })
    try:
        with http_urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        _log("get error:", url, repr(e))
        return None

def _try_fetch(url: str) -> Optional[Any]:
    last_err = None
    for i in range(max(1, ATTEMPTS)):
        raw = _http_get(url, timeout=TIMEOUT)
        if not raw:
            continue
        try:
            return json.loads(raw.decode("utf-8","ignore"))
        except Exception as e:
            last_err = e
    if last_err:
        _log("json parse error:", url, repr(last_err))
    return None

# ------------------ NORMALIZE/ALIAS ------------------
def _normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

def display_name_for(p: Dict[str, Any]) -> str:
    dn = (p.get("displayName") or "").strip()
    if dn:
        return dn
    first = (p.get("firstName") or "").strip()
    last  = (p.get("lastName") or "").strip()
    return (first + " " + last).strip()

# ------------------ PLAYERS CACHE ------------------
_PLAYERS: List[Dict[str, Any]] = []  # объединённый пул

def _index_by_pid(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows or []:
        pid = str(r.get("personId") or r.get("id") or "")
        if pid:
            out[pid] = r
    return out

def _classify_urls() -> Tuple[List[str], List[str]]:
    urls: List[str] = []
    if ENV_URLS:
        urls += [u.strip() for u in ENV_URLS.split(",") if u.strip()]
    if ENV_URL_LEGACY:
        urls += [u.strip() for u in ENV_URL_LEGACY.split(",") if u.strip()]
    if PLAYERS_URL:
        urls.append(PLAYERS_URL)
    if not urls:
        urls = [DEFAULT_PT, DEFAULT_NORM]

    pt, norm = [], []
    for u in urls:
        lu = u.lower()
        if "passthrough" in lu:
            pt.append(u)
        elif "normalized" in lu:
            norm.append(u)
        else:
            # неизвестно — считаем passthrough
            pt.append(u)
    if not pt:   pt   = [DEFAULT_PT]
    if not norm: norm = [DEFAULT_NORM]
    _log("pt_urls:", pt)
    _log("norm_urls:", norm)
    return pt, norm

# ------------------ EXTRACTORS ------------------
def _extract_normalized(j: Any) -> List[Dict[str, Any]]:
    """
    Стандартизируем под формат: personId, firstName, lastName, displayName, teamId
    normalized может НЕ содержать имен — тогда оставим пустыми, headshot дотащим по pid.
    """
    rows: List[Dict[str, Any]] = []
    if isinstance(j, list):
        src = j
    elif isinstance(j, dict):
        # возможные корни
        for key in ("players","data","items","result"):
            if isinstance(j.get(key), list):
                src = j[key]
                break
        else:
            src = []
    else:
        src = []

    for r in src:
        pid = str(r.get("personId") or r.get("id") or "").strip()
        if not pid: 
            continue
        first = (r.get("firstName") or "").strip()
        last  = (r.get("lastName") or "").strip()
        dn    = (r.get("displayName") or "").strip() or (first + " " + last).strip()
        team  = r.get("teamId")
        try: team = str(int(team))
        except: team = str(team or "0")

        rows.append({
            "personId": pid,
            "firstName": first,
            "lastName": last,
            "displayName": dn,
            "teamId": team,
            # подсказочный URL головы если есть
            "headshotURL": r.get("headshot") or r.get("img") or f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png",
        })
    return rows

def _extract_passthrough(j: Any) -> List[Dict[str, Any]]:
    """
    Любой богатый источник (ESPN/stats) — важны personId, имя, команда.
    """
    rows: List[Dict[str, Any]] = []

    def push(pid, first, last, dn, team):
        if not pid: 
            return
        try: team = str(int(team))
        except: team = str(team or "0")
        rows.append({
            "personId": str(pid),
            "firstName": (first or "").strip(),
            "lastName": (last or "").strip(),
            "displayName": (dn or "").strip() or (f"{first} {last}".strip()),
            "teamId": team,
        })

    if isinstance(j, list):
        src = j
    elif isinstance(j, dict):
        for key in ("players","athletes","data","items","result","roster"):
            v = j.get(key)
            if isinstance(v, list):
                src = v
                break
        else:
            src = []
    else:
        src = []

    for r in src:
        pid  = r.get("personId") or r.get("id") or r.get("playerId")
        first= r.get("firstName") or r.get("firstname") or r.get("first_name")
        last = r.get("lastName")  or r.get("lastname")  or r.get("last_name")
        dn   = r.get("displayName") or r.get("name") or r.get("fullName")
        team = r.get("teamId") or r.get("team_id") or r.get("team") or "0"
        # иногда team — dict
        if isinstance(team, dict):
            team = team.get("id") or team.get("teamId") or "0"
        push(pid, first, last, dn, team)

    return rows

# ------------------ MERGE ------------------
def _merge_pt_norm(pt: List[Dict[str, Any]], norm: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Приоритет: имена/команды — из PT, headshot/стабильные pid — из NORM.
    """
    ix_norm = _index_by_pid(norm)
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    # Сначала те, кто есть в passthrough — богаче именами
    for p in pt:
        pid = str(p.get("personId") or "")
        if not pid: 
            continue
        base = dict(p)
        if pid in ix_norm:
            base["headshotURL"] = ix_norm[pid].get("headshotURL") or base.get("headshotURL")
        out.append(base)
        seen.add(pid)

    # Затем «чистые» normalized, которых не было в pt
    for pid, n in ix_norm.items():
        if pid in seen: 
            continue
        out.append(dict(n))
    return out

def _merge_into_cache(rows: List[Dict[str, Any]]) -> None:
    global _PLAYERS
    if not rows: return
    ix = _index_by_pid(_PLAYERS)
    changed = False
    for r in rows:
        pid = str(r.get("personId") or "")
        if not pid: 
            continue
        if pid in ix:
            dst = ix[pid]
            # обновляем только полезное
            for k in ("displayName","firstName","lastName","teamId","headshotURL"):
                v = r.get(k)
                if v:
                    dst[k] = v
        else:
            _PLAYERS.append(r)
        changed = True
    if changed:
        _log("merged into cache:", len(rows), "rows")

# ------------------ REFRESH / ACCESSORS ------------------
def refresh_players() -> Tuple[int, str]:
    """
    Возвращает (count, src_text). src_text: "custom" | "merged(pt+norm)" | "norm" | "pt" | "none"
    """
    global _PLAYERS
    pt_urls, norm_urls = _classify_urls()

    # 1) Пытаемся загрузить PT
    pt_rows: List[Dict[str, Any]] = []
    for u in pt_urls:
        j = _try_fetch(u)
        cnt = 0
        if j:
            pt_rows = _extract_passthrough(j)
            cnt = len(pt_rows)
        _log("pt parsed:", cnt, "from", u)
        if cnt > 0:
            break

    # 2) PLAYERS_JSON_URL fallback (в PT слот)
    if not pt_rows and PLAYERS_JSON_URL:
        _log("fallback PLAYERS_JSON_URL:", PLAYERS_JSON_URL)
        j = _try_fetch(PLAYERS_JSON_URL)
        if j:
            pt_rows = _extract_passthrough(j) or _extract_normalized(j)
        _log("PLAYERS_JSON_URL parsed:", len(pt_rows))

    # 3) DEFAULT_PT fallback
    if not pt_rows and DEFAULT_PT:
        _log("fallback DEFAULT_PT:", DEFAULT_PT)
        j = _try_fetch(DEFAULT_PT)
        if j:
            pt_rows = _extract_passthrough(j)
        _log("DEFAULT_PT parsed:", len(pt_rows))

    # 4) Загружаем NORM
    norm_rows: List[Dict[str, Any]] = []
    for u in norm_urls:
        j = _try_fetch(u)
        cnt = 0
        if j:
            norm_rows = _extract_normalized(j)
            cnt = len(norm_rows)
        _log("norm parsed:", cnt, "from", u)
        if cnt > 0:
            break

    # 5) Сшиваем
    final_rows: List[Dict[str, Any]] = []
    src_label = "none"
    if pt_rows and norm_rows:
        final_rows = _merge_pt_norm(pt_rows, norm_rows)
        src_label = "merged(pt+norm)"
    elif pt_rows:
        final_rows = pt_rows
        src_label = "pt"
    elif norm_rows:
        final_rows = norm_rows
        src_label = "norm"
    else:
        final_rows = []

    _PLAYERS = final_rows
    _log(f"final players count: {len(_PLAYERS)} (source={src_label})")
    return len(_PLAYERS), src_label

def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    global _PLAYERS
    if force_refresh or not _PLAYERS:
        refresh_players()
    return _PLAYERS

# ------------------ SEARCH ------------------
def _online_find_in_passthrough(q: str, limit: int = 10) -> List[Dict[str, Any]]:
    qn = _normalize_name(q)
    if not qn: return []
    pt_urls, _ = _classify_urls()
    results: List[Dict[str, Any]] = []
    for u in pt_urls:
        j = _try_fetch(u)
        if not j: 
            continue
        rows = _extract_passthrough(j)
        for r in rows:
            hay = display_name_for(r)
            if qn in _normalize_name(hay):
                results.append(r)
                if len(results) >= limit:
                    break
        if results:
            break
    return results

def find_player_by_name(q: str) -> List[Dict[str, Any]]:
    if not q: 
        return []
    qn = _normalize_name(q)
    rows = get_players(False)
    hits: List[Dict[str, Any]] = []
    for r in rows:
        hay = display_name_for(r)
        if qn and qn in _normalize_name(hay):
            hits.append(r)
            if len(hits) >= 10:
                break
    if not hits:
        online = _online_find_in_passthrough(q, limit=10)
        if online:
            _merge_into_cache(online)
            hits = online
    return hits

# ------------------ HEADSHOT & LOGO ------------------
def ensure_headshot_png(player: Any) -> Optional[bytes]:
    """
    Принимает dict игрока ИЛИ personId. Возвращает PNG как bytes (для универсальности).
    """
    from PIL import Image
    pid = None
    url_hint = None
    if isinstance(player, dict):
        pid = str(player.get("personId") or player.get("id") or "").strip()
        url_hint = player.get("headshotURL")
    else:
        pid = str(player).strip()
    if not pid:
        return None

    # 1) /tmp cache
    tmp_path = HEADSHOT_TMP_FMT.format(pid=pid)
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path, "rb") as f:
                return f.read()
        except: 
            pass

    # 2) try hinted url
    def _download(u: str) -> Optional[bytes]:
        if not u: return None
        try:
            req = UrlRequest(u, headers={"User-Agent":"vm-plashki/1.0"})
            with http_urlopen(req, timeout=TIMEOUT) as r:
                data = r.read()
            # проверим что это картинка
            try:
                im = Image.open(io.BytesIO(data)).convert("RGBA")
                bio = io.BytesIO()
                im.save(bio, format="PNG")
                out = bio.getvalue()
                with open(tmp_path, "wb") as f:
                    f.write(out)
                return out
            except Exception:
                return None
        except Exception:
            return None

    # URL-кандидаты
    url_candidates: List[str] = []
    if url_hint:
        url_candidates.append(url_hint)
    url_candidates += [
        f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png",
        f"https://cdn.nba.com/headshots/nba/latest/260x190/{pid}.png",
        f"https://nba-players-proxy.znamteam-903.workers.dev/img?u={pid}",
    ]

    for u in url_candidates:
        data = _download(u)
        if data:
            return data

    return None

def ensure_team_logo_png(team_id: Any) -> Optional[str]:
    """
    Возвращает путь к локальному PNG логотипа команды, если найден.
    """
    tid = str(team_id or "0")
    # прямые пути
    for d in (LOGO_DIR_CACHED, LOGO_DIR_TEAMS):
        p1 = os.path.join(d, f"{tid}.png")
        if os.path.exists(p1):
            return p1
        # допускаем имена формата logo_{teamId}.png
        p2 = os.path.join(d, f"logo_{tid}.png")
        if os.path.exists(p2):
            return p2
        # перебор «на всякий»
        try:
            for fn in os.listdir(d):
                if not fn.lower().endswith(".png"): 
                    continue
                if tid in fn:
                    return os.path.join(d, fn)
        except FileNotFoundError:
            pass
    # generic
    gen = os.path.join(LOGO_DIR_TEAMS, "generic.png")
    if os.path.exists(gen):
        return gen
    return None

# ------------------ OVERRIDES (RU NAMES) ------------------
_OV_CACHE: Dict[str, str] = None  # type: ignore

def _ov_load_local() -> Dict[str, str]:
    try:
        if os.path.exists(OV_LOCAL):
            with open(OV_LOCAL, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return {str(k): str(v) for k,v in d.items()}
    except Exception as e:
        _log("ov local read error:", e)
    return {}

def _ov_save_local(d: Dict[str, str]) -> None:
    try:
        with open(OV_LOCAL, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        _log("ov local write error:", e)

def _gh_get_file() -> Optional[Tuple[str, Dict[str, str]]]:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return None
    url = f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}?ref={OV_GH_BRANCH}"
    req = UrlRequest(url, headers={"Authorization": f"token {OV_GH_TOKEN}", "User-Agent":"vm-plashki"})
    try:
        with http_urlopen(req, timeout=15) as r:
            js = json.loads(r.read().decode("utf-8","ignore"))
        content_b64 = js.get("content","")
        sha = js.get("sha")
        data = base64.b64decode(content_b64.encode("utf-8")).decode("utf-8","ignore")
        d = json.loads(data) if data else {}
        if not isinstance(d, dict): d = {}
        return sha, {str(k):str(v) for k,v in d.items()}
    except Exception as e:
        _log("ov gh get error:", e)
        return None

def _gh_put_file(d: Dict[str, str], prev_sha: Optional[str]) -> bool:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return False
    url = f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}"
    payload = {
        "message": "update players_overrides.json",
        "content": base64.b64encode(json.dumps(d, ensure_ascii=False).encode("utf-8")).decode("utf-8"),
        "branch": OV_GH_BRANCH,
    }
    if prev_sha:
        payload["sha"] = prev_sha
    body = json.dumps(payload).encode("utf-8")
    req = UrlRequest(url, data=body, headers={
        "Authorization": f"token {OV_GH_TOKEN}",
        "User-Agent":"vm-plashki",
        "Content-Type":"application/json",
    })
    try:
        with http_urlopen(req, timeout=15) as r:
            r.read()
        return True
    except Exception as e:
        _log("ov gh put error:", e)
        return False

def _ov_load() -> Dict[str, str]:
    global _OV_CACHE
    if _OV_CACHE is not None:
        return _OV_CACHE
    d = _ov_load_local()
    gh = _gh_get_file()
    if gh:
        _sha, gd = gh
        # GH — источник истины
        d = gd
    _OV_CACHE = d
    return d

def _ov_flush(d: Dict[str, str]) -> None:
    _OV_CACHE = d  # noqa: F841
    _ov_save_local(d)
    gh = _gh_get_file()
    prev_sha = gh[0] if gh else None
    _gh_put_file(d, prev_sha)

def overrides_get_name_ru(person_id: str) -> Optional[str]:
    d = _ov_load()
    return d.get(str(person_id))

def overrides_save_name_ru(person_id: str, name_ru: str) -> bool:
    person_id = str(person_id).strip()
    if not person_id or not name_ru:
        return False
    d = _ov_load()
    d[person_id] = name_ru.strip()
    _ov_flush(d)
    return True

# ------------------ PUBLIC TEST HOOKS (optional) ------------------
def search_players_loose(q: str) -> List[Dict[str, Any]]:
    """
    Для /api/telegram?action=test_find — мягкий поиск: локально, затем онлайн в PT.
    """
    qn = _normalize_name(q)
    rows = get_players(False)
    hits: List[Dict[str, Any]] = []
    for r in rows:
        if qn in _normalize_name(display_name_for(r)):
            hits.append(r)
            if len(hits) >= 10:
                break
    if not hits:
        online = _online_find_in_passthrough(q, limit=10)
        if online:
            _merge_into_cache(online)
            hits = online
    return hits
