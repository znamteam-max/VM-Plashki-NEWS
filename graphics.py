# graphics.py — DROP-IN совместимая версия под telegram.py
from __future__ import annotations

import os
from io import BytesIO
from typing import List, Tuple, Optional, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


# =========================
# Размеры и стили
# =========================
CANVAS_W = 1920
CANVAS_H = 1080

# Компактная «штора»
BAR_H = 220
PADDING = 28

# Диаметры (увеличили голову, чтобы не резать макушку)
HEAD_D = 320
LOGO_DISC_D = 90       # белый круг меньше
LOGO_INNER_D = 86      # сам логотип больше
ICON_SIZE = 28

# Типографика
NAME_SIZE = 62
STAT_VALUE_SIZE = 42
STAT_LABEL_SIZE = 20
RIGHT_TEXT_SIZE = 28

# Фикс-градиенты (по ТЗ)
GRAD_ORANGE_L = (255, 140, 0)
GRAD_ORANGE_R = (255, 201, 71)

GRAD_PURPLE_L = (79, 46, 126)
GRAD_PURPLE_R = (45, 24, 89)

GRAD_BLUE_L   = (24, 74, 130)
GRAD_BLUE_R   = (17, 47, 89)

GRAD_BAD_L = (84, 54, 48)
GRAD_BAD_R = (66, 44, 40)

# Ширина блока ограничена содержимым, но не больше 90% экрана
SAFE_W_RATIO = 0.90


# =========================
# Поиски путей (ТОЛЬКО ваши шрифты/иконки)
# =========================
HERE = os.path.dirname(os.path.abspath(__file__))
ROOTS = [
    HERE,
    os.path.join(HERE, "api"),
    os.path.dirname(HERE),
    os.path.join(os.path.dirname(HERE), "api"),
    os.getcwd(),
]

FONT_DIRS = []
for r in ROOTS:
    FONT_DIRS.append(os.path.join(r, "fonts"))
    FONT_DIRS.append(os.path.join(r, "api", "fonts"))

ASSET_DIRS = []
for r in ROOTS:
    ASSET_DIRS.append(os.path.join(r, "assets"))
    ASSET_DIRS.append(os.path.join(r, "api", "assets"))

def _find_in_dirs(filename: str, dirs: List[str]) -> Optional[str]:
    for d in dirs:
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p
    return None

_TRIED_FONTS_CACHE = {}  # filename -> resolved path or FileNotFoundError text

def _resolve_font_path(filename: str) -> str:
    if filename in _TRIED_FONTS_CACHE:
        val = _TRIED_FONTS_CACHE[filename]
        if isinstance(val, str):
            return val
        raise FileNotFoundError(val)
    p = _find_in_dirs(filename, FONT_DIRS)
    if p:
        _TRIED_FONTS_CACHE[filename] = p
        return p
    tried = [os.path.join(d, filename) for d in FONT_DIRS]
    msg = f"Font not found: {filename}; tried: " + " | ".join(tried)
    _TRIED_FONTS_CACHE[filename] = msg
    raise FileNotFoundError(msg)

def _asset_path(rel: str) -> Optional[str]:
    # rel вроде "icons/star.png"
    for base in ASSET_DIRS:
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    return None


# =========================
# Утилиты: шрифты/рисование
# =========================
def _ensure_font_by_name(filename: str, size: int) -> ImageFont.FreeTypeFont:
    path = _resolve_font_path(filename)
    return ImageFont.truetype(path, size=size)

def _font_name(sz: int) -> ImageFont.FreeTypeFont:
    # Имя игрока — Montserrat-Bold
    return _ensure_font_by_name("Montserrat-Bold.ttf", sz)

def _font_value(sz: int) -> ImageFont.FreeTypeFont:
    # Цифры — Exo2-Bold
    return _ensure_font_by_name("Exo2-Bold.ttf", sz)

def _font_label(sz: int) -> ImageFont.FreeTypeFont:
    # Подписи — Montserrat-SemiBold
    return _ensure_font_by_name("Montserrat-SemiBold.ttf", sz)

