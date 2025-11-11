# api/data.py
from __future__ import annotations
import os, io, json, base64, unicodedata
from typing import Any, Dict, List, Optional, Tuple, Iterable
from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

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

OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","").strip()
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()

ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
CACHE_DIR  = "/tmp"
OV_LOCAL   = os.path.join(CACHE_DIR, "players_overrides.json")

LOGO_DIR_CACHED = os.path.join(ASSETS_DIR, "cache")
LOGO_DIR_TEAMS  = os.path.join(ASSETS_DIR, "teams")

HEADSHOT_TMP_FMT = os.path.join(CACHE_DIR, "headshot_{pid}.png")

def _log(*a: Any) -> None:
    if DEBUG:
        try: print("[data]", *a, flush=True)
        except: pass

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
    last_err: Optional[Exception] = None
    for _ in range(max(1, ATTEMPTS)):
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

_PLAYERS: List[Dict[str, Any]] = []

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
            pt.append(u)
    if not pt:   pt   = [DEFAULT_PT]
    if not norm: norm = [DEFAULT_NORM]
    _log("pt_urls:", pt)
    _log("norm_urls:", norm)
    return pt, norm

def _row_from_headers(headers: List[str], row: List[Any]) -> Dict[str, Any]:
    hmap = {h.upper(): i for i, h in enumerate(headers or [])}
    def at(key: str) -> Any:
        i = hmap.get(key)
        return row[i] if (i is not None and 0 <= i < len(row)) else None

    pid  = at("PERSON_ID") or at("PLAYER_ID") or at("ID")
    first= at("FIRST_NAME") or ""
    last = at("LAST_NAME")  or ""
    dn   = at("DISPLAY_FIRST_LAST") or at("PLAYER") or ""
    team = at("TEAM_ID") or at("TEAMID") or at("TEAM") or "0"

    try: team = str(int(team))
    except: team = str(team or "0")

    if (not first or not last) and isinstance(dn, str) and dn.strip():
        parts = dn.strip().split()
        if len(parts) >= 2:
            first = first or parts[0]
            last  = last  or " ".join(parts[1:])

    return {
        "personId": str(pid or "").strip(),
        "firstName": (first or "").strip(),
        "lastName":  (last or "").strip(),
        "displayName": (str(dn or "").strip() or f"{first} {last}".strip()),
        "teamId": team,
    }

def _iter_records(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, list):
        for r in obj:
            if isinstance(r, dict):
                yield _coerce_player_dict(r)
        return
    if not isinstance(obj, dict):
        return

    hdrs = obj.get("headers")
    rows = obj.get("rowSet") or obj.get("rows")
    if isinstance(hdrs, list) and isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                rec = _row_from_headers(hdrs, row)
                if rec.get("personId"):
                    yield rec

    rs = obj.get("resultSets") or obj.get("resultSet")
    if isinstance(rs, list):
        for block in rs:
            if isinstance(block, dict):
                h2 = block.get("headers")
                r2 = block.get("rowSet") or block.get("rows")
                if isinstance(h2, list) and isinstance(r2, list):
                    for row in r2:
                        if isinstance(row, list):
                            rec = _row_from_headers(h2, row)
                            if rec.get("personId"):
                                yield rec
    elif isinstance(rs, dict):
        h2 = rs.get("headers")
        r2 = rs.get("rowSet") or rs.get("rows")
        if isinstance(h2, list) and isinstance(r2, list):
            for row in r2:
                if isinstance(row, list):
                    rec = _row_from_headers(h2, row)
                    if rec.get("personId"):
                        yield rec

    league = obj.get("league")
    if isinstance(league, dict):
        for key in ("standard","vegas","africa","sacramento"):
            v = league.get(key)
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict):
                        yield _coerce_player_dict(r)

    for key in ("players","athletes","items","data","result","roster"):
        v = obj.get(key)
        if isinstance(v, list):
            for r in v:
                if isinstance(r, dict):
                    yield _coerce_player_dict(r)
        elif isinstance(v, dict):
            for rec in _iter_records(v):
                yield rec

    for v in obj.values():
        if isinstance(v, list):
            for r in v:
                if isinstance(r, dict):
                    yield _coerce_player_dict(r)

def _coerce_player_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    pid  = r.get("personId") or r.get("id") or r.get("playerId") or r.get("PLAYER_ID") or r.get("PERSON_ID")
    first= r.get("firstName") or r.get("firstname") or r.get("FIRST_NAME")
    last = r.get("lastName")  or r.get("lastname")  or r.get("LAST_NAME")
    dn   = r.get("displayName") or r.get("name") or r.get("fullName") or r.get("DISPLAY_FIRST_LAST")
    team = r.get("teamId") or r.get("team_id") or r.get("TEAM_ID") or r.get("team") or "0"

    if isinstance(team, dict):
        team = team.get("id") or team.get("teamId") or "0"

    try: team = str(int(team))
    except: team = str(team or "0")

    if not dn:
        dn = f"{first or ''} {last or ''}".strip()

    return {
        "personId": str(pid or "").strip(),
        "firstName": (first or "").strip(),
        "lastName":  (last or "").strip(),
        "displayName": (dn or "").strip(),
        "teamId": team,
        "headshotURL": r.get("headshot") or r.get("img") or r.get("HEADSHOT") or None,
    }

