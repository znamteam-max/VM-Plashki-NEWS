# data.py — индекс игроков (лат/кириллица), русское имя, headshots/логотипы, подсказки, алиасы
# Спроектирован для Vercel/Serverless: устойчив к тайм-аутам, сетевым сбоям и "пустым" ответам
import os, json, unicodedata, re, time
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import requests
from difflib import SequenceMatcher
from datetime import datetime

# ============ базовая файловая структура/кэш ============
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

# персист-переопределения (для /setru, /setteam, /sethead, /addplayer)
OVERRIDES_RU_FILE    = CACHE / "overrides_ru.json"      # { "<player_id>": "Рус Имя" }
OVERRIDES_TEAM_FILE  = CACHE / "overrides_team.json"    # { "<player_id>": 16106127xxx }
OVERRIDES_PHOTO_FILE = CACHE / "overrides_photo.json"   # { "<player_id>": "https://..." | "local:/path" }
CUSTOM_PLAYERS_FILE  = CACHE / "custom_players.json"    # [ {personId, firstName, lastName, teamId, isActive} ]

# ============ окружение/флаги ============
REFRESH_SECONDS = int(os.getenv("PLAYERS_REFRESH_SECONDS", "86400"))  # 1 день
PLAYERS_CUSTOM_URL = os.getenv("PLAYERS_CUSTOM_URL", "").strip()
ENFORCE_SOURCE = os.getenv("PLAYERS_ENFORCE_SOURCE", "stats").lower()    # stats|any (мы используем stats)
ALLOW_LEGACY_FALLBACK = os.getenv("PLAYERS_ALLOW_LEGACY", "0") == "1"    # по умолчанию запрещено
MIN_EXPECTED = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))             # если меньше — считаем невалидным
REQUEST_TIMEOUT = int(os.getenv("PLAYERS_REQUEST_TIMEOUT", "20"))

def _log(msg: str):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"{ts} [info] [data] {msg}")

# ============ команды НБА ============
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

# ============ нормализация/транслит ============
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

# ============ ручные русские имена (базовый словарь) ============
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

# --- Фоллбек на случай полного провала источников (минимальный список) ---
FALLBACK_PLAYERS = [
    {"personId":"203999","firstName":"Nikola","lastName":"Jokic","teamId":"1610612743","isActive":True},
    {"personId":"201939","firstName":"Stephen","lastName":"Curry","teamId":"1610612744","isActive":True},
    {"personId":"2544","firstName":"LeBron","lastName":"James","teamId":"1610612747","isActive":True},
    {"personId":"1641707","firstName":"Victor","lastName":"Wembanyama","teamId":"1610612759","isActive":True},
    {"personId":"1629627","firstName":"Zion","lastName":"Williamson","teamId":"1610612740","isActive":True},
    {"personId":"203507","firstName":"Giannis","lastName":"Antetokounmpo","teamId":"1610612749","isActive":True},
    {"personId":"203954","firstName":"Joel","lastName":"Embiid","teamId":"1610612755","isActive":True},
    {"personId":"201142","firstName":"Kevin","lastName":"Durant","teamId":"1610612756","isActive":True},
    {"personId":"1629029","firstName":"Luka","lastName":"Doncic","teamId":"1610612742","isActive":True},
]

