# graphics.py
# Рендер плашек в фиксированную канву 1920x1080 RGBA.
# card / cardbad / cards — левый нижний угол.
# card2 — на всю ширину экрана, снизу.
from __future__ import annotations
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os, io, textwrap

CANVAS_W, CANVAS_H = 1920, 1080
MARGIN = 60

# Размеры блоков
CARD_H = 260
CARD_W = 1200
CARDS_RIGHT_W = 520       # правый блок у cards
CARD2_H = 300

HEAD_SIZE = 360           # высота портрета в одиночных карточках
LOGO_RING = 92            # диаметр белого круга под логотип
LOGO_INNER = 68           # логотип внутри круга

NAME_SIZE = 72            # имя
STATS_VAL = NAME_SIZE - 2 # требование: стата на 2 меньше имени
STATS_LAB = 28            # подпись показателя
RIGHT_TEXT = 40           # правый текст у cards

WHITE = (255,255,255,255)
BLACK = (0,0,0,255)
TRANSPARENT = (0,0,0,0)

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Надежный фолбек на DejaVu (есть в linux образах). Если нет — default.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

FONT_NAME  = _load_font(NAME_SIZE, bold=True)
FONT_STATV = _load_font(STATS_VAL, bold=True)
FONT_STATL = _load_font(STATS_LAB, bold=False)
FONT_RIGHT = _load_font(RIGHT_TEXT, bold=True)

def _safe_color(c: Tuple[str,str,str]) -> Tuple[int,int,int]:
    def _x(x):
        if isinstance(x, str):
            x = x.strip()
            if x.startswith("#"):
                x = x.lstrip("#")
                if len(x) == 6:
                    return tuple(int(x[i:i+2],16) for i in (0,2,4))
                if len(x) == 3:
                    q = tuple(int(x[i]*2,16) for i in range(3))
                    return q
            try:
                return tuple(int(v) for v in x.split(","))
            except: return (10,42,74)
        if isinstance(x, (tuple,list)) and len(x)>=3:
            return (int(x[0]), int(x[1]), int(x[2]))
        return (10,42,74)
    p = _x(c[0])
    return p

def _canvas() -> Image.Image:
    return Image.new("RGBA", (CANVAS_W, CANVAS_H), TRANSPARENT)

def _place_head(base: Image.Image, head: Image.Image, x: int, y_bottom: int, h: int) -> None:
    # центр-обрезка головы под квадрат, ресайз под высоту h, приклеиваем так,
    # чтобы нижняя граница совпала с y_bottom
    img = head.copy()
    w0, h0 = img.size
    side = min(w0, h0)
    left = (w0 - side)//2
    top  = (h0 - side)//2
    img = img.crop((left, top, left+side, top+side)).resize((h, h), Image.LANCZOS)
    y = y_bottom - h
    base.alpha_composite(img, (x, y))

def _circle_with_logo(base: Image.Image, logo: Optional[Image.Image], cx: int, cy: int) -> None:
    # белый круг и логотип по центру
    ring = Image.new("RGBA", (LOGO_RING, LOGO_RING), TRANSPARENT)
    d = ImageDraw.Draw(ring)
    d.ellipse((0,0,LOGO_RING,LOGO_RING), fill=WHITE)
    rx = cx - LOGO_RING//2
    ry = cy - LOGO_RING//2
    base.alpha_composite(ring, (rx, ry))
    if logo is not None:
        lg = logo.copy().convert("RGBA")
        lg = lg.resize((LOGO_INNER, LOGO_INNER), Image.LANCZOS)
        lx = cx - LOGO_INNER//2
        ly = cy - LOGO_INNER//2
        base.alpha_composite(lg, (lx, ly))

def _text(draw: ImageDraw.ImageDraw, xy: Tuple[int,int], text: str, font: ImageFont.FreeTypeFont, fill=WHITE):
    draw.text(xy, text, font=font, fill=fill)

