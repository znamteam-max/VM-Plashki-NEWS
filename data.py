# data.py — онлайн-индекс игроков (лат/кириллица), русское имя, headshots/логотипы, подсказки, алиасы
import os, json, unicodedata, re, time
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import requests
from difflib import SequenceMatcher

# ===== настройки через ENV =====
# Всегда брать список ИЗ СЕТИ (игнорировать локальный snapshot)
PLAYERS_ALWAYS_ONLINE = os.getenv("PLAYERS_ALWAYS_ONLINE", "1").lower() in ("1","true","yes")
# Как часто обновлять индекс (сек). 21600 = 6 часов.
REFRESH_SECONDS = int(os.getenv("PLAYERS_REFRESH_SECONDS", "21600"))
# Оставлять только активных игроков
ACTIVE_ONLY = os.getenv("PLAYERS_ACTIVE_ONLY", "1").lower() in ("1","true","yes")

# ===== пути и кэш =====
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
CACHE = Path("/tmp/nba_cache")
CACHE.mkdir(parents=True, exist_ok=True)

PLAYERS_CACHE = CACHE / "players_index.json"
ALIASES_FILE  = CACHE / "aliases.json"
HEAD_DIR = CACHE / "headshots"; HEAD_DIR.mkdir(exist_ok=True)
LOGO_DIR = ASSETS / "cache"  # ожидаются logo_<teamId>.png
ICON_STAR = str((ASSETS / "icons" / "star.png").resolve())

# ===== команды: цвет =====
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
    "giannis antetokounmpo":"Яннис Адетокунбо",
    "joel embiid":"Джоэл Эмбиид",
    "anthony davis":"Энтони Дэвис",
    "kyrie irving":"Кайри Ирвинг",
    "shai gilgeous alexander":"Шай Гилджес-Александр",
    "domantas sabonis":"Домантас Сабонис",
    "alperen sengun":"Алперен Шенгюн",
}

# --- фоллбек на случай полной недоступности сети ---
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
        ALIASES_FILE.write_text(json.dumps(_ALIASES), encoding="utf-8")
        return True
    except Exception:
        return False
_load_aliases()

# ===== загрузка игроков (всегда онлайн + ретраи) =====
def _fetch_players_payload() -> Dict[str, Any]:
    """
    Пытаемся получить список игроков через stats.nba.com (commonallplayers),
    с нужными заголовками. Если не вышло — пробуем старые data.nba.net/data.nba.com.
    В самом конце — короткий фоллбек.
    Возвращаем payload в формате {"league": {"standard": [ {personId, firstName, lastName, teamId}, ... ]}}
    чтобы остальной код (_build_index) работал без изменений.
    """
    # --- 1) stats.nba.com (нужны headers)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.nba.com/",
        "Origin": "https://www.nba.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
        # Не просим br, чтобы не требовать brotli в зависимостях:
        "Accept-Encoding": "gzip, deflate",
    }
    # Три сезона на случай межсезонья
    seasons = ["2025-26", "2024-25", "2023-24"]
    for season in seasons:
        url = ("https://stats.nba.com/stats/commonallplayers"
               f"?LeagueID=00&Season={season}&IsOnlyCurrentSeason=1")
        try:
            r = requests.get(url, headers=headers, timeout=12)
            if r.ok:
                j = r.json()
                rs = None
                # разные варианты ключей в ответе
                if isinstance(j.get("resultSets"), list) and j["resultSets"]:
                    rs = j["resultSets"][0]
                elif isinstance(j.get("resultSet"), dict):
                    rs = j["resultSet"]

                if rs:
                    headers_list = rs.get("headers") or []
                    rows = rs.get("rowSet") or []
                    # индексы нужных колонок
                    idx = {h: i for i, h in enumerate(headers_list)}
                    need = ("PERSON_ID", "FIRST_NAME", "LAST_NAME", "TEAM_ID")
                    if all(k in idx for k in need):
                        out = []
                        for row in rows:
                            person_id = str(row[idx["PERSON_ID"]])
                            first = row[idx["FIRST_NAME"]] or ""
                            last  = row[idx["LAST_NAME"]] or ""
                            team_id = str(row[idx["TEAM_ID"]] or "0")
                            out.append({
                                "personId": person_id,
                                "firstName": first,
                                "lastName": last,
                                "teamId": team_id,
                            })
                        if out:
                            return {"league": {"standard": out}}
        except Exception:
            pass

    # --- 2) старые JSON (иногда работают)
    urls_legacy = [
        "https://data.nba.net/prod/v1/2025/players.json",
        "https://data.nba.net/prod/v1/2024/players.json",
        "https://data.nba.com/data/10s/prod/v1/2025/players.json",
        "https://data.nba.com/data/10s/prod/v1/2024/players.json",
    ]
    for u in urls_legacy:
        try:
            r = requests.get(u, timeout=12)
            if r.ok:
                j = r.json()
                if j.get("league", {}).get("standard"):
                    return j
        except Exception:
            continue

    # --- 3) фоллбек
    return {"league": {"standard": FALLBACK_PLAYERS}}

