# api/graphics.py — cards renderer (drop-in, compat-safe)
from __future__ import annotations

import os
from io import BytesIO
from functools import lru_cache
from typing import List, Tuple, Optional, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

# =============== Canvas / layout ===============
CANVAS_W = int(os.getenv("CARD_W", 1920))
CANVAS_H = int(os.getenv("CARD_H", 1080))

BAR_H = 260
PADDING_X = 28
PADDING_Y = 28
SAFE_W_RATIO = 0.92

# Аватар и логотип
HEAD_D = 360            # больше, чтобы голову не резало
HEAD_OFFSET_Y = 110     # «выпирание» вверх из плашки
LOGO_CIRCLE_D = 88      # белый круг меньше
LOGO_INNER_D = 82       # сам логотип крупнее внутри круга

# Типографика
NAME_SIZE = 86
STAT_VALUE_SIZE = 56
STAT_LABEL_SIZE = 30
RIGHT_TEXT_SIZE = 40

# Фикс-градиенты
GRAD_ORANGE_L = (255, 140, 0)
GRAD_ORANGE_R = (255, 201, 71)

GRAD_BAD_L = (84, 54, 48)
GRAD_BAD_R = (66, 44, 40)

GRAD_PURPLE_L = (79, 46, 126)
GRAD_PURPLE_R = (45, 24, 89)
GRAD_BLUE_L   = (24, 74, 130)
GRAD_BLUE_R   = (17, 47, 89)

# =============== Paths ===============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIRS = [
    os.path.join(BASE_DIR, "fonts"),
    os.path.join(os.path.dirname(BASE_DIR), "api", "fonts"),
    os.path.join("/var/task", "api", "fonts"),
]
ASSETS_DIRS = [
    os.path.join(os.path.dirname(BASE_DIR), "assets"),
    os.path.join(BASE_DIR, "assets"),
]

FONT_EXO2_BOLD = "Exo2-Bold.ttf"
FONT_MONTSERRAT_BOLD = "Montserrat-Bold.ttf"
FONT_MONTSERRAT_SEMI = "Montserrat-SemiBold.ttf"

# =============== Utils ===============
def _find_file(name: str, folders: List[str]) -> Optional[str]:
    for folder in folders:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    return None

def _font_path(font_name: str) -> str:
    p = _find_file(font_name, FONTS_DIRS)
    if not p:
        raise FileNotFoundError(f"Font not found in api/fonts: {font_name}")
    return p

def _asset_path(rel: str) -> Optional[str]:
    for base in ASSETS_DIRS:
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    return None

@lru_cache(maxsize=64)
def _font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(font_name), size=size)

def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def _as_image(obj: Any) -> Optional[Image.Image]:
    """Принимает PIL.Image | bytes | str (путь / относительный путь) | None."""
    if obj is None:
        return None
    if isinstance(obj, Image.Image):
        return obj.convert("RGBA")
    if isinstance(obj, (bytes, bytearray)):
        return Image.open(BytesIO(obj)).convert("RGBA")
    if isinstance(obj, str):
        # абсолютный путь
        if os.path.isfile(obj):
            return Image.open(obj).convert("RGBA")
        # относительный к assets
        for base in ASSETS_DIRS:
            p = os.path.join(base, obj)
            if os.path.isfile(p):
                return Image.open(p).convert("RGBA")
        # если это не путь — считаем отсутствующим
        return None
    return None

def _paste_rgba(dst: Image.Image, src: Image.Image, xy: Tuple[int, int]) -> None:
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    dst.alpha_composite(src, xy)

def _circle_crop(im: Image.Image, diameter: int) -> Image.Image:
    im = ImageOps.fit(im.convert("RGBA"), (diameter, diameter), Image.LANCZOS, centering=(0.5, 0.35))
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask=mask)
    return out

