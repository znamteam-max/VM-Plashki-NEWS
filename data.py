# data.py — индекс игроков (лат/кириллица), sports.ru-резолвер, RU-оверайды, лого/фото
import os, json, unicodedata, re, time
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import requests
from difflib import SequenceMatcher
from urllib.parse import quote_plus

# ===== пути и кэш =====
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

# В Vercel писать можно только в /tmp
CACHE = Path("/tmp/nba_cache")
CACHE.mkdir(parents=True, exist_ok=True)

PLAYERS_CACHE = CACHE / "players_index.json"
ALIASES_FILE  = CACHE / "aliases.json"
RU_OVERRIDES_FILE   = CACHE / "ru_overrides.json"     # player_id -> "Русское Имя"
LASTNAME_RULES_FILE = CACHE / "lastname_rules.json"   # "jokic" -> "Йокич"
SPORTSRU_CACHE_FILE = CACHE / "sportsru_names.json"   # ascii_key -> "Русское Имя"

HEAD_DIR = CACHE / "headshots"
HEAD_DIR.mkdir(exist_ok=True)

# Логотипы должны лежать в репозитории: assets/cache/logo_<teamId>.png
LOGO_DIR = ASSETS / "cache"
ICON_STAR = str((ASSETS / "icons" / "star.png").resolve())

# Поведение загрузчика
REFRESH_SECONDS      = int(os.getenv("PLAYERS_REFRESH_SECONDS", "86400"))  # 1 день
PLAYERS_OFFLINE      = os.getenv("PLAYERS_OFFLINE", "1") == "1"            # офлайн по умолчанию
PLAYERS_SNAPSHOT_URL = os.getenv("PLAYERS_SNAPSHOT_URL", "").strip()       # опц.: прямая ссылка на готовый JSON

# sports.ru резолвер
SPORTSRU_RESOLVE  = os.getenv("SPORTSRU_RESOLVE", "1") == "1"
SPORTSRU_TIMEOUT  = int(os.getenv("SPORTSRU_TIMEOUT", "4"))
SPORTSRU_LANG_HDR = os.getenv("SPORTSRU_LANG", "ru-RU,ru;q=0.9")

# Админы (для /resolve в api)
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(",", " ").split() if x.strip().isdigit()}

# Опционально Upstash K/V (персистентность без деплоя правок)
UPSTASH_URL   = os.getenv("UPSTASH_URL", "").strip()
UPSTASH_TOKEN = os.getenv("UPSTASH_TOKEN", "").strip()

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

