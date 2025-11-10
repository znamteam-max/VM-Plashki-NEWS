# api/graphics.py
from __future__ import annotations
import io, os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Paths & strict fonts (ONLY local files from api/fonts)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(__file__)
FONT_DIR = os.path.join(HERE, "fonts")
ICONS_DIR = os.path.join(HERE, "assets", "icons")

def _font_strict(filename: str, size: int) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Font not found: {path}")
    return ImageFont.truetype(path, size=size)

def _font_exo_b(size: int):      # Player name
    return _font_strict("Exo2-Bold.ttf", size)

def _font_mont_b(size: int):     # Numbers
    return _font_strict("Montserrat-Bold.ttf", size)

def _font_mont_sb(size: int):    # Labels
    return _font_strict("Montserrat-SemiBold.ttf", size)

def _to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

# ---------------------------------------------------------------------------
# Canvas / sizes / gradients
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080

PADDING = 28
BAR_H = 220                 # компактная высота нижней планки
COL_W = 200                 # ширина одной колонки со статом

# Фирменные градиенты (фиксированные, не от команд)
ORANGE_LR = ("#FF8A00", "#FFC43A")        # /card, /cards (левая часть)
BAD_LR    = ("#4E342E", "#3E2723")        # /cardbad
DUO_L_LR  = ("#4E2A82", "#2E1A5B")        # /card2 левая половина (фиолетовая)
DUO_R_LR  = ("#0F417A", "#0B2E63")        # /card2 правая половина (синяя)
RIGHT_PANEL_LR = ("#2B2B2B", "#151515")   # /cards правая колонка

def _grad_lr(size: Tuple[int,int], c1: str, c2: str) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (w, h), c1)
    top  = Image.new("RGB", (w, h), c2)
    mask = Image.new("L", (w, 1))
    m = ImageDraw.Draw(mask)
    for x in range(w):
        m.point((x, 0), int(255 * x / max(1, w - 1)))
    mask = mask.resize((w, h))
    return Image.composite(top, base, mask)

# ---------------------------------------------------------------------------
# Helpers: measuring, icons, face crop
# ---------------------------------------------------------------------------
def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int,int]:
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_w: int, start_sz: int, min_sz: int, loader) -> ImageFont.FreeTypeFont:
    sz = start_sz
    while sz >= min_sz:
        f = loader(sz)
        w, _ = _text_size(draw, text, f)
        if w <= max_w:
            return f
        sz -= 2
    return loader(min_sz)

def _fit_pair_same_size(draw: ImageDraw.ImageDraw, t1: str, t2: str, max_w1: int, max_w2: int,
                        start_sz: int, min_sz: int, loader) -> ImageFont.FreeTypeFont:
    sz = start_sz
    while sz >= min_sz:
        f = loader(sz)
        w1, _ = _text_size(draw, t1, f)
        w2, _ = _text_size(draw, t2, f)
        if w1 <= max_w1 and w2 <= max_w2:
            return f
        sz -= 2
    return loader(min_sz)

def _circle_crop_bottom(img: Image.Image, diam: int) -> Image.Image:
    """
    Большой круг, который «сидит» низом; подбираем масштаб так,
    чтобы макушка помещалась в круг, а плечи попадали в нижнюю дугу.
    """
    # Чуть выше круга, чтобы макушка точно влезла
    target_h = int(diam * 1.18)
    scale = target_h / max(1, img.height)
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    head = img.resize((new_w, new_h), Image.LANCZOS)

    # Центрируем по X
    if new_w < diam:
        pad = (diam - new_w) // 2
        tmp = Image.new("RGBA", (diam, new_h), (0,0,0,0))
        tmp.alpha_composite(head, (pad, 0))
        head = tmp
    else:
        left = (new_w - diam) // 2
        head = head.crop((left, 0, left + diam, new_h))

    # Берём нижние 'diam' пикселей
    if head.height < diam:
        canvas = Image.new("RGBA", (diam, diam), (0,0,0,0))
        canvas.alpha_composite(head, (0, diam - head.height))
        head = canvas
    else:
        head = head.crop((0, head.height - diam, diam, head.height))

    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam, diam), (0,0,0,0))
    out.paste(head, (0,0), mask)
    return out

def _first_icon(*keywords: str) -> Optional[Image.Image]:
    if not os.path.isdir(ICONS_DIR):
        return None
    files = sorted(os.listdir(ICONS_DIR))
    for kw in keywords:
        for fn in files:
            low = fn.lower()
            if low.endswith((".png", ".webp")) and kw in low:
                try:
                    p = os.path.join(ICONS_DIR, fn)
                    im = Image.open(p).convert("RGBA")
                    return im
                except Exception:
                    continue
    return None

