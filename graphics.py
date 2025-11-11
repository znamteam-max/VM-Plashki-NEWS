# api/graphics.py
from __future__ import annotations
import os, io, math
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR  = os.path.join(ROOT_DIR, "fonts")
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
ICONS_DIR  = os.path.join(ASSETS_DIR, "icons")

# ---- ФОНТЫ (ТОЛЬКО те, что ты положил) ------------------------------------
EXO_BOLD_PATH   = os.path.join(FONTS_DIR, "Exo2-Bold.ttf")
MONTS_BOLD_PATH = os.path.join(FONTS_DIR, "Montserrat-Bold.ttf")
MONTS_SEMI_PATH = os.path.join(FONTS_DIR, "Montserrat-SemiBold.ttf")

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        raise OSError(f"Font not found: {path}")
    return ImageFont.truetype(path, size)

def font_name(size: int) -> ImageFont.FreeTypeFont:
    # Имя игрока — Montserrat Bold
    return _font(MONTS_BOLD_PATH, size)

def font_stat_num(size: int) -> ImageFont.FreeTypeFont:
    # Цифры — Exo2 Bold
    return _font(EXO_BOLD_PATH, size)

def font_stat_label(size: int) -> ImageFont.FreeTypeFont:
    # Подписи — Montserrat SemiBold
    return _font(MONTS_SEMI_PATH, size)

# ---- УТИЛИТЫ ---------------------------------------------------------------
def _linear_gradient(w: int, h: int, c1: Tuple[int,int,int], c2: Tuple[int,int,int]) -> Image.Image:
    """Простой горизонтальный градиент."""
    im = Image.new("RGB", (w, h), c1)
    draw = ImageDraw.Draw(im)
    for x in range(w):
        t = x / max(1, w - 1)
        r = int(c1[0] + (c2[0]-c1[0]) * t)
        g = int(c1[1] + (c2[1]-c1[1]) * t)
        b = int(c1[2] + (c2[2]-c1[2]) * t)
        draw.line([(x,0),(x,h)], fill=(r,g,b))
    return im

def _paste_circle(im: Image.Image, avatar: Image.Image, center: Tuple[int,int], radius: int):
    """Вклеивает avatar в круглую маску радиуса radius с центром center (RGBA)."""
    avatar = avatar.convert("RGBA")
    diameter = radius * 2
    avatar = avatar.resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diameter,diameter), fill=255)
    x = center[0] - radius
    y = center[1] - radius
    im.paste(avatar, (x, y), mask)

