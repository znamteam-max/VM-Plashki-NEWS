# graphics.py — 1920x1080, нижний левый угол, выровненные размеры и байтовый PNG
from __future__ import annotations
import io, os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

CANVAS_W, CANVAS_H = 1920, 1080

# Геометрия карточек
CARD_W_SINGLE = 1080      # ширина /card, /cardbad, /cards (левая часть)
CARD_W_DUO    = 1080      # ширина /card2 — по ТЗ «1080 в длину»
CARD_H        = 220       # высота основной плашки
GAP_BOTTOM    = 0         # плашка прижата к нижней границе экрана
GAP_RIGHT_COL = 10        # отступ между основной и правой колонкой (/cards)

# Фото игрока
HEAD_REL_H    = 0.95      # относительная высота головы к высоте плашки (чуть ниже верха)
HEAD_SHIFT_X  = 36        # сдвиг аватарки левее
HEAD_GAP_BOT  = 8         # на 5–10 пикселей выше нижней границы плашки

# Лого команды
LOGO_BASE     = 84        # базовый размер
LOGO_SCALE    = 1.5       # увеличить в 1.5 раза
LOGO_SIZE     = int(LOGO_BASE * LOGO_SCALE)

# Типографика
NAME_SIZE     = 62
STAT_NUM_SIZE = 48
STAT_LBL_SIZE = 24
NAME_WEIGHT   = "bold"

POOP_SIZE_REL = 1.9       # «в 2 раза крупнее» (чуть меньше двух, чтобы не вылезало)

# --------- утилиты ---------

def _search_font(names: List[str], size: int) -> ImageFont.FreeTypeFont:
    """Ищем ttf/otf в проектах; иначе — системный DejaVuSans."""
    here = os.path.dirname(__file__)
    places = [
        os.path.join(here, "assets", "fonts"),
        os.path.join(here, "api", "fonts"),
        os.path.join(here, "fonts"),
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts",
    ]
    for folder in places:
        if not os.path.isdir(folder):
            continue
        for nm in names:
            p = os.path.join(folder, nm)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size=size)
                except Exception:
                    pass
    # Фоллбэк — DejaVuSans
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except Exception:
        return ImageFont.load_default()

def _font_bold(sz: int) -> ImageFont.FreeTypeFont:
    return _search_font(
        ["Montserrat-Bold.ttf", "Exo2-Bold.ttf", "Montserrat-SemiBold.ttf", "DejaVuSans-Bold.ttf"],
        sz,
    )

def _font_reg(sz: int) -> ImageFont.FreeTypeFont:
    return _search_font(
        ["Montserrat-Regular.ttf", "Exo2-Regular.ttf", "DejaVuSans.ttf"],
        sz,
    )

def _draw_gradient_bar(im: Image.Image, xy: Tuple[int, int, int, int], c1: str, c2: str):
    """Простой горизонтальный градиент."""
    x0, y0, x1, y1 = xy
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    g = Image.new("RGB", (w, 1), c1)
    draw = ImageDraw.Draw(g)
    c1r, c1g, c1b = ImageColor.getrgb(c1)
    c2r, c2g, c2b = ImageColor.getrgb(c2)
    for i in range(w):
        t = i / (w - 1)
        r = int(c1r * (1 - t) + c2r * t)
        gch = int(c1g * (1 - t) + c2g * t)
        b = int(c1b * (1 - t) + c2b * t)
        draw.point((i, 0), (r, gch, b))
    g = g.resize((w, h))
    im.paste(g, (x0, y0))

# PIL < 10 не всегда подхватывает ImageColor напрямую из строки
from PIL import ImageColor  # noqa

def _bytes_png(im: Image.Image) -> bytes:
    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue()

def _team_colors(colors_tuple: Optional[Tuple[str, str, str]]) -> Tuple[str, str, str]:
    if not colors_tuple or not isinstance(colors_tuple, tuple) or len(colors_tuple) < 2:
        return ("#F5A623", "#FFD166", "#111111")
    c1, c2 = colors_tuple[0], colors_tuple[1]
    c3 = colors_tuple[2] if len(colors_tuple) >= 3 else "#111111"
    return (c1 or "#F5A623", c2 or "#FFD166", c3 or "#111111")

def _place_headshot(base: Image.Image, head: Image.Image, bar_xy: Tuple[int, int, int, int]):
    """Размещаем аватарку: левее на 30–40, нижний край на 5–10 px выше низа плашки."""
    x0, y0, x1, y1 = bar_xy
    bar_h = y1 - y0
    target_h = max(64, int(bar_h * HEAD_REL_H))
    hs = head.copy().convert("RGBA")
    ratio = target_h / max(1, hs.height)
    new_w = int(hs.width * ratio)
    hs = hs.resize((new_w, target_h), Image.LANCZOS)

    # позиция
    px = x0 + HEAD_SHIFT_X
    py = y1 - target_h - HEAD_GAP_BOT
    base.alpha_composite(hs, (px, py))
    return (px, py, px + hs.width, py + hs.height)

