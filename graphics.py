# api/graphics.py — DROP-IN совместимая версия под telegram.py
from __future__ import annotations

import os
from io import BytesIO
from typing import List, Tuple, Optional, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


# -----------------------------
# Константы макета
# -----------------------------
CANVAS_W = 1920
CANVAS_H = 1080

# Высота нижней «шторы» — компактнее
BAR_H = 220
PADDING = 28

# Диаметры
HEAD_D = 300               # круг головы крупнее — не режет макушку
LOGO_DISC_D = 92           # белый круг меньше
LOGO_INNER_D = 86          # сам логотип внутри крупнее
ICON_SIZE = 28             # размер иконок (звезда/какашка) без белого круга

# Типографика (единые размеры для всех карточек)
# Имена поменьше, цифры ещё меньше, подписи совсем маленькие
NAME_SIZE = 64
STAT_VALUE_SIZE = 46
STAT_LABEL_SIZE = 22
RIGHT_TEXT_SIZE = 30

# Фикс-градиенты (по ТЗ: card/cards — оранжевый; card2 — фиолетовый + синий; cardbad — коричневый)
GRAD_ORANGE_L = (255, 140, 0)
GRAD_ORANGE_R = (255, 201, 71)

GRAD_PURPLE_L = (79, 46, 126)
GRAD_PURPLE_R = (45, 24, 89)

GRAD_BLUE_L   = (24, 74, 130)
GRAD_BLUE_R   = (17, 47, 89)

GRAD_BAD_L = (84, 54, 48)
GRAD_BAD_R = (66, 44, 40)

# Ограничение по ширине блока (чтобы не растягивался во всю ширину без нужды)
SAFE_W_RATIO = 0.90


# -----------------------------
# Пути к шрифтам и иконкам
# -----------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")  # ИСКЛЮЧИТЕЛЬНО api/fonts — как просили

FONT_EXO2_BOLD = os.path.join(FONT_DIR, "Exo2-Bold.ttf")
FONT_MONTSERRAT_BOLD = os.path.join(FONT_DIR, "Montserrat-Bold.ttf")
FONT_MONTSERRAT_SEMI = os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf")

ASSETS_DIRS = [
    os.path.join(os.path.dirname(HERE), "assets"),
    os.path.join(HERE, "assets"),
]

def _asset_path(rel: str) -> Optional[str]:
    for base in ASSETS_DIRS:
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    return None


# -----------------------------
# Утилиты: шрифты, изображение, текст
# -----------------------------
def _ensure_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.isfile(path):
        # Явно падаем, если шрифт не найден (по ТЗ — только ваши шрифты)
        raise FileNotFoundError(f"Font not found: {path}")
    return ImageFont.truetype(path, size=size)

