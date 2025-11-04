# data.py — индекс игроков (через Cloudflare Worker-прокси), алиасы, фиксы русских имён,
# загрузка headshots, логотипы, подсказки.
#
# Ожидаемые ENV:
#   PLAYERS_URL           — ваш Cloudflare Worker, напр.: https://nba-players-proxy.<acct>.workers.dev/?season=2025-26
#   PLAYERS_REFRESH_SECONDS (опц., по умолчанию 86400)
#   ADMIN_IDS             — список телеграм-ID редакторов, через запятую (например: "123,456")
#
# Папки/файлы (Vercel):
#   /tmp/nba_cache                  — кэш (переживает в рамках одного контейнера)
#   /tmp/nba_cache/players_index.json
#   /tmp/nba_cache/aliases.json
#   /tmp/nba_cache/ru_overrides.json
#   /tmp/nba_cache/lastname_rules.json
#
# Замечание:
#   В индекс попадают активные игроки сезона (ROSTERSTATUS=1) из stats.nba.com/commonallplayers,
#   но забираются через ваш Cloudflare Worker (чтобы обойти ограничения и таймауты).
#

import os, json, unicodedata, re, time
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import requests
from difflib import SequenceMatcher

# ===== пути и кэш =====
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

CACHE = Path("/tmp/nba_cache")
CACHE.mkdir(parents=True, exist_ok=True)

PLAYERS_CACHE   = CACHE / "players_index.json"
ALIASES_FILE    = CACHE / "aliases.json"
OVERRIDES_FILE  = CACHE / "ru_overrides.json"      # pid -> "Русское Имя"
LASTNAME_FILE   = CACHE / "lastname_rules.json"    # "brooks" -> "Брукс"

HEAD_DIR = CACHE / "headshots"; HEAD_DIR.mkdir(exist_ok=True)
LOGO_DIR = ASSETS / "cache"  # ожидаются logo_<teamId>.png
ICON_STAR = str((ASSETS / "icons" / "star.png").resolve())

# ===== окружение =====
PLAYERS_URL = os.getenv("PLAYERS_URL", "").strip()
REFRESH_SECONDS = int(os.getenv("PLAYERS_REFRESH_SECONDS", "86400"))  # 1 день
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = []
for tok in re.split(r"[,\s]+", ADMIN_IDS_ENV):
    if tok.isdigit():
        ADMIN_IDS.append(int(tok))

# ===== команды и цвета =====
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

# ===== базовые суппорты транслита =====
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

# ===== вручную заданные русские формы (для самых популярных кейсов) =====
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

# ===== Фоллбек для старта (минимум) =====
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

# ===== персист частных фиксов =====
_RU_OVERRIDES: Dict[str, str] = {}      # pid(str) -> "Рус Имя"
_LASTNAME_RULES: Dict[str, str] = {}    # lastname(ascii lower) -> "Рус Фамилия"

def _load_overrides():
    global _RU_OVERRIDES, _LASTNAME_RULES
    try:
        if OVERRIDES_FILE.exists():
            _RU_OVERRIDES = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        else:
            _RU_OVERRIDES = {}
    except Exception:
        _RU_OVERRIDES = {}
    try:
        if LASTNAME_FILE.exists():
            _LASTNAME_RULES = json.loads(LASTNAME_FILE.read_text(encoding="utf-8"))
        else:
            _LASTNAME_RULES = {}
    except Exception:
        _LASTNAME_RULES = {}

def _save_overrides():
    try:
        OVERRIDES_FILE.write_text(json.dumps(_RU_OVERRIDES), encoding="utf-8")
    except Exception:
        pass
    try:
        LASTNAME_FILE.write_text(json.dumps(_LASTNAME_RULES), encoding="utf-8")
    except Exception:
        pass

_load_overrides()

def set_ru_override(pid: int, ru: str) -> int:
    _RU_OVERRIDES[str(int(pid))] = ru.strip()
    _save_overrides()
    return rebuild_index_inplace()

