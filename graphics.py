# graphics.py
# Рендер плашек: render_card, render_card2, render_card_bad, render_card_special
# Холст 1920x1080 RGBA (прозрачный). Все размеры/координаты -> int.
# Входы соответствуют api/telegram.py:
#   render_card(name_ru, team_text_unused, logo_img, colors_tuple, head_img, stats_list)
#   render_card2(ruA, teamA_unused, logoA_img, colorsA, headA, statsA,
#                ruB, teamB_unused, logoB_img, colorsB, headB, statsB)
#   render_card_bad(ru, team_unused, logo_unused, colors_unused, head, stats)
#   render_card_special(ru, team_unused, logo_img, colors, head, stats, right_text)

from __future__ import annotations
import os, io, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------- Канвас и константы ----------
CANVAS_W, CANVAS_H = 1920, 1080
MARGIN = 40

# Высоты панелей
CARD_H   = 260      # одиночные плашки (card, cardbad, левая часть cards)
CARDS_RH = 220      # правая доп-плашка у cards
CARD2_H  = 280      # двойная плашка (полная ширина)

# Скругления
RADIUS_RIGHT = 28
RADIUS_BOTH  = 28

# Цвета
BROWN_BAD = "#5A3A2C"   # фикс для cardbad
WHITE = "#FFFFFF"
BLACK = "#000000"
BLACK_ALPHA_160 = (0, 0, 0, 160)

# Сдвиг лого в белом кружке (вверх-влево)
LOGO_OFFSET = (-30, -30)
LOGO_CIRCLE_DIAM = 132  # диаметр белого кружка под лого

# HEADSHOT
HEAD_W = 280   # ширина фото (подгоняется по высоте панели)
HEAD_PAD = 16

# Иконка «какашка» (для cardbad) — внешний PNG, иначе emoji
POOP_ICON_PATH = os.getenv("POOP_ICON_PATH", "").strip()

# Кеш шрифтов
_FONT_CACHE = {}

def _try_font(names: List[str], size: int) -> ImageFont.FreeTypeFont:
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    # Дефолтные системные семейства (часто доступны в Linux)
    fam_bold = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf", "Arial-Bold.ttf", "Arialbd.ttf",
    ]
    fam_reg = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "DejaVuSans.ttf",
        "Arial.ttf",
    ]
    f = _try_font(fam_bold if bold else fam_reg, size)
    _FONT_CACHE[key] = f
    return f

def _text_size(text: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
    if not text:
        return (0, 0)
    # bbox более точен, чем .getsize
    try:
        bbox = font.getbbox(text)
        w = int(bbox[2] - bbox[0])
        h = int(bbox[3] - bbox[1])
        return (w, h)
    except Exception:
        return font.getsize(text)

def _safe_color(c) -> Tuple[int,int,int,int]:
    # colors может быть ('#112233', '#0A0A0A', '#112233') — берём 0-й
    if isinstance(c, (list, tuple)):
        c = c[0]
    if isinstance(c, str):
        c = c.strip()
        if c.startswith("#"):
            c = c[1:]
        if len(c) == 3:
            r, g, b = [int(x*2, 16) for x in c]
        else:
            r, g, b = int(c[0:2],16), int(c[2:4],16), int(c[4:6],16)
        return (r, g, b, 255)
    # если уже RGBA
    if isinstance(c, tuple):
        if len(c) == 3:
            return (c[0], c[1], c[2], 255)
        if len(c) == 4:
            return (int(c[0]), int(c[1]), int(c[2]), int(c[3]))
    # дефолт
    return (22, 28, 36, 255)

def _rounded_mask(w: int, h: int, r_tl: int, r_tr: int, r_br: int, r_bl: int) -> Image.Image:
    w, h = int(w), int(h)
    r_tl, r_tr, r_br, r_bl = map(int, (r_tl, r_tr, r_br, r_bl))
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)

    def corner(cx, cy, r, start, end):
        if r <= 0: return
        d.pieslice([cx-r, cy-r, cx+r, cy+r], start, end, fill=255)

    # центр
    d.rectangle([r_tl, 0, w - r_tr, h], fill=255)
    d.rectangle([0, r_tl, w, h - r_bl], fill=255)

    # углы
    corner(r_tl, r_tl, r_tl, 180, 270)  # tl
    corner(w - r_tr, r_tr, r_tr, 270, 360)  # tr
    corner(w - r_br, h - r_br, r_br, 0, 90)  # br
    corner(r_bl, h - r_bl, r_bl, 90, 180)  # bl
    return mask

