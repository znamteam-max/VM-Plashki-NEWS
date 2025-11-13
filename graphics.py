# graphics.py — 1920x1080, bottom-left cards, Pillow 10 safe
from __future__ import annotations
import os, io, math
from typing import List, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

# -------------------- constants --------------------
CANVAS_W, CANVAS_H = 1920, 1080
BAR_H = 220
BAR_W_SINGLE = 1180         # ширина основной плашки для single/special
BAR_GAP = 10                # зазор между основной и доп.плашкой
BAR_W_RIGHT = 420           # ширина правой колонки для /cards
BAR_W_DUO = 900             # ширина одной плашки в /card2
PAD = 36                    # внутренние отступы

HEAD_D = 220                # диаметр головы
HEAD_SHIFT_LEFT = 36        # сдвиг головы левее

# Цвета градиентов (левая — оранж, правая — тёмная)
GRAD_L = ("#FF8A00", "#FFC933")
GRAD_DARK = ("#151515", "#272727")
GRAD_BAD = ("#5B3A29", "#2E2018")

# Шрифты (значения по умолчанию; загрузчик найдёт их в доступных папках)
FONT_MONTS_BOLD = "Montserrat-Bold.ttf"
FONT_MONTS_SEMI = "Montserrat-SemiBold.ttf"
FONT_EXO_BOLD = "Exo2-Bold.ttf"

# Размеры шрифтов
NAME_SIZE = 68             # имя (уменьшено)
STAT_VALUE_SIZE = 56       # значения статистики (меньше имени)
STAT_LABEL_SIZE = 28       # подписи
INFO_SIZE = 28             # правый столбец /cards
POOP_SCALE = 2.0           # «💩» в 2 раза крупнее имени для cardbad

# -------------------- fonts --------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIRS = [
    os.getenv("FONTS_DIR"),
    os.path.join(_ROOT, "api", "fonts"),
    os.path.join(_ROOT, "assets", "fonts"),
    os.path.join(_ROOT, "fonts"),
    "/var/task/api/fonts",
    "/var/task/assets/fonts",
    "/var/task/fonts",
]

_font_cache = {}
def _find_font_path(filename: str) -> Optional[str]:
    if not filename:
        return None
    if os.path.isabs(filename) and os.path.exists(filename):
        return filename
    for d in _SEARCH_DIRS:
        if not d: 
            continue
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return None

def _truetype(filename: str, size: int) -> ImageFont.FreeTypeFont:
    key = (filename, size)
    if key in _font_cache:
        return _font_cache[key]
    p = _find_font_path(filename)
    try:
        if p:
            f = ImageFont.truetype(p, size)
        else:
            # fallback — не падаем, но вид будет проще
            f = ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f

def font_name(sz: int) -> ImageFont.FreeTypeFont:
    return _truetype(FONT_MONTS_BOLD, sz)

def font_stat_value(sz: int) -> ImageFont.FreeTypeFont:
    # если есть EXO — используем, иначе Montserrat Semi
    p = _find_font_path(FONT_EXO_BOLD)
    if p:
        return _truetype(FONT_EXO_BOLD, sz)
    return _truetype(FONT_MONTS_SEMI, sz)

def font_stat_label(sz: int) -> ImageFont.FreeTypeFont:
    return _truetype(FONT_MONTS_SEMI, sz)

