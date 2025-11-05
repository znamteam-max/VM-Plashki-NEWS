# data.py — индекс игроков (лат/кириллица), русское имя, headshots/логотипы, подсказки, алиасы
import os, json, unicodedata, re, time
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import requests
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse

# ===== пути и кэш =====
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
CACHE = Path("/tmp/nba_cache")
CACHE.mkdir(parents=True, exist_ok=True)

PLAYERS_CACHE = CACHE / "players_index.json"
ALIASES_FILE  = CACHE / "aliases.json"
EXTRA_PLAYERS_FILE = CACHE / "manual_players.json"   # для ручных добавлений (переживает инстанс до перезагрузки)
RU_OVERRIDES_FILE  = CACHE / "ru_overrides.json"     # для /setru

HEAD_DIR = CACHE / "headshots"; HEAD_DIR.mkdir(exist_ok=True)
LOGO_DIR = ASSETS / "cache"  # ожидаются logo_<teamId>.png
ICON_STAR = str((ASSETS / "icons" / "star.png").resolve())

REFRESH_SECONDS = int(os.getenv("PLAYERS_REFRESH_SECONDS", "86400"))  # 1 день
PLAYERS_CUSTOM_URL = (os.getenv("PLAYERS_CUSTOM_URL") or "").strip()

# ===== команды: имена и базовый цвет (primary) =====
TEAM_NAMES: Dict[int, str] = {
    1610612737:"Atlanta Hawks", 1610612738:"Boston Celtics", 1610612739:"Cleveland Cavaliers",
    1610612740:"New Orleans Pelicans", 1610612741:"Chicago Bulls", 1610612742:"Dallas Mavericks",
    1610612743:"Denver Nuggets", 1610612744:"Golden State Warriors", 1610612745:"Houston Rockets",
    1610612746:"LA Clippers", 1610612747:"Los Angeles Lakers", 1610612748:"Miami Heat",
    1610612749:"Milwaukee Bucks", 1610612750:"Minnesota Timberwolves", 1610612751:"Brooklyn Nets",
    1610612752:"New York Knicks", 1610612753:"Orlando Magic", 1610612754:"Indiana Pacers",
    1610612755:"Philadelphia 76ers", 1610612756:"Phoenix Suns", 1610612757:"Portland Trail Blazers",
    1610612758:"Sacramento Kings", 1610612759:"San Antonio Spurs", 1610612760:"Oklahoma City Thunder",
    1610612761:"Toronto Raptors", 1610612762:"Utah Jazz", 1610612763:"Memphis Grizzlies",
    1610612764:"Washington Wizards", 1610612765:"Detroit Pistons", 1610612766:"Charlotte Hornets",
}
TEAM_PRIMARY: Dict[int, str] = {
    1610612737:"#E03A3E", 1610612738:"#007A33", 1610612739:"#860038",
    1610612740:"#0C2340", 1610612741:"#CE1141", 1610612742:"#00538C",
    1610612743:"#0E2240", 1610612744:"#1D428A", 1610612745:"#CE1141",
    1610612746:"#C8102E", 1610612747:"#552583", 1610612748:"#98002E",
    1610612749:"#00471B", 1610612750:"#0C2340", 1610612751:"#000000",
    1610612752:"#006BB6", 1610612753:"#0077C0", 1610612754:"#002D62",
    1610612755:"#006BB6", 1610612756:"#1D1160", 1610612757:"#E03A3E",
    1610612758:"#5A2D81", 1610612759:"#000000", 1610612760:"#007AC1",
    1610612761:"#BA0C2F", 1610612762:"#002B5C", 1610612763:"#5D76A9",
    1610612764:"#002B5C", 1610612765:"#C8102E", 1610612766:"#00788C",
}
def _shade(hex_color: str, k: float) -> str:
    hex_color = hex_color.strip().lstrip("#")
    r = int(hex_color[0:2], 16); g = int(hex_color[2:4], 16); b = int(hex_color[4:6], 16)
    r = max(0, min(255, int(r*k))); g = max(0, min(255, int(g*k))); b = max(0, min(255, int(b*k)))
    return f"#{r:02X}{g:02X}{b:02X}"