def del_ru_override(pid: int) -> int:
    _RU_OVERRIDES.pop(str(int(pid)), None)
    _save_overrides()
    return rebuild_index_inplace()

def set_lastname_rule(latin_last: str, ru_last: str) -> int:
    _LASTNAME_RULES[_normalize_key(latin_last)] = ru_last.strip()
    _save_overrides()
    return rebuild_index_inplace()

def del_lastname_rule(latin_last: str) -> int:
    _LASTNAME_RULES.pop(_normalize_key(latin_last), None)
    _save_overrides()
    return rebuild_index_inplace()

# ===== загрузка с Cloudflare Worker (stats.nba.com/commonallplayers) =====
def _fetch_players_from_stats_commonallplayers(url: str) -> Dict[str, Any]:
    """
    Ждём ответ формата stats.nba.com/stats/commonallplayers (через ваш воркер).
    Возвращаем {"league": {"standard": [{"personId","firstName","lastName","teamId"}, ...]}}
    только для активных (ROSTERSTATUS=1).
    """
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    j = r.json()
    sets = j.get("resultSets") or j.get("ResultSets") or []
    if not sets:
        return {"league": {"standard": []}}

    headers = sets[0].get("headers") or sets[0].get("Headers") or []
    rows = sets[0].get("rowSet") or sets[0].get("RowSet") or []

    # иногда нужный сет не первый — найдём где есть PERSON_ID
    for s in sets:
        h = s.get("headers") or s.get("Headers") or []
        if "PERSON_ID" in h:
            headers = h
            rows = s.get("rowSet") or s.get("RowSet") or []
            break

    idx = {name: i for i, name in enumerate(headers)}
    need = ["PERSON_ID", "FIRST_NAME", "LAST_NAME", "TEAM_ID", "ROSTERSTATUS"]
    if not all(k in idx for k in need):
        return {"league": {"standard": []}}

    out = []
    for row in rows:
        roster = str(row[idx["ROSTERSTATUS"]])
        if roster not in ("1", "True", "true"):
            continue
        pid   = str(row[idx["PERSON_ID"]])
        first = str(row[idx["FIRST_NAME"]] or "").strip()
        last  = str(row[idx["LAST_NAME"]] or "").strip()
        team  = str(row[idx["TEAM_ID"]] or "0")
        out.append({"personId": pid, "firstName": first, "lastName": last, "teamId": team})
    return {"league": {"standard": out}}

# ===== сборка индекса =====
def _fetch_players_payload() -> Dict[str, Any]:
    # 0) Cloudflare Worker — приоритетно и стабильно
    if PLAYERS_URL:
        try:
            j = _fetch_players_from_stats_commonallplayers(PLAYERS_URL)
            if j.get("league", {}).get("standard"):
                return j
        except Exception:
            pass

    # 1) фоллбек, чтобы бот не «пустел»
    return {"league": {"standard": FALLBACK_PLAYERS}}

def _ru_display_for_player(full_ascii: str, pid: int | str) -> str:
    # 1) точечный override по PID
    ru = _RU_OVERRIDES.get(str(int(pid)))
    if ru:
        return ru

    # 2) готовые ручные маппинги на самых популярных
    base_key = _normalize_key(full_ascii)
    ru = RU_NAME_OVERRIDES.get(base_key)
    if not ru:
        ru = lat2cyr(full_ascii)

    # 3) применим правило фамилии, если есть
    #    (определяем фамилию по ascii last token из full_ascii)
    last_ascii = _normalize_key(full_ascii).split(" ")[-1]
    fix = _LASTNAME_RULES.get(last_ascii)
    if fix:
        # заменим последнюю "слово-подобную" часть в ru на фикc
        parts = ru.split()
        if parts:
            parts[-1] = fix
            ru = " ".join(parts)

    return ru