# --- text measuring helper (Pillow 10 compatible)
def _ts(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return (max(0, r - l), max(0, b - t))
    except Exception:
        try:
            return font.getsize(text)
        except Exception:
            s = getattr(font, "size", 16)
            return (len(text) * s // 2, s)

# -------------------- utils --------------------
def _to_rgba(im: Image.Image) -> Image.Image:
    return im.convert("RGBA") if im.mode != "RGBA" else im

def _save_png_bytes(im: Image.Image) -> bytes:
    bio = io.BytesIO()
    im.save(bio, format="PNG", optimize=True)
    return bio.getvalue()

def _round_rect(size: Tuple[int,int], r: int, fill: Tuple[int,int,int,int]) -> Image.Image:
    w, h = size
    base = Image.new("L", size, 0)
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((0,0,w,h), radius=r, fill=255)
    out = Image.new("RGBA", size, (0,0,0,0))
    fill_img = Image.new("RGBA", size, fill)
    out.paste(fill_img, (0,0), base)
    return out

def _linear_gradient(size: Tuple[int,int], c1: str, c2: str, horizontal=True) -> Image.Image:
    w, h = size
    base = Image.new("RGBA", (w, h), c1)
    top  = Image.new("RGBA", (w, h), c2)
    mask = Image.new("L", (w, h))
    md = ImageDraw.Draw(mask)
    if horizontal:
        for x in range(w):
            md.line([(x,0),(x,h)], fill=int(255*x/(w-1)) if w>1 else 255)
    else:
        for y in range(h):
            md.line([(0,y),(w,y)], fill=int(255*y/(h-1)) if h>1 else 255)
    base.paste(top, (0,0), mask)
    return base

def _paste(im: Image.Image, part: Image.Image, xy: Tuple[int,int]):
    im.alpha_composite(part, xy)

def _circle_crop(im: Image.Image, d: int) -> Image.Image:
    im = _to_rgba(im)
    im = ImageOps.fit(im, (d, d), method=Image.LANCZOS, centering=(0.5, 0.5))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0,0,d,d), fill=255)
    out = Image.new("RGBA", (d, d), (0,0,0,0))
    out.paste(im, (0,0), mask)
    return out

def _stats_layout(draw: ImageDraw.ImageDraw, stats: List[Tuple[str,str]]):
    """Вернёт список (value_text, label_text, value_w, label_w)."""
    out = []
    f_val = font_stat_value(STAT_VALUE_SIZE)
    f_lbl = font_stat_label(STAT_LABEL_SIZE)
    for v, l in (stats or [])[:3]:
        v = str(v); l = str(l)
        vw, vh = _ts(draw, v, f_val)
        lw, lh = _ts(draw, l, f_lbl)
        out.append((v, l, vw, lw))
    return out

def _ensure_stats_smaller_than_name():
    # гарантируем иерархию размеров (если кто-то поменяет константы)
    global STAT_VALUE_SIZE
    if STAT_VALUE_SIZE >= NAME_SIZE:
        STAT_VALUE_SIZE = max(24, NAME_SIZE - 8)

# -------------------- draw primitives --------------------
def _draw_main_bar(canvas: Image.Image, x: int, y: int, w: int, h: int, grad: Tuple[str,str]=GRAD_L):
    grad_img = _linear_gradient((w, h), grad[0], grad[1], horizontal=True)
    _paste(canvas, grad_img, (x, y))

def _draw_dark_bar(canvas: Image.Image, x: int, y: int, w: int, h: int, grad: Tuple[str,str]=GRAD_DARK, radius: int=0):
    if radius>0:
        rr = _round_rect((w, h), r=radius, fill=(0,0,0,0))
        dark = _linear_gradient((w, h), grad[0], grad[1], horizontal=True)
        rr = Image.alpha_composite(rr, dark)
        _paste(canvas, rr, (x, y))
    else:
        dark = _linear_gradient((w, h), grad[0], grad[1], horizontal=True)
        _paste(canvas, dark, (x, y))

