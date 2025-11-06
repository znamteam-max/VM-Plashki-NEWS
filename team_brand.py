# team_brand.py
from __future__ import annotations
import os, json, colorsys
from typing import Any, Dict, List, Tuple, Optional
from PIL import Image

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
TEAM_LOGO_DIRS = [
    os.path.join(ASSETS_DIR, "cache"),   # ваша папка с реальными логотипами
    os.path.join(ASSETS_DIR, "teams"),
]

# порядок источников палитры: пресет -> лого -> универсальные
TEAM_PALETTE_PRIORITY = os.getenv("TEAM_PALETTE_PRIORITY", "preset,logo,auto").strip().lower()

# персистентность дефолтного цвета команды
TEAM_OVERRIDES_ENV = os.getenv("TEAM_OVERRIDES_JSON", "").strip()
TEAM_OVERRIDES_TMP = "/tmp/team_overrides.json"
TEAM_OVERRIDES_DEFAULT = os.path.join(ASSETS_DIR, "team_overrides_default.json")

# официальные пресеты (можно расширять)
TEAMS_PRESET: Dict[str, List[str]] = {
    "1610612752": ["#F58426", "#006BB6", "#BEC0C2"],  # Knicks
    "1610612744": ["#1D428A", "#FFC72C", "#006BB6"],  # Warriors
    "1610612756": ["#1D1160", "#E56020", "#F9AD1B"],  # Suns
    "1610612743": ["#0E2240", "#FEC524", "#8B2131"],  # Nuggets
    "1610612738": ["#007A33", "#963821", "#BA9653"],  # Celtics
    "1610612737": ["#E03A3E", "#C1D32F", "#000000"],  # Hawks
    "1610612747": ["#552583", "#FDB927", "#000000"],  # Lakers
    "1610612742": ["#00538C", "#002B5E", "#B8C4CA"],  # Mavericks
}

def _log(*a: Any) -> None:
    try: print("[team_brand]", *a, flush=True)
    except: pass

def _read_json(path: str) -> Any:
    if not os.path.exists(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return None

def _write_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        _log("write json error:", e)

def _hex(c: Tuple[int,int,int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0,min(255,c[0])), max(0,min(255,c[1])), max(0,min(255,c[2]))
    )

def _hex_to_rgb(h: str) -> Tuple[int,int,int]:
    h = h.strip().lstrip("#")
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def _shade(rgb: Tuple[int,int,int], k: float) -> Tuple[int,int,int]:
    r,g,b = rgb
    return (max(0,min(255,int(r*k))),
            max(0,min(255,int(g*k))),
            max(0,min(255,int(b*k))))

def _is_bad_color(rgb: Tuple[int,int,int]) -> bool:
    r,g,b = [v/255.0 for v in rgb]
    h,l,s = colorsys.rgb_to_hls(r,g,b)
    if l > 0.93 or l < 0.07: return True
    if s < 0.10: return True
    return False

def _distinct(a: Tuple[int,int,int], b: Tuple[int,int,int], thr: int = 40) -> bool:
    return abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) >= thr

def _extract_palette(img: Image.Image, top_k: int = 4) -> List[str]:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    base = img.copy()
    base.thumbnail((240, 240))
    no_alpha = Image.new("RGB", base.size, (255,255,255))
    no_alpha.paste(base, mask=base.split()[3])
    q = no_alpha.convert("P", palette=Image.ADAPTIVE, colors=max(4, top_k))
    pal = q.getpalette()
    hist = q.histogram()
    idxs = sorted(range(len(hist)), key=lambda i: hist[i], reverse=True)[:top_k*3]
    result: List[Tuple[int,int,int]] = []
    for idx in idxs:
        if idx*3+2 >= len(pal): continue
        rgb = (pal[idx*3], pal[idx*3+1], pal[idx*3+2])
        if _is_bad_color(rgb): continue
        if not result or all(_distinct(rgb, r) for r in result):
            result.append(rgb)
        if len(result) >= top_k:
            break
    return [_hex(c) for c in result]

def _load_team_overrides() -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    dflt = _read_json(TEAM_OVERRIDES_DEFAULT)
    if isinstance(dflt, dict): merged.update(dflt)
    tmp = _read_json(TEAM_OVERRIDES_TMP)
    if isinstance(tmp, dict): merged.update(tmp)
    if TEAM_OVERRIDES_ENV:
        try:
            envd = json.loads(TEAM_OVERRIDES_ENV)
            if isinstance(envd, dict): merged.update(envd)
        except Exception as e:
            _log("TEAM_OVERRIDES_JSON parse error", e)
    return merged

def _save_team_overrides(data: Dict[str, Any]) -> None:
    _write_json(TEAM_OVERRIDES_TMP, data)

