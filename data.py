# data.py — глобальный индекс игроков (лат/кириллица), русское отображение, headshots/логотипы
import os, json, unicodedata, re, time
from pathlib import Path
from typing import Dict, Any, Tuple
import requests

# ===== пути и кэш =====
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
CACHE = Path("/tmp/nba_cache")
CACHE.mkdir(parents=True, exist_ok=True)

PLAYERS_CACHE = CACHE / "players_index.json"      # нормализованный индекс
HEAD_DIR = CACHE / "headshots"; HEAD_DIR.mkdir(exist_ok=True)
LOGO_DIR = ASSETS / "cache"                        # ожидаются logo_<teamId>.png
ICON_STAR = str((ASSETS / "icons" / "star.png").resolve())

REFRESH_SECONDS = int(os.getenv("PLAYERS_REFRESH_SECONDS", "86400"))  # 1 день

# ===== команды: имя и базовый цвет (primary). dark рассчитываем автоматически =====
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

# ===== нормализация + транслитерация =====
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def _normalize_key(s: str) -> str:
    s = _strip_accents(s).lower()
    s = re.sub(r"[^a-zа-яё0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# простая лат->кир транслитерация для имён
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
    # титульный регистр по словам
    return " ".join(w.capitalize() for w in txt.split())

# кир->лат для поиска (упрощённо)
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

# ===== ручные русские имена (точные написания для топов) =====
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

# ===== загрузка/сборка индекса игроков =====
def _fetch_players_payload() -> Dict[str, Any]:
    # сначала локальный snapshot (если вдруг положишь assets/players.json)
    local = ASSETS / "players.json"
    if local.exists():
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except Exception:
            pass
    # иначе тянем c NBA CDN (сначала текущий год, потом прошлый)
    urls = [
        "https://data.nba.com/data/10s/prod/v1/2025/players.json",
        "https://data.nba.com/data/10s/prod/v1/2024/players.json",
    ]
    for u in urls:
        try:
            r = requests.get(u, timeout=10)
            if r.ok:
                return r.json()
        except Exception:
            continue
    return {"league": {"standard": []}}

def _build_index() -> Dict[str, Any]:
    payload = _fetch_players_payload()
    players = payload.get("league", {}).get("standard", [])
    index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}

    for p in players:
        if not p.get("personId"): 
            continue
        pid = int(p["personId"])
        first = p.get("firstName","").strip()
        last  = p.get("lastName","").strip()
        team_id = int(p.get("teamId") or 0)
        team_name = TEAM_NAMES.get(team_id, "Free Agent")
        full_ascii = f"{first} {last}".strip()
        ascii_key = _normalize_key(full_ascii)
        # Покажем имя по-русски: override или транслит
        ru_display = RU_NAME_OVERRIDES.get(ascii_key, lat2cyr(full_ascii))

        rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": ru_display,
            "team_id": team_id,
            "team_name": team_name,
        }
        index["_byid"][pid] = rec

        # ключи для поиска: латиница/без акцентов, кириллица (транслит)
        cyr_key = _normalize_key(ru_display)
        index["_bykey"][ascii_key] = rec
        index["_bykey"][cyr_key] = rec

        # варианты без пробелов/дефисов
        index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = rec
        index["_bykey"][_normalize_key(ru_display.replace("-", " "))] = rec

    # ручные алиасы для известных прозвищ
    aliases = {
        "wemby":"victor wembanyama",
        "вемба":"victor wembanyama",
        "зайон":"zion williamson",
        "лёбра":"lebron james", "леброн":"lebron james",
        "стеф":"stephen curry", "стефан":"stephen curry",
        "йокич":"nikola jokic", "ёкич":"nikola jokic",
        "лука":"luka doncic",
        "яннис":"giannis antetokounmpo",
        "эмбиид":"joel embiid","эмбид":"joel embiid",
        "кайри":"kyrie irving",
        "шай":"shai gilgeous alexander",
    }
    for a, base in aliases.items():
        k = _normalize_key(a)
        basek = _normalize_key(base)
        if basek in index["_bykey"]:
            index["_bykey"][k] = index["_bykey"][basek]

    return index

_PLAYERS: Dict[str, Any] | None = None
_LAST_LOAD = 0

def _ensure_index():
    global _PLAYERS, _LAST_LOAD
    now = time.time()
    if _PLAYERS and (now - _LAST_LOAD) < REFRESH_SECONDS:
        return
    # пробуем взять из кэша
    if PLAYERS_CACHE.exists():
        try:
            data = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            _PLAYERS = data; _LAST_LOAD = now
        except Exception:
            _PLAYERS = None
    if not _PLAYERS:
        _PLAYERS = _build_index()
        try:
            PLAYERS_CACHE.write_text(json.dumps(_PLAYERS), encoding="utf-8")
        except Exception:
            pass
        _LAST_LOAD = now

def find_player_by_name(query: str) -> Dict[str, Any] | None:
    """
    Принимает имя на латинице или кириллице (в любом регистре/с акцентами).
    Возвращает dict: {id, full_name (латиница), display (КИРИЛЛИЦА), team_id, team_name}
    """
    if not query:
        return None
    _ensure_index()
    if not _PLAYERS:
        return None

    q = _normalize_key(query)
    # прямое совпадение
    rec = _PLAYERS["_bykey"].get(q)
    if rec:
        return rec

    # попробуем транслитнуть кириллицу в латиницу и поискать
    if re.search("[а-яё]", q):
        lat_guess = _normalize_key(cyr2lat(query))
        rec = _PLAYERS["_bykey"].get(lat_guess)
        if rec:
            return rec

    # попробуем транслитнуть латиницу -> кириллица и поискать
    lat_guess2 = _normalize_key(_strip_accents(query))
    if lat_guess2 != q:
        rec = _PLAYERS["_bykey"].get(lat_guess2)
        if rec:
            return rec
    cyr_guess = _normalize_key(lat2cyr(query))
    rec = _PLAYERS["_bykey"].get(cyr_guess)
    if rec:
        return rec

    # слабый поиск по фамилии (последнее слово)
    last = q.split(" ")[-1]
    if len(last) >= 3:
        for k, r in _PLAYERS["_bykey"].items():
            if k.endswith(" " + last) or k == last:
                return r

    return None

# ===== headshots & logos =====
def ensure_headshot_png(player_id: int, full_name: str) -> str:
    """
    Кладём headshot PNG в /tmp (Vercel writeable). Пробуем несколько размеров CDN.
    """
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
    # плейсхолдер
    return ICON_STAR

def ensure_team_logo_png(team_id: int) -> Tuple[str, Tuple[str,str,str]]:
    """
    Возвращает: (путь к PNG логотипу, (primary, dark, light))
    Логотип ищется в assets/cache/logo_<teamId>.png, иначе — плейсхолдер.
    Цвета: primary из словаря, dark — 90% от primary, light — белый.
    """
    if team_id and (LOGO_DIR / f"logo_{team_id}.png").exists():
        logo_path = str((LOGO_DIR / f"logo_{team_id}.png").resolve())
    else:
        logo_path = ICON_STAR
    primary = TEAM_PRIMARY.get(team_id, "#0EA5FF")
    dark = _shade(primary, 0.90)
    light = "#FFFFFF"
    return logo_path, (primary, dark, light)