# ===== ручные русские имена =====
RU_NAME_OVERRIDES: Dict[str,str] = {
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

# --- минимальный фоллбек, чтобы бот не был пустым ---
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

# ===== utils =====
def _json_load(path: Path) -> dict:
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: return {}
    return {}
def _json_save(path: Path, obj: dict) -> None:
    try: path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception: pass

# ===== алиасы для поиска =====
_ALIASES: Dict[str, str] = {}  # key(normalized alias) -> normalized base ascii-key

def _load_aliases():
    global _ALIASES
    if ALIASES_FILE.exists():
        try:
            _ALIASES = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
        except Exception:
            _ALIASES = {}
    else:
        _ALIASES = {}
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

# ===== Upstash K/V (опционально) =====
def _kv_get(key: str) -> Optional[dict]:
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return None
    try:
        r = requests.get(f"{UPSTASH_URL}/get/{key}",
                         headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                         timeout=5)
        if r.ok:
            j = r.json()
            val = j.get("result")
            if isinstance(val, str):
                return json.loads(val)
            if isinstance(val, dict):
                return val
    except Exception:
        pass
    return None

def _kv_set(key: str, obj: dict) -> bool:
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return False
    try:
        payload = json.dumps(obj, ensure_ascii=False)
        r = requests.get(f"{UPSTASH_URL}/set/{key}/{payload}",
                         headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                         timeout=5)
        return r.ok
    except Exception:
        return False

# ===== Глобальные мапы исправлений =====
_RU_OVERRIDES: Dict[str, str] = {}      # player_id(str) -> "Русское Имя"
_LASTNAME_RULES: Dict[str, str] = {}    # latin_last(lower) -> "Йокич"
_SPORTSRU_CACHE: Dict[str, str] = {}    # ascii_key -> "Русское Имя"

def _load_overrides():
    global _RU_OVERRIDES, _LASTNAME_RULES, _SPORTSRU_CACHE
    ru = _kv_get("ru_overrides") or _json_load(RU_OVERRIDES_FILE)
    ln = _kv_get("lastname_rules") or _json_load(LASTNAME_RULES_FILE)
    sc = _json_load(SPORTSRU_CACHE_FILE)
    _RU_OVERRIDES = {str(k): str(v) for k, v in (ru.items() if isinstance(ru, dict) else [])}
    _LASTNAME_RULES = {str(k).lower(): str(v) for k, v in (ln.items() if isinstance(ln, dict) else [])}
    _SPORTSRU_CACHE = {str(k): str(v) for k, v in (sc.items() if isinstance(sc, dict) else [])}

def _save_overrides():
    _json_save(RU_OVERRIDES_FILE, _RU_OVERRIDES)
    _json_save(LASTNAME_RULES_FILE, _LASTNAME_RULES)
    _json_save(SPORTSRU_CACHE_FILE, _SPORTSRU_CACHE)
    _kv_set("ru_overrides", _RU_OVERRIDES)
    _kv_set("lastname_rules", _LASTNAME_RULES)

_load_overrides()

# ===== sports.ru резолвер =====
_SPORTS_TITLE_RE = re.compile(r">([А-ЯЁA-Z][^<]{1,80}?)\s*[—\-–]\s*баскетбол", re.IGNORECASE)
_H1_RE = re.compile(r"<h1[^>]*>([^<]{2,80})</h1>", re.IGNORECASE)

def _sportsru_fetch_ru_name(en_full: str) -> Optional[str]:
    try:
        q = quote_plus(en_full)
        url = f"https://www.sports.ru/search/?q={q}&sort=rel"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; vm-plashki/1.0)",
            "Accept-Language": SPORTSRU_LANG_HDR,
        }
        r = requests.get(url, headers=headers, timeout=SPORTSRU_TIMEOUT)
        if not r.ok or not r.text:
            return None
        html = r.text
        m = _SPORTS_TITLE_RE.search(html)
        if m:
            name = m.group(1).strip()
            if re.search("[А-Яа-яЁё]", name):
                return name
        m2 = _H1_RE.search(html)
        if m2:
            name = m2.group(1).strip()
            if re.search("[А-Яа-яЁё]", name) and len(name) <= 80:
                return name
        return None
    except Exception:
        return None

def _ru_from_sportsru_cached(full_ascii: str) -> Optional[str]:
    key = _normalize_key(full_ascii)
    ru = _SPORTSRU_CACHE.get(key)
    if ru:
        return ru
    ru = _sportsru_fetch_ru_name(full_ascii)
    if ru:
        _SPORTSRU_CACHE[key] = ru
        _json_save(SPORTSRU_CACHE_FILE, _SPORTSRU_CACHE)
    return ru

def sportsru_force(pid: int, full_ascii: str) -> Optional[str]:
    ru = _sportsru_fetch_ru_name(full_ascii)
    if ru:
        _RU_OVERRIDES[str(pid)] = ru
        _SPORTSRU_CACHE[_normalize_key(full_ascii)] = ru
        _save_overrides()
        return ru
    return None

# ===== формирование display-имени =====
def _apply_lastname_rules(full_ascii: str, ru_guess: str) -> str:
    last_lat = _normalize_key(full_ascii).split(" ")[-1]
    fix = _LASTNAME_RULES.get(last_lat)
    if not fix:
        return ru_guess
    parts = ru_guess.split()
    if not parts:
        return ru_guess
    parts[-1] = fix
    return " ".join(parts)