def _load_icon(name: str, size: int, no_circle: bool = False) -> Image.Image:
    rel = os.path.join("icons", name)
    p = _asset_path(rel)
    if not p:
        im = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((0, 0, size - 1, size - 1), outline=(255, 215, 0, 255), width=3)
        return im
    im = Image.open(p).convert("RGBA")
    im = ImageOps.contain(im, (size, size), Image.LANCZOS)
    if no_circle:
        return im
    badge = Image.new("RGBA", (size + 16, size + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)
    d.ellipse((0, 0, badge.width - 1, badge.height - 1), fill=(255, 255, 255, 255))
    _paste_rgba(badge, im, ((badge.width - im.width)//2, (badge.height - im.height)//2))
    return badge

def _draw_gradient_rect(canvas: Image.Image, box: Tuple[int, int, int, int], c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    grad = Image.new("RGBA", (w, h), 0)
    d = ImageDraw.Draw(grad)
    for i in range(w):
        t = i / max(w - 1, 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        d.line([(i, 0), (i, h)], fill=(r, g, b, 255))
    _paste_rgba(canvas, grad, (x0, y0))

def _split_stats(stats: str) -> List[Tuple[str, str]]:
    if not stats:
        return []
    parts = [p.strip() for p in stats.replace(";", ",").split(",") if p.strip()]
    out: List[Tuple[str, str]] = []
    for p in parts:
        tokens = p.split()
        if not tokens:
            continue
        value = tokens[0]
        label = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        out.append((value, label))
    return out[:3]

# =============== Core ===============
def _build_left_module(
    canvas: Image.Image,
    baseline_y: int,
    player_img: Any,
    team_logo_img: Any,
    name_ru: str,
    stats: str,
    c_left: Tuple[int, int, int],
    c_right: Tuple[int, int, int],
    extra_right_w: int = 0,
) -> Tuple[int, int, int, int]:
    draw = ImageDraw.Draw(canvas)

    font_name = _font(FONT_MONTSERRAT_BOLD, NAME_SIZE)
    font_stat_val = _font(FONT_EXO2_BOLD, STAT_VALUE_SIZE)
    font_stat_lbl = _font(FONT_MONTSERRAT_SEMI, STAT_LABEL_SIZE)

    # to images
    player_img = _as_image(player_img)
    team_logo_img = _as_image(team_logo_img)

    # Name measure
    name_text = (name_ru or "").upper()
    name_w, name_h = _measure(draw, name_text, font_name)

    stat_pairs = _split_stats(stats)
    col_gap = 44
    vals_w = 0
    for value, label in stat_pairs:
        vw, _ = _measure(draw, value, font_stat_val)
        lw, _ = _measure(draw, label.upper(), font_stat_lbl)
        vals_w += max(vw, lw)
    vals_w += col_gap * max(len(stat_pairs) - 1, 0)

    head_w = HEAD_D
    logo_w = LOGO_CIRCLE_D
    inner_pad = 26
    min_w = PADDING_X + logo_w + 20 + head_w + inner_pad + max(name_w, vals_w) + PADDING_X
    block_w = min(min_w + extra_right_w, int(CANVAS_W * SAFE_W_RATIO))

    # центрируем общий блок (как было)
    x0 = (CANVAS_W - block_w) // 2
    x1 = x0 + block_w
    y1 = CANVAS_H - PADDING_Y
    y0 = y1 - BAR_H

    # фон
    _draw_gradient_rect(canvas, (x0, y0, x1, y1), c_left, c_right)

    # логотип слева
    logo_cx = x0 + PADDING_X + LOGO_CIRCLE_D // 2
    logo_cy = y1 - BAR_H // 2 + 6
    logo_badge = Image.new("RGBA", (LOGO_CIRCLE_D, LOGO_CIRCLE_D), (0, 0, 0, 0))
    d = ImageDraw.Draw(logo_badge)
    d.ellipse((0, 0, LOGO_CIRCLE_D - 1, LOGO_CIRCLE_D - 1), fill=(255, 255, 255, 255))
    if team_logo_img is not None:
        tl = ImageOps.contain(team_logo_img, (LOGO_INNER_D, LOGO_INNER_D), Image.LANCZOS)
        _paste_rgba(logo_badge, tl, ((logo_badge.width - tl.width)//2, (logo_badge.height - tl.height)//2))
    _paste_rgba(canvas, logo_badge, (logo_cx - logo_badge.width//2, logo_cy - logo_badge.height//2))

    # голова
    if player_img is None:
        head = Image.new("RGBA", (HEAD_D, HEAD_D), (0, 0, 0, 0))
    else:
        head = _circle_crop(player_img, HEAD_D)
    head_x = x0 + PADDING_X + LOGO_CIRCLE_D + 20
    head_y = y1 - HEAD_D + HEAD_OFFSET_Y - (BAR_H // 2)
    _paste_rgba(canvas, head, (head_x, head_y))

    # текстовый столбец — центр по своей области
    text_left = head_x + HEAD_D + inner_pad
    text_right = x1 - PADDING_X - extra_right_w
    text_center = (text_left + text_right) // 2

    # имя
    name_w, name_h = _measure(draw, name_text, font_name)
    name_x = text_center - name_w // 2
    name_y = y0 + 34
    draw.text((name_x, name_y), name_text, font=font_name, fill=(255, 255, 255, 255))

    # статы
    cols = []
    total_cols_w = 0
    for value, label in stat_pairs:
        vw, _ = _measure(draw, value, font_stat_val)
        lw, _ = _measure(draw, label.upper(), font_stat_lbl)
        w = max(vw, lw)
        cols.append((w, (value, label)))
        total_cols_w += w
    total_cols_w += col_gap * max(len(cols) - 1, 0)

    stats_y_value = name_y + name_h + 28
    stats_y_label = stats_y_value + STAT_VALUE_SIZE + 6

    x = text_center - total_cols_w // 2
    for w, (value, label) in cols:
        vw, _ = _measure(draw, value, font_stat_val)
        lw, _ = _measure(draw, label.upper(), font_stat_lbl)
        draw.text((x + (w - vw)//2, stats_y_value), value, font=font_stat_val, fill=(255, 255, 255, 255))
        draw.text((x + (w - lw)//2, stats_y_label), label.upper(), font=font_stat_lbl, fill=(255, 255, 255, 200))
        x += w + col_gap

    return (x0, y0, x1, y1)

# =============== Public API (back-compat) ===============
def render_card(canvas, player_img, team_logo_img, name_ru, stats, *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    _build_left_module(canvas, CANVAS_H - PADDING_Y, player_img, team_logo_img, name_ru, stats,
                       GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=0)
    return canvas

def render_cardbad(canvas, player_img, team_logo_img, name_ru, stats, *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    x0, y0, x1, y1 = _build_left_module(canvas, CANVAS_H - PADDING_Y, player_img, team_logo_img, name_ru, stats,
                                        GRAD_BAD_L, GRAD_BAD_R, extra_right_w=0)
    # 💩 слева от имени, без белого круга
    draw = ImageDraw.Draw(canvas)
    font_name = _font(FONT_MONTSERRAT_BOLD, NAME_SIZE)
    name_text = (name_ru or "").upper()
    name_w, name_h = _measure(draw, name_text, font_name)
    left_pad = PADDING_X + LOGO_CIRCLE_D + 20 + HEAD_D + 26
    center = (left_pad + (x1 - PADDING_X)) // 2
    name_x = center - name_w // 2
    name_y = y0 + 34
    poop = _load_icon("poop.png", size=34, no_circle=True)
    _paste_rgba(canvas, poop, (max(x0 + PADDING_X, name_x - 44), name_y + (name_h - poop.height)//2))
    return canvas

def render_cards(canvas, player_img, team_logo_img, name_ru, stats, right_text="молодец", *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)
    f_right = _font(FONT_MONTSERRAT_SEMI, RIGHT_TEXT_SIZE)
    txt_w, _ = _measure(draw, right_text, f_right)
    right_w = max(320, txt_w + 120)

    x0, y0, x1, y1 = _build_left_module(canvas, CANVAS_H - PADDING_Y, player_img, team_logo_img, name_ru, stats,
                                        GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=right_w)

    # правый блок без зазора
    r_x0 = x1 - right_w
    r_x1 = x1
    _draw_gradient_rect(canvas, (r_x0, y0, r_x1, y1), (36, 36, 36), (20, 20, 20))

    star = _load_icon("star.png", size=34, no_circle=True)  # без белого круга
    icon_y = y0 + BAR_H // 2 - star.height // 2
    _paste_rgba(canvas, star, (r_x0 + 28, icon_y))
    draw.text((r_x0 + 28 + star.width + 16, icon_y + (star.height - RIGHT_TEXT_SIZE)//2),
              right_text, font=f_right, fill=(255, 255, 255, 255))
    return canvas

def render_card2(canvas,
                 left_player_img, left_team_logo, left_name_ru, left_stats,
                 right_player_img, right_team_logo, right_name_ru, right_stats,
                 *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))

    layer_left = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    lx0, ly0, lx1, ly1 = _build_left_module(layer_left, CANVAS_H - PADDING_Y,
                                            left_player_img, left_team_logo, left_name_ru, left_stats,
                                            GRAD_PURPLE_L, GRAD_PURPLE_R, extra_right_w=0)
    left_crop = layer_left.crop((lx0, ly0, lx1, ly1))
    left_w = left_crop.width

    layer_right = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    rx0, ry0, rx1, ry1 = _build_left_module(layer_right, CANVAS_H - PADDING_Y,
                                            right_player_img, right_team_logo, right_name_ru, right_stats,
                                            GRAD_BLUE_L, GRAD_BLUE_R, extra_right_w=0)
    right_crop = layer_right.crop((rx0, ry0, rx1, ry1))
    right_w = right_crop.width

    total_w = left_w + right_w
    max_w = int(CANVAS_W * SAFE_W_RATIO)
    scale = 1.0 if total_w <= max_w else max_w / total_w

    if scale < 0.999:
        left_crop = left_crop.resize((int(left_w * scale), int(left_crop.height * scale)), Image.LANCZOS)
        right_crop = right_crop.resize((int(right_w * scale), int(right_crop.height * scale)), Image.LANCZOS)

    total_w = left_crop.width + right_crop.width
    start_x = (CANVAS_W - total_w) // 2
    y_bottom = CANVAS_H - PADDING_Y - left_crop.height

    _paste_rgba(canvas, left_crop, (start_x, y_bottom))
    _paste_rgba(canvas, right_crop, (start_x + left_crop.width, y_bottom))
    return canvas

# ---- Back-compat shim ----
def render_card_special(canvas, *args, **kwargs):
    """
    Обратная совместимость со старыми вызовами.
    Если передали right_text — рендерим как render_cards,
    иначе — как обычный render_card.
    """
    right_text = kwargs.pop("right_text", None)
    # старые сигнатуры могли передавать 5–7 позиционных аргументов
    if right_text is None and len(args) >= 6:
        # иногда right_text прилетает 6-м позиционным
        right_text = args[5]
        args = args[:5]
    try:
        if right_text is None:
            return render_card(canvas, *args, **kwargs)
        else:
            return render_cards(canvas, *args[:5], right_text, *args[6:], **kwargs)
    except TypeError:
        # если позиционные не совпали — пробуем безопаснее
        if right_text is None:
            return render_card(canvas, *args[:5], **kwargs)
        return render_cards(canvas, *args[:5], right_text, **kwargs)