def _panel(base: Image.Image, x: int, y: int, w: int, h: int, color, corners: Tuple[int,int,int,int]):
    w, h = int(w), int(h)
    x, y = int(x), int(y)
    fill = _safe_color(color)
    panel = Image.new("RGBA", (w, h), fill)
    tl, tr, br, bl = corners
    if any((tl, tr, br, bl)):
        mask = _rounded_mask(w, h, tl, tr, br, bl)
        base.alpha_composite(panel, (x, y), mask)
    else:
        base.alpha_composite(panel, (x, y))

def _draw_logo_circle(base: Image.Image, logo_img: Optional[Image.Image], cx: int, cy: int, diam: int):
    diam = int(diam)
    # белый круг
    circ = Image.new("RGBA", (diam, diam), (0,0,0,0))
    d = ImageDraw.Draw(circ)
    d.ellipse([0,0,diam,diam], fill=WHITE)
    base.alpha_composite(circ, (int(cx - diam/2), int(cy - diam/2)))

    if logo_img is None:
        return
    # логотип внутрь
    logo = logo_img.convert("RGBA")
    # паддинг 12%
    pad = int(diam * 0.12)
    box = (pad, pad, diam - pad, diam - pad)
    tw = box[2] - box[0]
    th = box[3] - box[1]
    # вписываем с сохранением пропорций
    lw, lh = logo.size
    scale = min(tw / lw, th / lh)
    nw, nh = max(1, int(lw * scale)), max(1, int(lh * scale))
    logo = logo.resize((nw, nh), Image.LANCZOS)
    # центрируем в белом круге
    ox = int(cx - nw/2)
    oy = int(cy - nh/2)
    base.alpha_composite(logo, (ox, oy))

def _paste_headshot(base: Image.Image, img: Image.Image, x: int, y_bottom: int, target_h: int) -> int:
    # вписать headshot по высоте target_h + небольшой выход
    if img is None:
        return 0
    head = img.convert("RGBA")
    w, h = head.size
    scale = (target_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    head = head.resize((nw, nh), Image.LANCZOS)
    # нижним краем к y_bottom
    pos = (int(x), int(y_bottom - nh))
    base.alpha_composite(head, pos)
    return nw

def _draw_centered_text(draw: ImageDraw.Draw, x: int, y_center: int, text: str, font: ImageFont.ImageFont, fill=WHITE) -> Tuple[int,int,int,int]:
    w, h = _text_size(text, font)
    tx = int(x)
    ty = int(y_center - h/2)
    draw.text((tx, ty), text, font=font, fill=fill)
    return (tx, ty, tx + w, ty + h)

def _wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.ImageFont, max_w: int) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    cur = []
    for w in words:
        test = (" ".join(cur+[w])).strip()
        tw,_ = _text_size(test, font)
        if tw <= max_w or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines

def _fit_name_and_stats(draw: ImageDraw.Draw, name: str, stats: List[Tuple[str,str]],
                        max_name_w: int, base_name_size: int,
                        stats_to_name_delta: int) -> Tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """Уменьшаем имя/статы пропорционально, чтобы имя влезало.
       Возвращает (font_name, font_value) — label потом берём чуть меньше."""
    name_size = int(base_name_size)
    val_size  = int(max(8, name_size - stats_to_name_delta))
    while name_size > 14:
        f_name = _font(name_size, bold=True)
        w_name,_ = _text_size(name, f_name)
        if w_name <= max_name_w:
            break
        name_size -= 2
        val_size  = max(8, name_size - stats_to_name_delta)
    return _font(name_size, bold=True), _font(val_size, bold=True)