# -------------------- single card --------------------
def _render_single_on(canvas: Image.Image, name_text: str, team_logo_img: Optional[Image.Image],
                      colors: Tuple[str,str,str], head_img: Image.Image,
                      stats: List[Tuple[str,str]], x0: int, y0: int, w: int, h: int,
                      grad: Tuple[str,str]=GRAD_L):
    draw = ImageDraw.Draw(canvas)

    # фон-плашка
    _draw_main_bar(canvas, x0, y0, w, h, grad)

    # команда (логотип)
    if team_logo_img:
        logo_d = 108
        logo = _circle_crop(team_logo_img, logo_d)
        lx = x0 + PAD + HEAD_D - 70  # чуток перекрываем голову
        ly = y0 + (h - logo_d)//2
        _paste(canvas, logo, (lx, ly))

    # голова
    head = _circle_crop(head_img, HEAD_D)
    hx = x0 - HEAD_SHIFT_LEFT
    hy = CANVAS_H - HEAD_D  # по нижней границе
    _paste(canvas, head, (hx, hy))

    # имя
    _ensure_stats_smaller_than_name()
    f_name = font_name(NAME_SIZE)
    name_x = x0 + PAD + HEAD_D + 40 - HEAD_SHIFT_LEFT
    name_y = y0 + 28
    draw.text((name_x, name_y), name_text, fill="white", font=f_name)

    # статы (до 3х)
    stat_items = _stats_layout(draw, stats)
    sx = name_x
    # отступ от имени: ширина имени + 40
    name_w, _ = _ts(draw, name_text, f_name)
    sx = name_x + name_w + 40

    col_w = 210
    f_val = font_stat_value(STAT_VALUE_SIZE)
    f_lbl = font_stat_label(STAT_LABEL_SIZE)
    sy_val = name_y + 4
    sy_lbl = sy_val + STAT_VALUE_SIZE + 6

    for i, (v, l, vw, lw) in enumerate(stat_items):
        cx = sx + i * col_w
        draw.text((cx, sy_val), v, fill="white", font=f_val)
        draw.text((cx, sy_lbl), l.upper(), fill="white", font=f_lbl)

# -------------------- /card --------------------
def render_card(mode: str, name_text: str, _unused: str,
                team_logo_img: Optional[Image.Image],
                colors: Tuple[str,str,str], head_img: Image.Image,
                stats: List[Tuple[str,str]]) -> bytes:
    """
    mode: 'single' (игнорируем, для совместимости)
    Возвращает PNG bytes 1920x1080.
    """
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0 = 0
    y0 = CANVAS_H - BAR_H
    w  = BAR_W_SINGLE
    h  = BAR_H

    _render_single_on(canvas, name_text, team_logo_img, colors, head_img, stats, x0, y0, w, h, GRAD_L)
    return _save_png_bytes(canvas)

# -------------------- /cardbad --------------------
def render_card_bad(name_text: str, head_img: Image.Image,
                    stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None) -> bytes:
    """
    Темная плашка + «💩» после имени, иконка в 2 раза крупнее имени.
    """
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0 = 0
    y0 = CANVAS_H - BAR_H
    w  = BAR_W_SINGLE
    h  = BAR_H

    # фон — коричневый градиент
    _draw_main_bar(canvas, x0, y0, w, h, GRAD_BAD)

    # голова + логотип по тем же правилам
    draw = ImageDraw.Draw(canvas)
    if team_logo_img:
        logo_d = 108
        logo = _circle_crop(team_logo_img, logo_d)
        lx = x0 + PAD + HEAD_D - 70
        ly = y0 + (h - logo_d)//2
        _paste(canvas, logo, (lx, ly))

    head = _circle_crop(head_img, HEAD_D)
    hx = x0 - HEAD_SHIFT_LEFT
    hy = CANVAS_H - HEAD_D
    _paste(canvas, head, (hx, hy))

    # имя + 💩
    _ensure_stats_smaller_than_name()
    fn = font_name(NAME_SIZE)
    name_x = x0 + PAD + HEAD_D + 40 - HEAD_SHIFT_LEFT
    name_y = y0 + 28
    draw.text((name_x, name_y), name_text, fill="white", font=fn)

    # «💩» после имени, вдвое крупнее
    poop = "💩"
    poop_font = font_name(int(NAME_SIZE * POOP_SCALE))
    name_w, _ = _ts(draw, name_text, fn)
    poop_x = name_x + name_w + 18
    poop_y = name_y - int(NAME_SIZE * 0.45)  # чуть поднять, чтобы базовая линия не сползала
    # Если emoji не поддерживается шрифтом — всё равно попробуем отрисовать
    draw.text((poop_x, poop_y), poop, fill="#FFB100", font=poop_font)

    # статы
    stat_items = _stats_layout(draw, stats)
    sx = poop_x + _ts(draw, poop, poop_font)[0] + 28
    col_w = 210
    f_val = font_stat_value(STAT_VALUE_SIZE)
    f_lbl = font_stat_label(STAT_LABEL_SIZE)
    sy_val = name_y + 4
    sy_lbl = sy_val + STAT_VALUE_SIZE + 6
    for i, (v, l, vw, lw) in enumerate(stat_items):
        cx = sx + i * col_w
        draw.text((cx, sy_val), v, fill="white", font=f_val)
        draw.text((cx, sy_lbl), l.upper(), fill="white", font=f_lbl)

    return _save_png_bytes(canvas)

