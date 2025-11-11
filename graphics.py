# graphics.py — DROP-IN v2
from __future__ import annotations

import os
from io import BytesIO
from typing import List, Tuple, Optional, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


# ---------------------------
# Канва и размеры
# ---------------------------
CANVAS_W = 1920
CANVAS_H = 1080

# Высоты баров
BAR_H_SINGLE = 220
BAR_H_DUO    = 240
BAR_H_RIGHT  = BAR_H_SINGLE  # для /cards правой панели

PADDING = 28

# Диаметры головы (card/cardbad/cards — меньше на ~1.3; card2 — компакт под бар)
HEAD_D_SINGLE = 246   # ~320 / 1.3
HEAD_D_DUO    = 220

# Логотип команды (x1.5 и ниже)
LOGO_DISC_D_SINGLE = 135
LOGO_INNER_D_SINGLE = 128

LOGO_DISC_D_DUO = 135
LOGO_INNER_D_DUO = 128

# Иконки
ICON_SIZE = 28

# Типографика
# single/cards: имя чуть меньше, статы немного крупнее
NAME_SIZE_SINGLE = 58
STAT_VALUE_SIZE_SINGLE = 42
STAT_LABEL_SIZE = 20

# duo: одинаковые размеры у обоих
NAME_SIZE_DUO = 58
STAT_VALUE_SIZE_DUO = 48

RIGHT_TEXT_SIZE = 28

# Фикс-градиенты
GRAD_ORANGE_L = (255, 140, 0)
GRAD_ORANGE_R = (255, 201, 71)

GRAD_PURPLE_L = (79, 46, 126)
GRAD_PURPLE_R = (45, 24, 89)

GRAD_BLUE_L   = (24, 74, 130)
GRAD_BLUE_R   = (17, 47, 89)

GRAD_BAD_L = (84, 54, 48)
GRAD_BAD_R = (66, 44, 40)

# Предел ширины «под контент» (для одиночных)
SAFE_W_RATIO = 0.90


# ---------------------------
# Поиск шрифтов/иконок
# ---------------------------
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

_TRIED_FONTS_CACHE = {}

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
    for base in ASSET_DIRS:
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------
# Утилиты рендера
# ---------------------------
def _ensure_font(filename: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_resolve_font_path(filename), size=size)

