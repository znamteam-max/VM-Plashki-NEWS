# scripts/build_players.py
# Активные игроки: balldontlie (Authorization: Bearer <BL_API_KEY>)
# Официальные NBA personId: набор зеркал (swar/bttmly/kshvmdn/mtthai), с разными схемами.
# Выход: assets/players.json в формате {"league":{"standard":[{"personId":"201939","firstName":"Stephen","lastName":"Curry","teamId":"1610612744"}, ...]}}

import os, json, re, sys
from typing import Dict, List, Iterable, Any
import requests
from unidecode import unidecode
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)
OUT = ASSETS / "players.json"

BL_KEY = os.getenv("BL_API_KEY")
if not BL_KEY:
    print("[sync] ERROR: missing BL_API_KEY (GitHub → Repo → Settings → Secrets → Actions)", file=sys.stderr)
    sys.exit(1)

# ---------- нормализация имён ----------
SUFFIXES = (" jr", " jr.", " sr", " sr.", " ii", " iii", " iv", " v")

def norm_name(s: str) -> str:
    s = unidecode(s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s\-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suf in SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s

# ---------- соответствие аббревиатур balldontlie → официальные TeamID ----------
TEAM_ABBR_TO_ID: Dict[str, int] = {
    "ATL":1610612737, "BOS":1610612738, "CLE":1610612739, "NOP":1610612740, "CHI":1610612741,
    "DAL":1610612742, "DEN":1610612743, "GSW":1610612744, "HOU":1610612745, "LAC":1610612746,
    "LAL":1610612747, "MIA":1610612748, "MIL":1610612749, "MIN":1610612750, "BKN":1610612751,
    "NYK":1610612752, "ORL":1610612753, "IND":1610612754, "PHI":1610612755, "PHX":1610612756,
    "POR":1610612757, "SAC":1610612758, "SAS":1610612759, "OKC":1610612760, "TOR":1610612761,
    "UTA":1610612762, "MEM":1610612763, "WAS":1610612764, "DET":1610612765, "CHA":1610612766,
}

# ---------- HTTP сессия ----------
session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {BL_KEY}",
    "User-Agent": "players-sync/1.2"
})

# ---------- balldontlie: активные игроки ----------
def fetch_active_players_balldontlie() -> List[dict]:
    base = "https://api.balldontlie.io/v1/players"
    per_page = 100
    page = 1
    out: List[dict] = []

    while True:
        params = {"per_page": per_page, "page": page, "active": "true"}
        r = session.get(base, params=params, timeout=40)
        r.raise_for_status()
        j = r.json()
        data = j.get("data") or []
        meta = j.get("meta") or {}
        total_pages = int(meta.get("total_pages") or 0)
        out.extend(data)
        if not data or total_pages == 0 or page >= total_pages:
            break
        page += 1

    return out

# ---------- загрузка и парсинг зеркал с personId ----------
def _safe_json(url: str) -> Any:
    r = requests.get(url, timeout=40)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        # иногда raw отдает текст — попробуем json.loads вручную
        txt = r.text
        return json.loads(txt)

def _iter_name_id_from_any(payload: Any) -> Iterable[tuple[str,int]]:
    """
    Универсальный парсер разных схем зеркал.
    На выход: (нормализованное_имя, personId)
    """
    if payload is None:
        return []
    # Вариант 1: список объектов [{...}, ...]
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                pid = (row.get("PlayerID") or row.get("PLAYER_ID") or
                       row.get("playerId") or row.get("id") or row.get("personId"))
                nm  = (row.get("PlayerName") or row.get("PLAYER_NAME") or
                       row.get("playerName") or row.get("full_name") or row.get("fullName") or
                       (f"{row.get('first_name','')} {row.get('last_name','')}".strip()))
                if not pid or not nm:
                    continue
                try:
                    pid_int = int(str(pid).strip())
                except Exception:
                    continue
                yield norm_name(nm), pid_int
            # если пришли строки — пропускаем
        return

    # Вариант 2: словарь с ключами "players"/"data"
    if isinstance(payload, dict):
        arr = payload.get("players") or payload.get("data") or payload.get("standard")
        if isinstance(arr, list):
            for row in arr:
                if not isinstance(row, dict):
                    continue
                pid = (row.get("PlayerID") or row.get("PLAYER_ID") or
                       row.get("playerId") or row.get("id") or row.get("personId"))
                nm  = (row.get("PlayerName") or row.get("PLAYER_NAME") or
                       row.get("playerName") or row.get("full_name") or row.get("fullName") or
                       (f"{row.get('first_name','')} {row.get('last_name','')}".strip()))
                if not pid or not nm:
                    continue
                try:
                    pid_int = int(str(pid).strip())
                except Exception:
                    continue
                yield norm_name(nm), pid_int
        return

    # Иное — игнор
    return []