def _draw_stats_row(draw: ImageDraw.Draw, x: int, y_center: int, stats: List[Tuple[str,str]],
                    value_font: ImageFont.ImageFont, label_font: ImageFont.ImageFont,
                    max_w: int, gap: int = 32) -> int:
    """Рисуем горизонтально: VALUE (крупно) над LABEL (мелко). Возвращает использованную ширину."""
    # подготовим размеры
    pairs = []
    for (val, label) in stats:
        vw, vh = _text_size(val, value_font)
        lw, lh = _text_size(label, label_font)
        w = max(vw, lw)
        h = vh + 6 + lh
        pairs.append((val, label, vw, vh, lw, lh, w, h))
    if not pairs:
        return 0

    total_w = sum(p[6] for p in pairs) + gap*(len(pairs)-1)
    # если не влезает — уменьшим value/label шрифты до тех пор, пока total_w <= max_w
    if total_w > max_w:
        # простое равномерное уменьшение
        vsize = value_font.size
        lsize = label_font.size
        while total_w > max_w and vsize > 10:
            vsize -= 2
            lsize = max(10, vsize - 10)
            value_font = _font(vsize, bold=True)
            label_font = _font(lsize, bold=False)
            pairs = []
            for (val, label) in stats:
                vw, vh = _text_size(val, value_font)
                lw, lh = _text_size(label, label_font)
                w = max(vw, lw)
                h = vh + 6 + lh
                pairs.append((val, label, vw, vh, lw, lh, w, h))
            total_w = sum(p[6] for p in pairs) + gap*(len(pairs)-1)

    # рисуем по центру от x
    cur_x = int(x)
    y_top = int(y_center - max(p[7] for p in pairs)/2)
    for val, label, vw, vh, lw, lh, w, h in pairs:
        # value по центру колонки
        vx = cur_x + (w - vw)//2
        vy = y_top
        draw.text((vx, vy), val, font=value_font, fill=WHITE)
        # label снизу
        lx = cur_x + (w - lw)//2
        ly = vy + vh + 6
        draw.text((lx, ly), label, font=label_font, fill=WHITE)
        cur_x += w + gap
    return int(total_w)

# ---------- CARD (одиночная стандартная) ----------
def render_card(name_ru: str, _team_unused, logo_img: Optional[Image.Image],
                colors, head_img: Optional[Image.Image], stats: List[Tuple[str,str]]) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    # геометрия
    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    # ширину основной панели делаем адаптивно: минимум 900, максимум 1320
    min_w, max_w = 900, 1320
    # прикидываем имя и статы, чтобы оценить нужную ширину
    name_font = _font(84, bold=True)
    name_w, _ = _text_size(name_ru, name_font)
    # базовая ширина под статы (до трёх пар комфортно)
    est_stats_w = 640 if stats else 0
    panel_w = max(min_w, min(max_w, name_w + 420 + est_stats_w))
    panel_w = int(panel_w)

    # панели и скругления: только справа
    corners = (0, RADIUS_RIGHT, RADIUS_RIGHT, 0)
    _panel(base, x, y, panel_w, CARD_H, colors, corners)

    # лого в белом круге
    cx = int(x + LOGO_CIRCLE_DIAM/2 + 30 + LOGO_OFFSET[0])
    cy = int(y + LOGO_CIRCLE_DIAM/2 + 30 + LOGO_OFFSET[1])
    _draw_logo_circle(base, logo_img, cx, cy, LOGO_CIRCLE_DIAM)

    # headshot (поверх панели)
    hs_x = x + HEAD_PAD
    hs_w = _paste_headshot(base, head_img, hs_x, y + CARD_H + 6, CARD_H + 24)

    # текстовая зона после headshot
    text_x = int(x + max(HEAD_W, hs_w) + 32)
    text_center_y = int(y + CARD_H/2)

    # динамический размер имени + статы
    f_name, f_val = _fit_name_and_stats(draw, name_ru, stats, panel_w - (text_x - x) - 40, 84, 10)
    f_label = _font(max(12, f_val.size - 16), bold=False)

    # имя
    _draw_centered_text(draw, text_x, text_center_y, name_ru, f_name)

    # статы справа от имени
    name_w_now, _ = _text_size(name_ru, f_name)
    stats_x = int(text_x + name_w_now + 40)
    stats_w = int(panel_w - (stats_x - x) - 40)
    if stats and stats_w > 60:
        _draw_stats_row(draw, stats_x, text_center_y, stats, f_val, f_label, stats_w, gap=36)

    return base

