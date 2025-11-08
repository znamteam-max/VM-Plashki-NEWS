# graphics.py
from __future__ import annotations
import io, os, math
from typing import List, Tuple, Dict, Any, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

# ---------- utils: colors / fonts ----------

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join([c*2 for c in h])
    if len(h) != 6:
        return (32, 32, 32)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def _rgb_to_hex(c: Tuple[int, int, int]) -> str:
    r,g,b = c
    return "#{:02X}{:02X}{:02X}".format(max(0,min(255,r)), max(0,min(255,g)), max(0,min(255,b)))

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _gradient(size: Tuple[int,int], top: Tuple[int,int,int], bottom: Tuple[int,int,int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h-1)
        r = int(_lerp(top[0], bottom[0], t))
        g = int(_lerp(top[1], bottom[1], t))
        b = int(_lerp(top[2], bottom[2], t))
        draw.line([(0, y), (w, y)], fill=(r,g,b,255))
    return img

def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    # попытки популярных файлов
    candidates = [
        os.path.join(FONTS_DIR, name),
        os.path.join(FONTS_DIR, "Montserrat-" + name),
        os.path.join(FONTS_DIR, "Montserrat-" + name.replace(".ttf","")),
        os.path.join(FONTS_DIR, "Exo2-" + name),
        os.path.join(FONTS_DIR, "Exo2-" + name.replace(".ttf","")),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    return ImageFont.load_default()

def _font_bold(sz: int) -> ImageFont.FreeTypeFont:
    return _load_font("Bold.ttf", sz)

def _font_semibold(sz: int) -> ImageFont.FreeTypeFont:
    # fallback, если SemiBold нет, возьмём Bold
    try:
        return _load_font("SemiBold.ttf", sz)
    except Exception:
        return _font_bold(sz)

def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_maker, max_w: int, max_h: int, start: int, min_size: int=14) -> Tuple[ImageFont.FreeTypeFont, int]:
    size = start
    text = text or ""
    while size >= min_size:
        f = font_maker(size)
        bbox = draw.multiline_textbbox((0,0), text, font=f, spacing=2, align="center")
        w = bbox[2]-bbox[0]; h = bbox[3]-bbox[1]
        if w <= max_w and h <= max_h:
            return f, size
        size -= 1
    return font_maker(min_size), min_size

def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, multiline: bool=True) -> Tuple[int,int]:
    if multiline:
        b = draw.multiline_textbbox((0,0), text, font=font, spacing=2)
    else:
        b = draw.textbbox((0,0), text, font=font)
    return (b[2]-b[0], b[3]-b[1])

def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ---------- corners system ----------

def _corner_flags(opts: dict, default_radius: int = 20):
    r = int(opts.get("radius", default_radius))
    no_round = bool(opts.get("no_round", False))
    round_all = bool(opts.get("round_all", False))
    round_left = bool(opts.get("round_left", True))
    round_right = bool(opts.get("round_right", True))

    if no_round:
        r = 0
        round_all = False
        round_left = False
        round_right = False

    return {
        "radius": r,
        "round_all": round_all,
        "round_left": round_left,
        "round_right": round_right,
        "radius_left": int(opts.get("radius_left", r if (round_left or round_all) else 0)),
        "radius_right": int(opts.get("radius_right", r if (round_right or round_all) else 0)),
    }

def _rounded_mask(size, *, radius=20, round_all=False, round_left=True, round_right=True,
                  radius_left=None, radius_right=None):
    w, h = size
    if radius_left is None: radius_left = radius if (round_left or round_all) else 0
    if radius_right is None: radius_right = radius if (round_right or round_all) else 0

    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)

    if radius_left == 0 and radius_right == 0:
        d.rectangle([0, 0, w, h], fill=255)
        return mask

    # центр
    d.rectangle([radius_left, 0, w - radius_right, h], fill=255)
    # левый край
    if radius_left > 0:
        d.pieslice([0, 0, 2*radius_left, 2*radius_left], 180, 270, fill=255)
        d.pieslice([0, h-2*radius_left, 2*radius_left, h], 90, 180, fill=255)
        d.rectangle([0, radius_left, radius_left, h - radius_left], fill=255)
    # правый край
    if radius_right > 0:
        d.pieslice([w-2*radius_right, 0, w, 2*radius_right], 270, 360, fill=255)
        d.pieslice([w-2*radius_right, h-2*radius_right, w, h], 0, 90, fill=255)
        d.rectangle([w - radius_right, radius_right, w, h - radius_right], fill=255)

    return mask