# ===== нормализация / транслит =====
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
def _normalize_key(s: str) -> str:
    s = _strip_accents(s).lower()
    s = re.sub(r"[^a-zа-яё0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

LAT_DIGRAPHS = [
    ("shch","щ"), ("sch","ш"), ("ch","ч"), ("sh","ш"), ("zh","ж"),
    ("yo","ё"), ("yu","ю"), ("ya","я"), ("ye","е"), ("yi","и"), ("kh","х"),
    ("ts","ц"), ("dz","дз")
]
LAT_SINGLE = {
    "a":"а","b":"б","c":"к","d":"д","e":"е","f":"ф","g":"г","h":"х","i":"и",
    "j":"дж","k":"к","l":"л","m":"м","n":"н","o":"о","p":"п","q":"к","r":"р",
    "s":"с","t":"т","u":"у","v":"в","w":"в","x":"кс","y":"й","z":"з",
}
def lat2cyr(name: str) -> str:
    s = _strip_accents(name).lower()
    for a,b in LAT_DIGRAPHS:
        s = s.replace(a,b)
    out = []
    for ch in s:
        out.append(LAT_SINGLE.get(ch, ch))
    txt = "".join(out)
    return " ".join(w.capitalize() for w in txt.split())

CYR_SINGLE = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ы":"y","ь":"","ъ":"","э":"e","ю":"yu","я":"ya",
}
def cyr2lat(s: str) -> str:
    t = []
    for ch in s.lower():
        t.append(CYR_SINGLE.get(ch, ch))
    return " ".join(w.capitalize() for w in "".join(t).split())

# ===== ручные русские имена (базовые) =====
RU_NAME_OVERRIDES_BASE: Dict[str,str] = {
    "victor wembanyama":"Виктор Вембаньяма",
    "zion williamson":"Зайон Уильямсон",
    "luka doncic":"Лука Дончич",
    "nikola jokic":"Никола Йокич",
    "lebron james":"Леброн Джеймс",
    "stephen curry":"Стефен Карри",
    "kevin durant":"Кевин Дюрант",
    "giannis antetokounmpo":"Яннис Адетокумбо",
    "joel embiid":"Джоэл Эмбиид",
    "anthony davis":"Энтони Дэвис",
    "kyrie irving":"Кайри Ирвинг",
    "shai gilgeous alexander":"Шай Гилджес-Александр",
    "domantas sabonis":"Домантас Сабонис",
    "alperen sengun":"Алперен Шенгюн",
}

