# scripts/build_players.py
# Собирает активных игроков из balldontlie и сопоставляет официальные NBA PlayerID.
# Результат пишет в assets/players.json в формате, совместимом с вашим data.py:
# {"league":{"standard":[{"personId":"201939","firstName":"Stephen","lastName":"Curry","teamId":"1610612744"}, ...]}}

import os, json, re, unicodedata, sys
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
    print("[sync] missing BL_API_KEY", file=sys.stderr)
    sys.exit(1)

# --- утилиты ---

SUFFIXES = (" jr", " jr.", " sr", " sr.", " ii", " iii", " iv", " v")

def norm_name(s: str) -> str:
    s = unidecode(s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s\-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # убираем суффиксы из конца ФИО
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
session.headers.update({"Authorization": BL_KEY, "User-Agent": "players-sync/1.0"})

def fetch_active_players_balldontlie() -> List[dict]:
    url = "https://api.balldontlie.io/v1/players/active"
    per_page = 100
    cursor = None
    out: List[dict] = []

    while True:
        params = {"per_page": per_page}
        if cursor:
            params["cursor"] = cursor
        r = session.get(url, params=params, timeout=40)
        r.raise_for_status()
        j = r.json()
        data = j.get("data") or []
        out.extend(data)
        cursor = j.get("meta", {}).get("next_cursor")
        if not cursor:
            break
    return out

def fetch_all_player_ids_historic() -> Dict[str, int]:
    """
    Берём публичное зеркало базы PlayerID (вся история НБА), чтобы получить официальные personId.
    Формат на стороне зеркала: список объектов с полями вроде "PlayerID" и "PlayerName".
    """
    # зеркало с описанием, что data/players.json содержит все PlayerIDs за историю
    # https://github.com/mtthai/nba-api-client  (см. README)
    raw_url = "https://raw.githubusercontent.com/mtthai/nba-api-client/master/data/players.json"
    r = requests.get(raw_url, timeout=40)
    r.raise_for_status()
    arr = r.json()
    name_to_id: Dict[str, int] = {}
    for row in arr:
        # максимально терпимо к схеме
        pid = row.get("PlayerID") or row.get("PLAYER_ID") or row.get("playerId") or row.get("id")
        nm  = row.get("PlayerName") or row.get("PLAYER_NAME") or row.get("playerName") or row.get("name")
        if not pid or not nm: 
            continue
        try:
            pid = int(pid)
        except Exception:
            continue
        k = norm_name(nm)
        name_to_id[k] = pid
    return name_to_id

def make_output(active_players: List[dict], hist_ids: Dict[str,int]) -> dict:
    std = []
    missing_ids = 0
    for p in active_players:
        fn = p.get("first_name","").strip()
        ln = p.get("last_name","").strip()
        team = p.get("team") or {}
        abbr = (team.get("abbreviation") or "").upper()
        team_id = TEAM_ABBR_TO_ID.get(abbr)
        if not fn or not ln or not team_id:
            continue

        # ключи сопоставления
        k1 = norm_name(f"{fn} {ln}")
        # иногда баллдон’тлай даёт middle/суффиксы — попробуем вариант без последнего слова
        alt = k1.split()
        if len(alt) >= 3:
            k2 = norm_name(" ".join([alt[0], alt[-1]]))
        else:
            k2 = None

        pid = hist_ids.get(k1) or (hist_ids.get(k2) if k2 else None)

        if not pid:
            # ещё одна попытка — без апострофов/дефисов
            k3 = norm_name(k1.replace("-", " ").replace("'", ""))
            pid = hist_ids.get(k3)
        if not pid:
            missing_ids += 1
            # пропускаем: без personId не достанем headshot с CDN
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