def _composite_with_corners(bg_color, size, opts):
    flags = _corner_flags(opts)
    layer = Image.new("RGBA", size, bg_color)
    mask = _rounded_mask(size, **flags)
    out = Image.new("RGBA", size, (0,0,0,0))
    out.paste(layer, (0,0), mask)
    return out

# ---------- icons / shapes ----------

def _try_load_icon(name: str, size: int) -> Optional[Image.Image]:
    cand = [
        os.path.join(ICONS_DIR, name),
        os.path.join(ICONS_DIR, name + ".png"),
        os.path.join(ICONS_DIR, name + ".webp"),
    ]
    for p in cand:
        if os.path.exists(p):
            try:
                im = Image.open(p).convert("RGBA")
                im = im.resize((size, size), Image.LANCZOS)
                return im
            except Exception:
                pass
    return None

def _draw_star(draw: ImageDraw.ImageDraw, x: int, y: int, r: int, fill=(255,191,0,255)):
    # простой 5-конечный
    pts = []
    for i in range(10):
        ang = math.pi/2 + i * math.pi/5
        rad = r if i % 2 == 0 else r*0.45
        pts.append((x + rad*math.cos(ang), y - rad*math.sin(ang)))
    draw.polygon(pts, fill=fill)

# ---------- shared painters ----------

