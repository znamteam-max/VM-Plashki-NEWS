# api/graphics.py — cards renderer (drop-in)
from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

# =========================
# Canvas / layout constants
# =========================
CANVAS_W = int(os.getenv("CARD_W", 1920))
CANVAS_H = int(os.getenv("CARD_H", 1080))

BAR_H = 260                    # высота нижней плашки
PADDING_X = 28
PADDING_Y = 28
SAFE_W_RATIO = 0.92            # максимальная суммарная ширина ленты из блоков

# Аватар игрока (круг) и логотип
HEAD_D = 320                   # диаметр выреза под голову (увеличен, чтобы не резало сверху)
HEAD_OFFSET_Y = 100            # насколько «выпирает» вверх из плашки
LOGO_CIRCLE_D = 92             # белый круг
LOGO_INNER_D = 78              # сам логотип (больше относительно белого круга)

# Типографика
NAME_SIZE = 86                 # имя игрока (чуть меньше, чем раньше)
STAT_VALUE_SIZE = 56           # цифры статистики
STAT_LABEL_SIZE = 30           # подписи к статистике
RIGHT_TEXT_SIZE = 40           # текст справа в cards

# Градиенты (фиксированные)
GRAD_ORANGE_L = (255, 140, 0)
GRAD_ORANGE_R = (255, 201, 71)

GRAD_BAD_L = (84, 54, 48)
GRAD_BAD_R = (66, 44, 40)

GRAD_PURPLE_L = (79, 46, 126)  # card2 левый фиолетовый
GRAD_PURPLE_R = (45, 24, 89)

GRAD_BLUE_L = (24, 74, 130)    # card2 правый синий
GRAD_BLUE_R = (17, 47, 89)

# =========================
# Paths: fonts & assets
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIRS = [
    os.path.join(BASE_DIR, "fonts"),
    os.path.join(os.path.dirname(BASE_DIR), "api", "fonts"),
    os.path.join("/var/task", "api", "fonts"),
]
ASSETS_DIRS = [
    os.path.join(os.path.dirname(BASE_DIR), "assets"),   # ../assets
    os.path.join(BASE_DIR, "assets"),                    # api/assets (на всякий случай)
]

FONT_EXO2_BOLD = "Exo2-Bold.ttf"
FONT_MONTSERRAT_BOLD = "Montserrat-Bold.ttf"
FONT_MONTSERRAT_SEMI = "Montserrat-SemiBold.ttf"


def _find_file(name: str, folders: List[str]) -> Optional[str]:
    for folder in folders:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def _font_path(font_name: str) -> str:
    p = _find_file(font_name, FONTS_DIRS)
    if not p:
        raise FileNotFoundError(f"Font not found in api/fonts: {font_name}")
    return p


def _asset_path(rel: str) -> Optional[str]:
    # rel вроде "icons/star.png"
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


def _paste_rgba(dst: Image.Image, src: Image.Image, xy: Tuple[int, int]) -> None:
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    dst.alpha_composite(src, xy)


def _circle_crop(im: Image.Image, diameter: int) -> Image.Image:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    im = ImageOps.fit(im, (diameter, diameter), Image.LANCZOS, centering=(0.5, 0.35))
    mask = Image.new("L", (diameter, diameter), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask=mask)
    return out