def _draw_stats_centered(draw: ImageDraw.ImageDraw, center_x: int, base_y: int,
                         stats: List[Tuple[str,str]], num_font: ImageFont.FreeTypeFont,
                         cap_font: ImageFont.FreeTypeFont, col_w: int = COL_W,
                         color=(255,255,255,255), max_cols: int = 3):
    show = stats[:max_cols]
    total_w = col_w * len(show) if show else 0
    start_x = int(center_x - total_w / 2)
    x = start_x
    for value, label in show:
        # число
        vw, vh = _text_size(draw, str(value), num_font)
        draw.text((x, base_y), str(value), font=num_font, fill=color)
        # подпись
        draw.text((x, base_y + vh + 6), (label or "").upper(), font=cap_font, fill=color)
        x += col_w

# ---------------------------------------------------------------------------
# Single card
# ---------------------------------------------------------------------------
def render_card(
    mode: str,                    # "single"
    player_name_ru: str,
    subtitle: str,                # не используется
    team_logo_img: Optional[Image.Image],
    colors_unused: Tuple[str,str,str],  # игнорируем, фикс градиент
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)

    # Геометрия элементов
    LOGO_D = 84              # белый круг поменьше
    LOGO_IN = int(LOGO_D * 0.88)  # логотип внутри побольше
    FACE_D = 300            # игрок крупнее
    gap_logo_face = 16
    gap_face_name = 28

    # Черновик для измерений
    scratch = Image.new("RGBA", (10,10))
    sd = ImageDraw.Draw(scratch)

    # Имя (подбираем размер, максимум под разумную ширину)
    # Предполагаем, что текстовая колонка ~ 900px
    name_max_w_soft = 950
    f_name = _fit_text(sd, player_name_ru.upper(), name_max_w_soft, start_sz=84, min_sz=46, loader=_font_exo_b)
    name_w, name_h = _text_size(sd, player_name_ru.upper(), f_name)

    # Ширина блока статов (до 3 колонок)
    stats_cols = min(3, len(stats))
    stats_total_w = COL_W * stats_cols

    # Вычисляем итоговую ширину плашки под контент
    content_w = (
        PADDING + LOGO_D + gap_logo_face + FACE_D + gap_face_name + max(name_w, stats_total_w) + PADDING
    )
    bar_w = max(int(content_w), int(CANVAS_W * 0.45))  # чуть шире при малом тексте, но не на весь экран

    # Рисуем нижний градиент (оранжевый), авто-ширина
    bar = _grad_lr((bar_w, BAR_H), *ORANGE_LR).convert("RGBA")
    canvas.alpha_composite(bar, (0, CANVAS_H - BAR_H))

    # Позиции
    y_bar = CANVAS_H - BAR_H
    logo_x = PADDING
    logo_y = y_bar + (BAR_H - LOGO_D)//2
    face_x = logo_x + LOGO_D + gap_logo_face
    face_y = y_bar + (BAR_H - FACE_D)//2 - 8  # чуть «ниже» в баре
    name_x = face_x + FACE_D + gap_face_name
    name_y = y_bar + 44

    # Лого в белом круге (круг меньше, логотип внутри больше)
    if team_logo_img is not None:
        disc = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
        # внутренняя маска для белого круга (делаем круг)
        m = Image.new("L", (LOGO_D, LOGO_D), 0)
        ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
        disc.putalpha(m)
        canvas.alpha_composite(disc, (logo_x, logo_y))

        lg = team_logo_img.convert("RGBA").resize((LOGO_IN, LOGO_IN), Image.LANCZOS)
        off = (logo_x + (LOGO_D - LOGO_IN)//2, logo_y + (LOGO_D - LOGO_IN)//2)
        canvas.alpha_composite(lg, off)

    # Фото игрока (крупнее, круг больше — голова не режется сверху)
    face = _circle_crop_bottom(headshot_img, FACE_D)
    canvas.alpha_composite(face, (face_x, face_y))

    # Имя
    draw.text((name_x, name_y), player_name_ru.upper(), font=f_name, fill=(255,255,255,255))
    name_w, name_h = _text_size(draw, player_name_ru.upper(), f_name)
    name_center_x = name_x + name_w//2

    # Статы — центр относительно центра имени
    f_num = _font_mont_b(56)
    f_cap = _font_mont_sb(24)
    stats_y = name_y + name_h + 12
    _draw_stats_centered(draw, name_center_x, stats_y, stats, f_num, f_cap)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Duo card (двойная, фикс-градиенты, без зазора; одинаковые размеры шрифтов)
# ---------------------------------------------------------------------------
def render_card2(
    name1: str, logo1: Optional[Image.Image], colors1_unused: Tuple[str,str,str], head1: Image.Image, stats1: List[Tuple[str,str]],
    name2: str, logo2: Optional[Image.Image], colors2_unused: Tuple[str,str,str], head2: Image.Image, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)
    half = CANVAS_W // 2
    y_bar = CANVAS_H - BAR_H

    # Рисуем левую и правую половину (фикс-градиенты), без зазора
    left_bar  = _grad_lr((half, BAR_H), *DUO_L_LR).convert("RGBA")
    right_bar = _grad_lr((half, BAR_H), *DUO_R_LR).convert("RGBA")
    canvas.alpha_composite(left_bar,  (0,     y_bar))
    canvas.alpha_composite(right_bar, (half, y_bar))

    # Общие размеры
    LOGO_D = 80
    LOGO_IN = int(LOGO_D * 0.9)
    FACE_D = 268
    gap_logo_face = 14
    gap_face_name = 22

    # Подгоняем ОДИН размер имени для обеих сторон
    scratch = Image.new("RGBA", (10,10))
    sd = ImageDraw.Draw(scratch)
    # Допустимая ширина колонки имени с запасом
    max_w_side = half - (PADDING + LOGO_D + gap_logo_face + FACE_D + gap_face_name + PADDING)
    f_name = _fit_pair_same_size(sd, name1.upper(), name2.upper(),
                                 max_w1=max_w_side, max_w2=max_w_side,
                                 start_sz=72, min_sz=44, loader=_font_exo_b)

    # ОДИН размер для чисел/лейблов
    f_num = _font_mont_b(54)
    f_cap = _font_mont_sb(24)

    def side(x0: int, name: str, logo: Optional[Image.Image], head: Image.Image, stats: List[Tuple[str,str]]):
        # логотип в белом круге
        if logo is not None:
            disc = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
            m = Image.new("L", (LOGO_D, LOGO_D), 0)
            ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
            disc.putalpha(m)
            lx = x0 + PADDING
            ly = y_bar + (BAR_H - LOGO_D)//2
            canvas.alpha_composite(disc, (lx, ly))

            lg = logo.convert("RGBA").resize((LOGO_IN, LOGO_IN), Image.LANCZOS)
            off = (lx + (LOGO_D - LOGO_IN)//2, ly + (LOGO_D - LOGO_IN)//2)
            canvas.alpha_composite(lg, off)

        # фото
        face = _circle_crop_bottom(head, FACE_D)
        fx = x0 + PADDING + LOGO_D + gap_logo_face
        fy = y_bar + (BAR_H - FACE_D)//2 - 6
        canvas.alpha_composite(face, (fx, fy))

        # имя
        name_x = fx + FACE_D + gap_face_name
        name_y = y_bar + 36
        draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))
        nw, nh = _text_size(draw, name.upper(), f_name)
        center_x = name_x + nw//2

        # статы — центр относительно имени
        _draw_stats_centered(draw, center_x, name_y + nh + 10, stats, f_num, f_cap)

    side(0,    name1, logo1, head1, stats1)
    side(half, name2, logo2, head2, stats2)
    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Special card (левая — как single с оранж.градиентом, правая колонка тёмная)
