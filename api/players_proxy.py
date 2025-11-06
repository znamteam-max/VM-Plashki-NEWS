# api/players_proxy.py
from __future__ import annotations
import os, time
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from data import (
    get_players, players_count, PLAYERS_SEASON as DATA_SEASON,
    _http_get_json, ensure_headshot_png
)

app = FastAPI(title="Players Proxy", version="1.0.0")
NBA_URL = "https://stats.nba.com/stats/commonallplayers"

def _ok(payload: Dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(
        content=payload,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
    )

@app.get("/api/players_proxy/health")
async def health() -> JSONResponse:
    return _ok({"ok": True, "ts": int(time.time())})

@app.get("/api/players_proxy/selftest")
async def selftest(season: Optional[str] = None) -> JSONResponse:
    env_season = os.getenv("PLAYERS_SEASON", DATA_SEASON)
    try:
        n = players_count(force_refresh=True)
        sample: List[Dict[str, Any]] = get_players()[:3]
        photos = [ensure_headshot_png(p, "260x190") for p in sample]
        return _ok({
            "ok": True,
            "env_season": env_season,
            "query_season": season or None,
            "count": n,
            "sample_photos": photos
        })
    except Exception as e:
        return _ok({"ok": False, "error": repr(e)}, status=500)

def _normalize_resultsets(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    rs = j.get("resultSets", [{}])[0]
    headers = rs.get("headers", [])
    rows = rs.get("rowSet", [])
    def gx(r, name):
        try:
            i = headers.index(name)
            return r[i]
        except Exception:
            return None
    out: List[Dict[str, Any]] = []
    for r in rows:
        pid = str(gx(r, "PERSON_ID") or "")
        fn  = str(gx(r, "FIRST_NAME") or "").strip()
        ln  = str(gx(r, "LAST_NAME") or "").strip()
        tid = str(gx(r, "TEAM_ID") or "0")
        act = gx(r, "ROSTERSTATUS")
        try:
            act = bool(int(act))
        except Exception:
            act = bool(act)
        out.append({
            "personId": pid, "firstName": fn, "lastName": ln,
            "teamId": tid or "0", "isActive": act,
            "photo": f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png" if pid else ""
        })
    return out

@app.get("/api/players_proxy/proxy")
async def proxy(
    season: str = Query(default=os.getenv("PLAYERS_SEASON", DATA_SEASON)),
    format: str = Query(default="normalized")  # normalized | passthrough
) -> JSONResponse:
    params = {
        "LeagueID": "00",
        "IsOnlyCurrentSeason": "0",
        "Season": season
    }
    try:
        url = NBA_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        j = _http_get_json(url, timeout=30, verify_ssl=True)
        if format == "passthrough":
            return _ok(j)
        else:
            data = _normalize_resultsets(j)
            return _ok({"season": season, "players": data})
    except Exception as e:
        return _ok({"ok": False, "error": repr(e)}, status=502)
