# graphics.py
from __future__ import annotations
import io, os, math
from typing import Any, Iterable, List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

# ------------------------------------------------------------
# ПУТИ / НАСТРОЙКИ
# ------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(ROOT, "assets", "fonts")
ICONS_DIR = os.path.join(ROOT, "assets", "icons")

FONT_PRIMARY = (
    os.path.join(FONTS_DIR, "Montserrat-Bold.ttf"),
    os.path.join(FONTS_DIR, "Exo2-Bold.ttf"),
)
FONT_SECONDARY = (
    os.path.join(FONTS_DIR, "Montserrat-SemiBold.ttf"),
    os.path.join(FONTS_DIR, "Exo2-Bold.ttf"),
)
FONT_FALLBACK = ImageFont.load_default()

CANVAS_W, CANVAS_H = 1600, 900
SAFE_PADDING = 40

# сместить логотип команды «на 30 px вверх и влево»
TEAM_LOGO_OFFSET = (-30, -30)

# коричневый для BAD
BAD_BROWN = (104, 73, 39)

# ------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ
# ------------------------------------------------------------
def _load_font(paths, size: int):
    for p in paths:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            continue
    return FONT_FALLBACK

def _hex_to_rgb(h) -> Tuple[int, int, int]:
    if isinstance(h, (tuple, list)) and len(h) >= 3:
        try:
            r, g, b = int(h[0]), int(h[1]), int(h[2])
            return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        except Exception:
            return (32, 32, 32)
    if isinstance(h, str):
        s = h.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) == 6:
            try:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            except Exception:
                pass
    return (32, 32, 32)

def _ensure_palette(colors: Any) -> Tuple[Tuple[int,int,int], Tuple[int,int,int], Tuple[int,int,int]]:
    if isinstance(colors, (list, tuple)) and len(colors) >= 3:
        return (_hex_to_rgb(colors[0]), _hex_to_rgb(colors[1]), _hex_to_rgb(colors[2]))
    return ((29,66,138), (0,40,100), (29,66,138))

def _save_png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()

def _draw_star(draw: ImageDraw.ImageDraw, xy: Tuple[int,int], r: int, fill=(255,191,0)):
    cx, cy = xy
    pts = []
    for i in range(10):
        ang = -math.pi/2 + i * math.pi/5
        rad = r if i % 2 == 0 else r * 0.45
        x = cx + rad * math.cos(ang)
        y = cy + rad * math.sin(ang)
        pts.append((x, y))
    draw.polygon(pts, fill=fill)

def _rounded_mask(size: Tuple[int,int], radius: int, corners=(True,True,True,True)) -> Image.Image:
    w, h = size
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    r = radius
    if not corners[0]: d.rectangle((0, 0, r, r), fill=255)                # TL
    if not corners[1]: d.rectangle((w - r, 0, w, r), fill=255)            # TR
    if not corners[2]: d.rectangle((w - r, h - r, w, h), fill=255)        # BR
    if not corners[3]: d.rectangle((0, h - r, r, h), fill=255)            # BL
    return m

def _paste_with_mask(dst: Image.Image, src: Image.Image, xy: Tuple[int,int]):
    if src.mode in ("RGBA", "LA"):
        dst.alpha_composite(src, xy)
    else:
        dst.paste(src, xy)

def _fit_text(font_paths, text: str, max_width: int, start_size: int, min_size: int) -> Tuple[ImageFont.ImageFont, int]:
    size = start_size
    while size >= min_size:
        f = _load_font(font_paths, size)
        w = f.getbbox(text)[2]
        if w <= max_width:
            return f, size
        size -= 1
    return _load_font(font_paths, min_size), min_size

def _wrap_text(font: ImageFont.ImageFont, text: str, max_width: int) -> List[str]:
    text = str(text or "")
    words = text.split()
    if not words:
        return []
    lines, cur = [], []
    while words:
        cur.append(words.pop(0))
        s = " ".join(cur)
        w = font.getbbox(s)[2]
        if w > max_width and len(cur) > 1:
            last = cur.pop()
            lines.append(" ".join(cur))
            cur = [last]
    if cur:
        lines.append(" ".join(cur))
    return lines

def _ensure_rgba(img_or_none: Optional[Image.Image]) -> Optional[Image.Image]:
    if img_or_none is None:
        return None
    if img_or_none.mode != "RGBA":
        return img_or_none.convert("RGBA")
    return img_or_none

