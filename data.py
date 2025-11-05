# data.py
# Универсальный источник игроков NBA для /api/telegram
# Поддерживает:
# - stats.nba.com proxied (CommonAllPlayers через Cloudflare Worker)
# - legacy data.nba.net (опционально)
# - локальный снапшот (опционально)
# - оверрайды (assets/players_overrides.json и/или PLAYERS_OVERRIDES_JSON)
# - кэш в памяти и на диске (/tmp)

from __future__ import annotations
import os
import json
import time
import traceback
from typing import Any, Dict, List, Tuple, Optional
from urllib.request import Request, urlopen

# --------- Конфиг через env ---------
PLAYERS_CUSTOM_URL       = os.getenv("PLAYERS_CUSTOM_URL", "").strip()
PLAYERS_ENFORCE_SOURCE   = os.getenv("PLAYERS_ENFORCE_SOURCE", "").strip().lower()  # "stats" | "legacy" | ""
ALLOW_LEGACY_FALLBACK    = os.getenv("PLAYERS_ALLOW_LEGACY", "0") == "1"
MIN_EXPECTED             = int(os.getenv("PLAYERS_MIN_EXPECTED", "350"))
DISABLE_LOCAL_SNAPSHOT   = os.getenv("PLAYERS_DISABLE_LOCAL", "1") == "1"
CACHE_TTL_SEC            = int(os.getenv("PLAYERS_CACHE_TTL", "3600"))
PHOTO_FMT                = os.getenv("PLAYERS_PHOTO_FMT", "https://cdn.nba.com/headshots/nba/latest/1040x760/{personId}.png")
LEGACY_URL               = os.getenv("PLAYERS_LEGACY_URL", "https://data.nba.net/data/10s/prod/v1/2025/players.json")  # на всякий

# Путь к локальным файлам
ROOT_DIR        = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR      = os.path.join(ROOT_DIR, "assets")
LOCAL_SNAPSHOT  = os.path.join(ASSETS_DIR, "players.json")
OVERRIDES_FILE  = os.path.join(ASSETS_DIR, "players_overrides.json")
CACHE_PATH      = os.path.join("/tmp", "players_cache.json")

# Модульный кэш
_CACHED: Dict[str, Any] = {
    "ts": 0.0,
    "players": None,   # type: Optional[List[Dict[str, Any]]]
    "index": None,     # type: Optional[Dict[str, Dict[str, Any]]]
}

# --------- Утилиты ---------
def _log(*args: Any) -> None:
    try:
        print("[players]", *args, flush=True)
    except Exception:
        pass

def _http_get_json(url: str, timeout: int = 15) -> Any:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (players-fetch)",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _read_json_file(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)

