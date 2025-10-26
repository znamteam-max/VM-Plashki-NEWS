# data.py — лёгкая версия для Vercel Free
import os
from pathlib import Path
from PIL import Image, ImageDraw

ASSETS = Path("assets")
CACHE = ASSETS / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

# Мини-палитры (добавим позже все 30)
TEAM_PALETTES = {
    1610612759: ("#000000", "#191A1C", "#FFFFFF"),  # Spurs
    1610612740: ("#0C2340", "#C8102E", "#85714D"),  # Pelicans
}

# Мини-словарь игроков для теста (можно дополнять)
PLAYER_INDEX = {
    "victor wembanyama": {
        "id": 1641705,
        "full_name": "Victor Wembanyama",
        "display": "VICTOR WEMBANYAMA",
        "team_id": 1610612759,
        "team_name": "San Antonio Spurs",
    },
    "виктор вембаньяма": {
        "id": 1641705,
        "full_name": "Victor Wembanyama",
        "display": "ВИКТОР ВЕМБАНЬЯМА",
        "team_id": 1610612759,
        "team_name": "San Antonio Spurs",
    },
    "zion williamson": {
        "id": 1629627,
        "full_name": "Zion Williamson",
        "display": "ZION WILLIAMSON",
        "team_id": 1610612740,
        "team_name": "New Orleans Pelicans",
    },
    "зайон уильямсон": {
        "id": 1629627,
        "full_name": "Zion Williamson",
        "display": "ЗАЙОН УИЛЬЯМСОН",
        "team_id": 1610612740,
        "team_name": "New Orleans Pelicans",
    },
}

def find_player_by_name(name: str):
    key = (name or "").strip().lower()
    p = PLAYER_INDEX.get(key)
    if not p:
        return None
    return p

def ensure_headshot_png(player_id: int, fallback_name: str) -> str:
    """Пытаемся взять headshot из NBA CDN, иначе рисуем плейсхолдер."""
    path = CACHE / f"head_{player_id}.png"
    if not path.exists():
        try:
            import requests
            url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        except Exception:
            im = Image.new("RGBA", (1040, 760), (60, 60, 60, 255))
            d = ImageDraw.Draw(im)
            d.text((40, 40), fallback_name, fill=(255, 255, 255, 255))
            im.save(path)
    return str(path)

def ensure_team_logo_png(team_id: int):
    """Берём PNG логотип из assets/cache если есть, иначе плейсхолдер-иконка."""
    png_path = CACHE / f"logo_{team_id}.png"
    if png_path.exists():
        colors = TEAM_PALETTES.get(team_id, ("#FF6A00", "#1A1A1A", "#FFFFFF"))
        return str(png_path), colors
    # плейсхолдер — звезда
    placeholder = ASSETS / "icons" / "star.png"
    colors = TEAM_PALETTES.get(team_id, ("#FF6A00", "#1A1A1A", "#FFFFFF"))
    return str(placeholder), colors