# ---------------------------------------------
# СТАТЫ → СТРОКИ (устойчиво к мусору)
# ---------------------------------------------
def _stats_to_lines(stats: Any, gap: int = 36) -> Tuple[List[str], List[str], int]:
    """Возвращает (values, labels, cols). Любой «мусор»/Image → пустые статы без падения."""
    if stats is None:
        return [], [], 0
    # Если прилетела картинка/любой неитерируемый — отключаем статы
    if isinstance(stats, Image.Image):
        return [], [], 0
    if not isinstance(stats, (list, tuple)):
        try:
            stats = list(stats)  # на случай генераторов
        except Exception:
            return [], [], 0

    vals, labels = [], []
    for item in stats:
        # item может быть чем угодно; аккуратно раскладываем
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            v, lab = item[0], item[1]
            vals.append(str(v).strip())
            labels.append(str(lab).strip())
        else:
            vals.append(str(item).strip())
            labels.append("")
    cols = max(0, len(vals))
    return vals, labels, cols

# ---------------------------------------------
# БАЗОВЫЕ СЛОИ / ЭЛЕМЕНТЫ
# ---------------------------------------------
def _draw_base_box(w: int, h: int, color: Tuple[int,int,int], radius: int, corners=(True,True,True,True)) -> Image.Image:
    box = Image.new("RGBA", (w, h), (0,0,0,0))
    mask = _rounded_mask((w, h), radius, corners=corners)
    layer = Image.new("RGBA", (w, h), color + (255,))
    box.paste(layer, (0,0), mask)
    return box

def _draw_gradient(w: int, h: int, top: Tuple[int,int,int], bottom: Tuple[int,int,int]) -> Image.Image:
    grad = Image.new("RGBA", (w, h), (0,0,0,0))
    d = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        d.line([(0,y),(w,y)], fill=(r,g,b,255))
    return grad

def _draw_team_logo_circle(base: Image.Image, logo_img: Optional[Image.Image], cx: int, cy: int, radius: int):
    if logo_img is None:
        return
    logo_img = _ensure_rgba(logo_img)
    r = radius
    circ = Image.new("RGBA", (r*2, r*2), (0,0,0,0))
    d = ImageDraw.Draw(circ)
    d.ellipse((0,0,r*2,r*2), fill=(255,255,255,255))
    inner = int(r * 1.4)
    L = logo_img.copy()
    L.thumbnail((inner, inner))
    lx = (circ.width - L.width)//2
    ly = (circ.height - L.height)//2
    circ.alpha_composite(L, (lx, ly))
    ox, oy = TEAM_LOGO_OFFSET
    base.alpha_composite(circ, (cx - r + ox, cy - r + oy))

def _place_headshot(base: Image.Image, head: Optional[Image.Image], x_right: int, y_bottom: int, max_w: int, max_h: int):
    if head is None:
        return
    H = _ensure_rgba(head)
    if H is None:
        return
    H = H.copy()
    H.thumbnail((max_w, max_h))
    x = x_right - H.width
    y = y_bottom - H.height
    base.alpha_composite(H, (x, y))