# --------- Парсеры источников ---------
def _extract_players(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Универсальный парсер:
    - stats.nba.com (proxied CommonAllPlayers): resultSets[0] -> headers/rowSet
    - legacy data.nba.net: league.standard
    Возвращает список словарей:
      {personId, firstName, lastName, teamId, isActive}
    """
    if not j:
        return []

    # Proxied stats.nba.com (CommonAllPlayers)
    if "resultSets" in j:
        try:
            rs = j["resultSets"][0]
            headers = rs["headers"]
            rowset = rs["rowSet"]

            pid_i  = headers.index("PERSON_ID")
            tid_i  = headers.index("TEAM_ID")
            act_i  = headers.index("ROSTERSTATUS") if "ROSTERSTATUS" in headers else None

            fn_i   = headers.index("FIRST_NAME") if "FIRST_NAME" in headers else None
            ln_i   = headers.index("LAST_NAME")  if "LAST_NAME"  in headers else None
            dfl_i  = headers.index("DISPLAY_FIRST_LAST") if "DISPLAY_FIRST_LAST" in headers else None
            dlcf_i = headers.index("DISPLAY_LAST_COMMA_FIRST") if "DISPLAY_LAST_COMMA_FIRST" in headers else None

            out: List[Dict[str, Any]] = []
            for row in rowset:
                personId = _safe_str(row[pid_i])
                teamId   = _safe_str(row[tid_i] or "0")

                # ROSTERSTATUS часто "1"/"0"
                isActive = True
                if act_i is not None:
                    try:
                        isActive = bool(int(row[act_i]))
                    except Exception:
                        isActive = bool(row[act_i])

                # Имя/фамилия
                if fn_i is not None and ln_i is not None:
                    firstName = _safe_str(row[fn_i]).strip()
                    lastName  = _safe_str(row[ln_i]).strip()
                elif dfl_i is not None:
                    disp = _safe_str(row[dfl_i]).strip()
                    parts = disp.split()
                    firstName = parts[0] if parts else ""
                    lastName  = " ".join(parts[1:]) if len(parts) > 1 else ""
                elif dlcf_i is not None:
                    disp2 = _safe_str(row[dlcf_i]).strip()
                    if "," in disp2:
                        lastName, firstName = [s.strip() for s in disp2.split(",", 1)]
                    else:
                        firstName, lastName = disp2, ""
                else:
                    firstName, lastName = "", ""

                out.append({
                    "personId": personId,
                    "firstName": firstName,
                    "lastName": lastName,
                    "teamId": teamId,
                    "isActive": isActive,
                })
            return out
        except Exception as e:
            _log("stats extract error:", e)
            _log(traceback.format_exc())
            return []

    # Legacy data.nba.net
    if j.get("league", {}).get("standard"):
        out: List[Dict[str, Any]] = []
        for p in j["league"]["standard"]:
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

    return []

# --------- Источники ---------
def _fetch_from_custom() -> List[Dict[str, Any]]:
    if not PLAYERS_CUSTOM_URL:
        return []
    try:
        j = _http_get_json(PLAYERS_CUSTOM_URL)
        players = _extract_players(j)
        _log(f"custom parsed {len(players)} from {PLAYERS_CUSTOM_URL}")
        return players
    except Exception as e:
        _log("custom fetch error:", e)
        _log(traceback.format_exc())
        return []

def _fetch_from_legacy() -> List[Dict[str, Any]]:
    try:
        j = _http_get_json(LEGACY_URL)
        players = _extract_players(j)
        _log(f"legacy parsed {len(players)} from {LEGACY_URL}")
        return players
    except Exception as e:
        _log("legacy fetch error:", e)
        _log(traceback.format_exc())
        return []

def _fetch_local_snapshot() -> List[Dict[str, Any]]:
    if DISABLE_LOCAL_SNAPSHOT:
        return []
    try:
        j = _read_json_file(LOCAL_SNAPSHOT)
        players = _extract_players(j) if isinstance(j, dict) else (j or [])
        _log(f"local snapshot parsed {len(players)} from {LOCAL_SNAPSHOT}")
        return players
    except Exception as e:
        _log("local snapshot error:", e)
        return []

# --------- Оверрайды ---------
def _load_overrides() -> Dict[str, Dict[str, Any]]:
    """
    Источник правок:
      1) ENV PLAYERS_OVERRIDES_JSON — строка JSON (приоритет)
      2) assets/players_overrides.json — файл в репозитории
    Формат на игрока (personId строкой):
      {"1627783": {"teamId": "1610612754", "photo": "...", "firstName": "...", "lastName": "..."}}
    Можно добавлять кастомных игроков с новым personId, но лучше опираться на реальные PERSON_ID.
    """
    env_raw = os.getenv("PLAYERS_OVERRIDES_JSON", "").strip()
    if env_raw:
        try:
            data = json.loads(env_raw)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            _log("overrides env parse error:", e)

    j = _read_json_file(OVERRIDES_FILE)
    if isinstance(j, dict):
        return {str(k): v for k, v in j.items() if isinstance(v, dict)}
    return {}

def _apply_overrides(players: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ov = _load_overrides()
    if not ov:
        return players

    by_id: Dict[str, Dict[str, Any]] = {p["personId"]: dict(p) for p in players}

    # применяем правки к существующим игрокам
    for pid, patch in ov.items():
        if pid in by_id:
            base = by_id[pid]
            if "firstName" in patch: base["firstName"] = _safe_str(patch["firstName"]).strip()
            if "lastName"  in patch: base["lastName"]  = _safe_str(patch["lastName"]).strip()
            if "teamId"    in patch: base["teamId"]    = _safe_str(patch["teamId"]).strip() or "0"
            if "isActive"  in patch: base["isActive"]  = bool(patch["isActive"])
            if "photo"     in patch: base["photo"]     = _safe_str(patch["photo"]).strip()
        else:
            # кастомный игрок (если вдруг нужен)
            add = {
                "personId": pid,
                "firstName": _safe_str(patch.get("firstName", "")),
                "lastName":  _safe_str(patch.get("lastName", "")),
                "teamId":    _safe_str(patch.get("teamId", "0")),
                "isActive":  bool(patch.get("isActive", True)),
            }
            if "photo" in patch:
                add["photo"] = _safe_str(patch["photo"])
            by_id[pid] = add

    # фото по умолчанию — если нет переопределения
    for p in by_id.values():
        if not p.get("photo"):
            p["photo"] = PHOTO_FMT.format(personId=p["personId"])

        # displayName для удобства
        fn = p.get("firstName", "").strip()
        ln = p.get("lastName", "").strip()
        p["displayName"] = (fn + " " + ln).strip() if (fn or ln) else p.get("displayName", "")

    return list(by_id.values())

# --------- Сборка индекса ---------
def _build_index() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    batches: List[Dict[str, Any]] = []

    # Принудительный источник?
    source = PLAYERS_ENFORCE_SOURCE
    if source == "stats":
        batches = _fetch_from_custom()
    elif source == "legacy":
        batches = _fetch_from_legacy()
    else:
        # smart order: custom -> (local?) -> legacy
        batches = _fetch_from_custom()
        if not batches and not DISABLE_LOCAL_SNAPSHOT:
            _log("custom empty — try local snapshot")
            batches = _fetch_local_snapshot()
        if (not batches or len(batches) < MIN_EXPECTED) and ALLOW_LEGACY_FALLBACK:
            _log("fallback to legacy allowed")
            batches = _fetch_from_legacy()

    if not batches:
        _log("no players parsed from any source")
        return [], {}

    # Применяем оверрайды и фото
    players = _apply_overrides(batches)

    # Индекс по personId
    index = {p["personId"]: p for p in players}

    _log(f"custom total parsed (after overrides): {len(players)}")
    return players, index

# --------- Кэш ---------
def _load_cache_from_disk() -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], float]]:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        j = _read_json_file(CACHE_PATH)
        if not isinstance(j, dict):
            return None
        ts = float(j.get("ts", 0))
        players = j.get("players") or []
        if not isinstance(players, list):
            players = []
        index = {p["personId"]: p for p in players if isinstance(p, dict) and p.get("personId")}
        return players, index, ts
    except Exception as e:
        _log("cache read error:", e)
        return None

def _save_cache_to_disk(players: List[Dict[str, Any]]) -> None:
    try:
        _write_json_file(CACHE_PATH, {"ts": time.time(), "players": players})
    except Exception as e:
        _log("cache write error:", e)

def _valid_cache(ts: float) -> bool:
    return (time.time() - ts) < CACHE_TTL_SEC

# --------- Публичные API-функции ---------
def get_players(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Возвращает список игроков (с оверрайдами), кэшируемый.
    """
    # память
    if not force_refresh and _CACHED["players"] and _valid_cache(_CACHED["ts"]):
        return _CACHED["players"]

    # диск
    if not force_refresh:
        disk = _load_cache_from_disk()
        if disk:
            players, index, ts = disk
            if _valid_cache(ts) and len(players) >= MIN_EXPECTED:
                _CACHED["players"] = players
                _CACHED["index"] = index
                _CACHED["ts"] = ts
                return players

    # билд
    players, index = _build_index()
    _CACHED["players"] = players
    _CACHED["index"] = index
    _CACHED["ts"] = time.time()
    if players:
        _save_cache_to_disk(players)
    return players

