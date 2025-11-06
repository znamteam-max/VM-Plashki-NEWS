# team_brand.py
from __future__ import annotations
import os, json, colorsys
from typing import Any, Dict, List, Tuple, Optional
from PIL import Image

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
TEAM_LOGOS_DIR = os.path.join(ASSETS_DIR, "teams")

# Персистентность в serverless:
# 1) ENV TEAM_OVERRIDES_JSON имеет приоритет (для долгосрочного хранения).
# 2) /tmp/team_overrides.json — runtime-перезапись командами через бота (пропадёт на холодном старте).
# 3) assets/team_overrides_default.json — опциональный "дефолтный" файл в репо.
TEAM_OVERRIDES_ENV = os.getenv("TEAM_OVERRIDES_JSON", "").strip()
TEAM_OVERRIDES_TMP = "/tmp/team_overrides.json"
TEAM_OVERRIDES_DEFAULT = os.path.join(ASSETS_DIR, "team_overrides_default.json")

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
    return "#{:02X}{:02X}{:02X}".format(max(0,min(255,c[0])), max(0,min(255,c[1])), max(0,min(255,c[2])))

def _shade(rgb: Tuple[int,int,int], k: float) -> Tuple[int,int,int]:
    r,g,b = rgb
    return (max(0,min(255,int(r*k))), max(0,min(255,int(g*k))), max(0,min(255,int(b*k))))

def _is_bad_color(rgb: Tuple[int,int,int]) -> bool:
    # отбрасываем почти белые/почти чёрные/очень низкую насыщенность
    r,g,b = [v/255.0 for v in rgb]
    h,l,s = colorsys.rgb_to_hls(r,g,b)
    if l > 0.93 or l < 0.07: return True
    if s < 0.10: return True
    return False

def _distinct(a: Tuple[int,int,int], b: Tuple[int,int,int], thr: int = 40) -> bool:
    return abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) >= thr

def _extract_palette(img: Image.Image, top_k: int = 4) -> List[str]:
    """
    Квантование логотипа до top_k цветов + фильтры, возвращаем HEX-список.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    # уменьшаем (быстрее, меньше шума)
    base = img.copy()
    base.thumbnail((200, 200))
    # чёрные/прозрачные пиксели игнорируем для более чистой палитры
    no_alpha = Image.new("RGB", base.size, (255,255,255))
    no_alpha.paste(base, mask=base.split()[3])
    q = no_alpha.convert("P", palette=Image.ADAPTIVE, colors=max(4, top_k))
    pal = q.getpalette()[:top_k*3]
    # собрать частоты
    hist = q.histogram()
    # Вытащим индексы самых частых
    idxs = sorted(range(len(hist)), key=lambda i: hist[i], reverse=True)[:top_k*2]
    result: List[Tuple[int,int,int]] = []
    for idx in idxs:
        if idx*3+2 >= len(pal): continue
        rgb = (pal[idx*3], pal[idx*3+1], pal[idx*3+2])
        if _is_bad_color(rgb): continue
        if not result: result.append(rgb)
        else:
            if all(_distinct(rgb, r) for r in result):
                result.append(rgb)
        if len(result) >= top_k:
            break
    # fallback: возьмём хотя бы что-то
    if not result:
        result = [(0, 122, 204)]  # синий по умолчанию
    return [_hex(c) for c in result]

def _load_team_overrides() -> Dict[str, Any]:
    # 1) ENV как верхний уровень
    if TEAM_OVERRIDES_ENV:
        try:
            d = json.loads(TEAM_OVERRIDES_ENV)
            if isinstance(d, dict): return d
        except Exception as e:
            _log("TEAM_OVERRIDES_JSON parse error", e)
    # 2) runtime /tmp
    tmp = _read_json(TEAM_OVERRIDES_TMP)
    if isinstance(tmp, dict): return tmp
    # 3) дефолт из репо
    dflt = _read_json(TEAM_OVERRIDES_DEFAULT)
    if isinstance(dflt, dict): return dflt
    return {}

def _save_team_overrides(data: Dict[str, Any]) -> None:
    _write_json(TEAM_OVERRIDES_TMP, data)

def get_team_logo_path(team_id: str) -> Optional[str]:
    """
    Ищем логотипы по путям:
      assets/teams/<teamId>.png
      assets/teams/<teamId>.svg (не обрабатываем SVG сейчас)
    """
    if not team_id: return None
    p1 = os.path.join(TEAM_LOGOS_DIR, f"{team_id}.png")
    if os.path.exists(p1): return p1
    # В качестве fallback можно держать generic-лого:
    p0 = os.path.join(TEAM_LOGOS_DIR, "generic.png")
    return p0 if os.path.exists(p0) else None

def get_team_brand(team_id: str) -> Tuple[Tuple[str,str,str], Optional[str], List[str]]:
    """
    Возвращает:
      (primary_hex, dark_hex, light_hex), logo_path, palette_candidates
    """
    team_id = str(team_id or "0")
    logo_path = get_team_logo_path(team_id)
    palette: List[str] = []
    if logo_path and os.path.exists(logo_path):
        try:
            with Image.open(logo_path) as im:
                palette = _extract_palette(im, top_k=4)
        except Exception as e:
            _log("palette extract error:", e)

    # overrides
    ov = _load_team_overrides()
    team_ov = ov.get(team_id) or {}
    primary = team_ov.get("primary") or (palette[0] if palette else "#007ACC")
    # производные тона для градиента
    # слева темнее, справа — основной
    def _hex_to_rgb(h: str) -> Tuple[int,int,int]:
        h = h.strip().lstrip("#")
        return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))
    rgb_primary = _hex_to_rgb(primary)
    dark = _hex(_shade(rgb_primary, 0.65))
    light = primary
    return (primary, dark, light), logo_path, palette

def set_team_primary_color(team_id: str, primary_hex: str) -> bool:
    team_id = str(team_id or "0")
    try:
        primary_hex = primary_hex.strip().upper()
        if not primary_hex.startswith("#") or len(primary_hex) != 7:
            return False
        ov = _load_team_overrides()
        entry = ov.get(team_id) or {}
        entry["primary"] = primary_hex
        ov[team_id] = entry
        _save_team_overrides(ov)
        _log("team color saved:", team_id, primary_hex)
        return True
    except Exception as e:
        _log("set_team_primary_color error:", e)
        return False
