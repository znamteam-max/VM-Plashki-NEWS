# data.py
from __future__ import annotations

import base64
import io
import json
import os
import re
import ssl
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from PIL import Image

from teams import TEAMS, GENERIC_NBA_PRIMARY

# ============================= ENV =============================
PLAYERS_SEASON = os.getenv("PLAYERS_SEASON", "2025-26").strip()

# Основные источники (Cloudflare Workers / прокси)
PLAYERS_USE_CUSTOM = os.getenv("PLAYERS_USE_CUSTOM", "1") == "1"
PLAYERS_CUSTOM_URL = os.getenv("PLAYERS_CUSTOM_URL", "").strip()
PLAYERS_CUSTOM_URLS = [
    u.strip() for u in os.getenv("PLAYERS_CUSTOM_URLS", "").split(",") if u.strip()
]

# Таймауты/ретраи для custom
CUSTOM_ATTEMPTS = int(os.getenv("PLAYERS_CUSTOM_ATTEMPTS", "3"))
CUSTOM_BASE_TIMEOUT = int(os.getenv("PLAYERS_CUSTOM_TIMEOUT", "30"))  # сек

# Legacy (data.nba.net) по умолчанию ВЫКЛ
ALLOW_LEGACY_FALLBACK = os.getenv("PLAYERS_ALLOW_LEGACY", "0") == "1"
ALLOW_INSECURE_SSL_LEGACY = os.getenv("PLAYERS_INSECURE_SSL", "0") == "1"
LEGACY_URL = os.getenv(
    "PLAYERS_LEGACY_URL", "https://data.nba.net/data/10s/prod/v1/2025/players.json"
)

# Удалённые снапшоты
PLAYERS_SNAPSHOT_URLS = [
    u.strip() for u in os.getenv("PLAYERS_SNAPSHOT_URLS", "").split(",") if u.strip()
]

# Локальный снапшот
DISABLE_LOCAL_SNAPSHOT = os.getenv("PLAYERS_DISABLE_LOCAL", "0") == "1"

# Прочее
MIN_EXPECTED = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))
CACHE_TTL_SEC = int(os.getenv("PLAYERS_CACHE_TTL", "43200"))  # 12h
PHOTO_FMT = os.getenv(
    "PLAYERS_PHOTO_FMT",
    "https://cdn.nba.com/headshots/nba/latest/{size}/{personId}.png",
)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
LOCAL_SNAPSHOT = os.path.join(ASSETS_DIR, "players.json")
CACHE_PATH = os.path.join("/tmp", "players_cache.json")

# ==== overrides persistence ====
OVERRIDES_FILE = os.path.join(ASSETS_DIR, "players_overrides.json")  # read-only в Vercel
OVERRIDES_TMP = os.path.join("/tmp", "players_overrides.json")       # read/write

# GitHub (если задано — коммитим изменения)
OVERRIDES_GH_TOKEN = os.getenv("OVERRIDES_GH_TOKEN", "").strip()
OVERRIDES_GH_REPO = os.getenv("OVERRIDES_GH_REPO", "").strip()  # "owner/repo"
OVERRIDES_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH", "main").strip()
OVERRIDES_GH_PATH = os.getenv("OVERRIDES_GH_PATH", "assets/players_overrides.json").strip()

# === кэш/прокси для изображений ===
IMG_CACHE_TTL_SEC = int(os.getenv("IMG_CACHE_TTL_SEC", "604800"))  # 7 дней
IMAGE_PROXY_URLS = [
    u.strip().rstrip("/") for u in os.getenv("IMAGE_PROXY_URLS", "").split(",") if u.strip()
]
IMG_CACHE_DIR = "/tmp/nba_img_cache"

_CACHED: Dict[str, Any] = {
    "ts": 0.0,
    "players": None,
    "index": None,
    "overrides": None,
    "last_source_url": None,
}

# ============================= utils =============================
def _log(*args: Any) -> None:
    try:
        print("[players]", *args, flush=True)
    except Exception:
        pass