def _find_in_dirs(team_id: str) -> Optional[str]:
    for d in TEAM_LOGO_DIRS:
        p = os.path.join(d, f"{team_id}.png")
        if os.path.exists(p): return p
    for d in TEAM_LOGO_DIRS:
        try:
            for fn in os.listdir(d):
                if not fn.lower().endswith(".png"): continue
                if team_id in fn:
                    return os.path.join(d, fn)
        except FileNotFoundError:
            continue
    for d in TEAM_LOGO_DIRS:
        gp = os.path.join(d, "generic.png")
        if os.path.exists(gp): return gp
    return None

def get_team_logo_path(team_id: str) -> Optional[str]:
    if not team_id: return None
    return _find_in_dirs(str(team_id))

def _merge_unique_hex(primary: List[str], extra: List[str], limit: int = 3) -> List[str]:
    out: List[str] = []
    def add(hexv: str):
        if not hexv or not hexv.startswith("#") or len(hexv) != 7: return
        try:
            rgb = _hex_to_rgb(hexv)
        except: return
        for h in out:
            rgb2 = _hex_to_rgb(h)
            if not _distinct(rgb, rgb2, thr=36):
                return
        out.append(hexv)
    for h in primary: add(h)
    for h in extra: add(h)
    return out[:limit] if limit > 0 else out

def list_palette_for_team(team_id: str) -> List[str]:
    team_id = str(team_id or "0")
    preset = TEAMS_PRESET.get(team_id) or []
    extracted: List[str] = []
    logo = get_team_logo_path(team_id)
    if logo and os.path.exists(logo):
        try:
            with Image.open(logo) as im:
                extracted = _extract_palette(im, top_k=4)
        except Exception as e:
            _log("palette extract error:", e)
    universal = ["#1D428A", "#FDB927", "#0B8043"]
    order = [s.strip() for s in TEAM_PALETTE_PRIORITY.split(",") if s.strip()]
    parts: List[List[str]] = []
    for src in order:
        if src == "preset": parts.append(preset)
        elif src == "logo": parts.append(extracted)
        elif src == "auto": parts.append(universal)
    if not parts: parts = [preset, extracted, universal]
    final: List[str] = []
    for block in parts:
        final = _merge_unique_hex(final, block, limit=0)
    return final[:3] if final else universal[:3]

def get_team_brand(team_id: str) -> Tuple[Tuple[str,str,str], Optional[str], List[str], bool]:
    team_id = str(team_id or "0")
    logo_path = get_team_logo_path(team_id)
    palette_candidates = list_palette_for_team(team_id)
    ov = _load_team_overrides()
    team_ov = ov.get(team_id) or {}
    has_saved = "primary" in team_ov and isinstance(team_ov["primary"], str) and team_ov["primary"].startswith("#")
    if has_saved:
        primary = team_ov["primary"]
    else:
        primary = palette_candidates[0] if palette_candidates else "#1D428A"
    rgb_primary = _hex_to_rgb(primary)
    dark = _hex(_shade(rgb_primary, 0.65))
    light = primary
    return (primary, dark, light), logo_path, palette_candidates, has_saved

def set_team_primary_color(team_id: str, primary_hex: str) -> bool:
    team_id = str(team_id or "0")
    try:
        primary_hex = primary_hex.strip().upper()
        if primary_hex != "AUTO":
            if not primary_hex.startswith("#") or len(primary_hex) != 7:
                return False
        ov = _load_team_overrides()
        entry = ov.get(team_id) or {}
        if primary_hex == "AUTO":
            if "primary" in entry: del entry["primary"]
        else:
            entry["primary"] = primary_hex
        ov[team_id] = entry
        _save_team_overrides(ov)
        _log("team color saved:", team_id, primary_hex)
        return True
    except Exception as e:
        _log("set_team_primary_color error:", e)
        return False

def color_name_ru(hex_color: str) -> str:
    try:
        r,g,b = _hex_to_rgb(hex_color)
        r_,g_,b_ = r/255.0, g/255.0, b/255.0
        h,l,s = colorsys.rgb_to_hls(r_, g_, b_)
        h = (h*360.0) % 360.0
        if l < 0.08: return "чёрный"
        if l > 0.92: return "белый"
        if s < 0.12: return "серый"
        if 42 <= h <= 58 and s > 0.6 and 0.35 <= l <= 0.75: return "золотой"
        if 15 <= h < 45:   return "оранжевый"
        if 45 <= h < 70:   return "жёлтый"
        if 70 <= h < 165:  return "зелёный"
        if 165 <= h < 200: return "бирюзовый"
        if 200 <= h < 255: return "синий"
        if 255 <= h < 290: return "фиолетовый"
        if 290 <= h < 340: return "розовый"
        return "красный"
    except:
        return "цвет"