def _draw_stats_row(draw: ImageDraw.ImageDraw, x: int, y: int, stats: List[Tuple[str,str]], max_w: int):
    # рисуем в одну строку: [val lab] [val lab] ...
    cur_x = x
    for (val, lab) in stats:
        val_w, val_h = draw.textbbox((0,0), val, font=FONT_STATV)[2:]
        lab_w, lab_h = draw.textbbox((0,0), lab, font=FONT_STATL)[2:]
        block_w = max(val_w, lab_w) + 40
        if cur_x + block_w > x + max_w:
            break
        draw.text((cur_x, y), val, font=FONT_STATV, fill=WHITE)
        draw.text((cur_x, y + val_h + 8), lab, font=FONT_STATL, fill=WHITE)
        cur_x += block_w

def _panel(base: Image.Image, x: int, y: int, w: int, h: int, color: Tuple[int,int,int], alpha: int=255):
    panel = Image.new("RGBA", (w, h), color + (alpha,))
    base.alpha_composite(panel, (x, y))

# ----------------- public API -----------------

def render_card(name_ru: str, subtitle: str, logo_img: Optional[Image.Image], colors: Tuple[str,str,str],
                head_img: Image.Image, stats: List[Tuple[str,str]]) -> Image.Image:
    base = _canvas()
    primary = _safe_color(colors)

    # геометрия
    x = MARGIN
    y = CANVAS_H - CARD_H - MARGIN

    # фон-плашка (без скруглений — стабильно)
    _panel(base, x, y, CARD_W, CARD_H, primary, alpha=230)

    # портрет слева крупно
    _place_head(base, head_img, x + 24, y + CARD_H, HEAD_SIZE)

    # логотип в белом круге
    _circle_with_logo(base, logo_img, x + 24 + HEAD_SIZE + 70, y + 54)

    draw = ImageDraw.Draw(base)

    # имя
    name_x = x + 24 + HEAD_SIZE + 140
    name_y = y + 24
    _text(draw, (name_x, name_y), name_ru, FONT_NAME, WHITE)

    # статистика
    stats_x = name_x
    stats_y = y + CARD_H - 24 - (STATS_VAL + STATS_LAB + 8)  # у нижнего края
    _draw_stats_row(draw, stats_x, stats_y, stats, max_w=CARD_W - (stats_x - x) - 24)

    return base

def render_card_bad(name_ru: str, subtitle: str, logo_img: Optional[Image.Image], colors: Tuple[str,str,str],
                    head_img: Image.Image, stats: List[Tuple[str,str]]) -> Image.Image:
    # используем коричневый независимо от colors
    base = _canvas()
    bad = (92, 58, 26)

    x = MARGIN
    y = CANVAS_H - CARD_H - MARGIN
    _panel(base, x, y, CARD_W, CARD_H, bad, alpha=240)

    _place_head(base, head_img, x + 24, y + CARD_H, HEAD_SIZE)
    _circle_with_logo(base, logo_img, x + 24 + HEAD_SIZE + 70, y + 54)

    draw = ImageDraw.Draw(base)
    name_x = x + 24 + HEAD_SIZE + 140
    name_y = y + 24
    _text(draw, (name_x, name_y), f"💩 {name_ru}", FONT_NAME, WHITE)

    stats_x = name_x
    stats_y = y + CARD_H - 24 - (STATS_VAL + STATS_LAB + 8)
    _draw_stats_row(draw, stats_x, stats_y, stats, max_w=CARD_W - (stats_x - x) - 24)
    return base