def _load_icon(name: str, size: int, no_circle: bool = False) -> Image.Image:
    # name: "star.png" / "poop.png"
    rel = os.path.join("icons", name)
    p = _asset_path(rel)
    if not p:
        # fallback — простой маркер
        im = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((0, 0, size - 1, size - 1), outline=(255, 215, 0, 255), width=3)
        return im
    im = Image.open(p).convert("RGBA")
    im = ImageOps.contain(im, (size, size), Image.LANCZOS)
    if no_circle:
        return im
    # круглый бейдж
    badge = Image.new("RGBA", (size + 16, size + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)
    d.ellipse((0, 0, badge.width - 1, badge.height - 1), fill=(255, 255, 255, 255))
    _paste_rgba(badge, im, ((badge.width - im.width) // 2, (badge.height - im.height) // 2))
    return badge


def _draw_gradient_rect(canvas: Image.Image, box: Tuple[int, int, int, int], c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    grad = Image.new("RGBA", (w, h), 0)
    draw = ImageDraw.Draw(grad)
    for i in range(w):
        t = i / max(w - 1, 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(i, 0), (i, h)], fill=(r, g, b, 255))
    _paste_rgba(canvas, grad, (x0, y0))


def _split_stats(stats: str) -> List[Tuple[str, str]]:
    """
    Принимает строку вида: "30 очков, 11 подборов, 11-14 с игры"
    Возвращает список [(value, label), ...]
    """
    if not stats:
        return []
    parts = [p.strip() for p in stats.replace(";", ",").split(",") if p.strip()]
    out: List[Tuple[str, str]] = []
    for p in parts:
        # выделяем первое «слово» как value, остальное — label
        tokens = p.split()
        if not tokens:
            continue
        value = tokens[0]
        label = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        out.append((value, label))
    return out[:3]


def _build_left_module(
    canvas: Image.Image,
    baseline_y: int,
    player_img: Image.Image,
    team_logo_img: Image.Image,
    name_ru: str,
    stats: str,
    c_left: Tuple[int, int, int],
    c_right: Tuple[int, int, int],
    extra_right_w: int = 0,
) -> Tuple[int, int, int, int]:
    """
    Рисует один левый блок (градиентную плашку) и возвращает его bbox.
    Ширина — адаптивная по контенту + optional правый блок.
    """
    draw = ImageDraw.Draw(canvas)

    font_name = _font(FONT_MONTSERRAT_BOLD, NAME_SIZE)
    font_stat_val = _font(FONT_EXO2_BOLD, STAT_VALUE_SIZE)
    font_stat_lbl = _font(FONT_MONTSERRAT_SEMI, STAT_LABEL_SIZE)

    # Измеряем имя
    name_w, name_h = _measure(draw, name_ru.upper(), font_name)

    # Готовим статистику
    stat_pairs = _split_stats(stats)
    col_gap = 44
    vals_w = 0
    for value, label in stat_pairs:
        vw, _ = _measure(draw, value, font_stat_val)
        lw, _ = _measure(draw, label.upper(), font_stat_lbl)
        vals_w += max(vw, lw)
    vals_w += col_gap * max(len(stat_pairs) - 1, 0)

    # Минимальная ширина блока: логотип + голова + отступы + имя + статистика
    head_w = HEAD_D
    logo_w = LOGO_CIRCLE_D
    inner_pad = 26
    min_w = PADDING_X + logo_w + inner_pad + head_w + inner_pad + max(name_w, vals_w) + PADDING_X

    block_w = min_w + extra_right_w
    max_w = int(CANVAS_W * SAFE_W_RATIO)
    block_w = min(block_w, max_w)

    # Центруем общий блок (включая extra_right_w)
    x0 = (CANVAS_W - block_w) // 2
    x1 = x0 + block_w
    y1 = CANVAS_H - PADDING_Y
    y0 = y1 - BAR_H

    # Фоновый градиент
    _draw_gradient_rect(canvas, (x0, y0, x1, y1), c_left, c_right)

    # Позиции слева: логотип и голова
    # Белый круг (меньше), внутри — бОльший логотип
    logo_cx = x0 + PADDING_X + LOGO_CIRCLE_D // 2
    logo_cy = y1 - BAR_H // 2 + 6
    logo_badge = Image.new("RGBA", (LOGO_CIRCLE_D, LOGO_CIRCLE_D), (0, 0, 0, 0))
    d = ImageDraw.Draw(logo_badge)
    d.ellipse((0, 0, LOGO_CIRCLE_D - 1, LOGO_CIRCLE_D - 1), fill=(255, 255, 255, 255))
    if team_logo_img is not None:
        tl = ImageOps.contain(team_logo_img.convert("RGBA"), (LOGO_INNER_D, LOGO_INNER_D), Image.LANCZOS)
        _paste_rgba(logo_badge, tl, ((logo_badge.width - tl.width)//2, (logo_badge.height - tl.height)//2))
    _paste_rgba(canvas, logo_badge, (logo_cx - logo_badge.width//2, logo_cy - logo_badge.height//2))

    # Голова игрока: круг вырезаем и чуть «выпираем» вверх сверх плашки
    head = _circle_crop(player_img, HEAD_D) if player_img is not None else Image.new("RGBA", (HEAD_D, HEAD_D), (0, 0, 0, 0))
    head_x = x0 + PADDING_X + LOGO_CIRCLE_D + 20
    head_y = y1 - HEAD_D + HEAD_OFFSET_Y - (BAR_H // 2)
    _paste_rgba(canvas, head, (head_x, head_y))

    # Текстовая колонка (имя и статистика) центрируется по своей колонке
    text_left = head_x + HEAD_D + inner_pad
    text_right = x1 - PADDING_X - extra_right_w
    text_center = (text_left + text_right) // 2

    # Имя
    name_text = name_ru.upper()
    name_w, name_h = _measure(draw, name_text, font_name)
    name_x = text_center - name_w // 2
    name_y = y0 + 34
    draw.text((name_x, name_y), name_text, font=font_name, fill=(255, 255, 255, 255))

    # Статистика — три колонки, центрируем общий блок
    cols = []
    total_cols_w = 0
    for value, label in stat_pairs:
        vw, vh = _measure(draw, value, font_stat_val)
        lw, lh = _measure(draw, label.upper(), font_stat_lbl)
        cols.append((max(vw, lw), (value, label)))
        total_cols_w += max(vw, lw)
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


# =========================
# Public API (совместимость)
# =========================
def render_card(canvas, player_img, team_logo_img, name_ru, stats, *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        player_img, team_logo_img, name_ru, stats,
        GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=0
    )
    return canvas


def render_cardbad(canvas, player_img, team_logo_img, name_ru, stats, *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    x0, y0, x1, y1 = _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        player_img, team_logo_img, name_ru, stats,
        GRAD_BAD_L, GRAD_BAD_R, extra_right_w=0
    )
    # 💩 рядом с именем (ищем x по центру текстового блока)
    draw = ImageDraw.Draw(canvas)
    font_name = _font(FONT_MONTSERRAT_BOLD, NAME_SIZE)
    name_text = name_ru.upper()
    name_w, name_h = _measure(draw, name_text, font_name)
    # Примерно тот же центр, что и в _build_left_module:
    # вычислим ориентировочно левую границу текста по содержимому
    # (ставим эмодзи слева от имени с небольшим отступом)
    left_pad = PADDING_X + LOGO_CIRCLE_D + 20 + HEAD_D + 26
    right_pad = x1 - PADDING_X
    center = (left_pad + right_pad) // 2
    name_x = center - name_w // 2
    name_y = y0 + 34
    poop = _load_icon("poop.png", size=34, no_circle=True)
    _paste_rgba(canvas, poop, (max(x0 + PADDING_X, name_x - 44), name_y + (name_h - poop.height)//2))
    return canvas


def render_cards(canvas, player_img, team_logo_img, name_ru, stats, right_text="молодец", *unused, **kwargs):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    # сначала — левый модуль, справа зарезервируем место под чёрный блок
    # ширину правого блока — от текста
    draw = ImageDraw.Draw(canvas)
    f_right = _font(FONT_MONTSERRAT_SEMI, RIGHT_TEXT_SIZE)
    txt_w, txt_h = _measure(draw, right_text, f_right)
    right_w = max(320, txt_w + 120)

    x0, y0, x1, y1 = _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        player_img, team_logo_img, name_ru, stats,
        GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=right_w
    )

    # правый блок — без зазора
    r_x0 = x1 - right_w
    r_x1 = x1
    _draw_gradient_rect(canvas, (r_x0, y0, r_x1, y1), (36, 36, 36), (20, 20, 20))

    star = _load_icon("star.png", size=34, no_circle=True)
    icon_y = y0 + BAR_H // 2 - star.height // 2
    _paste_rgba(canvas, star, (r_x0 + 28, icon_y))
    draw.text((r_x0 + 28 + star.width + 16, icon_y + (star.height - RIGHT_TEXT_SIZE)//2),
              right_text, font=f_right, fill=(255, 255, 255, 255))
    return canvas


def render_card2(
    canvas,
    left_player_img, left_team_logo, left_name_ru, left_stats,
    right_player_img, right_team_logo, right_name_ru, right_stats,
    *unused, **kwargs
):
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))

    # Рисуем левый на отдельном слое, чтобы получить ширину
    layer_left = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    lx0, ly0, lx1, ly1 = _build_left_module(
        layer_left, CANVAS_H - PADDING_Y,
        left_player_img, left_team_logo, left_name_ru, left_stats,
        GRAD_PURPLE_L, GRAD_PURPLE_R, extra_right_w=0
    )
    left_crop = layer_left.crop((lx0, ly0, lx1, ly1))
    left_w = left_crop.width

    # Правый — тоже на слое
    layer_right = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    rx0, ry0, rx1, ry1 = _build_left_module(
        layer_right, CANVAS_H - PADDING_Y,
        right_player_img, right_team_logo, right_name_ru, right_stats,
        GRAD_BLUE_L, GRAD_BLUE_R, extra_right_w=0
    )
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