def _build_index() -> Dict[str, Any]:
    payload = _fetch_players_payload()
    players = payload.get("league", {}).get("standard", [])
    index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}

    for p in players:
        if not p.get("personId"):
            continue
        if ACTIVE_ONLY and (p.get("isActive") is False):
            continue
        pid = int(p["personId"])
        first = p.get("firstName","").strip()
        last  = p.get("lastName","").strip()
        team_id = int(p.get("teamId") or 0)
        full_ascii = f"{first} {last}".strip()
        ascii_key = _normalize_key(full_ascii)
        ru_display = RU_NAME_OVERRIDES.get(ascii_key, lat2cyr(full_ascii))

        rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": ru_display,
            "team_id": team_id,
            "team_name": "",  # не используем сейчас, можно дополнить
        }
        index["_byid"][pid] = rec

        cyr_key = _normalize_key(ru_display)
        index["_bykey"][ascii_key] = rec
        index["_bykey"][cyr_key] = rec
        index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = rec
        index["_bykey"][_normalize_key(ru_display.replace("-", " "))] = rec

    # короткие алиасы/прозвища
    aliases = {
        "wemby":"victor wembanyama", "вемба":"victor wembanyama",
        "зайон":"zion williamson", "лука":"luka doncic", "йокич":"nikola jokic", "ёкич":"nikola jokic",
        "яннис":"giannis antetokounmpo", "эмбиид":"joel embiid", "эмбид":"joel embiid",
        "стеф":"stephen curry", "стефан":"stephen curry", "кайри":"kyrie irving",
        "шай":"shai gilgeous alexander",
    }
    for a, base in aliases.items():
        k = _normalize_key(a); basek = _normalize_key(base)
        if basek in index["_bykey"]:
            index["_bykey"][k] = index["_bykey"][basek]

    return index

_PLAYERS: Dict[str, Any] | None = None
_LAST_LOAD = 0

def _ensure_index(force_online: bool = False):
    """
    При пустом/устаревшем индексе — грузим онлайн и сохраняем в /tmp.
    Если сеть умерла — берём из кэша /tmp, и только если нет — фоллбек.
    """
    global _PLAYERS, _LAST_LOAD
    now = time.time()

    if _PLAYERS and not force_online and (now - _LAST_LOAD) < REFRESH_SECONDS:
        return

    # попытка загрузить из /tmp кэша, если недавно ломалось
    if not force_online and PLAYERS_CACHE.exists() and not _PLAYERS:
        try:
            data = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            if data.get("_byid"):
                _PLAYERS = data
                _LAST_LOAD = now
        except Exception:
            _PLAYERS = None

    # онлайн загрузка
    online = _build_index()
    if online.get("_byid"):
        _PLAYERS = online
        _LAST_LOAD = now
        try:
            PLAYERS_CACHE.write_text(json.dumps(_PLAYERS), encoding="utf-8")
        except Exception:
            pass
        return

    # если онлайн пуст, но есть кэш — остаёмся на кэше
    if _PLAYERS:
        return

    # последний шанс — фоллбек
    _PLAYERS = _build_index()
    _LAST_LOAD = now

def force_refresh_players() -> int:
    """Принудительно перегрузить индекс онлайн."""
    global _PLAYERS, _LAST_LOAD
    _PLAYERS = None
    _LAST_LOAD = 0
    _ensure_index(force_online=True)
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

def get_player_by_id(pid: int) -> Optional[Dict[str, Any]]:
    _ensure_index()
    return _PLAYERS["_byid"].get(int(pid)) if _PLAYERS else None

def players_count() -> int:
    _ensure_index()
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

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