def _with_query(url: str, **extra: str) -> str:
    pr = urlparse(url)
    q = dict(parse_qsl(pr.query))
    q.update({k: v for k, v in extra.items() if v is not None})
    return urlunparse(pr._replace(query=urlencode(q)))


def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def _read_json_file(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _http_get_json(url: str, timeout: int = 30, verify_ssl: bool = True) -> Any:
    ctx = None
    if not verify_ssl:
        ctx = ssl._create_unverified_context()
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (players-fetch; like Gecko)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Origin": "https://www.nba.com",
            "Referer": "https://www.nba.com/",
        },
    )
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)


def _http_get_bytes(url: str, timeout: int = 12, verify_ssl: bool = True) -> Optional[bytes]:
    ctx = None
    if not verify_ssl:
        ctx = ssl._create_unverified_context()
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (players-fetch; like Gecko)",
                "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Referer": "https://www.nba.com/",
                "Origin": "https://www.nba.com",
            },
        )
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except Exception as e:
        _log("img get error", url, repr(e))
        return None


def _get_json_with_retries(
    url: str, attempts: int, base_timeout: int, verify_ssl: bool = True
) -> Any:
    last_err: Optional[BaseException] = None
    for i in range(attempts):
        try:
            j = _http_get_json(url, timeout=base_timeout + i * 10, verify_ssl=verify_ssl)
            return j
        except BaseException as e:
            last_err = e
            _log(f"fetch try {i+1}/{attempts} failed:", repr(e), "for", url)
            time.sleep(1.25 * (i + 1))
    if last_err:
        raise last_err

# ============================= overrides load/save =============================
def _load_overrides_from_env() -> Dict[str, Dict[str, Any]]:
    env_raw = os.getenv("PLAYERS_OVERRIDES_JSON", "").strip()
    if env_raw:
        try:
            d = json.loads(env_raw)
            if isinstance(d, dict):
                return {str(k): v for k, v in d.items() if isinstance(v, dict)}
        except Exception as e:
            _log("overrides env parse error:", e)
    return {}


def _load_overrides_from_assets() -> Dict[str, Dict[str, Any]]:
    j = _read_json_file(OVERRIDES_FILE)
    if isinstance(j, dict):
        return {str(k): v for k, v in j.items() if isinstance(v, dict)}
    return {}


def _load_overrides_from_tmp() -> Dict[str, Dict[str, Any]]:
    j = _read_json_file(OVERRIDES_TMP)
    if isinstance(j, dict):
        return {str(k): v for k, v in j.items() if isinstance(v, dict)}
    return {}