def _extract_normalized(j: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if j is None:
        return rows
    for rec in _iter_records(j):
        if rec.get("personId"):
            if not rec.get("headshotURL"):
                pid = rec["personId"]
                rec["headshotURL"] = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
            rows.append(rec)
    return rows

def _extract_passthrough(j: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if j is None:
        return rows
    for rec in _iter_records(j):
        if rec.get("personId"):
            rows.append(rec)
    return rows

def _merge_pt_norm(pt: List[Dict[str, Any]], norm: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ix_norm = _index_by_pid(norm)
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for p in pt:
        pid = str(p.get("personId") or "")
        if not pid:
            continue
        base = dict(p)
        if pid in ix_norm:
            base["headshotURL"] = ix_norm[pid].get("headshotURL") or base.get("headshotURL")
        out.append(base); seen.add(pid)
    for pid, n in ix_norm.items():
        if pid in seen: continue
        out.append(dict(n))
    return out

def _merge_into_cache(rows: List[Dict[str, Any]]) -> None:
    global _PLAYERS
    if not rows: return
    ix = _index_by_pid(_PLAYERS)
    changed = False
    for r in rows:
        pid = str(r.get("personId") or "")
        if not pid: continue
        if pid in ix:
            dst = ix[pid]
            for k in ("displayName","firstName","lastName","teamId","headshotURL"):
                v = r.get(k)
                if v: dst[k] = v
        else:
            _PLAYERS.append(r)
        changed = True
    if changed: _log("merged into cache:", len(rows), "rows")

def refresh_players() -> Tuple[int, str]:
    global _PLAYERS
    pt_urls, norm_urls = _classify_urls()

    pt_rows: List[Dict[str, Any]] = []
    for u in pt_urls:
        j = _try_fetch(u); cnt = 0
        if j is not None:
            pt_rows = _extract_passthrough(j); cnt = len(pt_rows)
        _log("pt parsed:", cnt, "from", u)
        if cnt > 0: break

    if not pt_rows and PLAYERS_JSON_URL:
        _log("fallback PLAYERS_JSON_URL:", PLAYERS_JSON_URL)
        j = _try_fetch(PLAYERS_JSON_URL)
        if j is not None:
            pt_rows = _extract_passthrough(j) or _extract_normalized(j)
        _log("PLAYERS_JSON_URL parsed:", len(pt_rows))

    if not pt_rows and DEFAULT_PT:
        _log("fallback DEFAULT_PT:", DEFAULT_PT)
        j = _try_fetch(DEFAULT_PT)
        if j is not None:
            pt_rows = _extract_passthrough(j)
        _log("DEFAULT_PT parsed:", len(pt_rows))

    norm_rows: List[Dict[str, Any]] = []
    for u in norm_urls:
        j = _try_fetch(u); cnt = 0
        if j is not None:
            norm_rows = _extract_normalized(j); cnt = len(norm_rows)
        _log("norm parsed:", cnt, "from", u)
        if cnt > 0: break

    if pt_rows and norm_rows:
        _PLAYERS = _merge_pt_norm(pt_rows, norm_rows); src_label = "merged(pt+norm)"
    elif pt_rows:
        _PLAYERS = pt_rows; src_label = "pt"
    elif norm_rows:
        _PLAYERS = norm_rows; src_label = "norm"
    else:
        _PLAYERS = []; src_label = "none"
    _log(f"final players count: {len(_PLAYERS)} (source={src_label})")
    return len(_PLAYERS), src_label

def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    global _PLAYERS
    if force_refresh or not _PLAYERS:
        refresh_players()
    return _PLAYERS

def _online_find_in_passthrough(q: str, limit: int = 10) -> List[Dict[str, Any]]:
    qn = _normalize_name(q)
    if not qn: return []
    pt_urls, _ = _classify_urls()
    results: List[Dict[str, Any]] = []
    for u in pt_urls:
        j = _try_fetch(u)
        if not j: continue
        rows = _extract_passthrough(j)
        for r in rows:
            hay = display_name_for(r)
            if qn in _normalize_name(hay):
                results.append(r)
                if len(results) >= limit: break
        if results: break
    return results

def find_player_by_name(q: str) -> List[Dict[str, Any]]:
    if not q: return []
    qn = _normalize_name(q)
    rows = get_players(False)
    hits: List[Dict[str, Any]] = []
    for r in rows:
        if qn and qn in _normalize_name(display_name_for(r)):
            hits.append(r)
            if len(hits) >= 10: break
    if not hits:
        online = _online_find_in_passthrough(q, limit=10)
        if online:
            _merge_into_cache(online)
            hits = online
    return hits

def ensure_headshot_png(player: Any) -> Optional[bytes]:
    from PIL import Image
    pid = None
    url_hint = None
    if isinstance(player, dict):
        pid = str(player.get("personId") or player.get("id") or "").strip()
        url_hint = player.get("headshotURL")
    else:
        pid = str(player).strip()
    if not pid: return None

    tmp_path = HEADSHOT_TMP_FMT.format(pid=pid)
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path, "rb") as f:
                return f.read()
        except: pass

    def _download(u: str) -> Optional[bytes]:
        if not u: return None
        try:
            req = UrlRequest(u, headers={"User-Agent":"vm-plashki/1.0"})
            with http_urlopen(req, timeout=TIMEOUT) as r:
                data = r.read()
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            bio = io.BytesIO(); im.save(bio, format="PNG")
            out = bio.getvalue()
            with open(tmp_path, "wb") as f:
                f.write(out)
            return out
        except Exception:
            return None

    url_candidates: List[str] = []
    if url_hint: url_candidates.append(url_hint)
    url_candidates += [
        f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png",
        f"https://cdn.nba.com/headshots/nba/latest/260x190/{pid}.png",
        f"https://nba-players-proxy.znamteam-903.workers.dev/img?u={pid}",
    ]
    for u in url_candidates:
        data_bytes = _download(u)
        if data_bytes: return data_bytes
    return None

def ensure_team_logo_png(team_id: Any) -> Optional[str]:
    tid = str(team_id or "0")
    for d in (LOGO_DIR_CACHED, LOGO_DIR_TEAMS):
        p1 = os.path.join(d, f"{tid}.png")
        if os.path.exists(p1): return p1
        p2 = os.path.join(d, f"logo_{tid}.png")
        if os.path.exists(p2): return p2
        try:
            for fn in os.listdir(d):
                if not fn.lower().endswith(".png"): 
                    continue
                if tid in fn:
                    return os.path.join(d, fn)
        except FileNotFoundError:
            pass
    gen = os.path.join(LOGO_DIR_TEAMS, "generic.png")
    if os.path.exists(gen): return gen
    return None

# ------------------ OVERRIDES (RU NAMES) -------------------------------------
_OV_CACHE: Optional[Dict[str, str]] = None
_GH_SHA: Optional[str] = None

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

def _gh_get_file() -> Optional[Tuple[Optional[str], Dict[str, str]]]:
    global _GH_SHA
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return None
    url = f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}?ref={OV_GH_BRANCH}"
    headers = {
        "Authorization": f"Bearer {OV_GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vm-plashki",
    }
    req = UrlRequest(url, headers=headers)
    try:
        with http_urlopen(req, timeout=15) as r:
            if r.status == 404:
                _log("ov gh get: 404 (will create on put)", OV_GH_PATH)
                _GH_SHA = None
                return (None, {})
            js = json.loads(r.read().decode("utf-8","ignore"))
        content_b64 = js.get("content","")
        sha = js.get("sha")
        data = base64.b64decode(content_b64.encode("utf-8")).decode("utf-8","ignore")
        d = json.loads(data) if data else {}
        if not isinstance(d, dict): d = {}
        _GH_SHA = sha
        _log("overrides loaded:", len(d), "sha:", sha)
        return sha, {str(k):str(v) for k,v in d.items()}
    except Exception as e:
        _log("ov gh get error:", e)
        return None

def _gh_put_file(d: Dict[str, str], prev_sha: Optional[str]) -> bool:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return False
    # Требование GitHub API: каталог должен существовать заранее!
    # Убедись, что папка assets/ есть в репозитории.
    url = f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}"
    payload = {
        "message": "update players_overrides.json",
        "content": base64.b64encode(json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8")).decode("utf-8"),
        "branch": OV_GH_BRANCH,
    }
    if prev_sha:
        payload["sha"] = prev_sha
    headers = {
        "Authorization": f"Bearer {OV_GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vm-plashki",
        "Content-Type": "application/json",
    }
    req = UrlRequest(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with http_urlopen(req, timeout=15) as r:
            r.read()
        return True
    except Exception as e:
        _log("ov gh put error:", e)
        return False

def _ov_load() -> Dict[str, str]:
    global _OV_CACHE, _GH_SHA
    if _OV_CACHE is not None:
        return _OV_CACHE
    d = _ov_load_local()
    gh = _gh_get_file()
    if gh:
        _GH_SHA, gd = gh
        d = gd or d
    _OV_CACHE = d
    return d

def _ov_flush(d: Dict[str, str]) -> None:
    global _OV_CACHE, _GH_SHA
    _OV_CACHE = d
    _ov_save_local(d)
    gh = _gh_get_file()
    prev_sha = gh[0] if gh else _GH_SHA
    ok = _gh_put_file(d, prev_sha)
    if ok:
        _gh_get_file()  # обновим sha

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

def search_players_loose(q: str) -> List[Dict[str, Any]]:
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