def _place_logo(base: Image.Image, logo: Optional[Image.Image], bar_xy: Tuple[int, int, int, int]):
    if logo is None:
        return
    x0, y0, x1, y1 = bar_xy
    L = LOGO_SIZE
    lg = logo.copy().convert("RGBA")
    lg = ImageOps.contain(lg, (L, L), Image.LANCZOS)
    # почти к нижней границе
    px = x0 + 14
    py = y1 - lg.height - 12
    base.alpha_composite(lg, (px, py))

def _stats_layout(draw: ImageDraw.ImageDraw, x: int, baseline_y: int, stats: List[Tuple[str, str]]):
    """Рисуем 3 колонки: число (крупнее), подпись (меньше)."""
    fn = _font_bold(STAT_NUM_SIZE)
    fl = _font_reg(STAT_LBL_SIZE)
    cur_x = x
    for value, label in stats[:3]:
        wv, hv = draw.textsize(value, font=fn)
        wl, hl = draw.textsize(label, font=fl)
        draw.text((cur_x, baseline_y - hv), value, fill="white", font=fn)
        draw.text((cur_x, baseline_y + 8), label, fill="white", font=fl)
        cur_x += max(160, wv + 120)

# --------- публичные рендеры ---------

def render_card(mode: str, name_ru: str, name_en: str, team_logo_img: Optional[Image.Image],
                team_colors: Optional[Tuple[str, str, str]], head_img: Image.Image,
                stats: List[Tuple[str, str]]) -> bytes:
    """
    mode: 'single' (игнорируется, оставлено для совместимости).
    """
    c1, c2, c3 = _team_colors(team_colors)
    im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))

    # основная плашка (левый нижний угол)
    bar_x0 = 0
    bar_y1 = CANVAS_H - GAP_BOTTOM
    bar_x1 = bar_x0 + CARD_W_SINGLE
    bar_y0 = bar_y1 - CARD_H
    _draw_gradient_bar(im, (bar_x0, bar_y0, bar_x1, bar_y1), c1, c2)

    # аватарка
    head_box = _place_headshot(im, head_img, (bar_x0, bar_y0, bar_x1, bar_y1))

    # лого
    _place_logo(im, team_logo_img, (bar_x0, bar_y0, bar_x1, bar_y1))

    # имя (слева направо после аватарки)
    draw = ImageDraw.Draw(im)
    fn = _font_bold(NAME_SIZE)
    name_text = (name_ru or name_en or "").strip() or "ИГРОК"
    # старт от правого края аватарки + небольшой отступ
    name_x = head_box[2] + 22
    name_y = bar_y0 + 28
    draw.text((name_x, name_y), name_text, fill="white", font=fn)

    # статы — меньше имён
    # выравнивание: имена и статы по одному горизонтальному уровню на всех карточках
    stats_baseline = name_y + 78  # единый уровень для всех шаблонов
    _stats_layout(draw, name_x, stats_baseline, stats)

    return _bytes_png(im)

def render_card_bad(name_ru: str, head_img: Image.Image, stats: List[Tuple[str, str]],
                    team_logo_img: Optional[Image.Image] = None) -> bytes:
    """BAD-вариант: та же плашка, после имени — 💩 в 2× размере."""
    im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    # цвет у BAD плашки сделаем тёмным/угольным
    c1, c2 = "#2B2B2B", "#111111"

    bar_x0 = 0
    bar_y1 = CANVAS_H
    bar_x1 = bar_x0 + CARD_W_SINGLE
    bar_y0 = bar_y1 - CARD_H
    _draw_gradient_bar(im, (bar_x0, bar_y0, bar_x1, bar_y1), c1, c2)

    head_box = _place_headshot(im, head_img, (bar_x0, bar_y0, bar_x1, bar_y1))
    _place_logo(im, team_logo_img, (bar_x0, bar_y0, bar_x1, bar_y1))

    draw = ImageDraw.Draw(im)
    fn = _font_bold(NAME_SIZE)
    name_text = (name_ru or "ИГРОК").strip()
    name_x = head_box[2] + 22
    name_y = bar_y0 + 28
    draw.text((name_x, name_y), name_text, fill="white", font=fn)

    # 💩 «после имени», крупно
    poop = "💩"
    # иногда шрифты без emoji — пробуем жирный рег/болд, если квадрат — падаем на '!*'
    poop_font = _font_bold(int(NAME_SIZE * POOP_SIZE_REL))
    poop_w, poop_h = draw.textsize(poop, font=poop_font)
    # если ширина очень маленькая — значит глиф не поддержан; нарисуем «!*»
    if poop_w <= NAME_SIZE // 3:
        poop = "!*"
        poop_font = _font_bold(int(NAME_SIZE * 1.8))
        poop_w, poop_h = draw.textsize(poop, font=poop_font)
    draw.text((name_x + draw.textsize(name_text, font=fn)[0] + 18, name_y - 10), poop, fill="#FFB100", font=poop_font)

    stats_baseline = name_y + 78
    _stats_layout(draw, name_x, stats_baseline, stats)

    return _bytes_png(im)