def fetch_all_player_ids_historic() -> Dict[str,int]:
    mirrors = [
        # nba_api (swar) — чаще всего актуальный формат: list[ { "id": 201939, "full_name": "Stephen Curry", ... } ]
        "https://raw.githubusercontent.com/swar/nba_api/master/nba_api/stats/library/data/players.json",
        # дубликат пути через docs/ (на случай перемещений в репо)
        "https://raw.githubusercontent.com/swar/nba_api/master/docs/nba_api/stats/library/data/players.json",
        # bttmly/nba — старая, но популярная база
        "https://raw.githubusercontent.com/bttmly/nba/master/data/players.json",
        # kshvmdn/nba.js — похожая структура
        "https://raw.githubusercontent.com/kshvmdn/nba.js/master/data/players.json",
        # mtthai (как у тебя было — оставим как запасной)
        "https://raw.githubusercontent.com/mtthai/nba-api-client/master/data/players.json",
    ]
    name_to_id: Dict[str,int] = {}
    for url in mirrors:
        try:
            payload = _safe_json(url)
            added = 0
            for k, pid in _iter_name_id_from_any(payload):
                if k and pid and k not in name_to_id:
                    name_to_id[k] = pid
                    added += 1
            print(f"[sync] mirror ok: {url} (+{added}, total={len(name_to_id)})")
            if len(name_to_id) > 500:  # достаточно
                break
        except Exception as e:
            print(f"[sync] mirror fail: {url} -> {type(e).__name__}: {e}")
            continue
    if len(name_to_id) < 100:
        raise RuntimeError("too few ids from mirrors")
    return name_to_id

# ---------- сборка результата ----------
def make_output(active_players: List[dict], hist_ids: Dict[str,int]) -> dict:
    std = []
    missing_ids = 0
    for p in active_players:
        fn = (p.get("first_name") or "").strip()
        ln = (p.get("last_name") or "").strip()
        team = p.get("team") or {}
        abbr = (team.get("abbreviation") or "").upper()
        team_id = TEAM_ABBR_TO_ID.get(abbr)
        if not fn or not ln or not team_id:
            continue

        k1 = norm_name(f"{fn} {ln}")
        pid = hist_ids.get(k1)

        if not pid:
            # без дефиса/апострофа
            k2 = norm_name(k1.replace("-", " ").replace("'", ""))
            pid = hist_ids.get(k2)

        if not pid and " " in k1:
            # имя + последняя фамилия
            parts = k1.split()
            k3 = norm_name(f"{parts[0]} {parts[-1]}")
            pid = hist_ids.get(k3)

        if not pid:
            missing_ids += 1
            # без personId не достанем официальный headshot → пропускаем
            continue

        std.append({
            "personId": str(pid),
            "firstName": fn,
            "lastName": ln,
            "teamId": str(team_id),
        })

    std.sort(key=lambda x: (int(x["teamId"]), x["lastName"], x["firstName"]))
    print(f"[sync] active: in={len(active_players)}, matched_with_ids={len(std)}, missing_ids={missing_ids}")
    return {"league": {"standard": std}}

def main():
    try:
        active = fetch_active_players_balldontlie()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            print("[sync] ERROR 401 from balldontlie: проверь BL_API_KEY и формат 'Authorization: Bearer <KEY>'", file=sys.stderr)
        else:
            print(f"[sync] balldontlie HTTPError: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[sync] balldontlie error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        hist_ids = fetch_all_player_ids_historic()
    except Exception as e:
        print(f"[sync] historic IDs mirrors error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    out = make_output(active, hist_ids)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[sync] wrote {OUT} ({len(out['league']['standard'])} players)")

if __name__ == "__main__":
    main()