def _build_index(payload: Dict[str, Any]) -> Dict[str, Any]:
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

        rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": _ru_display_for_player(full_ascii, pid),
            "team_id": team_id,
            "team_name": team_name,
        }
        index["_byid"][pid] = rec

        # ключи для поиска
        ascii_key = _normalize_key(full_ascii)
        cyr_key   = _normalize_key(rec["display"])
        index["_bykey"][ascii_key] = rec
        index["_bykey"][cyr_key]   = rec
        index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = rec
        index["_bykey"][_normalize_key(rec["display"].replace("-", " "))] = rec

    # короткие алиасы (пример)
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

    # кастомные алиасы (из файла)
    for k, basek in _ALIASES.items():
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
    # пробуем из кэша
    if PLAYERS_CACHE.exists():
        try:
            _PLAYERS = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            _LAST_LOAD = now
            return
        except Exception:
            _PLAYERS = None
    # иначе грузим
    payload = _fetch_players_payload()
    _PLAYERS = _build_index(payload)
    try:
        PLAYERS_CACHE.write_text(json.dumps(_PLAYERS), encoding="utf-8")
    except Exception:
        pass
    _LAST_LOAD = now

def rebuild_index_inplace() -> int:
    """Пересобирает индекс с учётом сохранённых оверрайдов/правил без сетевых запросов,
       использует последний payload из кэша players_index.json по id/full_name/team_id/team_name.
       Если кэша нет — честно перезагрузит payload.
    """
    global _PLAYERS, _LAST_LOAD
    now = time.time()
    base = None
    if PLAYERS_CACHE.exists():
        try:
            base = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            base = None
    if not base:
        payload = _fetch_players_payload()
        _PLAYERS = _build_index(payload)
        try:
            PLAYERS_CACHE.write_text(json.dumps(_PLAYERS), encoding="utf-8")
        except Exception:
            pass
        _LAST_LOAD = now
        return len(_PLAYERS.get("_byid", {}))
    # пересчитать display'и
    out = {"_bykey": {}, "_byid": {}}
    for pid, rec in base.get("_byid", {}).items():
        pid_int = int(pid)
        full_ascii = rec["full_name"]
        team_id = int(rec.get("team_id") or 0)
        team_name = TEAM_NAMES.get(team_id, "Free Agent")
        newrec = {
            "id": pid_int,
            "full_name": full_ascii,
            "display": _ru_display_for_player(full_ascii, pid_int),
            "team_id": team_id,
            "team_name": team_name,
        }
        out["_byid"][pid_int] = newrec
        out["_bykey"][_normalize_key(full_ascii)] = newrec
        out["_bykey"][_normalize_key(newrec["display"])] = newrec
    _PLAYERS = out
    try:
        PLAYERS_CACHE.write_text(json.dumps(_PLAYERS), encoding="utf-8")
    except Exception:
        pass
    _LAST_LOAD = now
    return len(_PLAYERS.get("_byid", {}))

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

    # 1) алиас
    base = _apply_alias(q)
    if base and base in _PLAYERS["_bykey"]:
        return _PLAYERS["_bykey"][base]

    # 2) прямое совпадение
    rec = _PLAYERS["_bykey"].get(q)
    if rec:
        return rec

    # 3) кири -> лат
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

# ===== sports.ru auto (по запросу /resolve) — опционально, best-effort =====
def sportsru_force(pid: int, full_ascii: str) -> Optional[str]:
    """
    Пытаемся найти русскую форму на sports.ru (поиск).
    Возвращает строку или None. Это best-effort и может не сработать.
    """
    try:
        q = requests.utils.quote(full_ascii)
        url = f"https://www.sports.ru/search/?q={q}"
        r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if not r.ok:
            return None
        # очень грубо: ищем "— баскетболист" или заголовки ссылок с кириллицей
        m = re.search(r">([А-ЯЁа-яё][^<]{2,50})</a>", r.text)
        if m:
            name = m.group(1).strip()
            # sanity: не слишком длинно и есть пробел
            if " " in name and len(name) <= 60:
                _RU_OVERRIDES[str(int(pid))] = name
                _save_overrides()
                return name
    except Exception:
        return None
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