def render_card2(name_ru_1: str, team_logo_1: Optional[Image.Image], colors_1: Optional[Tuple[str, str, str]],
                 head_1: Image.Image, stats_1: List[Tuple[str, str]],
                 name_ru_2: str, team_logo_2: Optional[Image.Image], colors_2: Optional[Tuple[str, str, str]],
                 head_2: Image.Image, stats_2: List[Tuple[str, str]]) -> bytes:
    """Две плашки на одной полосе, строго выровненные имена/стат-блоки. Общая ширина 1080."""
    im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    bar_x0 = 0
    bar_y1 = CANVAS_H
    bar_x1 = bar_x0 + CARD_W_DUO
    bar_y0 = bar_y1 - CARD_H

    # делим полосу пополам
    mid = bar_x0 + CARD_W_DUO // 2

    # левая половина
    c1L, c2L, _ = _team_colors(colors_1)
    _draw_gradient_bar(im, (bar_x0, bar_y0, mid, bar_y1), c1L, c2L)
    boxL = _place_headshot(im, head_1, (bar_x0, bar_y0, mid, bar_y1))
    _place_logo(im, team_logo_1, (bar_x0, bar_y0, mid, bar_y1))

    # правая половина
    c1R, c2R, _ = _team_colors(colors_2)
    _draw_gradient_bar(im, (mid, bar_y0, bar_x1, bar_y1), c1R, c2R)
    boxR = _place_headshot(im, head_2, (mid, bar_y0, bar_x1, bar_y1))
    _place_logo(im, team_logo_2, (mid, bar_y0, bar_x1, bar_y1))

    draw = ImageDraw.Draw(im)
    fn = _font_bold(NAME_SIZE)

    # Единый уровень для имён и статов
    name_y = bar_y0 + 28
    stats_baseline = name_y + 78

    # левая сторона
    nm1 = (name_ru_1 or "ИГРОК 1").strip()
    n1x = boxL[2] + 22
    draw.text((n1x, name_y), nm1, fill="white", font=fn)
    _stats_layout(draw, n1x, stats_baseline, stats_1)

    # правая сторона
    nm2 = (name_ru_2 or "ИГРОК 2").strip()
    n2x = boxR[2] + 22
    draw.text((n2x, name_y), nm2, fill="white", font=fn)
    _stats_layout(draw, n2x, stats_baseline, stats_2)

    return _bytes_png(im)

def render_card_special(name_ru: str, team_logo_img: Optional[Image.Image], team_colors: Optional[Tuple[str, str, str]],
                        head_img: Image.Image, stats: List[Tuple[str, str]], info_text: str) -> bytes:
    """Левая плашка + отдельная правая колонка (в 10 px)."""
    c1, c2, _ = _team_colors(team_colors)
    im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))

    # левая основная
    bar_x0 = 0
    bar_y1 = CANVAS_H
    bar_x1 = bar_x0 + CARD_W_SINGLE
    bar_y0 = bar_y1 - CARD_H
    _draw_gradient_bar(im, (bar_x0, bar_y0, bar_x1, bar_y1), c1, c2)
    head_box = _place_headshot(im, head_img, (bar_x0, bar_y0, bar_x1, bar_y1))
    _place_logo(im, team_logo_img, (bar_x0, bar_y0, bar_x1, bar_y1))

    draw = ImageDraw.Draw(im)
    fn = _font_bold(NAME_SIZE)
    name_text = (name_ru or "").strip() or "ИГРОК"
    name_x = head_box[2] + 22
    name_y = bar_y0 + 28
    draw.text((name_x, name_y), name_text, fill="white", font=fn)

    stats_baseline = name_y + 78
    _stats_layout(draw, name_x, stats_baseline, stats)

    # правая колонка — отдельный прямоугольник справа, отступ 10 px
    col_w = 540
    col_x0 = bar_x1 + GAP_RIGHT_COL
    col_x1 = col_x0 + col_w
    col_y0, col_y1 = bar_y0, bar_y1
    # затемнение
    col = Image.new("RGBA", (col_w, CARD_H), (0, 0, 0, 180))
    im.alpha_composite(col, (col_x0, col_y0))

    # текст в правой колонке
    info = (info_text or "").strip()
    if info:
        f_info = _font_reg(28)
        # небольшие поля
        tx, ty = col_x0 + 18, col_y0 + 22
        # мягкая разбивка по строкам
        max_w = col_w - 36
        words = info.split()
        cur = ""
        lines = []
        for w in words:
            t = (cur + " " + w).strip()
            if draw.textsize(t, font=f_info)[0] <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        for i, line in enumerate(lines[:6]):
            draw.text((tx, ty + i * 34), line, fill="white", font=f_info)

    return _bytes_png(im)