def _font_right(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font_by_name("Montserrat-SemiBold.ttf", sz)

def _as_image(obj: Any) -> Optional[Image.Image]:
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
        # относительный к assets (на случай, если передают 'teams/1610612747.png')
        p = _asset_path(obj)
        if p:
            return Image.open(p).convert("RGBA")
        return None
    return None

def _to_png_bytes(img: Image.Image) -> bytes:
    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def _grad_lr(w: int, h: int, c1: Tuple[int,int,int], c2: Tuple[int,int,int]) -> Image.Image:
    im = Image.new("RGBA", (w, h), 0)
    d = ImageDraw.Draw(im)
    for x in range(w):
        t = x / max(1, w - 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        d.line([(x,0),(x,h)], fill=(r,g,b,255))
    return im

def _circle_crop_face(img: Image.Image, diam: int) -> Image.Image:
    """
    Вписываем голову в круг. Центр по Y смещаем вверх (0.34) → в круг попадает макушка.
    """
    face = ImageOps.fit(img, (diam, diam), Image.LANCZOS, centering=(0.5, 0.34))
    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam, diam), (0,0,0,0))
    out.paste(face, (0,0), mask)
    return out

def _load_icon(name: str, size: int, plain: bool=True) -> Image.Image:
    # Иконки лежат в assets/icons
    rel = os.path.join("icons", name)
    p = _asset_path(rel)
    if not p:
        return Image.new("RGBA", (size, size), (0,0,0,0))
    im = Image.open(p).convert("RGBA")
    im = ImageOps.contain(im, (size, size), Image.LANCZOS)
    if plain:
        return im
    # Вариант с белым кругом (сейчас не нужен)
    circle = Image.new("RGBA", (size+12, size+12), (0,0,0,0))
    d = ImageDraw.Draw(circle)
    d.ellipse((0,0,circle.width-1,circle.height-1), fill=(255,255,255,255))
    circle.alpha_composite(im, ((circle.width-im.width)//2, (circle.height-im.height)//2))
    return circle


# =========================
# Сборка левого блока
# =========================
def _build_left_block(
    canvas: Image.Image,
    name_ru: str,
    team_logo_img: Optional[Image.Image],
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    grad_left: Tuple[int,int,int],
    grad_right: Tuple[int,int,int],
    extra_right_w: int = 0,   # ширина правой панели (для /cards)
) -> Tuple[int,int,int,int]:
    """
    Рисует левый блок (лого в белом круге + голова + имя + статы) и
    возвращает (x0,y0,x1,y1) занятую область.
    Ширина блока зависит от контента и ограничена SAFE_W_RATIO.
    Все тексты центрированы относительно «колонны имени».
    """
    draw = ImageDraw.Draw(canvas)

    # Имя — всегда строка
    name_text = str(name_ru or "").upper()

    # Шрифты
    f_name = _font_name(NAME_SIZE)
    f_val  = _font_value(STAT_VALUE_SIZE)
    f_lbl  = _font_label(STAT_LABEL_SIZE)

    # Размер имени
    name_w, name_h = _measure(draw, name_text, f_name)

    # Подготовка статов
    cols: List[Tuple[int, Tuple[str,str]]] = []
    gap = 34
    stats = stats[:3] if stats else []
    total_cols_w = 0
    for value, label in stats:
        v = str(value)
        l = str(label).upper()
        vw, _ = _measure(draw, v, f_val)
        lw, _ = _measure(draw, l, f_lbl)
        w = max(vw, lw)
        cols.append((w, (v, l)))
        total_cols_w += w
    if cols:
        total_cols_w += gap * (len(cols) - 1)

    # Ширина блока: слева фикс (лого+отступ+голова+отступ), справа — по контенту + правая панель (если есть)
    left_fixed = PADDING + LOGO_DISC_D + 18 + HEAD_D + 24
    right_text_w = max(name_w, total_cols_w)
    min_block_w = left_fixed + right_text_w + PADDING + extra_right_w

    block_w = int(min(min_block_w, CANVAS_W * SAFE_W_RATIO))
    block_h = BAR_H

    # Центрируем блок по низу
    x0 = (CANVAS_W - block_w) // 2
    y1 = CANVAS_H - PADDING
    y0 = y1 - block_h
    x1 = x0 + block_w

    # Градиент фона (без скруглений)
    bar = _grad_lr(block_w, block_h, grad_left, grad_right)
    canvas.alpha_composite(bar, (x0, y0))

    # Логотип слева в белом кружке (кружок меньше, логотип больше)
    logo_x = x0 + PADDING
    logo_y = y0 + (block_h - LOGO_DISC_D)//2
    if team_logo_img is not None:
        disc = Image.new("RGBA", (LOGO_DISC_D, LOGO_DISC_D), (0,0,0,0))
        d = ImageDraw.Draw(disc)
        d.ellipse((0,0,LOGO_DISC_D-1,LOGO_DISC_D-1), fill=(255,255,255,255))
        lg = ImageOps.contain(team_logo_img, (LOGO_INNER_D, LOGO_INNER_D), Image.LANCZOS)
        disc.alpha_composite(lg, ((disc.width - lg.width)//2, (disc.height - lg.height)//2))
        canvas.alpha_composite(disc, (logo_x, logo_y))

    # Голова — круг, чуть «приподняли» (centering=0.34), чтобы не резать макушку
    face = _circle_crop_face(headshot_img, HEAD_D)
    face_x = logo_x + LOGO_DISC_D + 18
    face_y = y0 + (block_h - HEAD_D)//2 - 4
    face_y = max(0, face_y)
    canvas.alpha_composite(face, (face_x, face_y))

    # Текстовая колонна (центр относительно области справа от головы)
    text_left  = face_x + HEAD_D + 24
    text_right = x1 - PADDING - extra_right_w
    text_center = (text_left + text_right) // 2

    # Имя
    name_x = text_center - name_w // 2
    name_y = y0 + 28
    draw.text((name_x, name_y), name_text, font=f_name, fill=(255,255,255,255))

    # Статы (центрируются относительно text_center)
    if cols:
        stats_y_val  = name_y + name_h + 16
        stats_y_lbl  = stats_y_val + STAT_VALUE_SIZE + 6
        cur_x = text_center - total_cols_w // 2
        for w, (v, l) in cols:
            vw, _ = _measure(draw, v, f_val)
            lw, _ = _measure(draw, l, f_lbl)
            draw.text((cur_x + (w - vw)//2, stats_y_val), v, font=f_val, fill=(255,255,255,255))
            draw.text((cur_x + (w - lw)//2, stats_y_lbl), l, font=f_lbl, fill=(255,255,255,210))
            cur_x += w + gap

    return (x0, y0, x1, y1)


# =========================
# Публичные функции (сигнатуры ровно как ждёт telegram.py)
# =========================
def render_card(
    mode: str,                    # "single" — игнорируем
    player_name_ru: str,
    subtitle: str,                # игнорируем
    team_logo_img: Any,
    colors: Tuple[str,str,str],   # игнорируем (фикс-градиент)
    headshot_img: Any,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    head = _as_image(headshot_img) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo = _as_image(team_logo_img)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    _build_left_block(canvas, player_name_ru, logo, head, stats, GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=0)
    return _to_png_bytes(canvas)


def render_card2(
    name1: str, logo1: Any, colors1: Tuple[str,str,str], head1: Any, stats1: List[Tuple[str,str]],
    name2: str, logo2: Any, colors2: Tuple[str,str,str], head2: Any, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    head1 = _as_image(head1) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    head2 = _as_image(head2) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo1 = _as_image(logo1)
    logo2 = _as_image(logo2)

    # Левая половина
    layer_l = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    l_box = _build_left_block(layer_l, name1, logo1, head1, stats1, GRAD_PURPLE_L, GRAD_PURPLE_R, extra_right_w=0)
    left_crop = layer_l.crop(l_box)

    # Правая половина
    layer_r = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    r_box = _build_left_block(layer_r, name2, logo2, head2, stats2, GRAD_BLUE_L, GRAD_BLUE_R, extra_right_w=0)
    right_crop = layer_r.crop(r_box)

    # Склейка без зазора, с возможным масштабом до 90% ширины экрана
    total_w = left_crop.width + right_crop.width
    max_w = int(CANVAS_W * SAFE_W_RATIO)
    scale = 1.0 if total_w <= max_w else max_w / total_w
    if scale < 0.999:
        left_crop  = left_crop.resize((int(left_crop.width * scale),  int(left_crop.height * scale)),  Image.LANCZOS)
        right_crop = right_crop.resize((int(right_crop.width * scale), int(right_crop.height * scale)), Image.LANCZOS)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    total_w = left_crop.width + right_crop.width
    start_x = (CANVAS_W - total_w) // 2
    y = CANVAS_H - PADDING - left_crop.height

    canvas.alpha_composite(left_crop, (start_x, y))
    canvas.alpha_composite(right_crop, (start_x + left_crop.width, y))
    return _to_png_bytes(canvas)


def render_card_special(
    name: str,
    logo: Any,
    colors: Tuple[str,str,str],      # игнорируем
    head: Any,
    stats: List[Tuple[str,str]],
    right_text: str,
    **kwargs
) -> bytes:
    head_img = _as_image(head) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo_img = _as_image(logo)

    # Оценка ширины правой панели по тексту
    tmp = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    dtmp = ImageDraw.Draw(tmp)
    f_right = _font_right(RIGHT_TEXT_SIZE)
    tw, _ = _measure(dtmp, str(right_text or ""), f_right)
    right_w = max(320, min(int(CANVAS_W * 0.33), tw + 120))

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0, y0, x1, y1 = _build_left_block(canvas, name, logo_img, head_img, stats,
                                       GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=right_w)

    # Правая панель (тёмная), примыкает без зазора
    rx0 = x1 - right_w
    bar = _grad_lr(right_w, BAR_H, (36,36,36), (20,20,20))
    canvas.alpha_composite(bar, (rx0, y0))

    # Звезда без белого круга
    star = _load_icon("star.png", ICON_SIZE, plain=True)
    icon_y = y0 + (BAR_H - star.height)//2
    canvas.alpha_composite(star, (rx0 + 24, icon_y))

    draw = ImageDraw.Draw(canvas)
    draw.text((rx0 + 24 + star.width + 14, icon_y + (star.height - RIGHT_TEXT_SIZE)//2 + 2),
              str(right_text or ""), font=f_right, fill=(255,255,255,255))

    return _to_png_bytes(canvas)


def render_card_bad(
    name: str,
    head: Any,
    stats: List[Tuple[str,str]],
    team_logo_img: Any = None,
    **kwargs
) -> bytes:
    head_img = _as_image(head) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo_img = _as_image(team_logo_img)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0, y0, x1, y1 = _build_left_block(canvas, name, logo_img, head_img, stats,
                                       GRAD_BAD_L, GRAD_BAD_R, extra_right_w=0)

    # Иконка 💩 без белого круга рядом с именем
    draw = ImageDraw.Draw(canvas)
    f_name = _font_name(NAME_SIZE)
    name_text = str(name or "").upper()
    name_w, name_h = _measure(draw, name_text, f_name)

    left_fixed = PADDING + LOGO_DISC_D + 18 + HEAD_D + 24
    text_left  = x0 + left_fixed
    text_right = x1 - PADDING
    text_center = (text_left + text_right) // 2
    name_x = text_center - name_w // 2
    name_y = y0 + 28

    poop = _load_icon("poop.png", ICON_SIZE, plain=True)
    canvas.alpha_composite(poop, (max(x0 + PADDING, name_x - (poop.width + 16)),
                                  name_y + (name_h - poop.height)//2))
    return _to_png_bytes(canvas)