def _font_name(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font(FONT_MONTSERRAT_BOLD, sz)

def _font_value(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font(FONT_EXO2_BOLD, sz)

def _font_label(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font(FONT_MONTSERRAT_SEMI, sz)

def _font_right(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font(FONT_MONTSERRAT_SEMI, sz)

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
        # относительный к assets
        for base in ASSETS_DIRS:
            p = os.path.join(base, obj)
            if os.path.isfile(p):
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
    Вписываем голову в круг; чуть сдвигаем центр вверх, чтобы не резало макушку.
    """
    # Чуть смещаем центр по Y (больше "головы" выше)
    face = ImageOps.fit(img, (diam, diam), Image.LANCZOS, centering=(0.5, 0.38))
    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam, diam), (0,0,0,0))
    out.paste(face, (0,0), mask)
    return out

def _load_icon(name: str, size: int, plain: bool=True) -> Image.Image:
    # plain=True -> без белого круга (по ТЗ)
    rel = os.path.join("icons", name)
    p = _asset_path(rel)
    if not p:
        # Фолбэк: пустой прозрачный
        return Image.new("RGBA", (size, size), (0,0,0,0))
    im = Image.open(p).convert("RGBA")
    im = ImageOps.contain(im, (size, size), Image.LANCZOS)
    if plain:
        return im
    # белый круг (на всякий случай, но нам сейчас не нужен)
    circle = Image.new("RGBA", (size+12, size+12), (0,0,0,0))
    d = ImageDraw.Draw(circle)
    d.ellipse((0,0,circle.width-1,circle.height-1), fill=(255,255,255,255))
    circle.alpha_composite(im, ((circle.width-im.width)//2, (circle.height-im.height)//2))
    return circle


# -----------------------------
# Вспомогательный «левый модуль»
# (лого + голова + имя + статы)
# -----------------------------
def _build_left_block(
    canvas: Image.Image,
    name_ru: str,
    team_logo_img: Optional[Image.Image],
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    grad_left: Tuple[int,int,int],
    grad_right: Tuple[int,int,int],
    extra_right_w: int = 0,   # для cards (правая колонка)
) -> Tuple[int,int,int,int]:
    """
    Рисует левый блок и возвращает его bounding box (x0,y0,x1,y1).
    Блок по ширине = содержимому, ограничен SAFE_W_RATIO от ширины экрана.
    Все тексты центрируются относительно колонны имени.
    """
    draw = ImageDraw.Draw(canvas)

    # Приводим имя к строке (устраняет "'Image' object has no attribute 'upper'")
    name_text = str(name_ru or "").upper()

    # Шрифты
    f_name = _font_name(NAME_SIZE)
    f_val  = _font_value(STAT_VALUE_SIZE)
    f_lbl  = _font_label(STAT_LABEL_SIZE)

    # Метрики текста
    name_w, name_h = _measure(draw, name_text, f_name)

    # Метрики статов
    cols: List[Tuple[int, Tuple[str,str]]] = []
    gap = 36
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

    # Итоговая минимальная ширина блока по контенту
    left_fixed = PADDING + LOGO_DISC_D + 18 + HEAD_D + 24   # лого + отступ + голова + отступ до текста
    right_text_w = max(name_w, total_cols_w)
    min_block_w = left_fixed + right_text_w + PADDING + extra_right_w

    block_w = int(min(min_block_w, CANVAS_W * SAFE_W_RATIO))
    block_h = BAR_H

    # Позиционируем блок по центру внизу
    x0 = (CANVAS_W - block_w) // 2
    y1 = CANVAS_H - PADDING
    y0 = y1 - block_h
    x1 = x0 + block_w

    # Градиентный фон без скруглений
    bar = _grad_lr(block_w, block_h, grad_left, grad_right)
    canvas.alpha_composite(bar, (x0, y0))

    # Логотип слева в белом кружке (кружок — меньше, сам логотип — крупнее)
    if team_logo_img is not None:
        disc = Image.new("RGBA", (LOGO_DISC_D, LOGO_DISC_D), (0,0,0,0))
        d = ImageDraw.Draw(disc)
        d.ellipse((0,0,LOGO_DISC_D-1,LOGO_DISC_D-1), fill=(255,255,255,255))
        lg = ImageOps.contain(team_logo_img, (LOGO_INNER_D, LOGO_INNER_D), Image.LANCZOS)
        disc.alpha_composite(lg, ((disc.width - lg.width)//2, (disc.height - lg.height)//2))
        logo_x = x0 + PADDING
        logo_y = y0 + (block_h - LOGO_DISC_D)//2
        canvas.alpha_composite(disc, (logo_x, logo_y))
    else:
        logo_x = x0 + PADDING

    # Голова — круг, чуть выходит вверх/вниз от шторы, центр по высоте блока
    face = _circle_crop_face(headshot_img, HEAD_D)
    face_x = logo_x + LOGO_DISC_D + 18
    face_y = y0 + (block_h - HEAD_D)//2 - 4  # слегка выше центра
    face_y = max(0, face_y)                  # не вылезти за канвас
    canvas.alpha_composite(face, (face_x, face_y))

    # Текстовая колонна
    text_left  = face_x + HEAD_D + 24
    text_right = x1 - PADDING - extra_right_w
    text_center = (text_left + text_right) // 2

    # Имя (по центру своей области)
    name_x = text_center - name_w // 2
    name_y = y0 + 28
    draw.text((name_x, name_y), name_text, font=f_name, fill=(255,255,255,255))

    # Статы (по центру под именем)
    if cols:
        stats_y_val  = name_y + name_h + 18
        stats_y_lbl  = stats_y_val + STAT_VALUE_SIZE + 6
        cur_x = text_center - total_cols_w // 2
        for w, (v, l) in cols:
            vw, _ = _measure(draw, v, f_val)
            lw, _ = _measure(draw, l, f_lbl)
            draw.text((cur_x + (w - vw)//2, stats_y_val), v, font=f_val, fill=(255,255,255,255))
            draw.text((cur_x + (w - lw)//2, stats_y_lbl), l, font=f_lbl, fill=(255,255,255,210))
            cur_x += w + gap

    return (x0, y0, x1, y1)


# -----------------------------
# Публичный API (СТРОГО как ждёт telegram.py)
# -----------------------------
def render_card(
    mode: str,                    # "single" — игнорируем
    player_name_ru: str,
    subtitle: str,                # игнорируем
    team_logo_img: Any,
    colors: Tuple[str,str,str],   # игнорируем (у нас фикс-градиенты)
    headshot_img: Any,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    """
    /card <имя> | <статы>
    Фон — оранжевый градиент.
    """
    # Привести входы к Image
    head = _as_image(headshot_img)
    logo = _as_image(team_logo_img)
    if head is None:
        head = Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    _build_left_block(canvas, player_name_ru, logo, head, stats, GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=0)
    return _to_png_bytes(canvas)


def render_card2(
    name1: str, logo1: Any, colors1: Tuple[str,str,str], head1: Any, stats1: List[Tuple[str,str]],
    name2: str, logo2: Any, colors2: Tuple[str,str,str], head2: Any, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    """
    /card2 <имя1> | <статы1> || <имя2> | <статы2>
    Левая половина — фиолетовый градиент, правая — синий.
    Плашки соединены без зазора, размеры шрифтов совпадают у обеих сторон.
    """
    head1 = _as_image(head1) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    head2 = _as_image(head2) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo1 = _as_image(logo1)
    logo2 = _as_image(logo2)

    # рендерим каждую половину на своём слое, затем обрезаем до блока и склеиваем
    layer_l = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    l_box = _build_left_block(layer_l, name1, logo1, head1, stats1, GRAD_PURPLE_L, GRAD_PURPLE_R, extra_right_w=0)
    left_crop = layer_l.crop(l_box)

    layer_r = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    r_box = _build_left_block(layer_r, name2, logo2, head2, stats2, GRAD_BLUE_L, GRAD_BLUE_R, extra_right_w=0)
    right_crop = layer_r.crop(r_box)

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
    canvas.alpha_composite(right_crop, (start_x + left_crop.width, y))  # без зазора
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
    """
    /cards <имя> | <статы> | <правый текст>
    Левая часть — оранжевый градиент; правая узкая панель — тёмная; иконка звезды БЕЗ белого круга.
    """
    head_img = _as_image(head) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo_img = _as_image(logo)

    # прикидываем ширину правой панели (по тексту)
    tmp_canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(tmp_canvas)
    f_right = _font_right(RIGHT_TEXT_SIZE)
    tw, th = _measure(draw, str(right_text or ""), f_right)
    right_w = max(320, min(int(CANVAS_W * 0.33), tw + 120))

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0, y0, x1, y1 = _build_left_block(canvas, name, logo_img, head_img, stats, GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=right_w)

    # правая панель без зазора
    rx0 = x1 - right_w
    rx1 = x1
    bar = _grad_lr(right_w, BAR_H, (36,36,36), (20,20,20))
    canvas.alpha_composite(bar, (rx0, y0))

    # звезда без белого круга
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
    """
    /cardbad <имя> | <статы>
    Коричневый градиент, иконка «💩» БЕЗ белого круга, логотип команды слева рядом с головой (как у обычной карточки).
    """
    head_img = _as_image(head) or Image.new("RGBA", (HEAD_D, HEAD_D), (0,0,0,0))
    logo_img = _as_image(team_logo_img)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0, y0, x1, y1 = _build_left_block(canvas, name, logo_img, head_img, stats, GRAD_BAD_L, GRAD_BAD_R, extra_right_w=0)

    # иконка 💩 слева от имени, без белого круга
    draw = ImageDraw.Draw(canvas)
    f_name = _font_name(NAME_SIZE)
    name_text = str(name or "").upper()
    name_w, name_h = _measure(draw, name_text, f_name)
    # вычисляем центр той же текстовой области (как в _build_left_block)
    # повторяем расчёт: left_fixed
    left_fixed = PADDING + LOGO_DISC_D + 18 + HEAD_D + 24
    text_left  = x0 + left_fixed
    text_right = x1 - PADDING
    text_center = (text_left + text_right) // 2
    name_x = text_center - name_w // 2
    name_y = y0 + 28

    poop = _load_icon("poop.png", ICON_SIZE, plain=True)
    canvas.alpha_composite(poop, (max(x0 + PADDING, name_x - (poop.width + 16)), name_y + (name_h - poop.height)//2))
    return _to_png_bytes(canvas)