def _font_name(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font("Montserrat-Bold.ttf", sz)

def _font_value(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font("Exo2-Bold.ttf", sz)

def _font_label(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font("Montserrat-SemiBold.ttf", sz)

def _font_right(sz: int) -> ImageFont.FreeTypeFont:
    return _ensure_font("Montserrat-SemiBold.ttf", sz)

def _as_image(obj: Any) -> Optional[Image.Image]:
    if obj is None:
        return None
    if isinstance(obj, Image.Image):
        return obj.convert("RGBA")
    if isinstance(obj, (bytes, bytearray)):
        return Image.open(BytesIO(obj)).convert("RGBA")
    if isinstance(obj, str):
        if os.path.isfile(obj):
            return Image.open(obj).convert("RGBA")
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
    face = ImageOps.fit(img, (diam, diam), Image.LANCZOS, centering=(0.5, 0.34))
    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam, diam), (0,0,0,0))
    out.paste(face, (0,0), mask)
    return out

def _load_icon(name: str, size: int) -> Image.Image:
    rel = os.path.join("icons", name)
    p = _asset_path(rel)
    if not p:
        return Image.new("RGBA", (size, size), (0,0,0,0))
    im = Image.open(p).convert("RGBA")
    return ImageOps.contain(im, (size, size), Image.LANCZOS)

def _fit_same_font_size(draw: ImageDraw.ImageDraw, texts: List[str], base_size: int, min_size: int, max_w: int, font_loader) -> ImageFont.FreeTypeFont:
    sz = base_size
    while sz >= min_size:
        f = font_loader(sz)
        ok = True
        for t in texts:
            w, _ = _measure(draw, t, f)
            if w > max_w:
                ok = False
                break
        if ok:
            return f
        sz -= 2
    return font_loader(min_size)


# ---------------------------
# Базовый блок для одиночных (подгон по контенту)
# ---------------------------
def _build_single_block(
    canvas: Image.Image,
    name_ru: str,
    team_logo_img: Optional[Image.Image],
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    grad_left: Tuple[int,int,int],
    grad_right: Tuple[int,int,int],
) -> Tuple[int,int,int,int]:
    draw = ImageDraw.Draw(canvas)

    bar_h = BAR_H_SINGLE
    head_d = HEAD_D_SINGLE
    logo_d = LOGO_DISC_D_SINGLE
    logo_in = LOGO_INNER_D_SINGLE

    f_name = _font_name(NAME_SIZE_SINGLE)
    f_val  = _font_value(STAT_VALUE_SIZE_SINGLE)
    f_lbl  = _font_label(STAT_LABEL_SIZE)

    name_text = str(name_ru or "").upper()
    name_w, name_h = _measure(draw, name_text, f_name)

    gap = 34
    cols = []
    total_cols_w = 0
    for value, label in (stats or [])[:3]:
        v = str(value)
        l = str(label).upper()
        vw, _ = _measure(draw, v, f_val)
        lw, _ = _measure(draw, l, f_lbl)
        w = max(vw, lw)
        cols.append((w, (v, l)))
        total_cols_w += w
    if cols:
        total_cols_w += gap * (len(cols) - 1)

    left_fixed = PADDING + logo_d + 18 + head_d + 24
    right_text_w = max(name_w, total_cols_w)
    min_block_w = left_fixed + right_text_w + PADDING
    block_w = int(min(min_block_w, CANVAS_W * SAFE_W_RATIO))

    x0 = (CANVAS_W - block_w) // 2
    y1 = CANVAS_H - PADDING
    y0 = y1 - bar_h
    x1 = x0 + block_w

    bar = _grad_lr(block_w, bar_h, grad_left, grad_right)
    canvas.alpha_composite(bar, (x0, y0))

    # Лого ниже (почти к нижней границе)
    logo_x = x0 + PADDING
    logo_y = y0 + bar_h - logo_d - 6
    if team_logo_img is not None:
        disc = Image.new("RGBA", (logo_d, logo_d), (0,0,0,0))
        d = ImageDraw.Draw(disc)
        d.ellipse((0,0,logo_d-1,logo_d-1), fill=(255,255,255,255))
        lg = ImageOps.contain(team_logo_img, (logo_in, logo_in), Image.LANCZOS)
        disc.alpha_composite(lg, ((disc.width - lg.width)//2, (disc.height - lg.height)//2))
        canvas.alpha_composite(disc, (logo_x, logo_y))

    # Голова — левее на ~24px и на 8px выше низа бара
    face = _circle_crop_face(headshot_img, head_d)
    face_x = logo_x + logo_d + 18 - 24
    face_y = y1 - head_d - 8
    canvas.alpha_composite(face, (face_x, max(0, face_y)))

    # Текстовая колонна
    text_left  = face_x + head_d + 24
    text_right = x1 - PADDING
    text_center = (text_left + text_right) // 2

    name_x = text_center - name_w // 2
    name_y = y0 + 22
    draw.text((name_x, name_y), name_text, font=f_name, fill=(255,255,255,255))

    if cols:
        stats_y_val  = name_y + name_h + 16
        stats_y_lbl  = stats_y_val + STAT_VALUE_SIZE_SINGLE + 6
        cur_x = text_center - total_cols_w // 2
        for w, (v, l) in cols:
            vw, _ = _measure(draw, v, f_val)
            lw, _ = _measure(draw, l, f_lbl)
            draw.text((cur_x + (w - vw)//2, stats_y_val), v, font=f_val, fill=(255,255,255,255))
            draw.text((cur_x + (w - lw)//2, stats_y_lbl), l, font=f_lbl, fill=(255,255,255,210))
            cur_x += w + gap

    return (x0, y0, x1, y1)


# ---------------------------
# Duo (во всю ширину, прикреплён к низу)
# ---------------------------
def _build_duo_side(
    canvas: Image.Image,
    x0: int,
    name: str,
    logo_img: Optional[Image.Image],
    head_img: Image.Image,
    stats: List[Tuple[str,str]],
    grad_left: Tuple[int,int,int],
    grad_right: Tuple[int,int,int],
    # общие метки Y для выравнивания (имя и статы одинаково по высоте)
    name_y: int,
    stats_y_val: int,
    stats_y_lbl: int,
    # одинаковые шрифты на обеих сторонах
    f_name: ImageFont.FreeTypeFont,
    f_val: ImageFont.FreeTypeFont,
    f_lbl: ImageFont.FreeTypeFont,
) -> None:
    draw = ImageDraw.Draw(canvas)

    half_w = CANVAS_W // 2
    bar_h = BAR_H_DUO
    head_d = HEAD_D_DUO
    logo_d = LOGO_DISC_D_DUO
    logo_in = LOGO_INNER_D_DUO

    # Фон половины
    bar = _grad_lr(half_w, bar_h, grad_left, grad_right)
    y0 = CANVAS_H - PADDING - bar_h
    canvas.alpha_composite(bar, (x0, y0))
    y1 = y0 + bar_h

    # Лого — почти у низа
    logo_x = x0 + PADDING
    logo_y = y0 + bar_h - logo_d - 6
    if logo_img is not None:
        disc = Image.new("RGBA", (logo_d, logo_d), (0,0,0,0))
        d = ImageDraw.Draw(disc)
        d.ellipse((0,0,logo_d-1,logo_d-1), fill=(255,255,255,255))
        lg = ImageOps.contain(logo_img, (logo_in, logo_in), Image.LANCZOS)
        disc.alpha_composite(lg, ((disc.width - lg.width)//2, (disc.height - lg.height)//2))
        canvas.alpha_composite(disc, (logo_x, logo_y))

    # Голова — левее и на 8px выше низа бара
    face = _circle_crop_face(head_img, head_d)
    face_x = logo_x + logo_d + 18 - 24
    face_y = y1 - head_d - 8
    canvas.alpha_composite(face, (face_x, max(0, face_y)))

    # Текстовая колонна (центр между головой и правой кромкой половины)
    text_left  = face_x + head_d + 24
    text_right = x0 + half_w - PADDING
    text_center = (text_left + text_right) // 2

    name_text = str(name or "").upper()
    name_w, name_h = _measure(draw, name_text, f_name)
    name_x = text_center - name_w // 2
    draw.text((name_x, name_y), name_text, font=f_name, fill=(255,255,255,255))

    # Статы
    gap = 34
    cols = []
    total_cols_w = 0
    for value, label in (stats or [])[:3]:
        v = str(value)
        l = str(label).upper()
        vw, _ = _measure(draw, v, f_val)
        lw, _ = _measure(draw, l, f_lbl)
        w = max(vw, lw)
        cols.append((w, (v, l)))
        total_cols_w += w
    if cols:
        total_cols_w += gap * (len(cols) - 1)
        cur_x = text_center - total_cols_w // 2
        for w, (v, l) in cols:
            vw, _ = _measure(draw, v, f_val)
            lw, _ = _measure(draw, l, f_lbl)
            draw.text((cur_x + (w - vw)//2, stats_y_val), v, font=f_val, fill=(255,255,255,255))
            draw.text((cur_x + (w - lw)//2, stats_y_lbl), l, font=f_lbl, fill=(255,255,255,210))
            cur_x += w + gap


# ---------------------------
# Публичные рендеры
# ---------------------------
def render_card(
    mode: str,
    player_name_ru: str,
    subtitle: str,
    team_logo_img: Any,
    colors: Tuple[str,str,str],
    headshot_img: Any,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    head = _as_image(headshot_img) or Image.new("RGBA", (HEAD_D_SINGLE, HEAD_D_SINGLE), (0,0,0,0))
    logo = _as_image(team_logo_img)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    _build_single_block(canvas, player_name_ru, logo, head, stats, GRAD_ORANGE_L, GRAD_ORANGE_R)
    return _to_png_bytes(canvas)


def render_card2(
    name1: str, logo1: Any, colors1: Tuple[str,str,str], head1: Any, stats1: List[Tuple[str,str]],
    name2: str, logo2: Any, colors2: Tuple[str,str,str], head2: Any, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    head1 = _as_image(head1) or Image.new("RGBA", (HEAD_D_DUO, HEAD_D_DUO), (0,0,0,0))
    head2 = _as_image(head2) or Image.new("RGBA", (HEAD_D_DUO, HEAD_D_DUO), (0,0,0,0))
    logo1 = _as_image(logo1)
    logo2 = _as_image(logo2)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)

    # Общие Y для выравнивания
    y0 = CANVAS_H - PADDING - BAR_H_DUO
    name_y      = y0 + 20
    stats_y_val = name_y + NAME_SIZE_DUO + 16
    stats_y_lbl = stats_y_val + STAT_VALUE_SIZE_DUO + 6

    # Один и тот же размер шрифта имени на обеих половинах
    half_w = CANVAS_W // 2
    # прикидываем максимально возможную ширину текстовой колонны (без головы/логотипа)
    # приблизительно:
    max_text_w = half_w - (PADDING + LOGO_DISC_D_DUO + 18 - 24 + HEAD_D_DUO + 24) - PADDING
    f_name = _fit_same_font_size(draw, [str(name1 or "").upper(), str(name2 or "").upper()],
                                 NAME_SIZE_DUO, 40, max_text_w, _font_name)
    f_val = _font_value(STAT_VALUE_SIZE_DUO)
    f_lbl = _font_label(STAT_LABEL_SIZE)

    # Левая половина (фиолетовый)
    _build_duo_side(canvas, 0, name1, logo1, head1, stats1,
                    GRAD_PURPLE_L, GRAD_PURPLE_R,
                    name_y, stats_y_val, stats_y_lbl,
                    f_name, f_val, f_lbl)

    # Правая половина (синий)
    _build_duo_side(canvas, half_w, name2, logo2, head2, stats2,
                    GRAD_BLUE_L, GRAD_BLUE_R,
                    name_y, stats_y_val, stats_y_lbl,
                    f_name, f_val, f_lbl)

    return _to_png_bytes(canvas)


def render_card_special(
    name: str,
    logo: Any,
    colors: Tuple[str,str,str],
    head: Any,
    stats: List[Tuple[str,str]],
    right_text: str,
    **kwargs
) -> bytes:
    head_img = _as_image(head) or Image.new("RGBA", (HEAD_D_SINGLE, HEAD_D_SINGLE), (0,0,0,0))
    logo_img = _as_image(logo)

    # Оценим ширину правой панели по тексту
    tmp = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    dtmp = ImageDraw.Draw(tmp)
    f_right = _font_right(RIGHT_TEXT_SIZE)
    tw, _ = _measure(dtmp, str(right_text or ""), f_right)
    right_w = max(320, min(int(CANVAS_W * 0.33), tw + 120))

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    # Левый блок «под контент» плюс место под правую панель (визуально сцеплены)
    x0, y0, x1, y1 = _build_single_block(canvas, name, logo_img, head_img, stats,
                                         GRAD_ORANGE_L, GRAD_ORANGE_R)
    # рисуем правую панель ровно встык к левому блоку
    rx0 = x1 - right_w
    y0p = y1 - BAR_H_RIGHT
    bar = _grad_lr(right_w, BAR_H_RIGHT, (36,36,36), (20,20,20))
    canvas.alpha_composite(bar, (rx0, y0p))

    # Звезда без белого круга
    star = _load_icon("star.png", ICON_SIZE)
    icon_y = y0p + (BAR_H_RIGHT - star.height)//2
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
    head_img = _as_image(head) or Image.new("RGBA", (HEAD_D_SINGLE, HEAD_D_SINGLE), (0,0,0,0))
    logo_img = _as_image(team_logo_img)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x0, y0, x1, y1 = _build_single_block(canvas, name, logo_img, head_img, stats,
                                         GRAD_BAD_L, GRAD_BAD_R)

    # Иконка 💩 рядом с именем (без белого круга)
    draw = ImageDraw.Draw(canvas)
    f_name = _font_name(NAME_SIZE_SINGLE)
    name_text = str(name or "").upper()
    name_w, name_h = _measure(draw, name_text, f_name)

    # как и в single: вычислим текстовую колонну ещё раз
    # (формулы синхронизированы с _build_single_block)
    head_d = HEAD_D_SINGLE
    logo_d = LOGO_DISC_D_SINGLE
    text_left  = x0 + (PADDING + logo_d + 18 + head_d + 24) - 24 + head_d + 24 - head_d - 24  # безопасный центр расчёта
    text_left  = x0 + PADDING + logo_d + 18 - 24 + head_d + 24
    text_right = x1 - PADDING
    text_center = (text_left + text_right) // 2
    name_x = text_center - name_w // 2
    name_y = y0 + 22

    poop = _load_icon("poop.png", ICON_SIZE)
    canvas.alpha_composite(poop, (max(x0 + PADDING, name_x - (poop.width + 16)),
                                  name_y + (name_h - poop.height)//2))
    return _to_png_bytes(canvas)