# -------------------- /card2 --------------------
def render_card2(name1: str, logo1_img: Optional[Image.Image], colors1: Tuple[str,str,str], head1_img: Image.Image, stats1: List[Tuple[str,str]],
                 name2: str, logo2_img: Optional[Image.Image], colors2: Tuple[str,str,str], head2_img: Image.Image, stats2: List[Tuple[str,str]]) -> bytes:
    """
    Две отдельные плашки слева направо, между ними 20 px.
    """
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    y0 = CANVAS_H - BAR_H
    w  = BAR_W_DUO
    h  = BAR_H
    gap = 20

    # левая
    _render_single_on(canvas, name1, logo1_img, colors1, head1_img, stats1, 0, y0, w, h, GRAD_L)
    # правая
    x2 = w + gap
    _render_single_on(canvas, name2, logo2_img, colors2, head2_img, stats2, x2, y0, w, h, GRAD_L)

    return _save_png_bytes(canvas)

# -------------------- /cards (special) --------------------
def render_card_special(name_text: str, team_logo_img: Optional[Image.Image],
                        colors: Tuple[str,str,str], head_img: Image.Image,
                        stats: List[Tuple[str,str]], info_text: str) -> bytes:
    """
    Основная плашка слева + отдельная правая колонка (10 px справа), с тёмным градиентом.
    """
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    y0 = CANVAS_H - BAR_H
    x0 = 0
    w_left = BAR_W_SINGLE
    h = BAR_H

    # основная слева
    _render_single_on(canvas, name_text, team_logo_img, colors, head_img, stats, x0, y0, w_left, h, GRAD_L)

    # правая колонка отдельно
    x_right = x0 + w_left + BAR_GAP
    _draw_dark_bar(canvas, x_right, y0, BAR_W_RIGHT, h, GRAD_DARK, radius=0)

    # текст в правой колонке
    draw = ImageDraw.Draw(canvas)
    f_info = font_stat_label(INFO_SIZE)
    tx = x_right + 42
    ty = y0 + 24

    # делаем простой перенос
    def wrap_text(t: str, max_w: int) -> List[str]:
        words = (t or "").split()
        line = ""
        out = []
        for w in words:
            test = (line + " " + w).strip()
            if _ts(draw, test, f_info)[0] <= max_w:
                line = test
            else:
                if line:
                    out.append(line)
                line = w
        if line:
            out.append(line)
        return out

    max_w = BAR_W_RIGHT - 2*42
    # маркер и строка
    bullet = "★"
    bw, bh = _ts(draw, bullet, f_info)
    draw.text((tx, ty), bullet, fill="#FFB100", font=f_info)
    lines = wrap_text(info_text, max_w - (bw + 16))
    if not lines:
        lines = [""]

    # первая строка — рядом со звёздочкой
    draw.text((tx + bw + 16, ty), lines[0], fill="white", font=f_info)
    # остальные — ниже
    for i, line in enumerate(lines[1:], start=1):
        draw.text((tx, ty + i* (INFO_SIZE + 6)), line, fill="white", font=f_info)

    return _save_png_bytes(canvas)
