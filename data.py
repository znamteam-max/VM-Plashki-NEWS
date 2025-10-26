
import os
from pathlib import Path
import requests
from nba_api.stats.static import players
from nba_api.stats.endpoints import commonplayerinfo

ASSETS = Path("assets")
CACHE = ASSETS / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

TEAM_PALETTES = {
    1610612759: ("#000000", "#191A1C", "#FFFFFF"),  # SAS
    1610612755: ("#006BB6", "#002B5C", "#ED174C"),  # PHI
    1610612740: ("#0C2340", "#C8102E", "#85714D"),  # NOP (пример)
    1610612762: ("#002B5C", "#6CAEDF", "#FFFFFF"),  # UTA (пример)
}

def find_player_by_name(name: str):
    name = name.strip()
    found = players.find_players_by_full_name(name)
    if not found:
        return None
    p = found[0]
    pid = p["id"]
    info = commonplayerinfo.CommonPlayerInfo(player_id=pid).get_dict()["resultSets"][0]["rowSet"][0]
    team_id = info[18]
    team_name = info[20]
    display = info[3]
    return {
        "id": pid,
        "full_name": p["full_name"],
        "display": display,
        "team_id": team_id,
        "team_name": team_name,
    }

def ensure_headshot_png(player_id: int, fallback_name: str) -> str:
    url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
    path = CACHE / f"head_{player_id}.png"
    if not path.exists():
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        except Exception:
            from PIL import Image, ImageDraw
            im = Image.new("RGBA", (1040, 760), (128, 128, 128, 255))
            d = ImageDraw.Draw(im); d.text((20, 20), fallback_name, fill=(255,255,255,255))
            im.save(path)
    return str(path)

def ensure_team_logo_png(team_id: int):
    import cairosvg
    svg_url = f"https://cdn.nba.com/logos/nba/{team_id}/global/L/logo.svg"
    png_path = CACHE / f"logo_{team_id}.png"
    if not png_path.exists():
        r = requests.get(svg_url, timeout=20)
        r.raise_for_status()
        cairosvg.svg2png(bytestring=r.content, write_to=str(png_path), output_width=320, output_height=320)
    colors = TEAM_PALETTES.get(team_id, ("#FF6A00", "#1A1A1A", "#FFFFFF"))
    return str(png_path), colors