# ---------- CARD BAD (коричневый + 💩) ----------
def render_card_bad(name_ru: str, _team_unused, _logo_unused,
                    _colors_unused, head_img: Optional[Image.Image], stats: List[Tuple[str,str]]) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    panel_w = 1160  # фикс пошире под 💩
    corners = (0, RADIUS_RIGHT, RADIUS_RIGHT, 0)
    _panel(base, x, y, panel_w, CARD_H, BROWN_BAD, corners)

    # headshot
    hs_x = x + HEAD_PAD
    hs_w = _paste_headshot(base, head_img, hs_x, y + CARD_H + 6, CARD_H + 24)

    text_x = int(x + max(HEAD_W, hs_w) + 32)
    text_center_y = int(y + CARD_H/2)

    # Имя + 💩
    f_name = _font(84, bold=True)
    name_w, name_h = _text_size(name_ru, f_name)
    _draw_centered_text(draw, text_x, text_center_y, name_ru, f_name)

    poop_x = int(text_x + name_w + 18)
    # PNG иконка если есть, иначе emoji
    if POOP_ICON_PATH and os.path.exists(POOP_ICON_PATH):
        try:
            poop = Image.open(POOP_ICON_PATH).convert("RGBA")
            # высота иконки на уровень baseline имени (чуть ниже центра)
            target_h = int(name_h * 0.95)
            scale = target_h / poop.height
            poop = poop.resize((max(1, int(poop.width*scale)), target_h), Image.LANCZOS)
            # нижняя граница на линии низа имени
            _, ty, _, by = _draw_centered_text(draw, text_x, text_center_y, name_ru, f_name)
            base.alpha_composite(poop, (poop_x, int(by - poop.height)))
        except Exception:
            draw.text((poop_x, text_center_y - name_h//2), "💩", font=_font(int(name_h*0.9), bold=False), fill=WHITE)
    else:
        draw.text((poop_x, text_center_y - name_h//2), "💩", font=_font(int(name_h*0.9), bold=False), fill=WHITE)

    # Статы
    stats_x = int(poop_x + 64)
    stats_w = int(panel_w - (stats_x - x) - 40)
    if stats and stats_w > 60:
        f_val   = _font(70, bold=True)
        f_label = _font(52, bold=False)
        _draw_stats_row(draw, stats_x, text_center_y, stats, f_val, f_label, stats_w, gap=36)

    return base

# ---------- CARDS (с правой доп-плашкой) ----------
def render_card_special(name_ru: str, _team_unused, logo_img: Optional[Image.Image],
                        colors, head_img: Optional[Image.Image], stats: List[Tuple[str,str]],
                        right_text: str) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    # Левая основная панель
    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H

    # Ширина левой: 1140 по умолчанию
    left_w = 1140
    corners_left = (0, RADIUS_RIGHT, RADIUS_RIGHT, 0)
    _panel(base, x, y, left_w, CARD_H, colors, corners_left)

    # Белый круг с лого (+сдвиг)
    cx = int(x + LOGO_CIRCLE_DIAM/2 + 30 + LOGO_OFFSET[0])
    cy = int(y + LOGO_CIRCLE_DIAM/2 + 30 + LOGO_OFFSET[1])
    _draw_logo_circle(base, logo_img, cx, cy, LOGO_CIRCLE_DIAM)

    # headshot
    hs_x = x + HEAD_PAD
    hs_w = _paste_headshot(base, head_img, hs_x, y + CARD_H + 6, CARD_H + 24)

    text_x = int(x + max(HEAD_W, hs_w) + 32)
    text_center_y = int(y + CARD_H/2)

    # имя + статы (динамика)
    f_name, f_val = _fit_name_and_stats(draw, name_ru, stats, left_w - (text_x - x) - 40, 84, 10)
    f_label = _font(max(12, f_val.size - 16), bold=False)

    _draw_centered_text(draw, text_x, text_center_y, name_ru, f_name)

    name_w_now, _ = _text_size(name_ru, f_name)
    stats_x = int(text_x + name_w_now + 40)
    stats_w = int(left_w - (stats_x - x) - 40)
    if stats and stats_w > 60:
        _draw_stats_row(draw, stats_x, text_center_y, stats, f_val, f_label, stats_w, gap=36)

    # Правая доп-плашка (вдвое уже левой)
    right_w = max(420, int(left_w // 2))
    rx = int(x + left_w + 10)  # отступ 10px
    ry = int(CANVAS_H - MARGIN - CARDS_RH)
    corners_right = (RADIUS_BOTH, RADIUS_BOTH, RADIUS_BOTH, RADIUS_BOTH)
    _panel(base, rx, ry, right_w, CARDS_RH, (0,0,0,180), corners_right)  # полупрозрачная

    # текст в правой, со звёздочкой и переносами
    pad = 26
    tx = int(rx + pad)
    ty_center = int(ry + CARDS_RH/2)
    # шрифт правого блока
    f_right = _font(38, bold=True)
    # добавим обязательную пустую строку в конец
    txt = ("⭐ " + (right_text or "").strip()).strip()
    max_w = int(right_w - pad*2)
    lines = _wrap_text(draw, txt, f_right, max_w)
    if not lines:
        lines = ["⭐"]  # хотя бы что-то
    # "+ пустая строка"
    lines.append("")

    # рисуем по центру блока
    line_h = max(32, int(f_right.size * 1.18))
    total_h = line_h * len(lines)
    top = int(ty_center - total_h/2)
    for i,ln in enumerate(lines):
        lw,_ = _text_size(ln, f_right)
        draw.text((tx, top + i*line_h), ln, font=f_right, fill=WHITE)

    return base

# ---------- CARD2 (двойная, во всю ширину снизу, без скруглений) ----------
def render_card2(ruA: str, _teamA_unused, logoA_img: Optional[Image.Image], colorsA,
                 headA: Optional[Image.Image], statsA: List[Tuple[str,str]],
                 ruB: str, _teamB_unused, logoB_img: Optional[Image.Image], colorsB,
                 headB: Optional[Image.Image], statsB: List[Tuple[str,str]]) -> Image.Image:

    base = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    y = CANVAS_H - MARGIN - CARD2_H
    x = 0  # растягиваем на всю ширину
    w = CANVAS_W
    h = CARD2_H

    # без скруглений: две половины
    half_w = int(w // 2)

    # подложки
    _panel(base, x, y, half_w, h, colorsA, (0,0,0,0))
    _panel(base, x + half_w, y, half_w, h, colorsB, (0,0,0,0))

    # лого кружки (по одному на каждую сторону)
    cxA = int(x + half_w*0.03 + LOGO_CIRCLE_DIAM/2 + LOGO_OFFSET[0])
    cyA = int(y + LOGO_CIRCLE_DIAM/2 + 24 + LOGO_OFFSET[1])
    _draw_logo_circle(base, logoA_img, cxA, cyA, LOGO_CIRCLE_DIAM)

    cxB = int(x + half_w + half_w*0.03 + LOGO_CIRCLE_DIAM/2 + LOGO_OFFSET[0])
    cyB = int(y + LOGO_CIRCLE_DIAM/2 + 24 + LOGO_OFFSET[1])
    _draw_logo_circle(base, logoB_img, cxB, cyB, LOGO_CIRCLE_DIAM)

    # headshots — у левого/правого краёв каждой половины
    hsA_x = int(x + half_w*0.02)
    hsB_x = int(x + w - half_w*0.02 - HEAD_W)
    _paste_headshot(base, headA, hsA_x, y + h + 6, h + 30)
    _paste_headshot(base, headB, hsB_x, y + h + 6, h + 30)

    # Текстовые зоны
    pad = 32
    # Левая
    left_text_x  = int(x + half_w*0.12 + pad)
    left_text_w  = int(half_w - (left_text_x - x) - pad)
    left_center_y = int(y + h/2)

    # Правая
    right_text_x  = int(x + half_w + half_w*0.12 + pad)
    right_text_w  = int(half_w - (right_text_x - (x + half_w)) - pad)
    right_center_y = int(y + h/2)

    # Имя на 2pt больше, чем цифры статистики
    # Подбор динамики под ширину текста
    f_nameA, f_valA = _fit_name_and_stats(draw, ruA, statsA, left_text_w, 88, 2)
    # гарантия разницы 2pt (если вдруг _fit вернул одинаковые)
    if f_valA.size > f_nameA.size - 2:
        f_valA = _font(max(10, f_nameA.size - 2), bold=True)
    f_labA = _font(max(10, f_valA.size - 12), bold=False)

    f_nameB, f_valB = _fit_name_and_stats(draw, ruB, statsB, right_text_w, 88, 2)
    if f_valB.size > f_nameB.size - 2:
        f_valB = _font(max(10, f_nameB.size - 2), bold=True)
    f_labB = _font(max(10, f_valB.size - 12), bold=False)

    # Имя A
    _draw_centered_text(draw, left_text_x, left_center_y, ruA, f_nameA)
    nameA_w,_ = _text_size(ruA, f_nameA)
    statsA_x = int(left_text_x + nameA_w + 36)
    statsA_w = int(left_text_w - nameA_w - 36)
    if statsA and statsA_w > 60:
        _draw_stats_row(draw, statsA_x, left_center_y, statsA, f_valA, f_labA, statsA_w, gap=28)

    # Имя B
    _draw_centered_text(draw, right_text_x, right_center_y, ruB, f_nameB)
    nameB_w,_ = _text_size(ruB, f_nameB)
    statsB_x = int(right_text_x + nameB_w + 36)
    statsB_w = int(right_text_w - nameB_w - 36)
    if statsB and statsB_w > 60:
        _draw_stats_row(draw, statsB_x, right_center_y, statsB, f_valB, f_labB, statsB_w, gap=28)

    return base