# ---------------------------------------------------------------------------
def render_card_special(
    name: str, logo: Optional[Image.Image], colors_unused: Tuple[str,str,str],
    head: Image.Image, stats: List[Tuple[str,str]], right_text: str, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)
    y_bar = CANVAS_H - BAR_H

    # Геометрия слева
    LOGO_D = 84
    LOGO_IN = int(LOGO_D * 0.88)
    FACE_D = 280
    gap_logo_face = 16
    gap_face_name = 26

    # Правую колонку сделаем фикс ширины
    RIGHT_W = 420

    scratch = Image.new("RGBA", (10,10))
    sd = ImageDraw.Draw(scratch)
    f_name = _fit_text(sd, name.upper(), 900, start_sz=76, min_sz=44, loader=_font_exo_b)
    name_w, name_h = _text_size(sd, name.upper(), f_name)
    stats_total_w = COL_W * min(3, len(stats))

    left_content_w = PADDING + LOGO_D + gap_logo_face + FACE_D + gap_face_name + max(name_w, stats_total_w) + PADDING
    left_w = max(int(left_content_w), int(CANVAS_W*0.42))

    # Левая оранжевая часть
    left_bar  = _grad_lr((left_w, BAR_H), *ORANGE_LR).convert("RGBA")
    canvas.alpha_composite(left_bar, (0, y_bar))

    # Правая колонка — тёмная
    right_bar = _grad_lr((RIGHT_W, BAR_H), *RIGHT_PANEL_LR).convert("RGBA")
    canvas.alpha_composite(right_bar, (left_w, y_bar))

    # ЛОГО слева
    if logo is not None:
        disc = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
        m = Image.new("L", (LOGO_D, LOGO_D), 0)
        ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
        disc.putalpha(m)
        lx = PADDING
        ly = y_bar + (BAR_H - LOGO_D)//2
        canvas.alpha_composite(disc, (lx, ly))

        lg = logo.convert("RGBA").resize((LOGO_IN, LOGO_IN), Image.LANCZOS)
        off = (lx + (LOGO_D - LOGO_IN)//2, ly + (LOGO_D - LOGO_IN)//2)
        canvas.alpha_composite(lg, off)

    # Фото слева
    face = _circle_crop_bottom(head, FACE_D)
    fx = PADDING + LOGO_D + gap_logo_face
    fy = y_bar + (BAR_H - FACE_D)//2 - 8
    canvas.alpha_composite(face, (fx, fy))

    # Имя + статы слева
    name_x = fx + FACE_D + gap_face_name
    name_y = y_bar + 38
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))
    name_w, name_h = _text_size(draw, name.upper(), f_name)
    center_x = name_x + name_w//2
    _draw_stats_centered(draw, center_x, name_y + name_h + 10, stats, _font_mont_b(54), _font_mont_sb(24))

    # Правая колонка: текст + звезда БЕЗ белого круга
    star = _first_icon("star")
    if star is not None:
        st_size = 56
        st = star.resize((st_size, st_size), Image.LANCZOS)
        canvas.alpha_composite(st, (left_w + PADDING, y_bar + 20))

    # многострочный правый текст
    tx = left_w + PADDING + (64 if star is not None else 0) + 12
    ty = y_bar + 28
    tw = RIGHT_W - (tx - left_w) - PADDING
    f_txt = _font_mont_sb(28)

    # перенос построчный
    lines: List[str] = []
    for par in (right_text or "").split("\n"):
        s = par.strip()
        if not s:
            lines.append("")
            continue
        words = s.split()
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            wpx, _ = _text_size(draw, test, f_txt)
            if wpx <= tw:
                cur = test
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)
    # печать
    y = ty
    for ln in lines[:8]:
        draw.text((tx, y), ln, font=f_txt, fill=(255,255,255,255))
        y += 36

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# BAD card (коричневый, «какашка» без белого круга, лого у игрока)
# ---------------------------------------------------------------------------
def render_card_bad(
    name: str, head: Image.Image, stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)
    y_bar = CANVAS_H - BAR_H

    # Геометрия и ширина по контенту
    LOGO_D = 84
    LOGO_IN = int(LOGO_D * 0.88)
    FACE_D = 288
    gap_logo_face = 14
    gap_face_name = 24

    scratch = Image.new("RGBA", (10,10))
    sd = ImageDraw.Draw(scratch)
    f_name = _fit_text(sd, name.upper(), 920, start_sz=76, min_sz=44, loader=_font_exo_b)
    name_w, name_h = _text_size(sd, name.upper(), f_name)
    stats_total_w = COL_W * min(3, len(stats))
    content_w = PADDING + LOGO_D + gap_logo_face + FACE_D + gap_face_name + max(name_w, stats_total_w) + PADDING
    bar_w = max(int(content_w), int(CANVAS_W * 0.46))

    # Коричневый бар
    bar = _grad_lr((bar_w, BAR_H), *BAD_LR).convert("RGBA")
    canvas.alpha_composite(bar, (0, y_bar))

    # Логотип команды слева (рядом с игроком)
    if team_logo_img is not None:
        disc = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
        m = Image.new("L", (LOGO_D, LOGO_D), 0)
        ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
        disc.putalpha(m)
        lx = PADDING
        ly = y_bar + (BAR_H - LOGO_D)//2
        canvas.alpha_composite(disc, (lx, ly))

        lg = team_logo_img.convert("RGBA").resize((LOGO_IN, LOGO_IN), Image.LANCZOS)
        off = (lx + (LOGO_D - LOGO_IN)//2, ly + (LOGO_D - LOGO_IN)//2)
        canvas.alpha_composite(lg, off)

    # Фото
    face = _circle_crop_bottom(head, FACE_D)
    fx = PADDING + LOGO_D + gap_logo_face
    fy = y_bar + (BAR_H - FACE_D)//2 - 6
    canvas.alpha_composite(face, (fx, fy))

    # «какашка» без белого круга
    poop = _first_icon("poop", "pile", "shit", "kaka")
    if poop is not None:
        st = poop.resize((40, 40), Image.LANCZOS)
        canvas.alpha_composite(st, (fx + FACE_D + 6, y_bar + 10))

    # Имя + статы
    name_x = fx + FACE_D + gap_face_name + 50  # чуть отступ, чтобы иконка влезла слева
    name_y = y_bar + 38
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))
    nw, nh = _text_size(draw, name.upper(), f_name)
    center_x = name_x + nw//2
    _draw_stats_centered(draw, center_x, name_y + nh + 10, stats, _font_mont_b(54), _font_mont_sb(24))

    return _to_png_bytes(canvas)