def _merge_overrides(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k not in out:
            out[k] = v
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def _github_get_file(repo: str, path: str, ref: str) -> Tuple[Optional[str], Optional[str]]:
    if not (OVERRIDES_GH_TOKEN and repo and path):
        return None, None
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {OVERRIDES_GH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "players-overrides-bot",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        if "content" in data and "sha" in data:
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
    except Exception as e:
        _log("github get error:", e)
    return None, None


def _github_put_file(
    repo: str, path: str, ref: str, content_str: str, sha: Optional[str]
) -> bool:
    if not (OVERRIDES_GH_TOKEN and repo and path):
        return False
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        payload = {
            "message": "Update players_overrides.json via bot",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
            "branch": ref,
        }
        if sha:
            payload["sha"] = sha
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="PUT",
            headers={
                "Authorization": f"Bearer {OVERRIDES_GH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "players-overrides-bot",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            _ = r.read()
        return True
    except Exception as e:
        _log("github put error:", e)
        return False


def _load_overrides() -> Dict[str, Dict[str, Any]]:
    if _CACHED.get("overrides") is not None:
        return _CACHED["overrides"]
    env_ov = _load_overrides_from_env()
    assets_ov = _load_overrides_from_assets()
    tmp_ov = _load_overrides_from_tmp()
    gh_ov: Dict[str, Any] = {}
    content, _sha = _github_get_file(OVERRIDES_GH_REPO, OVERRIDES_GH_PATH, OVERRIDES_GH_BRANCH)
    if content:
        try:
            gh_ov_raw = json.loads(content)
            if isinstance(gh_ov_raw, dict):
                gh_ov = {str(k): v for k, v in gh_ov_raw.items() if isinstance(v, dict)}
        except Exception as e:
            _log("github json parse error:", e)
    ov = {}
    for src in (env_ov, assets_ov, gh_ov, tmp_ov):
        ov = _merge_overrides(ov, src)
    _CACHED["overrides"] = ov
    return ov


def _save_overrides(ov: Dict[str, Dict[str, Any]]) -> bool:
    ok = True
    _current, sha = _github_get_file(OVERRIDES_GH_REPO, OVERRIDES_GH_PATH, OVERRIDES_GH_BRANCH)
    try:
        content_str = json.dumps(ov, ensure_ascii=False, indent=2)
        if not _github_put_file(OVERRIDES_GH_REPO, OVERRIDES_GH_PATH, OVERRIDES_GH_BRANCH, content_str, sha):
            ok = False
    except Exception as e:
        _log("save to github failed:", e)
        ok = False
    try:
        _write_json_file(OVERRIDES_TMP, ov)
    except Exception as e:
        _log("save to /tmp failed:", e)
        ok = False
    _CACHED["overrides"] = ov
    return ok

def set_player_alias(person_id: str, alias: str) -> bool:
    ov = dict(_load_overrides())
    pid = str(person_id)
    ent = dict(ov.get(pid) or {})
    aliases = set([a.strip().lower() for a in ent.get("aliases", []) if isinstance(a, str)])
    aliases.add(alias.strip().lower())
    ent["aliases"] = sorted(list(aliases))
    ov[pid] = ent
    return _save_overrides(ov)

def set_player_ru_name(person_id: str, ru_name: str, alias: Optional[str] = None) -> bool:
    ov = dict(_load_overrides())
    pid = str(person_id)
    ent = dict(ov.get(pid) or {})
    ent["ruName"] = ru_name.strip()
    aliases = set([a.strip().lower() for a in ent.get("aliases", []) if isinstance(a, str)])
    if alias:
        aliases.add(alias.strip().lower())
    parts = ru_name.strip().split()
    if len(parts) >= 1:
        aliases.add(parts[-1].lower())
    ent["aliases"] = sorted(list(aliases))
    ov[pid] = ent
    return _save_overrides(ov)

def set_player_team(person_id: str, team_id: str) -> bool:
    ov = dict(_load_overrides())
    pid = str(person_id)
    ent = dict(ov.get(pid) or {})
    ent["teamId"] = str(team_id)
    ov[pid] = ent
    return _save_overrides(ov)

def get_overrides() -> Dict[str, Dict[str, Any]]:
    return dict(_load_overrides())

# ============================= parsing =============================
def _extract_players(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not j:
        return []
    if "resultSets" in j:
        try:
            rs = j["resultSets"][0]
            headers = rs["headers"]; rows = rs["rowSet"]
            def idx(name: str) -> Optional[int]:
                try: return headers.index(name)
                except ValueError: return None
            i_pid = idx("PERSON_ID"); i_tid = idx("TEAM_ID"); i_act = idx("ROSTERSTATUS")
            i_fn  = idx("FIRST_NAME"); i_ln  = idx("LAST_NAME")
            i_dfl = idx("DISPLAY_FIRST_LAST"); i_lcf = idx("DISPLAY_LAST_COMMA_FIRST")
            out: List[Dict[str, Any]] = []
            for r in rows:
                pid = _safe_str(r[i_pid]) if i_pid is not None else ""
                tid = _safe_str(r[i_tid]) if i_tid is not None else "0"
                active = True
                if i_act is not None:
                    try: active = bool(int(r[i_act]))
                    except Exception: active = bool(r[i_act])
                if i_fn is not None and i_ln is not None:
                    fn = _safe_str(r[i_fn]).strip(); ln = _safe_str(r[i_ln]).strip()
                elif i_dfl is not None:
                    disp = _safe_str(r[i_dfl]).strip()
                    parts = disp.split(); fn = parts[0] if parts else ""; ln = " ".join(parts[1:]) if len(parts) > 1 else ""
                elif i_lcf is not None and "," in _safe_str(r[i_lcf]):
                    ln, fn = [s.strip() for s in _safe_str(r[i_lcf]).split(",", 1)]
                else:
                    fn, ln = "", ""
                out.append({"personId": pid, "firstName": fn, "lastName": ln, "teamId": tid or "0", "isActive": active})
            return out
        except Exception as e:
            _log("stats extract error:", e); _log(traceback.format_exc()); return []
    league_std = j.get("league", {}).get("standard")
    if isinstance(league_std, list):
        out: List[Dict[str, Any]] = []
        for p in league_std:
            try:
                out.append({
                    "personId": _safe_str(p.get("personId") or ""),
                    "firstName": _safe_str(p.get("firstName") or "").strip(),
                    "lastName":  _safe_str(p.get("lastName")  or "").strip(),
                    "teamId":    _safe_str(p.get("teamId")    or "0"),
                    "isActive":  bool(p.get("isActive", True)),
                })
            except Exception:
                continue
        return out
    if isinstance(j, list) and j and isinstance(j[0], dict) and "personId" in j[0]:
        return j
    return []

# ============================= sources =============================
def _build_custom_candidates(bases: List[str]) -> List[str]:
    candidates: List[str] = []
    def _add(u: str):
        if u and u not in candidates: candidates.append(u)
    for base in bases:
        if not base: continue
        b = base.strip(); _add(b)
        pr = urlparse(b); path = pr.path or ""; qs = dict(parse_qsl(pr.query))
        if "/players" in path:
            for fmt in ("normalized", "passthrough"):
                u = _with_query(b, season=qs.get("season") or PLAYERS_SEASON, format=fmt); _add(u)
        else:
            root = b.rstrip("/")
            for fmt in ("normalized", "passthrough"):
                u = f"{root}/players"; u = _with_query(u, season=PLAYERS_SEASON, format=fmt); _add(u)
        if pr.scheme and pr.netloc:
            root2 = f"{pr.scheme}://{pr.netloc}"
            u2 = _with_query(root2, season=PLAYERS_SEASON); _add(u2)
    if not candidates:
        root = "https://nba-players-proxy.znamteam-903.workers.dev"
        candidates += [
            f"{root}/players?season={PLAYERS_SEASON}&format=normalized",
            f"{root}/players?season={PLAYERS_SEASON}&format=passthrough",
            f"{root}?season={PLAYERS_SEASON}",
        ]
    out: List[str] = []; seen = set()
    for u in candidates:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def _fetch_from_custom(url: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not PLAYERS_USE_CUSTOM:
        return [], None
    bases: List[str] = []
    if url and url.strip(): bases.append(url.strip())
    if PLAYERS_CUSTOM_URL: bases.append(PLAYERS_CUSTOM_URL)
    if PLAYERS_CUSTOM_URLS: bases.extend(PLAYERS_CUSTOM_URLS)
    candidates = _build_custom_candidates(bases)
    _log("custom candidates:", len(candidates))
    best: List[Dict[str, Any]] = []; best_url: Optional[str] = None
    for u in candidates:
        try:
            j = _get_json_with_retries(u, attempts=CUSTOM_ATTEMPTS, base_timeout=CUSTOM_BASE_TIMEOUT, verify_ssl=True)
            p = _extract_players(j)
            _log(f"custom parsed {len(p)} from {u}")
            if len(p) > len(best):
                best = p; best_url = u
            if len(p) >= MIN_EXPECTED:
                return p, u
        except BaseException as e:
            _log("custom fetch error:", u, repr(e)); _log(traceback.format_exc())
    return best, best_url

def _fetch_from_legacy() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not ALLOW_LEGACY_FALLBACK: return [], None
    url = LEGACY_URL
    try:
        j = _get_json_with_retries(url, attempts=2, base_timeout=15, verify_ssl=True)
        p = _extract_players(j); _log(f"legacy parsed {len(p)} from {url}")
        return p, url
    except BaseException as e:
        msg = repr(e); _log("legacy fetch error:", msg)
        if "CERTIFICATE_VERIFY_FAILED" in msg or isinstance(e, URLError):
            http_url = "http://" + url[len("https://"):] if url.startswith("https://") else url
            try:
                j2 = _get_json_with_retries(http_url, attempts=2, base_timeout=15, verify_ssl=(not ALLOW_INSECURE_SSL_LEGACY))
                p2 = _extract_players(j2)
                _log(f"legacy (http{' insecure' if ALLOW_INSECURE_SSL_LEGACY else ''}) parsed {len(p2)} from {http_url}")
                return p2, http_url
            except BaseException as e2:
                _log("legacy http fallback failed:", repr(e2))
        return [], None

def _fetch_remote_snapshots() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    best: List[Dict[str, Any]] = []; best_u: Optional[str] = None
    for u in PLAYERS_SNAPSHOT_URLS:
        try:
            j = _get_json_with_retries(u, attempts=2, base_timeout=20, verify_ssl=True)
            p = _extract_players(j); _log(f"remote snapshot parsed {len(p)} from {u}")
            if len(p) > len(best): best = p; best_u = u
            if len(p) >= MIN_EXPECTED: return p, u
        except BaseException as e:
            _log("remote snapshot error:", u, repr(e))
    return best, best_u

def _fetch_local_snapshot() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if DISABLE_LOCAL_SNAPSHOT: return [], None
    try:
        j = _read_json_file(LOCAL_SNAPSHOT)
        players = _extract_players(j) if isinstance(j, dict) else (j or [])
        _log(f"local snapshot parsed {len(players)} from {LOCAL_SNAPSHOT}")
        return players, LOCAL_SNAPSHOT
    except Exception as e:
        _log("local snapshot error:", e); return [], None

# ============================= image helpers (cache/proxy) =============================
def _img_cache_path(key: str) -> str:
    os.makedirs(IMG_CACHE_DIR, exist_ok=True)
    safe = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")
    return os.path.join(IMG_CACHE_DIR, safe + ".bin")

def _img_cache_get(key: str) -> Optional[bytes]:
    p = _img_cache_path(key)
    if not os.path.exists(p): return None
    try:
        if (time.time() - os.path.getmtime(p)) > IMG_CACHE_TTL_SEC: return None
        with open(p, "rb") as f: return f.read()
    except Exception:
        return None

def _img_cache_put(key: str, data: bytes) -> None:
    try:
        p = _img_cache_path(key)
        with open(p, "wb") as f: f.write(data)
    except Exception:
        pass

def _fetch_bytes_with_fallback(urls: List[str]) -> Optional[bytes]:
    for u in urls:
        for _ in range(2):
            b = _http_get_bytes(u, timeout=8, verify_ssl=True)
            if b: return b
    for u in urls:
        for proxy in IMAGE_PROXY_URLS:
            prox = f"{proxy}/img?u={quote(u, safe='')}"
            b = _http_get_bytes(prox, timeout=10, verify_ssl=True)
            if b: return b
    return None

# ============================= enriched helpers =============================
def ensure_headshot_png(p: Dict[str, Any], size: str = "1040x760") -> str:
    pid = str(p.get("personId") or "").strip()
    if not pid:
        return PHOTO_FMT.format(size=size, personId="0")
    ov = _load_overrides().get(pid) or {}
    photo = ov.get("photo")
    if isinstance(photo, str) and photo.strip():
        return photo.strip()
    return PHOTO_FMT.format(size=size, personId=pid)

def open_headshot_variants(head_url: str) -> Optional[Image.Image]:
    sizes = ["1040x760", "520x380", "260x190"]
    pid = None
    m = re.search(r"/(\d+)\.png", head_url)
    if m: pid = m.group(1)
    candidates: List[str] = []
    if pid:
        hosts = [
            "https://cdn.nba.com/headshots/nba/latest/{size}/{pid}.png",
            "https://ak-static.cms.nba.com/wp-content/uploads/headshots/nba/latest/{size}/{pid}.png",
            "https://ak-static.nba.com/wp-content/uploads/headshots/nba/latest/{size}/{pid}.png",
        ]
        for s in sizes:
            for h in hosts:
                candidates.append(h.format(size=s, pid=pid))
    else:
        candidates = [head_url]
    cache_key = f"headshot:{pid}" if pid else f"headshot_url:{head_url}"
    cached = _img_cache_get(cache_key)
    if cached:
        try: return Image.open(io.BytesIO(cached)).convert("RGBA")
        except Exception: pass
    raw = _fetch_bytes_with_fallback(candidates)
    if not raw: return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        _img_cache_put(cache_key, raw)
        return im
    except Exception:
        return None

def display_name_for(p: Dict[str, Any]) -> str:
    pid = str(p.get("personId") or "")
    ov = _load_overrides().get(pid) or {}
    ru = _safe_str(ov.get("ruName") or "").strip()
    if ru: return ru
    fn, ln = _safe_str(p.get("firstName") or "").strip(), _safe_str(p.get("lastName") or "").strip()
    disp = (fn + " " + ln).strip()
    return disp or _safe_str(p.get("displayName") or "").strip()

def overrides_aliases_for(pid: str) -> List[str]:
    ent = _load_overrides().get(str(pid)) or {}
    al = ent.get("aliases") or []
    if not isinstance(al, list): return []
    return [str(x).strip().lower() for x in al if isinstance(x, str)]

# --------------------------- BRAND / TEAMS ---------------------------
def _hex_to_rgb(h: str) -> Tuple[int,int,int]:
    h = h.strip()
    if h.startswith("#"): h = h[1:]
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def _clamp(x:int)->int: return max(0, min(255, x))

def _shade(rgb: Tuple[int,int,int], k: float)->Tuple[int,int,int]:
    return (_clamp(int(rgb[0]*k)), _clamp(int(rgb[1]*k)), _clamp(int(rgb[2]*k)))

def _team_logo_cache_key(team_id: str) -> str:
    return f"teamlogo:{team_id}"

def _team_logo_candidates(team_id: str, espn: str) -> List[str]:
    # ESPN PNG, 500px → запасной 200px
    return [
        f"https://a.espncdn.com/i/teamlogos/nba/500/{espn}.png",
        f"https://a.espncdn.com/i/teamlogos/nba/200/{espn}.png",
        # иногда у NBA CDN встречаются PNG
        f"https://cdn.nba.com/logos/nba/{team_id}/global/L/logo.png",
    ]

def get_team_brand(team_id: str) -> Tuple[Tuple[str, str, str], Optional[str]]:
    """
    Возвращает (primary_hex, dark_hex, light_hex), path_to_cached_logo_png.
    Если team_id не найден или 0 — отдаём нейтральный бренд NBA и None для лого.
    """
    team_id = str(team_id or "0")
    t = TEAMS.get(team_id)
    primary = t["primary"] if t else GENERIC_NBA_PRIMARY
    rgb = _hex_to_rgb(primary)
    dark = "#%02X%02X%02X" % _shade(rgb, 0.65)
    light = "#%02X%02X%02X" % _shade(rgb, 1.0)
    logo_path: Optional[str] = None
    if t:
        espn = t["espn"]
        key = _team_logo_cache_key(team_id)
        cached = _img_cache_get(key)
        if cached:
            pth = os.path.join(IMG_CACHE_DIR, key.replace(":", "_") + ".png")
            try:
                with open(pth, "wb") as f:
                    f.write(cached)
                logo_path = pth
            except Exception:
                pass
        else:
            urls = _team_logo_candidates(team_id, espn)
            raw = _fetch_bytes_with_fallback(urls)
            if raw:
                pth = os.path.join(IMG_CACHE_DIR, key.replace(":", "_") + ".png")
                try:
                    with open(pth, "wb") as f:
                        f.write(raw)
                    _img_cache_put(key, raw)
                    logo_path = pth
                except Exception:
                    logo_path = None
    return (primary, dark, light), logo_path

# ============================= cache/build =============================
def _load_cache_from_disk() -> Optional[Tuple[List[Dict[str, Any]], float]]:
    if not os.path.exists(CACHE_PATH): return None
    try:
        j = _read_json_file(CACHE_PATH)
        if not isinstance(j, dict): return None
        ts = float(j.get("ts", 0))
        players = j.get("players") or []
        if not isinstance(players, list): players = []
        return players, ts
    except Exception as e:
        _log("cache read error:", e); return None

def _save_cache_to_disk(players: List[Dict[str, Any]]) -> None:
    try:
        _write_json_file(CACHE_PATH, {"ts": time.time(), "players": players})
    except Exception as e:
        _log("cache write error:", e)

def _valid_cache(ts: float) -> bool:
    return (time.time() - ts) < CACHE_TTL_SEC

def _apply_overrides(players: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ov = _load_overrides()
    by_id = {p["personId"]: dict(p) for p in (players or [])}
    if ov:
        for pid, patch in ov.items():
            if pid in by_id:
                base = by_id[pid]
                if "firstName" in patch: base["firstName"] = _safe_str(patch["firstName"]).strip()
                if "lastName"  in patch: base["lastName"]  = _safe_str(patch["lastName"]).strip()
                if "teamId"    in patch: base["teamId"]    = _safe_str(patch["teamId"]).strip() or "0"
                if "isActive"  in patch: base["isActive"]  = bool(patch["isActive"])
                if "photo"     in patch and patch["photo"]: base["photo"] = _safe_str(patch["photo"]).strip()
            else:
                add = {
                    "personId": pid,
                    "firstName": _safe_str(patch.get("firstName", "")),
                    "lastName":  _safe_str(patch.get("lastName", "")),
                    "teamId":    _safe_str(patch.get("teamId", "0")),
                    "isActive":  bool(patch.get("isActive", True)),
                }
                if "photo" in patch and patch["photo"]: add["photo"] = _safe_str(patch["photo"])
                by_id[pid] = add
    out: List[Dict[str, Any]] = []
    for p in by_id.values():
        if not p.get("photo"):
            p["photo"] = PHOTO_FMT.format(size="520x380", personId=p["personId"])
        fn, ln = p.get("firstName","").strip(), p.get("lastName","").strip()
        p["displayName"] = (fn + " " + ln).strip() if (fn or ln) else p.get("displayName","")
        out.append(p)
    return out

def _build_from_sources() -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
    custom, custom_url = _fetch_from_custom()
    if custom and len(custom) >= MIN_EXPECTED:
        return custom, "custom", custom_url
    rem, rem_u = _fetch_remote_snapshots()
    if rem and len(rem) >= MIN_EXPECTED:
        return rem, "remote_snapshot", rem_u
    leg, leg_u = _fetch_from_legacy()
    if leg and len(leg) >= MIN_EXPECTED:
        return leg, "legacy", leg_u
    loc, loc_u = _fetch_local_snapshot()
    if loc and len(loc) > 0:
        return loc, "local", loc_u
    best = custom if len(custom) >= len(rem) else rem
    best_u = custom_url if len(custom) >= len(rem) else rem_u
    if len(leg) > len(best):
        best, best_u = leg, leg_u
    if len(loc) > len(best):
        best, best_u = loc, loc_u
    tag = "partial"
    return (best or []), tag, best_u

# ============================= public API =============================
def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    if not force_refresh and _CACHED["players"] and _valid_cache(_CACHED["ts"]):
        return _CACHED["players"]
    if not force_refresh:
        disk = _load_cache_from_disk()
        if disk:
            players, ts = disk
            if _valid_cache(ts) and players:
                _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": ts})
                return players
    raw, source, source_url = _build_from_sources()
    players = _apply_overrides(raw)
    if players:
        _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": time.time(), "last_source_url": source_url})
        _save_cache_to_disk(players)
        _log(f"final players count: {len(players)} (source={source}) url: {source_url or '-'}")
        return players
    disk = _load_cache_from_disk()
    if disk:
        players, ts = disk
        if players:
            _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": ts})
            _log(f"using stale disk cache: {len(players)} players")
            return players
    _log("no players parsed from any source (after all fallbacks)")
    return []

def get_players_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    ps = get_players(force_refresh=force_refresh)
    idx = {p["personId"]: p for p in ps}
    _CACHED["index"] = idx
    return idx

def refresh_players(drop_cache: bool = False) -> Tuple[int, Dict[str, Any]]:
    prev = _CACHED.get("players") or []
    try:
        raw, source, source_url = _build_from_sources()
        players = _apply_overrides(raw)
        if players and len(players) >= MIN_EXPECTED:
            _CACHED.update({"players": players, "index": {p["personId"]: p for p in players}, "ts": time.time(), "last_source_url": source_url})
            _save_cache_to_disk(players)
            return len(players), {"ok": True, "players_indexed": len(players), "source": source, "source_url": source_url}
        else:
            if prev:
                return len(prev), {"ok": True, "players_indexed": len(prev), "source": "cache_preserved", "source_url": _CACHED.get("last_source_url")}
            return 0, {"ok": False, "error": "no_source_available"}
    except Exception as e:
        _log("refresh error:", e)
        if prev:
            return len(prev), {"ok": True, "players_indexed": len(prev), "source": "cache_preserved", "source_url": _CACHED.get("last_source_url")}
        return 0, {"ok": False, "error": repr(e)}

def drop_players_cache() -> bool:
    try:
        _CACHED["ts"] = 0.0; _CACHED["players"] = None; _CACHED["index"] = None; _CACHED["last_source_url"] = None
        if os.path.exists(CACHE_PATH):
            try: os.remove(CACHE_PATH)
            except Exception: pass
        _log("players cache dropped"); return True
    except Exception as e:
        _log("drop cache error:", e); return False

def find_player_by_name(query: str) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q: return []
    res: List[Dict[str, Any]] = []
    for p in get_players():
        pid = p.get("personId")
        name = (p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}").strip()
        dn = (name or "").lower()
        ok = (q in dn)
        if not ok:
            ov = _load_overrides().get(str(pid)) or {}
            ru = str(ov.get("ruName") or "").strip().lower()
            if ru and q in ru: ok = True
            if not ok:
                aliases = overrides_aliases_for(str(pid))
                if q in aliases: ok = True
        if ok: res.append(p)
    def key(p: Dict[str, Any]):
        dn = (p.get("displayName") or "").lower()
        return (0 if p.get("isActive", True) else 1, abs(len(dn) - len(q)), len(dn))
    return sorted(res, key=key)

# совместимость/экспорт
def players_count(force_refresh: bool = False) -> int:
    return len(get_players(force_refresh=force_refresh))

def players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    return get_players(force_refresh=force_refresh)

def players_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    return get_players_index(force_refresh=force_refresh)

__all__ = [
    "get_players", "get_players_index", "refresh_players", "drop_players_cache",
    "find_player_by_name", "players_count", "players", "players_index",
    "ensure_headshot_png", "open_headshot_variants", "display_name_for",
    "set_player_ru_name", "set_player_team", "set_player_alias", "get_overrides",
    "get_team_brand",
]