def get_players_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Возвращает индекс игроков по personId.
    """
    if not force_refresh and _CACHED["index"] and _valid_cache(_CACHED["ts"]):
        return _CACHED["index"]

    players = get_players(force_refresh=force_refresh)
    index = {p["personId"]: p for p in players}
    _CACHED["index"] = index
    return index

def refresh_players(drop_cache: bool = False) -> Tuple[int, Dict[str, Any]]:
    """
    Принудительно обновляет кэш списка игроков.
    """
    if drop_cache:
        drop_players_cache()
    players = get_players(force_refresh=True)
    return len(players), {"ok": True, "players_indexed": len(players)}

def drop_players_cache() -> bool:
    """
    Сбрасывает кэш в памяти и на диске. Экспортируется для /api/telegram?action=refresh&drop_cache=1.
    """
    try:
        _CACHED["ts"] = 0.0
        _CACHED["players"] = None
        _CACHED["index"] = None
        if os.path.exists(CACHE_PATH):
            try:
                os.remove(CACHE_PATH)
            except Exception:
                pass
        _log("players cache dropped")
        return True
    except Exception as e:
        _log("drop cache error:", e)
        return False

# Удобные helper-ы (по желанию)
def find_player_by_name(query: str) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    res: List[Dict[str, Any]] = []
    for p in get_players():
        name = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if q in (name or "").lower():
            res.append(p)
    return res
