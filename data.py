# data.py — индекс игроков (лат/кириллица), русское имя, headshots/логотипы, подсказки, алиасы
import os, json, unicodedata, re, time, logging
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import requests
from difflib import SequenceMatcher

# ===== логирование =====
DEBUG = os.getenv("DEBUG", "0") == "1"
logger = logging.getLogger("players")
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)

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

REFRESH_SECONDS = int(os.getenv("PLAYERS_REFRESH_SECONDS", "86400"))  # 1 день
CUSTOM_URL = os.getenv("PLAYERS_CUSTOM_URL", "").strip()

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

# --- Минимальный фоллбек на случай проблем с сетью/эндпоинтами ---
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
    """Добавляет алиас: alias_text -> base_full_ascii (оба нормализуются)."""
    try:
        k = _normalize_key(alias_text)
        base_k = _normalize_key(base_full_ascii)
        _ALIASES[k] = base_k
        ALIASES_FILE.write_text(json.dumps(_ALIASES, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False
_load_aliases()

# ===== нормализация разных источников к единому виду {"league":{"standard":[...]}} =====
def _normalize_to_league_standard(j: Any) -> Dict[str, Any]:
    """
    Возвращает {"league":{"standard":[{personId, firstName, lastName, teamId, isActive?}]}}
    Поддерживает три формы входа:
      1) уже league.standard;
      2) stats.nba.com resultSets PlayerList;
      3) массив объектов (игроков).
    """
    # 1) Уже в нужном формате
    if isinstance(j, dict) and isinstance(j.get("league", {}).get("standard"), list):
        std = j["league"]["standard"]
        return {"league": {"standard": std}}

    # 2) stats.nba.com resultSets
    if isinstance(j, dict) and isinstance(j.get("resultSets"), list):
        std: List[Dict[str, Any]] = []
        for rs in j["resultSets"]:
            name = rs.get("name") or rs.get("Name") or ""
            headers = rs.get("headers") or []
            rows = rs.get("rowSet") or []
            if str(name).lower() != "playerlist":
                continue
            # ожидаем заголовки PERSON_ID, FIRST_NAME, LAST_NAME, TEAM_ID, ROSTERSTATUS
            idx = {h: i for i, h in enumerate(headers)}
            for row in rows:
                try:
                    pid = str(row[idx.get("PERSON_ID")])
                    first = str(row[idx.get("FIRST_NAME")] or "")
                    last  = str(row[idx.get("LAST_NAME")] or "")
                    team  = str(row[idx.get("TEAM_ID")] or "0")
                    # ROSTERSTATUS: 1/0
                    is_active = bool(int(row[idx.get("ROSTERSTATUS")])) if idx.get("ROSTERSTATUS") is not None else True
                    std.append({
                        "personId": pid, "firstName": first, "lastName": last,
                        "teamId": team, "isActive": is_active
                    })
                except Exception:
                    continue
        return {"league": {"standard": std}}

    # 3) Плоский массив
    if isinstance(j, list):
        std = []
        for obj in j:
            if not isinstance(obj, dict): continue
            pid = str(obj.get("personId") or obj.get("PlayerID") or obj.get("id") or "")
            first = obj.get("firstName") or obj.get("first_name") or ""
            last  = obj.get("lastName") or obj.get("last_name") or ""
            team  = str(obj.get("teamId") or obj.get("TeamID") or obj.get("team_id") or "0")
            if not pid or not (first or last): continue
            std.append({
                "personId": pid, "firstName": str(first), "lastName": str(last),
                "teamId": team, "isActive": (team != "0")
            })
        return {"league": {"standard": std}}

    # неизвестный формат -> пусто
    return {"league": {"standard": []}}

# ===== загрузка игроков =====
def _fetch_players_payload() -> Dict[str, Any]:
    """
    1) Если задан PLAYERS_CUSTOM_URL — используем его.
    2) Иначе пробуем несколько legacy URL (часто ломаются).
    3) Если ничего не вышло — фоллбек на мини-список.
    """
    # 1) Кастомный воркер (рекомендуется)
    if CUSTOM_URL:
        try:
            url = CUSTOM_URL
            # если в урле нет query, добавим active=1 по умолчанию
            if "?" not in url:
                url = url + "?active=1"
            logger.info("[players] custom URL GET: %s", url)
            r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0 bot"})
            logger.info("[players] custom URL status=%s", r.status_code)
            if r.ok:
                j = r.json()
                norm = _normalize_to_league_standard(j)
                players = norm.get("league", {}).get("standard") or []
                logger.info("[players] custom URL parsed: players=%d", len(players))
                if players:
                    return norm
        except Exception as e:
            logger.info("[players] custom URL error: %s: %r", type(e).__name__, e)

    # 2) Legacy источники (часто 403/SSL/таймаут)
    legacy_urls = [
        "https://data.nba.com/data/10s/prod/v1/2025/players.json",
        "https://data.nba.com/data/10s/prod/v1/2024/players.json",
        "https://data.nba.net/prod/v1/2025/players.json",
        "https://data.nba.net/prod/v1/2024/players.json",
    ]
    for u in legacy_urls:
        try:
            r = requests.get(u, timeout=12, headers={"User-Agent":"Mozilla/5.0 bot"})
            if r.ok:
                j = r.json()
                norm = _normalize_to_league_standard(j)
                players = norm.get("league", {}).get("standard") or []
                logger.info("[players] legacy OK %s -> %d", u, len(players))
                if players:
                    return norm
            else:
                logger.info("[players] legacy GET %s: status=%s", u, r.status_code)
        except Exception as e:
            logger.info("[players] legacy error %s: %s", u, repr(e))
            continue

    # 3) Фоллбек
    logger.info("[players] FALLBACK used (%d)", len(FALLBACK_PLAYERS))
    return {"league": {"standard": FALLBACK_PLAYERS}}

def _build_index() -> Dict[str, Any]:
    payload = _fetch_players_payload()
    players = payload.get("league", {}).get("standard", []) or []
    index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}

    for p in players:
        try:
            pid = int(str(p.get("personId")).strip())
        except Exception:
            continue
        if not pid:
            continue
        first = str(p.get("firstName","")).strip()
        last  = str(p.get("lastName","")).strip()
        team_id = int(str(p.get("teamId") or "0"))
        team_name = TEAM_NAMES.get(team_id, "Free Agent")
        full_ascii = f"{first} {last}".strip()
        if not full_ascii:
            continue
        ascii_key = _normalize_key(full_ascii)
        ru_display = RU_NAME_OVERRIDES.get(ascii_key, lat2cyr(full_ascii))

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

    # прозвища (короткие алиасы)
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

_PLAYERS: Dict[str, Any] | None = None
_LAST_LOAD = 0

def _ensure_index(force: bool=False):
    global _PLAYERS, _LAST_LOAD
    now = time.time()
    if not force and _PLAYERS and (now - _LAST_LOAD) < REFRESH_SECONDS:
        return
    # читать из кэша
    if not force and PLAYERS_CACHE.exists():
        try:
            _PLAYERS = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            _LAST_LOAD = now
            return
        except Exception:
            _PLAYERS = None
    # заново собрать
    _PLAYERS = _build_index()
    try:
        PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
        logger.info("[players] cache save ok")
    except Exception:
        pass
    _LAST_LOAD = now

def drop_players_cache():
    """Полностью сбрасывает индекс и кэш, чтобы перезалить."""
    global _PLAYERS, _LAST_LOAD
    _PLAYERS, _LAST_LOAD = None, 0
    try:
        if PLAYERS_CACHE.exists():
            PLAYERS_CACHE.unlink()
    except Exception:
        pass

def get_player_by_id(pid: int) -> Optional[Dict[str, Any]]:
    _ensure_index()
    return _PLAYERS["_byid"].get(int(pid)) if _PLAYERS else None

def players_count() -> int:
    _ensure_index()
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

def _apply_alias(q_norm: str) -> Optional[str]:
    """Если q_norm — алиас, вернуть базовый ascii-key."""
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

    # 3) Кири -> лат
    if re.search("[а-яё]", q):
        lat_guess = _normalize_key(cyr2lat(query))
        rec = _PLAYERS["_bykey"].get(lat_guess)
        if rec:
            return rec

    # 4) Лат -> лат без акцентов
    lat_guess2 = _normalize_key(_strip_accents(query))
    if lat_guess2 != q:
        rec = _PLAYERS["_bykey"].get(lat_guess2)
        if rec:
            return rec

    # 5) Лат -> кир транслит (иногда помогает)
    cyr_guess = _normalize_key(lat2cyr(query))
    rec = _PLAYERS["_bykey"].get(cyr_guess)
    if rec:
        return rec

    # 6) Фамилия
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