def _load_png_from_bytes(png_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")

def _try_load_icon(name: str, fallback_draw: Optional[Tuple[int,int,int]] = None) -> Optional[Image.Image]:
    path = os.path.join(ICONS_DIR, name)
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    return None

def _draw_stats_row(draw: ImageDraw.ImageDraw, x: int, baseline_y: int,
                    items: Tuple[Tuple[str,str], ...],
                    num_f: ImageFont.FreeTypeFont, lab_f: ImageFont.FreeTypeFont,
                    color=(255,255,255), gap=84) -> int:
    """Рисует: [(num, label), ...]. Возвращает ширину ряда."""
    cur_x = x
    for num, lab in items:
        # число
        w_num, h_num = draw.textbbox((0,0), num, font=num_f)[2:]
        draw.text((cur_x, baseline_y - h_num), num, font=num_f, fill=color)
        # подпись ниже
        w_lab, h_lab = draw.textbbox((0,0), lab, font=lab_f)[2:]
        draw.text((cur_x, baseline_y + 8), lab, font=lab_f, fill=color)
        block_w = max(w_num, w_lab)
        cur_x += block_w + gap
    return cur_x - x

# ---- КОНСТАНТЫ ВЕРСТКИ -----------------------------------------------------
W, H = 1920, 1080                # общий холст
CARD_H        = 220              # высота плашки (все просили сделать ниже)
MARGIN        = 24
GAP_CARDS     = 10               # отступ между основной и доп. плашкой (cards)
NAME_SIZE     = 64               # имя игрока (меньше, чем раньше)
STAT_NUM      = 46               # цифры
STAT_LAB      = 24               # подписи
HEAD_R        = 138              # радиус круга для головы (чуть уменьшили)
HEAD_SHIFT_Y  = 10               # на 5–10 px выше низа
HEAD_SHIFT_X  = 36               # левее на 30–40 px
TEAM_LOGO_D   = 96               # логотип команды (крупнее в 1.5x)
TEAM_LOGO_Y_PAD = 18             # почти у нижней кромки

# Градиенты (фиксированные, НЕ по цветам команды)
GRAD_ORANGE = ((255, 143, 26), (255, 209, 74))         # card / cards
GRAD_PURPLE = ((61, 34, 116), (42, 26, 90))            # левая половина card2
GRAD_BLUE   = ((27, 73, 132), (17, 55, 104))           # правая половина card2
GRAD_DARK   = ((32, 32, 32), (20, 20, 20))             # доп. модуль в cards

WHITE = (255,255,255)

# ---- ОСНОВНАЯ ПЛАШКА --------------------------------------------------------
def render_card(name_ru: str,
                stats: Tuple[str,str,str],  # ("30", "11", "11-14")
                head_png: bytes,
                team_logo_path: Optional[str] = None) -> Image.Image:
    """
    Возвращает PIL.Image (RGBA). Плашка в левом нижнем углу, на всю ширину.
    """
    name_ru = str(name_ru or "").upper()
    num1, num2, num3 = stats

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    # фоновая плашка
    grad = _linear_gradient(W, CARD_H, *GRAD_ORANGE).convert("RGBA")
    canvas.paste(grad, (0, H - CARD_H))

    draw = ImageDraw.Draw(canvas)
    # команда (крупнее и у нижней кромки)
    if team_logo_path and os.path.exists(team_logo_path):
        team = Image.open(team_logo_path).convert("RGBA")
        team = team.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        # белый тонкий круг-«подложка»
        bg = Image.new("RGBA", (TEAM_LOGO_D+18, TEAM_LOGO_D+18), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - 9
        canvas.paste(bg, (bx-9, by-9), bg)
        canvas.paste(team, (bx, by), team)

    # голова — левее на 30–40, чуть выше нижней границы
    head = _load_png_from_bytes(head_png)
    cx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    cy = H - HEAD_SHIFT_Y - HEAD_R
    _paste_circle(canvas, head, (cx, cy), HEAD_R)

    # имя
    name_f = font_name(NAME_SIZE)
    name_y = H - CARD_H + 44
    name_x = cx + HEAD_R + 28
    draw.text((name_x, name_y), name_ru, font=name_f, fill=WHITE)

    # статистика (меньше имени!)
    num_f  = font_stat_num(STAT_NUM)
    lab_f  = font_stat_label(STAT_LAB)
    stats_y = name_y + 78
    _draw_stats_row(draw, name_x, stats_y, (
        (num1, "ОЧКИ"),
        (num2, "ПОДБОРЫ"),
        (num3, "С ИГРЫ"),
    ), num_f, lab_f)

    return canvas

# ---- ПЛАШКА «ПЛОХО» ---------------------------------------------------------
def render_cardbad(name_ru: str,
                   stats: Tuple[str,str,str],
                   head_png: bytes,
                   team_logo_path: Optional[str] = None) -> Image.Image:
    """
    То же, но коричневый градиент и «poop» после имени (крупнее в 2 раза).
    """
    name_ru = str(name_ru or "").upper()
    num1, num2, num3 = stats

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    grad = _linear_gradient(W, CARD_H, (78,52,48), (54,36,33)).convert("RGBA")
    canvas.paste(grad, (0, H - CARD_H))
    draw = ImageDraw.Draw(canvas)

    if team_logo_path and os.path.exists(team_logo_path):
        team = Image.open(team_logo_path).convert("RGBA")
        team = team.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        bg = Image.new("RGBA", (TEAM_LOGO_D+18, TEAM_LOGO_D+18), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - 9
        canvas.paste(bg, (bx-9, by-9), bg)
        canvas.paste(team, (bx, by), team)

    head = _load_png_from_bytes(head_png)
    cx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    cy = H - HEAD_SHIFT_Y - HEAD_R
    _paste_circle(canvas, head, (cx, cy), HEAD_R)

    name_f = font_name(NAME_SIZE)
    name_y = H - CARD_H + 44
    name_x = cx + HEAD_R + 28
    draw.text((name_x, name_y), name_ru, font=name_f, fill=WHITE)

    # Poop-иконка сразу после имени (в 2 раза крупнее условной базовой 24 → ~48)
    poop = _try_load_icon("poop.png")
    if poop:
        h = int(NAME_SIZE * 0.9)  # по высоте к тексту
        w = int(poop.width * h / poop.height)
        poop = poop.resize((w, h), Image.LANCZOS)
        nx, ny, nx2, ny2 = draw.textbbox((name_x, name_y), name_ru, font=name_f)
        canvas.paste(poop, (nx2 + 16, name_y - int(h*0.1)), poop)

    num_f  = font_stat_num(STAT_NUM)
    lab_f  = font_stat_label(STAT_LAB)
    stats_y = name_y + 78
    _draw_stats_row(draw, name_x, stats_y, (
        (num1, "ОЧКИ"),
        (num2, "ПОДБОРЫ"),
        (num3, "С ИГРЫ"),
    ), num_f, lab_f)

    return canvas

# ---- ПЛАШКА С ДОП. МОДУЛЕМ (звезда без круга) -------------------------------
def render_cards(name_ru: str,
                 stats: Tuple[str,str,str],
                 extra_text: str,
                 head_png: bytes,
                 team_logo_path: Optional[str] = None) -> Image.Image:
    """
    Основная оранжевая плашка + справа отдельный темный модуль (через 10px).
    У звезды НЕТ белого круга.
    """
    name_ru = str(name_ru or "").upper()
    num1, num2, num3 = stats

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    main_w = int(W * 0.58)
    extra_w = int(W * 0.28)

    # главная
    main = _linear_gradient(main_w, CARD_H, *GRAD_ORANGE).convert("RGBA")
    canvas.paste(main, (0, H - CARD_H))

    # доп. модуль справа (через 10px)
    extra = _linear_gradient(extra_w, CARD_H, *GRAD_DARK).convert("RGBA")
    canvas.paste(extra, (main_w + GAP_CARDS, H - CARD_H))

    draw = ImageDraw.Draw(canvas)

    # команда
    if team_logo_path and os.path.exists(team_logo_path):
        team = Image.open(team_logo_path).convert("RGBA")
        team = team.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        bg = Image.new("RGBA", (TEAM_LOGO_D+18, TEAM_LOGO_D+18), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - 9
        canvas.paste(bg, (bx-9, by-9), bg)
        canvas.paste(team, (bx, by), team)

    # голова
    head = _load_png_from_bytes(head_png)
    cx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    cy = H - HEAD_SHIFT_Y - HEAD_R
    _paste_circle(canvas, head, (cx, cy), HEAD_R)

    # имя
    name_f = font_name(NAME_SIZE)
    name_y = H - CARD_H + 44
    name_x = cx + HEAD_R + 28
    draw.text((name_x, name_y), name_ru, font=name_f, fill=WHITE)

    # статистика
    num_f  = font_stat_num(STAT_NUM)
    lab_f  = font_stat_label(STAT_LAB)
    stats_y = name_y + 78
    _draw_stats_row(draw, name_x, stats_y, (
        (num1, "ОЧКИ"),
        (num2, "ПОДБОРЫ"),
        (num3, "С ИГРЫ"),
    ), num_f, lab_f)

    # звезда + текст в доп. модуле
    star = _try_load_icon("star.png")
    ex_x = main_w + GAP_CARDS + 28
    ex_y = H - CARD_H + 54
    if star:
        h = 40
        w = int(star.width * h / star.height)
        star = star.resize((w, h), Image.LANCZOS)
        canvas.paste(star, (ex_x, ex_y), star)
        ex_x += w + 14
    draw.text((ex_x, ex_y), str(extra_text or ""), font=font_name(32), fill=WHITE)

    return canvas

# ---- ДВУХСТОРОННЯЯ ПЛАШКА (два игрока, строго во всю ширину) ----------------
def render_card2(left_name_ru: str, left_stats: Tuple[str,str,str], left_head_png: bytes, left_team_logo_path: Optional[str],
                 right_name_ru: str, right_stats: Tuple[str,str,str], right_head_png: bytes, right_team_logo_path: Optional[str]) -> Image.Image:
    """
    Два блока, одинаковые по высоте, выровненные имена и статистика вровень.
    Градиенты фиксированные (фиолетовый/синий).
    """
    ln = str(left_name_ru or "").upper()
    rn = str(right_name_ru or "").upper()
    l1, l2, l3 = left_stats
    r1, r2, r3 = right_stats

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    half = W // 2

    # лево/право
    left_grad  = _linear_gradient(half, CARD_H, *GRAD_PURPLE).convert("RGBA")
    right_grad = _linear_gradient(W - half, CARD_H, *GRAD_BLUE).convert("RGBA")
    canvas.paste(left_grad, (0, H - CARD_H))
    canvas.paste(right_grad, (half, H - CARD_H))

    draw = ImageDraw.Draw(canvas)

    # общий baseline для имени/статов
    name_y  = H - CARD_H + 44
    stats_y = name_y + 78

    # ЛЕВО
    if left_team_logo_path and os.path.exists(left_team_logo_path):
        team = Image.open(left_team_logo_path).convert("RGBA")
        team = team.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        bg = Image.new("RGBA", (TEAM_LOGO_D+18, TEAM_LOGO_D+18), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - 9
        canvas.paste(bg, (bx-9, by-9), bg)
        canvas.paste(team, (bx, by), team)

    lhead = _load_png_from_bytes(left_head_png)
    lcx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    lcy = H - HEAD_SHIFT_Y - HEAD_R
    _paste_circle(canvas, lhead, (lcx, lcy), HEAD_R)

    name_f = font_name(NAME_SIZE)
    num_f  = font_stat_num(STAT_NUM)
    lab_f  = font_stat_label(STAT_LAB)

    l_name_x = lcx + HEAD_R + 28
    draw.text((l_name_x, name_y), ln, font=name_f, fill=WHITE)
    _draw_stats_row(draw, l_name_x, stats_y, (
        (l1, "ОЧКИ"), (l2, "ПЕРЕДАЧИ" if "пас" in l2.lower() or "assist" in l2.lower() else "ПОДБОРЫ"), (l3, "С ИГРЫ"),
    ), num_f, lab_f)

    # ПРАВО
    if right_team_logo_path and os.path.exists(right_team_logo_path):
        team = Image.open(right_team_logo_path).convert("RGBA")
        team = team.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        bg = Image.new("RGBA", (TEAM_LOGO_D+18, TEAM_LOGO_D+18), (255,255,255,240))
        bx = half + MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - 9
        canvas.paste(bg, (bx-9, by-9), bg)
        canvas.paste(team, (bx, by), team)

    rhead = _load_png_from_bytes(right_head_png)
    rcx = half + MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    rcy = H - HEAD_SHIFT_Y - HEAD_R
    _paste_circle(canvas, rhead, (rcx, rcy), HEAD_R)

    r_name_x = rcx + HEAD_R + 28
    draw.text((r_name_x, name_y), rn, font=name_f, fill=WHITE)
    _draw_stats_row(draw, r_name_x, stats_y, (
        (r1, "ОЧКИ"), (r2, "ПЕРЕДАЧИ" if "пас" in r2.lower() or "assist" in r2.lower() else "ПОДБОРЫ"), (r3, "С ИГРЫ"),
    ), num_f, lab_f)

    return canvas