def _load_json_file(p: Path, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

RU_NAME_OVERRIDES_EXTRA: Dict[str, str] = _load_json_file(RU_OVERRIDES_FILE, {})
def set_ru_name(full_ascii: str, ru_name: str) -> bool:
    """Сохранить RU-имя в персистентный оверрайд (/setru использует)."""
    try:
        full_ascii = " ".join(full_ascii.split())
        k = _normalize_key(full_ascii)
        RU_NAME_OVERRIDES_EXTRA[k] = ru_name
        RU_OVERRIDES_FILE.write_text(json.dumps(RU_NAME_OVERRIDES_EXTRA, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False

def _ru_display_for(full_ascii: str) -> str:
    k = _normalize_key(full_ascii)
    return RU_NAME_OVERRIDES_EXTRA.get(k) or RU_NAME_OVERRIDES_BASE.get(k) or lat2cyr(full_ascii)

# --- Минимальный фоллбек ---
FALLBACK_PLAYERS = [
    {"personId":"203999","firstName":"Nikola","lastName":"Jokic","teamId":"1610612743"},
    {"personId":"201939","firstName":"Stephen","lastName":"Curry","teamId":"1610612744"},
    {"personId":"2544","firstName":"LeBron","lastName":"James","teamId":"1610612747"},
    {"personId":"1641707","firstName":"Victor","lastName":"Wembanyama","teamId":"1610612759"},
    {"personId":"1629627","firstName":"Zion","lastName":"Williamson","teamId":"1610612740"},
    {"personId":"203507","firstName":"Giannis","lastName":"Antetokounmpo","teamId":"1610612749"},
    {"personId":"203954","firstName":"Joel","lastName":"Embiid","teamId":"1610612755"},
    {"personId":"201142","firstName":"Kevin","lastName":"Durant","teamId":"1610612756"},
    {"personId":"1629029","firstName":"Luka","lastName":"Doncic","teamId":"1610612742"},
]

# ===== алиасы, задаваемые из бота =====
_ALIASES: Dict[str, str] = {}  # key(normalized alias) -> normalized base ascii-key
def _load_aliases():
    global _ALIASES
    _ALIASES.update(_load_json_file(ALIASES_FILE, {}))
def add_alias(alias_text: str, base_full_ascii: str) -> bool:
    try:
        k = _normalize_key(alias_text)
        base_k = _normalize_key(base_full_ascii)
        _ALIASES[k] = base_k
        ALIASES_FILE.write_text(json.dumps(_ALIASES, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False
_load_aliases()

# ===== ручные игроки (добавления из бота) =====
_MANUAL_PLAYERS: List[Dict[str, Any]] = _load_json_file(EXTRA_PLAYERS_FILE, [])
def add_manual_player(person_id: int, first: str, last: str, team_id: int) -> bool:
    """Добавить игрока вручную (например, дебютант G-League/Евролиги)."""
    try:
        person_id = int(person_id)
        team_id = int(team_id)
        _MANUAL_PLAYERS[:] = [p for p in _MANUAL_PLAYERS if str(p.get("personId")) != str(person_id)]
        _MANUAL_PLAYERS.append({
            "personId": str(person_id),
            "firstName": first.strip(),
            "lastName": last.strip(),
            "teamId": str(team_id),
            "isActive": True,
        })
        EXTRA_PLAYERS_FILE.write_text(json.dumps(_MANUAL_PLAYERS, ensure_ascii=False), encoding="utf-8")
        # сбросим индекс, чтобы подхватился сразу
        drop_players_cache()
        return True
    except Exception:
        return False

def set_player_team_by_id(person_id: int, team_id: int) -> bool:
    """Сменить команду игрока по ID (например, Сиакам TOR -> IND)."""
    person_id, team_id = int(person_id), int(team_id)
    # правим manual-слой
    changed = False
    for p in _MANUAL_PLAYERS:
        if str(p.get("personId")) == str(person_id):
            p["teamId"] = str(team_id); changed = True
    if changed:
        EXTRA_PLAYERS_FILE.write_text(json.dumps(_MANUAL_PLAYERS, ensure_ascii=False), encoding="utf-8")
        drop_players_cache()
        return True
    # если в manual ещё нет — создадим патч-запись (с пустыми именами, подтянем из индекса при сборке)
    _MANUAL_PLAYERS.append({
        "personId": str(person_id), "firstName":"", "lastName":"", "teamId": str(team_id), "isActive": True
    })
    EXTRA_PLAYERS_FILE.write_text(json.dumps(_MANUAL_PLAYERS, ensure_ascii=False), encoding="utf-8")
    drop_players_cache()
    return True

# ===== утилиты загрузки =====
_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
}
def _log(msg: str):  # компактный лог в Vercel
    print(f"[players] {msg}")

def _fetch_json(url: str, timeout: int = 15):
    try:
        r = requests.get(url, headers=_UA, timeout=timeout)
        if not r.ok:
            _log(f"GET {url} -> status={r.status_code}")
            return None
        return r.json()
    except Exception as e:
        _log(f"GET fail: {url} -> {e.__class__.__name__}: {e}")
        return None

def _parse_resultsets_style(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    sets = j.get("resultSets") or j.get("ResultSets") or []
    if not sets:
        return []
    rs = sets[0]
    headers = [h.lower() for h in rs.get("headers", [])]
    rows = rs.get("rowSet", [])
    out = []
    for row in rows:
        d = dict(zip(headers, row))
        out.append({
            "personId": str(d.get("person_id") or d.get("personid") or ""),
            "firstName": str(d.get("first_name") or d.get("firstname") or ""),
            "lastName":  str(d.get("last_name") or d.get("lastname") or ""),
            "teamId":    str(d.get("team_id") or d.get("teamid") or "0"),
            "isActive":  bool(int(d.get("rosterstatus") or d.get("is_active") or 1)),
        })
    return out

def _extract_players(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    std = (payload.get("league") or {}).get("standard")
    if isinstance(std, list) and std:
        return std
    rs = _parse_resultsets_style(payload)
    if rs:
        return rs
    return []

def _url_with_params(base: str, add_query: str) -> str:
    # аккуратно добавляем параметры к существующим
    parsed = urlparse(base)
    q = parsed.query
    q = f"{q}&{add_query}" if q else add_query
    return urlunparse(parsed._replace(query=q))

def _fetch_from_custom() -> List[List[Dict[str, Any]]]:
    """Собирает ответы с воркера во всех комбинациях параметров и возвращает списки игроков."""
    if not PLAYERS_CUSTOM_URL:
        return []
    candidates = []
    base = PLAYERS_CUSTOM_URL.rstrip("/")
    # пробуем без параметров
    candidates.append(base)
    # сезон 2025-26
    candidates.append(_url_with_params(base, "season=2025-26"))
    # явные источники
    candidates.append(_url_with_params(base, "source=stats"))
    candidates.append(_url_with_params(base, "source=legacy"))
    candidates.append(_url_with_params(base, "season=2025-26&source=stats"))
    candidates.append(_url_with_params(base, "season=2025-26&source=legacy"))

    seen_urls = set()
    results: List[List[Dict[str, Any]]] = []
    for u in candidates:
        if u in seen_urls:
            continue
        seen_urls.add(u)
        j = _fetch_json(u, timeout=18)
        if not j:
            continue
        arr = _extract_players(j)
        _log(f"custom parsed: {len(arr)} from {u}")
        if arr:
            results.append(arr)
    return results

def _fetch_from_legacy() -> List[List[Dict[str, Any]]]:
    urls = [
        "https://data.nba.com/data/10s/prod/v1/2025/players.json",
        "https://data.nba.com/data/10s/prod/v1/2024/players.json",
        "https://data.nba.net/prod/v1/2025/players.json",
        "https://data.nba.net/prod/v1/2024/players.json",
    ]
    out = []
    for u in urls:
        j = _fetch_json(u, timeout=12)
        arr = _extract_players(j) if j else []
        _log(f"legacy parsed: {len(arr)} from {u}")
        if arr:
            out.append(arr)
    return out

def _fetch_local_snapshot() -> List[List[Dict[str, Any]]]:
    local = ASSETS / "players.json"
    if local.exists() and local.stat().st_size > 0:
        try:
            j = json.loads(local.read_text(encoding="utf-8"))
            arr = _extract_players(j)
            _log(f"local snapshot: {len(arr)}")
            if arr:
                return [arr]
        except Exception:
            pass
    return []

def _merge_players(list_of_lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Дедуп по personId, приоритет: первые массивы приоритнее."""
    merged: Dict[str, Dict[str, Any]] = {}
    for arr in list_of_lists:
        for p in arr:
            pid = str(p.get("personId") or "").strip()
            if not pid:
                continue
            if pid in merged:
                # если новый источник знает команду лучше — обновим
                old = merged[pid]
                old_team = int(str(old.get("teamId") or "0"))
                new_team = int(str(p.get("teamId") or "0"))
                if new_team and (not old_team or old_team == 0):
                    merged[pid] = p
            else:
                merged[pid] = p
    # подмешиваем ручных
    for p in _MANUAL_PLAYERS:
        pid = str(p.get("personId") or "").strip()
        if not pid:
            continue
        merged[pid] = p
    return list(merged.values())

def _build_index() -> Dict[str, Any]:
    batches: List[List[Dict[str, Any]]] = []

    # 0) custom worker (всевозможные параметры)
    batches.extend(_fetch_from_custom())

    # 1) локальный снапшот
    if not batches:
        batches.extend(_fetch_local_snapshot())

    # 2) legacy NBA (на случай блокировок stats)
    if sum(len(b) for b in batches) < 400:
        batches.extend(_fetch_from_legacy())

    # если вообще пусто — фоллбек
    if not batches:
        batches = [[p for p in FALLBACK_PLAYERS]]

    players = _merge_players(batches)
    _log(f"merged players total: {len(players)}")

    index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}

    # Для патчей set_player_team_by_id, где first/last могли быть пустыми
    # попробуем подставить имена из лучших источников
    by_id_best_names: Dict[str, Tuple[str,str]] = {}
    for arr in batches:
        for p in arr:
            pid = str(p.get("personId") or "").strip()
            if not pid:
                continue
            fn = (p.get("firstName") or "").strip()
            ln = (p.get("lastName") or "").strip()
            if pid not in by_id_best_names and (fn or ln):
                by_id_best_names[pid] = (fn, ln)

    for p in players:
        if not p.get("personId"):
            continue
        try:
            pid = int(str(p["personId"]).strip())
        except Exception:
            continue
        first = str(p.get("firstName","")).strip()
        last  = str(p.get("lastName","")).strip()
        if not first and not last:
            # заполним именами из других источников, если есть
            tpl = by_id_best_names.get(str(pid))
            if tpl:
                first, last = tpl
        try:
            team_id = int(str(p.get("teamId") or 0))
        except Exception:
            team_id = 0
        team_name = TEAM_NAMES.get(team_id, "Free Agent")
        full_ascii = f"{first} {last}".strip()
        ascii_key = _normalize_key(full_ascii) if full_ascii else str(pid)
        ru_display = _ru_display_for(full_ascii) if full_ascii else f"ID {pid}"

        rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": ru_display,
            "team_id": team_id,
            "team_name": team_name,
        }
        index["_byid"][pid] = rec

        # ключи для поиска
        if full_ascii:
            cyr_key = _normalize_key(ru_display)
            index["_bykey"][ascii_key] = rec
            index["_bykey"][cyr_key] = rec
            index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = rec
            index["_bykey"][_normalize_key(ru_display.replace("-", " "))] = rec

    # короткие прозвища
    aliases = {
        "wemby":"victor wembanyama", "вемба":"victor wembanyama",
        "зайон":"zion williamson", "лука":"luka doncic", "йокич":"nikola jokic", "ёкич":"nikola jokic",
        "яннис":"giannis antetokounmpo", "эмбиид":"joel embiid", "эмбид":"joel embiid",
        "стеф":"stephen curry", "стефан":"stephen curry", "кайри":"kyrie irving",
        "шай":"shai gilgeous alexander",
    }
    for a, base in aliases.items():
        k = _normalize_key(a)
        basek = _normalize_key(base)
        if basek in index["_bykey"]:
            index["_bykey"][k] = index["_bykey"][basek]

    return index

# ===== кэш индекса =====
_PLAYERS: Optional[Dict[str, Any]] = None
_LAST_LOAD: float = 0.0

def _ensure_index():
    global _PLAYERS, _LAST_LOAD
    now = time.time()
    if _PLAYERS and (now - _LAST_LOAD) < REFRESH_SECONDS:
        return
    if PLAYERS_CACHE.exists():
        try:
            _PLAYERS = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            _LAST_LOAD = now
        except Exception:
            _PLAYERS = None
    if not _PLAYERS:
        _PLAYERS = _build_index()
        try:
            PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        _LAST_LOAD = now

def drop_players_cache() -> bool:
    global _PLAYERS, _LAST_LOAD
    _PLAYERS = None
    _LAST_LOAD = 0.0
    try:
        if PLAYERS_CACHE.exists():
            PLAYERS_CACHE.unlink()
    except Exception:
        pass
    return True

def refresh_players_index() -> int:
    drop_players_cache()
    _ensure_index()
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

def players_count() -> int:
    _ensure_index()
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

def get_player_by_id(pid: int) -> Optional[Dict[str, Any]]:
    _ensure_index()
    return _PLAYERS["_byid"].get(int(pid)) if _PLAYERS else None

def _apply_alias(q_norm: str) -> Optional[str]:
    return _ALIASES.get(q_norm)

def find_player_by_name(query: str) -> Optional[Dict[str, Any]]:
    if not query:
        return None
    _ensure_index()
    if not _PLAYERS:
        return None

    q = _normalize_key(query)

    base = _apply_alias(q)
    if base and base in _PLAYERS["_bykey"]:
        return _PLAYERS["_bykey"][base]

    rec = _PLAYERS["_bykey"].get(q)
    if rec:
        return rec

    if re.search("[а-яё]", q):
        lat_guess = _normalize_key(cyr2lat(query))
        rec = _PLAYERS["_bykey"].get(lat_guess)
        if rec:
            return rec

    lat_guess2 = _normalize_key(_strip_accents(query))
    if lat_guess2 != q:
        rec = _PLAYERS["_bykey"].get(lat_guess2)
        if rec:
            return rec

    cyr_guess = _normalize_key(lat2cyr(query))
    rec = _PLAYERS["_bykey"].get(cyr_guess)
    if rec:
        return rec

    last = q.split(" ")[-1]
    if len(last) >= 3:
        for k, r in _PLAYERS["_bykey"].items():
            if k.endswith(" " + last) or k == last:
                return r

    return None

def suggest_players(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    _ensure_index()
    if not _PLAYERS or not query:
        return []
    q = _normalize_key(query)
    seen, scored = set(), []
    for rec in _PLAYERS["_byid"].values():
        keys = [rec["full_name"], rec["display"]]
        s = 0.0
        for key in keys:
            k = _normalize_key(key)
            r = SequenceMatcher(None, q, k).ratio()
            if k.startswith(q) or any(w.startswith(q) for w in k.split()):
                r += 0.1
            s = max(s, r)
        scored.append((s, rec))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for s, rec in scored:
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        out.append(rec)
        if len(out) >= limit:
            break
    return out

# ===== headshots & logos =====
def ensure_headshot_png(player_id: int, full_name: str) -> str:
    path = HEAD_DIR / f"{player_id}.png"
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    urls = [
        f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png",
        f"https://cdn.nba.com/headshots/nba/latest/260x190/{player_id}.png",
    ]
    for u in urls:
        try:
            r = requests.get(u, timeout=8)
            if r.ok and r.content and r.content[:4] == b"\x89PNG":
                path.write_bytes(r.content)
                return str(path)
        except Exception:
            continue
    return ICON_STAR

def ensure_team_logo_png(team_id: int) -> Tuple[str, Tuple[str,str,str]]:
    if team_id and (LOGO_DIR / f"logo_{team_id}.png").exists():
        logo_path = str((LOGO_DIR / f"logo_{team_id}.png").resolve())
    else:
        logo_path = ICON_STAR
    primary = TEAM_PRIMARY.get(team_id, "#0EA5FF")
    dark = _shade(primary, 0.90)
    light = "#FFFFFF"
    return logo_path, (primary, dark, light)