# ============ алиасы ============
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
    """
    Добавляет алиас: alias_text -> base_full_ascii (оба нормализуются).
    """
    try:
        k = _normalize_key(alias_text)
        base_k = _normalize_key(base_full_ascii)
        _ALIASES[k] = base_k
        ALIASES_FILE.write_text(json.dumps(_ALIASES, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False

def _apply_alias(q_norm: str) -> Optional[str]:
    return _ALIASES.get(q_norm)

_load_aliases()

# ============ персист-переопределения (load/save) ============
def _load_json_file(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def _save_json_file(path: Path, data) -> bool:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False

_OVR_RU    = _load_json_file(OVERRIDES_RU_FILE, {})
_OVR_TEAM  = _load_json_file(OVERRIDES_TEAM_FILE, {})
_OVR_PHOTO = _load_json_file(OVERRIDES_PHOTO_FILE, {})
_CUSTOM    = _load_json_file(CUSTOM_PLAYERS_FILE, [])

def set_ru_display(player_id: int, ru_name: str) -> bool:
    _OVR_RU[str(int(player_id))] = ru_name.strip()
    ok = _save_json_file(OVERRIDES_RU_FILE, _OVR_RU)
    if ok:
        _bust_players_cache_memory()
    return ok

def set_player_team(player_id: int, team_id: int) -> bool:
    _OVR_TEAM[str(int(player_id))] = int(team_id)
    ok = _save_json_file(OVERRIDES_TEAM_FILE, _OVR_TEAM)
    if ok:
        _bust_players_cache_memory()
    return ok

def set_player_photo(player_id: int, url_or_local: str) -> bool:
    _OVR_PHOTO[str(int(player_id))] = url_or_local.strip()
    ok = _save_json_file(OVERRIDES_PHOTO_FILE, _OVR_PHOTO)
    return ok

def add_custom_player(personId: int, firstName: str, lastName: str, teamId: int = 0, isActive: bool = True) -> bool:
    rec = {
        "personId": str(int(personId)),
        "firstName": firstName.strip(),
        "lastName": lastName.strip(),
        "teamId": str(int(teamId)) if teamId else "0",
        "isActive": bool(isActive),
    }
    # обновим, если уже существует
    exists = False
    for i, r in enumerate(_CUSTOM):
        if str(r.get("personId")) == rec["personId"]:
            _CUSTOM[i] = rec
            exists = True
            break
    if not exists:
        _CUSTOM.append(rec)
    ok = _save_json_file(CUSTOM_PLAYERS_FILE, _CUSTOM)
    if ok:
        _bust_players_cache_memory()
    return ok

# ============ сетевые утилиты ============
def _fetch_json(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(url, timeout=timeout)
        if r.ok:
            return r.json()
        return None
    except Exception as e:
        _log(f"GET fail: {url} -> {type(e).__name__}: {e}")
        return None

def _url_with_params(base: str, qs: str) -> str:
    if "?" in base:
        return f"{base}&{qs}"
    return f"{base}?{qs}"

# ============ парсеры источников ============
def _extract_players(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Универсальный парсер:
    - stats.nba.com формат (через Cloudflare worker): resultSets -> PlayerList
    - legacy data.nba.net: league.standard
    Возвращает список записей вида {personId, firstName, lastName, teamId, isActive}
    """
    if not j:
        return []

    # stats.nba.com proxied
    if "resultSets" in j:
        try:
            rs = j["resultSets"][0]
            headers = rs["headers"]
            rowset = rs["rowSet"]
            # ожидаемые столбцы
            pid_i = headers.index("PERSON_ID")
            fn_i  = headers.index("FIRST_NAME")
            ln_i  = headers.index("LAST_NAME")
            tid_i = headers.index("TEAM_ID")
            act_i = headers.index("ROSTERSTATUS") if "ROSTERSTATUS" in headers else None
            out = []
            for row in rowset:
                personId = str(row[pid_i])
                firstName = str(row[fn_i] or "").strip()
                lastName  = str(row[ln_i] or "").strip()
                teamId    = str(row[tid_i] or "0")
                isActive  = bool(row[act_i]) if act_i is not None else True
                out.append({
                    "personId": personId,
                    "firstName": firstName,
                    "lastName": lastName,
                    "teamId": teamId,
                    "isActive": isActive,
                })
            return out
        except Exception as e:
            _log(f"stats extract error: {e}")
            return []

    # legacy data.nba.net
    if j.get("league", {}).get("standard"):
        out = []
        for p in j["league"]["standard"]:
            try:
                out.append({
                    "personId": str(p.get("personId") or ""),
                    "firstName": str(p.get("firstName") or "").strip(),
                    "lastName": str(p.get("lastName") or "").strip(),
                    "teamId": str(p.get("teamId") or "0"),
                    "isActive": bool(p.get("isActive", True)),
                })
            except Exception:
                continue
        return out

    return []

def _fetch_local_snapshot() -> List[List[Dict[str, Any]]]:
    local = ASSETS / "players.json"
    if local.exists() and local.stat().st_size > 0:
        try:
            j = json.loads(local.read_text(encoding="utf-8"))
            arr = _extract_players(j)
            if arr:
                _log(f"local snapshot parsed: {len(arr)}")
                return [arr]
        except Exception:
            pass
    return []

def _fetch_from_custom() -> List[List[Dict[str, Any]]]:
    if not PLAYERS_CUSTOM_URL:
        _log("custom URL is empty")
        return []
    base = PLAYERS_CUSTOM_URL.rstrip("/")
    # Жёстко тянем stats-варианты (worker)
    candidates = [
        base,
        _url_with_params(base, "season=2025-26"),
        _url_with_params(base, "season=2025-26&source=stats"),
    ]
    seen, results = set(), []
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        j = _fetch_json(u, timeout=REQUEST_TIMEOUT)
        arr = _extract_players(j) if j else []
        _log(f"custom parsed {len(arr)} from {u}")
        if arr:
            results.append(arr)
    return results

def _fetch_from_legacy() -> List[List[Dict[str, Any]]]:
    # Должно быть отключено по умолчанию — включайте только если очень нужно
    urls = [
        "https://data.nba.net/prod/v1/2025/players.json",
        "https://data.nba.net/prod/v1/2024/players.json",
        "https://data.nba.com/data/10s/prod/v1/2025/players.json",
        "https://data.nba.com/data/10s/prod/v1/2024/players.json",
    ]
    results = []
    for u in urls:
        j = _fetch_json(u, timeout=REQUEST_TIMEOUT)
        arr = _extract_players(j) if j else []
        _log(f"legacy parsed {len(arr)} from {u}")
        if arr:
            results.append(arr)
    return results

# ============ мердж игроков ============
def _merge_players(batches: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Сливает списки игроков. Схема приоритетов:
      1) custom (ваш воркер stats) — самые приоритетные и первые
      2) local snapshot
      3) legacy (если разрешён)
      4) FALLBACK + CUSTOM_PLAYERS
    Последующие батчи не затирают teamId, если он уже есть от более приоритетного источника.
    """
    by_id: Dict[int, Dict[str, Any]] = {}

    # 0) сначала кастомно добавленные игроки (чтобы им можно было потом проставить teamId/имена)
    for cp in (_CUSTOM or []):
        try:
            pid = int(cp.get("personId"))
            by_id[pid] = {
                "id": pid,
                "first": cp.get("firstName","").strip(),
                "last": cp.get("lastName","").strip(),
                "team_id": int(cp.get("teamId") or 0),
                "active": bool(cp.get("isActive", True)),
            }
        except Exception:
            continue

    def _upsert_batch(batch: List[Dict[str, Any]]):
        for p in batch:
            try:
                pid = int(p.get("personId"))
                first = p.get("firstName","").strip()
                last  = p.get("lastName","").strip()
                team  = int(p.get("teamId") or 0)
                active = bool(p.get("isActive", True))
            except Exception:
                continue

            if pid not in by_id:
                by_id[pid] = {"id": pid, "first": first, "last": last, "team_id": team, "active": active}
            else:
                # обновим имя/фамилию если пусто
                if not by_id[pid].get("first") and first:
                    by_id[pid]["first"] = first
                if not by_id[pid].get("last") and last:
                    by_id[pid]["last"] = last
                # команду — только если в текущей записи >0, а у нас 0
                if team and not by_id[pid].get("team_id"):
                    by_id[pid]["team_id"] = team
                # активность — если не была задана
                if "active" not in by_id[pid]:
                    by_id[pid]["active"] = active

    for batch in batches:
        _upsert_batch(batch)

    # если пусто — добавим FALLBACK
    if not by_id:
        for p in FALLBACK_PLAYERS:
            try:
                pid = int(p["personId"])
                by_id[pid] = {
                    "id": pid,
                    "first": p["firstName"], "last": p["lastName"],
                    "team_id": int(p.get("teamId") or 0),
                    "active": bool(p.get("isActive", True)),
                }
            except Exception:
                continue

    # применим ручные overrides команд
    for k, v in list(_OVR_TEAM.items()):
        try:
            pid = int(k); tid = int(v)
            if pid in by_id:
                by_id[pid]["team_id"] = tid
        except Exception:
            continue

    # финальный список
    out = list(by_id.values())
    out.sort(key=lambda r: (0 if r.get("active") else 1, r.get("last",""), r.get("first","")))
    return out

# ============ построение индекса ============
def _extract_display(first: str, last: str, pid: int) -> str:
    full_ascii = f"{first} {last}".strip()
    ascii_key = _normalize_key(full_ascii)
    # Сначала явные русские оверрайды по ASCII-ключу (базовый словарь)
    ru = RU_NAME_OVERRIDES.get(ascii_key)
    if not ru:
        # затем персональные overrides по ID
        ru = _OVR_RU.get(str(pid))
    if not ru:
        ru = lat2cyr(full_ascii)
    return ru

def _build_index() -> Dict[str, Any]:
    batches: List[List[Dict[str, Any]]] = []

    # 1) custom (ваш Cloudflare worker, stats 2025-26)
    custom_batches = _fetch_from_custom()
    if custom_batches:
        batches.extend(custom_batches)
        total = sum(len(b) for b in custom_batches)
        _log(f"custom total parsed: {total}")
        if total < MIN_EXPECTED and ALLOW_LEGACY_FALLBACK:
            _log(f"custom too small ({total}) — legacy fallback allowed -> try legacy")
            batches.extend(_fetch_from_legacy())
    else:
        _log("custom empty — try local snapshot")
        batches.extend(_fetch_local_snapshot())
        if not batches and ALLOW_LEGACY_FALLBACK:
            _log("no local — using legacy (allowed)")
            batches.extend(_fetch_from_legacy())

    players_list = _merge_players(batches)
    _log(f"merged players total: {len(players_list)}")

    # формируем индекс
    index: Dict[str, Any] = {"_bykey": {}, "_byid": {}}
    for r in players_list:
        pid = int(r["id"])
        first = r.get("first","").strip()
        last  = r.get("last","").strip()
        team_id = int(r.get("team_id") or 0)
        team_name = TEAM_NAMES.get(team_id, "Free Agent")
        full_ascii = f"{first} {last}".strip()
        ru_display = _extract_display(first, last, pid)

        rec = {
            "id": pid,
            "full_name": full_ascii,
            "display": ru_display,
            "team_id": team_id,
            "team_name": team_name,
        }
        index["_byid"][pid] = rec

        ascii_key = _normalize_key(full_ascii)
        cyr_key   = _normalize_key(ru_display)

        index["_bykey"][ascii_key] = rec
        index["_bykey"][cyr_key] = rec
        index["_bykey"][_normalize_key(full_ascii.replace("-", " "))] = rec
        index["_bykey"][_normalize_key(ru_display.replace("-", " "))] = rec

    # короткие прозвища (можно расширять)
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

# ============ кэш и API ============
_PLAYERS: Dict[str, Any] | None = None
_LAST_LOAD = 0

def _bust_players_cache_memory():
    global _PLAYERS, _LAST_LOAD
    _PLAYERS = None
    _LAST_LOAD = 0

def drop_players_cache() -> bool:
    """
    Полностью сбрасывает JSON-кэш на /tmp и память. Используйте при refresh.
    """
    try:
        if PLAYERS_CACHE.exists():
            PLAYERS_CACHE.unlink(missing_ok=True)
    except Exception:
        pass
    _bust_players_cache_memory()
    return True

def _ensure_index():
    global _PLAYERS, _LAST_LOAD
    now = time.time()
    if _PLAYERS and (now - _LAST_LOAD) < REFRESH_SECONDS:
        return
    # пробуем читать кэш
    if PLAYERS_CACHE.exists():
        try:
            _PLAYERS = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
            _LAST_LOAD = now
            return
        except Exception:
            _PLAYERS = None
    # иначе строим с нуля
    _PLAYERS = _build_index()
    try:
        PLAYERS_CACHE.write_text(json.dumps(_PLAYERS, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    _LAST_LOAD = now

def players_count() -> int:
    _ensure_index()
    return len(_PLAYERS["_byid"]) if _PLAYERS else 0

def get_player_by_id(pid: int) -> Optional[Dict[str, Any]]:
    _ensure_index()
    return _PLAYERS["_byid"].get(int(pid)) if _PLAYERS else None

# ============ поиск ============
def find_player_by_name(query: str) -> Optional[Dict[str, Any]]:
    """Возвращает {id, full_name, display, team_id, team_name}."""
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

    # 5) Лат -> кир транслит
    cyr_guess = _normalize_key(lat2cyr(query))
    rec = _PLAYERS["_bykey"].get(cyr_guess)
    if rec:
        return rec

    # 6) По фамилии
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

# ============ headshots & logos ============
def ensure_headshot_png(player_id: int, full_name: str) -> str:
    """
    Пытается скачать PNG головы.
    - Сначала проверяет overrides_photo (URL или local:/abs/path).
    - Потом cdn.nba.com 1040x760 -> 260x190.
    - Иначе иконка-стаб.
    """
    # override фото
    ovr = _OVR_PHOTO.get(str(int(player_id)))
    if ovr:
        if ovr.startswith("local:"):
            p = ovr[len("local:") : ].strip()
            if Path(p).exists():
                return p
        elif ovr.startswith("http"):
            path = HEAD_DIR / f"{player_id}.png"
            if not (path.exists() and path.stat().st_size > 0):
                try:
                    r = requests.get(ovr, timeout=12)
                    if r.ok and r.content and r.content[:4] == b"\x89PNG":
                        path.write_bytes(r.content)
                        return str(path)
                except Exception:
                    pass
            else:
                return str(path)

    # стандартные источники
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