def _ru_display_for_player(full_ascii: str, pid: int) -> str:
    ru = _RU_OVERRIDES.get(str(pid))
    if ru:
        return ru
    if SPORTSRU_RESOLVE:
        ru2 = _ru_from_sportsru_cached(full_ascii)
        if ru2:
            _RU_OVERRIDES[str(pid)] = ru2
            _save_overrides()
            return ru2
    ascii_key = _normalize_key(full_ascii)
    ru_guess = RU_NAME_OVERRIDES.get(ascii_key, lat2cyr(full_ascii))
    return _apply_lastname_rules(full_ascii, ru_guess)

# ===== загрузка снапшота игроков =====
def _load_local_snapshot() -> Optional[Dict[str, Any]]:
    p = ASSETS / "players.json"
    if p.exists() and p.stat().st_size > 1000:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            if j.get("league", {}).get("standard"):
                print("[players] snapshot: assets/players.json OK")
                return j
        except Exception:
            pass
    print("[players] snapshot: assets/players.json MISSING/SMALL")
    return None

def _load_snapshot_from_url() -> Optional[Dict[str, Any]]:
    if not PLAYERS_SNAPSHOT_URL:
        return None
    try:
        r = requests.get(PLAYERS_SNAPSHOT_URL, timeout=8, headers={"User-Agent":"vm-plashki/1.0"})
        if r.ok:
            j = r.json()
            std = j.get("league", {}).get("standard") or []
            print(f"[players] SNAPSHOT URL OK: players={len(std)}")
            return j if std else None
    except Exception as e:
        print(f"[players] SNAPSHOT URL FAIL: {type(e).__name__}: {e}")
    return None

def _build_index_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    players = payload.get("league", {}).get("standard", [])
    index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}
    for p in players:
        if not p.get("personId"):
            continue
        try:
            pid = int(p["personId"])
        except Exception:
            continue
        first = p.get("firstName","").strip()
        last  = p.get("lastName","").strip()
        try:
            team_id = int(p.get("teamId") or 0)
        except Exception:
            team_id = 0
        team_name = TEAM_NAMES.get(team_id, "Free Agent")
        full_ascii = f"{first} {last}".strip()
        ascii_key = _normalize_key(full_ascii)
        ru_display = _ru_display_for_player(full_ascii, pid)

        rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": ru_display,
            "team_id": team_id,
            "team_name": team_name,
        }
        index["_byid"][pid] = rec

        # ключи для поиска
        cyr_key = _normalize_key(ru_display)
        index["_bykey"][ascii_key] = rec
        index["_bykey"][cyr_key] = rec
        index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = rec
        index["_bykey"][_normalize_key(ru_display.replace("-", " "))] = rec

    # короткие алиасы
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

_PLAYERS: Optional[Dict[str, Any]] = None
_LAST_LOAD = 0

def _ensure_index(force: bool = False):
    """Офлайн-приоритет: assets → /tmp cache → (опц.) SNAPSHOT_URL → fallback."""
    global _PLAYERS, _LAST_LOAD
    now = time.time()
    if _PLAYERS and not force and (now - _LAST_LOAD) < REFRESH_SECONDS:
        return

    # 1) assets/players.json (репозиторий)
    snap = _load_local_snapshot()
    if snap:
        _PLAYERS = _build_index_from_payload(snap)
        _LAST_LOAD = now
        try:
            PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return

    # 2) /tmp cache
    if PLAYERS_CACHE.exists():
        try:
            _PLAYERS = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            _LAST_LOAD = now
            print("[players] loaded from /tmp cache")
            return
        except Exception:
            _PLAYERS = None

    # 3) быстрая ссылка (если офлайн=0 и URL задан)
    if not PLAYERS_OFFLINE:
        url_snap = _load_snapshot_from_url()
        if url_snap:
            _PLAYERS = _build_index_from_payload(url_snap)
            _LAST_LOAD = now
            try:
                PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            return

    # 4) fallback
    print("[players] FALLBACK used (9)")
    _PLAYERS = _build_index_from_payload({"league":{"standard": FALLBACK_PLAYERS}})
    _LAST_LOAD = now
    try:
        PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def players_count() -> int:
    _ensure_index()
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

def get_player_by_id(pid: int) -> Optional[Dict[str, Any]]:
    _ensure_index()
    return _PLAYERS["_byid"].get(int(pid)) if _PLAYERS else None