def _place_team_logo_circle(base: Image.Image, logo_img: Optional[Image.Image], cx: int, cy: int, diameter: int, *, dx: int=-30, dy: int=-30):
    if logo_img is None: return
    r = diameter//2
    # маска-круг
    mask = Image.new("L", (diameter, diameter), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([0,0,diameter,diameter], fill=255)

    # подложка (слегка затемнённый круг)
    circle = Image.new("RGBA", (diameter, diameter), (0,0,0,50))
    base.alpha_composite(circle, (cx - r, cy - r))

    # лого впишем внутрь круга с полями 10%
    pad = int(diameter*0.12)
    box_w = diameter - pad*2
    box_h = diameter - pad*2
    im = logo_img.copy()
    im = im.resize((box_w, box_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (diameter, diameter), (0,0,0,0))
    canvas.paste(im, (pad, pad), im)
    # смещение
    base.paste(canvas, (cx - r + dx, cy - r + dy), mask)

def _ensure_palette(colors: Any) -> Tuple[Tuple[int,int,int], Tuple[int,int,int], Tuple[int,int,int]]:
    # ожидаем ('#A', '#B', '#C')
    if isinstance(colors, (list, tuple)) and len(colors) >= 3:
        return _hex_to_rgb(colors[0]), _hex_to_rgb(colors[1]), _hex_to_rgb(colors[2])
    # дефолт
    return (29,66,138), (0,40,100), (29,66,138)

def _stats_to_lines(stats: List[Tuple[str,str]]) -> List[Tuple[str,str]]:
    out = []
    for v, label in (stats or [])[:6]:
        val = (str(v) or "").strip()
        lab = (str(label) or "").strip().upper()
        out.append((val, lab))
    return out

# ---------- RENDER: SINGLE (card) ----------

def render_card(template: str,
                player_name: str,
                subtitle: str,
                team_logo_img: Optional[Image.Image],
                colors: Any,
                headshot_img: Optional[Image.Image],
                stats: List[Tuple[str,str]],
                **opts) -> bytes:
    """
    template: "single" | "cards" (для совместимости — игнорируется, но сохраняем сигнатуру)
    **opts: round_left, round_right, no_round, logo_dx, logo_dy, width, height
    """
    W = int(opts.get("width", 1280))
    H = int(opts.get("height", 720))
    primary, dark, light = _ensure_palette(colors)

    base = Image.new("RGBA", (W, H), (0,0,0,0))
    # фон градиент
    bg = _gradient((W, H), top=dark, bottom=light)
    # углы: только справа (если не переопределили)
    corner_opts = {"round_left": bool(opts.get("round_left", False)),
                   "round_right": bool(opts.get("round_right", True)),
                   "radius": int(opts.get("radius", 24)),
                   "no_round": bool(opts.get("no_round", False))}
    bg_shaped = _composite_with_corners((0,0,0,0), (W,H), corner_opts)
    bg_shaped.alpha_composite(bg, (0,0))
    base.alpha_composite(bg_shaped, (0,0))

    draw = ImageDraw.Draw(base)

    # имя игрока
    name_area = (int(W*0.06), int(H*0.12), int(W*0.76), int(H*0.38))  # x1,y1,x2,y2
    name_w = name_area[2]-name_area[0]
    name_h = name_area[3]-name_area[1]
    name_font, name_size = _fit_font(draw, player_name or "", _font_bold, name_w, name_h, start=int(H*0.18), min_size=20)
    nx = name_area[0] + (name_w - _text_size(draw, player_name, name_font, False)[0])//2
    ny = name_area[1] + (name_h - _text_size(draw, player_name, name_font, False)[1])//2
    draw.text((nx, ny), player_name or "", font=name_font, fill=(255,255,255,255))

    # статистика (в столбик)
    stat_lines = _stats_to_lines(stats)
    stat_x = name_area[0]
    stat_y = int(H*0.44)
    line_h = int(H*0.09)
    val_color = (255,255,255,255)
    lbl_color = (230,230,230,210)
    # авто размер: пусть цифры поменьше имени
    val_font = _font_bold(max(18, name_size-12))
    lbl_font = _font_semibold(max(14, name_size-16))
    for i,(val,lbl) in enumerate(stat_lines):
        y = stat_y + i*line_h
        # центрируем по ширине области — относительно имени
        vw, vh = _text_size(draw, val, val_font, False)
        lw, lh = _text_size(draw, lbl, lbl_font, False)
        cx = name_area[0] + name_w//2
        draw.text((cx - vw//2, y), val, font=val_font, fill=val_color)
        draw.text((cx - lw//2, y + vh + 6), lbl, font=lbl_font, fill=lbl_color)

    # лого в кружке
    logo_cx = int(W*0.88)
    logo_cy = int(H*0.18)
    logo_d = int(H*0.22)
    _place_team_logo_circle(base, team_logo_img, logo_cx, logo_cy, logo_d,
                            dx=int(opts.get("logo_dx", -30)), dy=int(opts.get("logo_dy", -30)))

    # headshot — сверху всех
    if headshot_img is not None:
        # правее и ниже центра
        hs = headshot_img.copy().resize((int(W*0.45), int(H*0.9)), Image.LANCZOS)
        base.alpha_composite(hs, (int(W*0.52), int(H*0.05)))

    return _png_bytes(base)

# ---------- RENDER: SIDE NOTE (cardS) ----------

def render_card_special(player_name: str,
                        left_stats: List[Tuple[str,str]],
                        right_text: str,
                        team_logo_img: Optional[Image.Image],
                        colors: Any,
                        headshot_img: Optional[Image.Image],
                        **opts) -> bytes:
    """
    Левая основная плашка + узкий правый блок с текстом (⭐).
    Правый блок ~ вдвое уже левого.
    """
    W = int(opts.get("width", 1280))
    H = int(opts.get("height", 720))
    primary, dark, light = _ensure_palette(colors)

    base = Image.new("RGBA", (W, H), (0,0,0,0))

    # размеры панелей
    right_w = int(W * 0.32)  # ~вдвое уже левого
    left_w  = W - right_w
    right_x = left_w
    left_x  = 0

    # ЛЕВЫЙ: скругления только справа
    left_bg = _gradient((left_w, H), top=dark, bottom=light)
    left_masked = _composite_with_corners((0,0,0,0), (left_w, H), {"round_left": False, "round_right": True, "radius": int(opts.get("radius", 24))})
    left_masked.alpha_composite(left_bg, (0,0))
    base.alpha_composite(left_masked, (left_x, 0))

    # ПРАВЫЙ: скругления с обеих сторон
    right_bg = _gradient((right_w, H), top=_hex_to_rgb(_rgb_to_hex(primary)), bottom=light)
    right_masked = _composite_with_corners((0,0,0,0), (right_w, H), {"round_left": True, "round_right": True, "radius": int(opts.get("radius", 24))})
    right_masked.alpha_composite(right_bg, (0,0))
    base.alpha_composite(right_masked, (right_x, 0))

    draw = ImageDraw.Draw(base)

    # Имя по центру слева
    name_area = (int(left_x+left_w*0.06), int(H*0.10), int(left_x+left_w*0.92), int(H*0.32))
    nw = name_area[2]-name_area[0]; nh = name_area[3]-name_area[1]
    name_font, name_size = _fit_font(draw, player_name or "", _font_bold, nw, nh, start=int(H*0.16))
    nx = name_area[0] + (nw - _text_size(draw, player_name, name_font, False)[0])//2
    ny = name_area[1] + (nh - _text_size(draw, player_name, name_font, False)[1])//2
    draw.text((nx, ny), player_name or "", font=name_font, fill=(255,255,255,255))

    # Статы слева под именем
    stat_lines = _stats_to_lines(left_stats)
    stat_x0 = name_area[0]
    stat_w  = nw
    y0 = int(H*0.38)
    line_h = int(H*0.10)
    val_font = _font_bold(max(18, name_size-10))
    lbl_font = _font_semibold(max(14, name_size-14))
    for i,(val,lbl) in enumerate(stat_lines):
        y = y0 + i*line_h
        vw, vh = _text_size(draw, val, val_font, False)
        lw, lh = _text_size(draw, lbl, lbl_font, False)
        cx = stat_x0 + stat_w//2
        draw.text((cx - vw//2, y), val, font=val_font, fill=(255,255,255,255))
        draw.text((cx - lw//2, y + vh + 6), lbl, font=lbl_font, fill=(235,235,235,220))

    # Правый текст — отдельный слой поверх (не режется) + нижний запас
    right_pad = int(H*0.06)
    rt_x = right_x + int(right_w*0.10)
    rt_y = int(H*0.16)
    rt_w = int(right_w*0.80)
    rt_h = int(H*0.68)
    right_layer = Image.new("RGBA", (rt_w, rt_h + 24), (0,0,0,0))
    rdraw = ImageDraw.Draw(right_layer)

    # звезда
    star = _try_load_icon("star", size=int(H*0.05))
    sx = 0
    if star:
        right_layer.alpha_composite(star, (0, 0))
        sx = star.width + 12
    else:
        _draw_star(rdraw, 12, 12, r=int(H*0.025))

    note_text = (right_text or "").strip()
    if note_text:
        note_text = " " + note_text  # пробел после звезды
    note_text = note_text.rstrip() + "\n\u00A0"  # псевдо-пустая строка снизу

    note_font, _ = _fit_font(rdraw, note_text, _font_semibold, rt_w - sx, rt_h, start=int(H*0.06), min_size=18)
    rdraw.multiline_text((sx, 0), note_text, font=note_font, fill=(255,255,255,240), spacing=6, align="left")
    base.alpha_composite(right_layer, (rt_x, rt_y))

    # лого круга (на левом блоке)
    _place_team_logo_circle(base, team_logo_img, cx=int(left_x+left_w*0.90), cy=int(H*0.16),
                            diameter=int(H*0.18),
                            dx=int(opts.get("logo_dx", -30)), dy=int(opts.get("logo_dy", -30)))

    # headshot — поверх
    if headshot_img is not None:
        hs = headshot_img.copy().resize((int(W*0.40), int(H*0.9)), Image.LANCZOS)
        base.alpha_composite(hs, (int(left_x+left_w*0.55), int(H*0.05)))

    return _png_bytes(base)

# ---------- RENDER: BAD (cardBAD) ----------

def render_card_bad(player_name: str,
                    team_logo_img: Optional[Image.Image],
                    stats: List[Tuple[str,str]],
                    **opts) -> bytes:
    """
    Всегда «плохой» коричневый, 💩 справа от имени, без скруглений слева (только справа).
    """
    W = int(opts.get("width", 1280))
    H = int(opts.get("height", 720))
    # коричневая палитра
    top = (88, 56, 28)
    bot = (122, 78, 41)

    base = Image.new("RGBA", (W, H), (0,0,0,0))
    bg = _gradient((W, H), top=top, bottom=bot)
    shaped = _composite_with_corners((0,0,0,0), (W, H), {"round_left": False, "round_right": True, "radius": int(opts.get("radius", 24))})
    shaped.alpha_composite(bg, (0,0))
    base.alpha_composite(shaped, (0,0))

    draw = ImageDraw.Draw(base)

    # имя + 💩 (иконку опускаем чуть ниже)
    name_area = (int(W*0.06), int(H*0.15), int(W*0.80), int(H*0.40))
    nw = name_area[2]-name_area[0]; nh = name_area[3]-name_area[1]
    name_font, name_sz = _fit_font(draw, player_name or "", _font_bold, nw, nh, start=int(H*0.20))
    tx = name_area[0] + (nw - _text_size(draw, player_name, name_font, False)[0])//2
    ty = name_area[1] + (nh - _text_size(draw, player_name, name_font, False)[1])//2
    draw.text((tx, ty), player_name or "", font=name_font, fill=(255,255,255,255))

    poop = _try_load_icon("poop", size=int(name_sz*1.05))
    if poop:
        base.alpha_composite(poop, (tx + _text_size(draw, player_name, name_font, False)[0] + 18,
                                    ty + int(name_sz*0.15)))

    # статы
    stat_lines = _stats_to_lines(stats)
    y0 = int(H*0.46)
    line_h = int(H*0.1)
    val_font = _font_bold(max(18, name_sz - 8))
    lbl_font = _font_semibold(max(14, name_sz - 12))
    for i,(val,lbl) in enumerate(stat_lines):
        y = y0 + i*line_h
        vw, vh = _text_size(draw, val, val_font, False)
        lw, lh = _text_size(draw, lbl, lbl_font, False)
        cx = int(W*0.43)
        draw.text((cx - vw//2, y), val, font=val_font, fill=(255,255,255,255))
        draw.text((cx - lw//2, y + vh + 6), lbl, font=lbl_font, fill=(235,235,235,220))

    # логотип круга
    _place_team_logo_circle(base, team_logo_img, cx=int(W*0.90), cy=int(H*0.18),
                            diameter=int(H*0.20),
                            dx=int(opts.get("logo_dx", -30)), dy=int(opts.get("logo_dy", -30)))

    return _png_bytes(base)

# ---------- RENDER: DOUBLE (card2) ----------

def render_card2(left_name: str,
                 left_stats: List[Tuple[str,str]],
                 right_name: str,
                 right_stats: List[Tuple[str,str]],
                 left_logo: Optional[Image.Image],
                 right_logo: Optional[Image.Image],
                 left_colors: Any,
                 right_colors: Any,
                 **opts) -> bytes:
    """
    Две команды: без скруглений вообще (no_round=True).
    Имена — КРУПНЕЕ статов на 2pt. Размеры синхронизированы между сторонами.
    """
    W = int(opts.get("width", 1280))
    H = int(opts.get("height", 720))
    lw = W//2
    rw = W - lw

    lp, ld, ll = _ensure_palette(left_colors)
    rp, rd, rl = _ensure_palette(right_colors)

    base = Image.new("RGBA", (W, H), (0,0,0,0))

    # Левый фон
    lbg = _gradient((lw, H), top=ld, bottom=ll)
    lmask = _composite_with_corners((0,0,0,0), (lw, H), {"no_round": True})
    lmask.alpha_composite(lbg, (0,0))
    base.alpha_composite(lmask, (0,0))

    # Правый фон
    rbg = _gradient((rw, H), top=rd, bottom=rl)
    rmask = _composite_with_corners((0,0,0,0), (rw, H), {"no_round": True})
    rmask.alpha_composite(rbg, (0,0))
    base.alpha_composite(rmask, (lw,0))

    draw = ImageDraw.Draw(base)

    # --- авто-подгон шрифтов: имя больше статов на 2pt, одинаковые на обеих сторонах
    name_box_h = int(H*0.28)
    stat_box_h = int(H*0.22)
    name_max_w = int(lw*0.84)
    stat_max_w = int(lw*0.84)

    # подгоняем отдельно, потом берём минимум, чтобы одинаково
    lf_draw = ImageDraw.Draw(Image.new("RGBA", (lw,H)))
    rf_draw = ImageDraw.Draw(Image.new("RGBA", (rw,H)))

    lf_name_f, lf_name_sz = _fit_font(lf_draw, left_name or "", _font_bold, name_max_w, name_box_h, start=int(H*0.18))
    rf_name_f, rf_name_sz = _fit_font(rf_draw, right_name or "", _font_bold, name_max_w, name_box_h, start=int(H*0.18))
    target_name_sz = min(lf_name_sz, rf_name_sz)

    # статы на 2pt меньше имени
    target_stat_sz = max(14, target_name_sz - 2)

    # убедимся, что статы влезают; если нет — уменьшим оба синхронно
    left_stat_line = "\n".join([f"{v}" for v,_ in _stats_to_lines(left_stats)])
    right_stat_line = "\n".join([f"{v}" for v,_ in _stats_to_lines(right_stats)])
    lf_stat_f, lf_stat_sz = _fit_font(lf_draw, left_stat_line, _font_bold, stat_max_w, stat_box_h, start=target_stat_sz)
    rf_stat_f, rf_stat_sz = _fit_font(rf_draw, right_stat_line, _font_bold, stat_max_w, stat_box_h, start=target_stat_sz)

    final_stat_sz = min(lf_stat_sz, rf_stat_sz)
    final_name_sz = max(final_stat_sz + 2, min(target_name_sz, 128))
    left_name_font  = _font_bold(final_name_sz)
    right_name_font = _font_bold(final_name_sz)
    left_stat_font  = _font_bold(final_stat_sz)
    right_stat_font = _font_bold(final_stat_sz)
    left_lbl_font   = _font_semibold(max(12, final_stat_sz - 4))
    right_lbl_font  = _font_semibold(max(12, final_stat_sz - 4))

    # Рисуем левую сторону (центрирование по вертикали блоков)
    def _draw_side(x0: int, width: int, name: str, stats: List[Tuple[str,str]], name_font, stat_font, lbl_font, logo_img):
        # имя
        name_area = (x0 + (width - name_max_w)//2, int(H*0.12), x0 + (width + name_max_w)//2, int(H*0.12)+name_box_h)
        nw = name_area[2]-name_area[0]; nh = name_area[3]-name_area[1]
        nx = name_area[0] + (nw - _text_size(draw, name, name_font, False)[0])//2
        ny = name_area[1] + (nh - _text_size(draw, name, name_font, False)[1])//2
        draw.text((nx, ny), name or "", font=name_font, fill=(255,255,255,255))

        # статы (значение + подпись, по центру)
        lines = _stats_to_lines(stats)
        y0 = int(H*0.44)
        step = int(H*0.10)
        for i,(v,l) in enumerate(lines):
            y = y0 + i*step
            vw, vh = _text_size(draw, v, stat_font, False)
            lw_, lh_ = _text_size(draw, l,  lbl_font, False)
            cx = x0 + width//2
            draw.text((cx - vw//2, y), v, font=stat_font, fill=(255,255,255,255))
            draw.text((cx - lw_//2, y + vh + 6), l, font=lbl_font, fill=(235,235,235,220))

        # логотип
        _place_team_logo_circle(base, logo_img, cx=x0 + int(width*0.90), cy=int(H*0.16),
                                diameter=int(H*0.18),
                                dx=int(opts.get("logo_dx", -30)), dy=int(opts.get("logo_dy", -30)))

    _draw_side(0, lw, left_name or "", left_stats, left_name_font, left_stat_font, left_lbl_font, left_logo)
    _draw_side(lw, rw, right_name or "", right_stats, right_name_font, right_stat_font, right_lbl_font, right_logo)

    return _png_bytes(base)

# ---------- COMPAT: doctor / fallback ----------

def render_card_drN(*args, **kwargs) -> bytes:
    """
    Заглушка для совместимости: рендерим одинарную плашку.
    """
    # ожидаем: (player_name, team_logo, colors, headshot, stats)
    if len(args) >= 5:
        name = args[0]
        team_logo = args[1]
        colors = args[2]
        head = args[3]
        stats = args[4]
        return render_card("single", name, "", team_logo, colors, head, stats, **kwargs)
    # иначе просто пустышка
    W = int(kwargs.get("width", 1280)); H = int(kwargs.get("height", 720))
    img = Image.new("RGBA", (W,H), (20,20,20,255))
    return _png_bytes(img)
