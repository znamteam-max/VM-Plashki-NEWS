# scripts/build_players.py
# Собирает активных игроков из balldontlie (по ключу) и сопоставляет им официальные NBA personId.
# Результат: assets/players.json -> {"league":{"standard":[{"personId":"201939","firstName":"Stephen","lastName":"Curry","teamId":"1610612744"}, ...]}}

import os, json, re, sys
from typing import Dict, List
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

# --- нормализация имён для сопоставления ---
SUFFIXES = (" jr", " jr.", " sr", " sr.", " ii", " iii", " iv", " v")

def norm_name(s: str) -> str:
    s = unidecode(s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s\-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suf in SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s

# соответствие аббревиатур к официальным NBA TeamID (161061xxxx)
TEAM_ABBR_TO_ID: Dict[str, int] = {
    "ATL":1610612737, "BOS":1610612738, "CLE":1610612739, "NOP":1610612740, "CHI":1610612741,
    "DAL":1610612742, "DEN":1610612743, "GSW":1610612744, "HOU":1610612745, "LAC":1610612746,
    "LAL":1610612747, "MIA":1610612748, "MIL":1610612749, "MIN":1610612750, "BKN":1610612751,
    "NYK":1610612752, "ORL":1610612753, "IND":1610612754, "PHI":1610612755, "PHX":1610612756,
    "POR":1610612757, "SAC":1610612758, "SAS":1610612759, "OKC":1610612760, "TOR":1610612761,
    "UTA":1610612762, "MEM":1610612763, "WAS":1610612764, "DET":1610612765, "CHA":1610612766,
}

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {BL_KEY}",   # ВАЖНО: Bearer <KEY>
    "User-Agent": "players-sync/1.1"
})

def fetch_active_players_balldontlie() -> List[dict]:
    """Пагинация v1: ?per_page=&page=; фильтр активных: active=true"""
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
        total_pages = int(meta.get("total_pages") or 0)  # у них так называется
        out.extend(data)
        # print(f"[sync] page {page}/{total_pages}: +{len(data)}", flush=True)
        if not data or total_pages == 0 or page >= total_pages:
            break
        page += 1

    return out

def fetch_all_player_ids_historic() -> Dict[str, int]:
    """
    Публичное зеркало всех PlayerID за историю (GitHub).
    Берём пары (имя → personId) для сопоставления с balldontlie.
    """
    raw_url = "https://raw.githubusercontent.com/mtthai/nba-api-client/master/data/players.json"
    r = requests.get(raw_url, timeout=40)
    r.raise_for_status()
    arr = r.json()
    name_to_id: Dict[str, int] = {}
    for row in arr:
        pid = row.get("PlayerID") or row.get("PLAYER_ID") or row.get("playerId") or row.get("id")
        nm  = row.get("PlayerName") or row.get("PLAYER_NAME") or row.get("playerName") or row.get("name")
        if not pid or not nm:
            continue
        try:
            pid = int(pid)
        except Exception:
            continue
        name_to_id[norm_name(nm)] = pid
    return name_to_id

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
            # варианты без дефиса/апострофа:
            k2 = norm_name(k1.replace("-", " ").replace("'", ""))
            pid = hist_ids.get(k2)

        if not pid and " " in k1:
            # fallback: берем только имя + последнюю фамилию
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
        # Покажем подсказку, если ключ не принят
        if e.response is not None and e.response.status_code == 401:
            print("[sync] ERROR 401 from balldontlie: проверь BL_API_KEY и формат заголовка 'Authorization: Bearer <KEY>'", file=sys.stderr)
        else:
            print(f"[sync] balldontlie HTTPError: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[sync] balldontlie error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        hist_ids = fetch_all_player_ids_historic()
    except Exception as e:
        print(f"[sync] historic IDs mirror error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    out = make_output(active, hist_ids)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[sync] wrote {OUT} ({len(out['league']['standard'])} players)")

if __name__ == "__main__":
    main()