# ------------------------------------------------------------
# ОДИНОЧНАЯ ПЛАШКА (card)
# ------------------------------------------------------------
def render_card(template_key: str,
                player_name: str,
                team_text: str,
                logo_img: Optional[Image.Image],
                colors: Any,
                head_img: Optional[Image.Image],
                stats: Any,
                **kwargs) -> bytes:
    c1, c2, _ = _ensure_palette(colors)
    W, H = 1280, 720
    im = Image.new("RGBA", (W, H), (0,0,0,0))

    grad = _draw_gradient(W, H, c1, c2)
    # только справа скругления
    mask = _rounded_mask((W, H), radius=48, corners=(False, True, True, False))
    grad.putalpha(mask)
    im.alpha_composite(grad, (0, 0))

    _draw_team_logo_circle(im, logo_img, cx=SAFE_PADDING + 120, cy=SAFE_PADDING + 120, radius=84)

    name_font = _load_font(FONT_PRIMARY, 132)
    stat_big = _load_font(FONT_PRIMARY, 88)
    stat_small = _load_font(FONT_SECONDARY, 52)

    vals, labels, cols = _stats_to_lines(stats)

    # центрируем блок имени+статов
    col_gap = 46
    col_w = min(240, (W - SAFE_PADDING*2) // max(1, cols) - col_gap) if cols else 0
    stats_w = cols * col_w + (cols - 1) * col_gap if cols else 0
    name_w = name_font.getbbox(str(player_name))[2]
    name_h = name_font.getbbox(str(player_name))[3] - name_font.getbbox(str(player_name))[1]
    block_w = max(name_w, stats_w)
    stats_big_h = stat_big.getbbox("88")[3] - stat_big.getbbox("88")[1]
    stats_small_h = stat_small.getbbox("ОЧКИ")[3] - stat_small.getbbox("ОЧКИ")[1]
    block_h = name_h + (28 if cols else 0) + (stats_big_h + 10 + stats_small_h if cols else 0)

    bx = (W - block_w)//2
    by = (H - block_h)//2
    d = ImageDraw.Draw(im)
    d.text((bx, by), str(player_name), font=name_font, fill=(255,255,255,255))

    if cols:
        sx = (W - stats_w)//2
        sy = by + name_h + 28
        for i in range(cols):
            cx = sx + i * (col_w + col_gap)
            v = vals[i] if i < len(vals) else ""
            vw = stat_big.getbbox(v)[2]
            d.text((cx + (col_w - vw)//2, sy), v, font=stat_big, fill=(255,255,255,255))
            lab = labels[i] if i < len(labels) else ""
            lw = stat_small.getbbox(lab)[2]
            vh = stat_big.getbbox(v)[3] - stat_big.getbbox(v)[1]
            d.text((cx + (col_w - lw)//2, sy + vh + 10), lab, font=stat_small, fill=(255,255,255,220))

    _place_headshot(im, head_img, x_right=W - SAFE_PADDING, y_bottom=H - SAFE_PADDING, max_w=720, max_h=720)
    return _save_png(im)

# ------------------------------------------------------------
# ДВОЙНАЯ ПЛАШКА (card2) — без скруглений вообще
# ------------------------------------------------------------
def render_card2(template_key: str,
                 p1_name: str, _team1: str, logo1: Optional[Image.Image], colors1: Any, head1: Optional[Image.Image], stats1: Any,
                 p2_name: str, _team2: str, logo2: Optional[Image.Image], colors2: Any, head2: Optional[Image.Image], stats2: Any,
                 **kwargs) -> bytes:
    c1L, c2L, _ = _ensure_palette(colors1)
    c1R, c2R, _ = _ensure_palette(colors2)

    W, H = 1600, 720
    im = Image.new("RGBA", (W, H), (0,0,0,0))

    # Никаких масок → без скруглений
    left = _draw_gradient(W//2, H, c1L, c2L)
    right = _draw_gradient(W//2, H, c1R, c2R)
    im.alpha_composite(left, (0, 0))
    im.alpha_composite(right, (W//2, 0))

    # Имя должно быть ≥ цифр. Найдём общий размер, но крупный.
    max_name_w = W//2 - SAFE_PADDING*2
    base_try = 132
    f1, s1 = _fit_text(FONT_PRIMARY, str(p1_name), max_name_w, base_try, 48)
    f2, s2 = _fit_text(FONT_PRIMARY, str(p2_name), max_name_w, base_try, 48)
    name_size = min(s1, s2)               # одинаковый
    stat_size = max(24, name_size - 2)    # на 2 меньше, чем имя

    f_name = _load_font(FONT_PRIMARY, name_size)
    f_val  = _load_font(FONT_PRIMARY, stat_size)
    f_lab  = _load_font(FONT_SECONDARY, max(18, stat_size - 8))

    _draw_team_logo_circle(im, logo1, cx=SAFE_PADDING + 120, cy=SAFE_PADDING + 120, radius=84)
    _draw_team_logo_circle(im, logo2, cx=W - (SAFE_PADDING + 120), cy=SAFE_PADDING + 120, radius=84)

    def draw_side(x0: int, side_name: str, head: Optional[Image.Image], stats_any: Any):
        vals, labs, cols = _stats_to_lines(stats_any)
        d = ImageDraw.Draw(im)
        nw = f_name.getbbox(str(side_name))[2]
        nh = f_name.getbbox(str(side_name))[3] - f_name.getbbox(str(side_name))[1]
        nx = x0 + (W//2 - nw)//2
        ny = (H - nh)//2 - 80
        d.text((nx, ny), str(side_name), font=f_name, fill=(255,255,255,255))

        if cols:
            col_gap = 36
            col_w = min(240, (W//2 - SAFE_PADDING*2) // max(1, cols) - col_gap)
            stats_w = cols * col_w + (cols - 1) * col_gap
            sx = x0 + (W//2 - stats_w)//2
            sy = ny + nh + 24
            for i in range(cols):
                cx = sx + i * (col_w + col_gap)
                v = vals[i] if i < len(vals) else ""
                vw = f_val.getbbox(v)[2]
                d.text((cx + (col_w - vw)//2, sy), v, font=f_val, fill=(255,255,255,255))
                lab = labs[i] if i < len(labs) else ""
                lw = f_lab.getbbox(lab)[2]
                vh = f_val.getbbox(v)[3] - f_val.getbbox(v)[1]
                d.text((cx + (col_w - lw)//2, sy + vh + 8), lab, font=f_lab, fill=(255,255,255,220))

        _place_headshot(im, head, x_right=x0 + W//2 - SAFE_PADDING, y_bottom=H - SAFE_PADDING, max_w=600, max_h=620)

    draw_side(0, p1_name, head1, stats1)
    draw_side(W//2, p2_name, head2, stats2)
    return _save_png(im)

# ------------------------------------------------------------
# ОСНОВНАЯ + ДОП. ПАНЕЛЬ (cardS)
#   — основная с закруглением только справа;
#   — правая доп.плашка со скруглениями по всем углам;
#   — внизу доп.плашки добавляем пустую строку.
# ------------------------------------------------------------
def render_card_special(template_key: str,
                        player_name: str,
                        right_text: Any,
                        logo_img: Optional[Image.Image],
                        colors: Any,
                        head_img: Optional[Image.Image],
                        stats: Any = None,
                        **kwargs) -> bytes:
    c1, c2, _ = _ensure_palette(colors)
    W, H = 1600, 720
    im = Image.new("RGBA", (W, H), (0,0,0,0))

    main_w = int(W * 0.65)
    main = _draw_gradient(main_w, H, c1, c2)
    main_mask = _rounded_mask((main_w, H), radius=48, corners=(False, True, True, False))  # только справа
    main.putalpha(main_mask)
    im.alpha_composite(main, (0,0))

    right_w = max(420, main_w // 2)
    right = _draw_gradient(right_w, H, c1, c2)
    right_mask = _rounded_mask((right_w, H), radius=40, corners=(True, True, True, True))
    right.putalpha(right_mask)
    im.alpha_composite(right, (main_w + 12, 0))

    _draw_team_logo_circle(im, logo_img, cx=SAFE_PADDING + 120, cy=SAFE_PADDING + 120, radius=84)

    name_font = _load_font(FONT_PRIMARY, 128)
    stat_big  = _load_font(FONT_PRIMARY, 86)
    stat_small= _load_font(FONT_SECONDARY, 48)
    vals, labs, cols = _stats_to_lines(stats)

    name_w = name_font.getbbox(str(player_name))[2]
    name_h = name_font.getbbox(str(player_name))[3] - name_font.getbbox(str(player_name))[1]
    col_gap = 42
    col_w = min(220, (main_w - SAFE_PADDING*2) // max(1, cols) - col_gap) if cols else 0
    stats_w = cols * col_w + (cols - 1)*col_gap if cols else 0
    block_w = max(name_w, stats_w)
    stats_big_h = stat_big.getbbox("88")[3] - stat_big.getbbox("88")[1]
    stats_small_h = stat_small.getbbox("ОЧКИ")[3] - stat_small.getbbox("ОЧКИ")[1]
    block_h = name_h + (24 if cols else 0) + (stats_big_h + 8 + stats_small_h if cols else 0)

    bx = SAFE_PADDING + (main_w - SAFE_PADDING*2 - block_w)//2 + SAFE_PADDING//2
    by = (H - block_h)//2

    d = ImageDraw.Draw(im)
    d.text((bx, by), str(player_name), font=name_font, fill=(255,255,255,255))

    if cols:
        sx = SAFE_PADDING + (main_w - stats_w)//2
        sy = by + name_h + 24
        for i in range(cols):
            cx = sx + i * (col_w + col_gap)
            v = vals[i] if i < len(vals) else ""
            vw = stat_big.getbbox(v)[2]
            d.text((cx + (col_w - vw)//2, sy), v, font=stat_big, fill=(255,255,255,255))
            lab = labs[i] if i < len(labs) else ""
            lw = stat_small.getbbox(lab)[2]
            vh = stat_big.getbbox(v)[3] - stat_big.getbbox(v)[1]
            d.text((cx + (col_w - lw)//2, sy + vh + 8), lab, font=stat_small, fill=(255,255,255,220))

    # Правая панель: иконка звезды + переносы + пустая строка снизу
    right_pad = 36
    rx = main_w + 12 + right_pad
    ry = right_pad + 10
    rmax = right_w - right_pad*2

    rt_font = _load_font(FONT_PRIMARY, 64)
    d_star = ImageDraw.Draw(im)
    star_y = ry + 26
    _draw_star(d_star, (rx + 18, star_y), 16, fill=(255,200,0))
    rx_text = rx + 44

    lines = _wrap_text(rt_font, str(right_text or ""), rmax - 44)
    if not lines:
        lines = [" "]
    lines.append("")  # пустая строка
    lh = (rt_font.getbbox("A")[3] - rt_font.getbbox("A")[1]) + 6
    for i, line in enumerate(lines):
        d.text((rx_text, ry + i*lh), line, font=rt_font, fill=(255,255,255,240))

    _place_headshot(im, head_img, x_right=main_w - SAFE_PADDING, y_bottom=H - SAFE_PADDING, max_w=600, max_h=640)
    return _save_png(im)

# ------------------------------------------------------------
# BAD — всегда коричневый (скругления только справа), какашка крупнее
# ------------------------------------------------------------
def _load_icon(name: str, size: int) -> Optional[Image.Image]:
    path = os.path.join(ICONS_DIR, name)
    if not os.path.exists(path):
        return None
    try:
        im = Image.open(path).convert("RGBA")
        im.thumbnail((size, size))
        return im
    except Exception:
        return None

def render_card_bad(template_key: str,
                    player_name: str,
                    _team_text: str,
                    _logo_img: Optional[Image.Image],
                    _colors_unused: Any,
                    head_img: Optional[Image.Image],
                    stats: Any,
                    **kwargs) -> bytes:
    W, H = 1400, 560
    im = Image.new("RGBA", (W, H), (0,0,0,0))

    box = _draw_base_box(W, H, BAD_BROWN, radius=44, corners=(False, True, True, False))
    im.alpha_composite(box, (0, 0))

    d = ImageDraw.Draw(im)
    name_font = _load_font(FONT_PRIMARY, 132)
    stat_big  = _load_font(FONT_PRIMARY, 90)
    stat_small= _load_font(FONT_SECONDARY, 50)
    vals, labs, cols = _stats_to_lines(stats)

    nx = SAFE_PADDING + 20
    ny = SAFE_PADDING + 40
    d.text((nx, ny), str(player_name), font=name_font, fill=(255,255,255,255))
    name_w = name_font.getbbox(str(player_name))[2]
    name_h = name_font.getbbox(str(player_name))[3] - name_font.getbbox(str(player_name))[1]

    poop = _load_icon("poop.png", size=140) or _load_icon("poop@2x.png", size=140)
    if poop:
        px = nx + name_w + 16
        py = ny + name_h - poop.height + 18  # опустить на ~15-20 px
        im.alpha_composite(poop, (px, py))

    if cols:
        col_gap = 42
        col_w = min(230, (W - SAFE_PADDING*2) // max(1, cols) - col_gap)
        stats_w = cols * col_w + (cols - 1) * col_gap
        sx = SAFE_PADDING + (W - SAFE_PADDING*2 - stats_w)//2
        sy = ny + name_h + 24

        for i in range(cols):
            cx = sx + i * (col_w + col_gap)
            v = vals[i] if i < len(vals) else ""
            vw = stat_big.getbbox(v)[2]
            d.text((cx + (col_w - vw)//2, sy), v, font=stat_big, fill=(255,255,255,255))
            lab = labs[i] if i < len(labs) else ""
            lw = stat_small.getbbox(lab)[2]
            vh = stat_big.getbbox(v)[3] - stat_big.getbbox(v)[1]
            d.text((cx + (col_w - lw)//2, sy + vh + 8), lab, font=stat_small, fill=(255,255,255,230))

    _place_headshot(im, head_img, x_right=W - SAFE_PADDING, y_bottom=H - SAFE_PADDING, max_w=520, max_h=520)
    return _save_png(im)

# ------------------------------------------------------------
# Резервный рендер
# ------------------------------------------------------------
def render_card_drN(*args, **kwargs) -> bytes:
    try:
        return render_card("single", *args, **kwargs)
    except Exception:
        im = Image.new("RGBA", (10, 10), (0,0,0,0))
        return _save_png(im)