def render_card_special(name_ru: str, subtitle: str, logo_img: Optional[Image.Image], colors: Tuple[str,str,str],
                        head_img: Image.Image, stats: List[Tuple[str,str]], right_text: str) -> Image.Image:
    base = _canvas()
    primary = _safe_color(colors)

    # левая основная карта
    x = MARGIN
    y = CANVAS_H - CARD_H - MARGIN
    _panel(base, x, y, CARD_W, CARD_H, primary, alpha=230)
    _place_head(base, head_img, x + 24, y + CARD_H, HEAD_SIZE)
    _circle_with_logo(base, logo_img, x + 24 + HEAD_SIZE + 70, y + 54)

    draw = ImageDraw.Draw(base)
    name_x = x + 24 + HEAD_SIZE + 140
    name_y = y + 24
    _text(draw, (name_x, name_y), name_ru, FONT_NAME, WHITE)

    stats_x = name_x
    stats_y = y + CARD_H - 24 - (STATS_VAL + STATS_LAB + 8)
    _draw_stats_row(draw, stats_x, stats_y, stats, max_w=CARD_W - (stats_x - x) - 24)

    # правый блок с текстом
    rx = x + CARD_W + 20
    rw = CARDS_RIGHT_W
    ry = CANVAS_H - CARD_H - MARGIN
    _panel(base, rx, ry, rw, CARD_H, (20,20,20), alpha=210)

    # звезда + текст, переносы + пустая строка внизу
    rt = (right_text or "").rstrip() + "\n "
    wrap = textwrap.fill(rt, width=28, break_long_words=False, replace_whitespace=False)
    _text(draw, (rx + 24, ry + 24), "★", _load_font(RIGHT_TEXT+10, True), WHITE)
    _text(draw, (rx + 24 + 50, ry + 24), wrap, FONT_RIGHT, WHITE)

    return base

def render_card2(nameA: str, subA: str, logoA: Optional[Image.Image], colorsA: Tuple[str,str,str], headA: Image.Image, statsA: List[Tuple[str,str]],
                 nameB: str, subB: str, logoB: Optional[Image.Image], colorsB: Tuple[str,str,str], headB: Image.Image, statsB: List[Tuple[str,str]]) -> Image.Image:
    base = _canvas()
    # полоса по всей ширине снизу
    y = CANVAS_H - CARD2_H
    _panel(base, 0, y, CANVAS_W, CARD2_H, (0,0,0), alpha=160)

    # левая половина
    half_w = CANVAS_W // 2
    pxA = MARGIN
    _panel(base, pxA, y, half_w - MARGIN*1.5, CARD2_H, _safe_color(colorsA), alpha=220)
    # правая половина
    pxB = half_w + int(MARGIN*0.5)
    _panel(base, pxB, y, half_w - MARGIN*1.5, CARD2_H, _safe_color(colorsB), alpha=220)

    draw = ImageDraw.Draw(base)

    # A: портрет + логотип + имя + стата
    _place_head(base, headA, pxA + 24, y + CARD2_H, int(CARD2_H*0.9))
    _circle_with_logo(base, logoA, pxA + 24 + int(CARD2_H*0.9) + 60, y + 54)
    nameAx = pxA + 24 + int(CARD2_H*0.9) + 130
    nameAy = y + 24
    _text(draw, (nameAx, nameAy), nameA, FONT_NAME, WHITE)
    _draw_stats_row(draw, nameAx, y + CARD2_H - 24 - (STATS_VAL + STATS_LAB + 8), statsA, max_w=half_w - (nameAx - pxA) - int(MARGIN*1.5))

    # B: портрет справа зеркально
    _place_head(base, headB, pxB + 24, y + CARD2_H, int(CARD2_H*0.9))
    _circle_with_logo(base, logoB, pxB + 24 + int(CARD2_H*0.9) + 60, y + 54)
    nameBx = pxB + 24 + int(CARD2_H*0.9) + 130
    nameBy = y + 24
    _text(draw, (nameBx, nameBy), nameB, FONT_NAME, WHITE)
    _draw_stats_row(draw, nameBx, y + CARD2_H - 24 - (STATS_VAL + STATS_LAB + 8), statsB, max_w=half_w - (nameBx - pxB) - int(MARGIN*1.5))

    # центральный разделитель
    draw.rectangle((CANVAS_W//2 - 3, y + 16, CANVAS_W//2 + 3, y + CARD2_H - 16), fill=(255,255,255,120))
    return base