def _apply_alias(q_norm: str) -> Optional[str]:
    return _ALIASES.get(q_norm)

def find_player_by_name(query: str) -> Optional[Dict[str, Any]]:
    """Возвращает {id, full_name (лат), display (РУС), team_id, team_name}."""
    if not query:
        return None
    _ensure_index()
    if not _PLAYERS:
        return None

    q = _normalize_key(query)

    # 1) Алиасы, заданные из бота
    base = _apply_alias(q)
    if base and base in _PLAYERS["_bykey"]:
        return _PLAYERS["_bykey"][base]

    # 2) Прямые совпадения
    rec = _PLAYERS["_bykey"].get(q)
    if rec:
        return rec

    # 3) кириллица -> лат
    if re.search("[а-яё]", q):
        lat_guess = _normalize_key(cyr2lat(query))
        rec = _PLAYERS["_bykey"].get(lat_guess)
        if rec:
            return rec

    # 4) лат без акцентов
    lat_guess2 = _normalize_key(_strip_accents(query))
    if lat_guess2 != q:
        rec = _PLAYERS["_bykey"].get(lat_guess2)
        if rec:
            return rec

    # 5) лат -> кир транслит
    cyr_guess = _normalize_key(lat2cyr(query))
    rec = _PLAYERS["_bykey"].get(cyr_guess)
    if rec:
        return rec

    # 6) по фамилии
    last = q.split(" ")[-1]
    if len(last) >= 3:
        for k, r in _PLAYERS["_bykey"].items():
            if k.endswith(" " + last) or k == last:
                return r

    return None

def suggest_players(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Топ-совпадения по похожести (для «Игрок не найден»)."""
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
            r = requests.get(u, timeout=6, headers={"User-Agent":"vm-plashki/1.0"})
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

# ======= Переиндексация (главное, чтобы /setru и /fixlast применялись сразу) =======
def rebuild_index_inplace() -> int:
    """
    Полная пересборка in-memory индекса из уже известных игроков (без сетевых загрузок).
    Сохраняет в /tmp кэш.
    """
    global _PLAYERS, _LAST_LOAD
    _ensure_index()
    if not _PLAYERS:
        return 0
    # исходники берём из текущего _byid
    items = list(_PLAYERS["_byid"].values())
    new_index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}
    for rec in items:
        pid = int(rec["id"])
        full_ascii = rec["full_name"]
        team_id = int(rec.get("team_id") or 0)
        team_name = rec.get("team_name") or TEAM_NAMES.get(team_id, "Free Agent")
        ru_display = _ru_display_for_player(full_ascii, pid)

        new_rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": ru_display,
            "team_id": team_id,
            "team_name": team_name,
        }
        new_index["_byid"][pid] = new_rec

        # ключи для поиска
        ascii_key = _normalize_key(full_ascii)
        cyr_key   = _normalize_key(ru_display)
        new_index["_bykey"][ascii_key] = new_rec
        new_index["_bykey"][cyr_key]   = new_rec
        new_index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = new_rec
        new_index["_bykey"][_normalize_key(ru_display.replace("-", " "))] = new_rec

    _PLAYERS = new_index
    _LAST_LOAD = time.time()
    try:
        PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return len(_PLAYERS["_byid"])

def set_lastname_rule(latin_last: str, ru_last: str) -> int:
    """
    Добавить/обновить массовое правило по фамилии (например, brooks -> Брукс).
    Возвращает количество записей после пересборки.
    """
    _LASTNAME_RULES[_normalize_key(latin_last)] = ru_last
    _save_overrides()
    return rebuild_index_inplace()

def del_lastname_rule(latin_last: str) -> int:
    _LASTNAME_RULES.pop(_normalize_key(latin_last), None)
    _save_overrides()
    return rebuild_index_inplace()

def set_ru_override(pid: int, ru_name: str) -> int:
    _RU_OVERRIDES[str(int(pid))] = ru_name
    _save_overrides()
    return rebuild_index_inplace()

def del_ru_override(pid: int) -> int:
    _RU_OVERRIDES.pop(str(int(pid)), None)
    _save_overrides()
    return rebuild_index_inplace()
