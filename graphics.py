# api/graphics.py
from __future__ import annotations
import io, os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Paths & strict fonts (only your TTFs, no fallbacks)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(__file__)

FONT_DIR_CANDIDATES = [
    os.path.join(HERE, "fonts"),                  # api/fonts (локально)
    os.path.join(os.path.dirname(HERE), "api", "fonts"),
    "/var/task/api/fonts",                        # serverless
    "/var/task/fonts",                            # иногда пакуют так
]

ICON_DIR_CANDIDATES = [
    os.path.join(HERE, "assets", "icons"),        # api/assets/icons
    os.path.join(os.path.dirname(HERE), "api", "assets", "icons"),
    "/var/task/api/assets/icons",
]

def _first_existing(*paths: str) -> Optional[str]:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None

def _font_path(filename: str) -> str:
    for d in FONT_DIR_CANDIDATES:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            if os.getenv("DEBUG", "1") in ("1","true","True"):
                print(f"[fonts] using {p}")
            return p
    raise FileNotFoundError(f"Font not found: {os.path.join('.../fonts', filename)}")

def _icon_path(filename: str) -> Optional[str]:
    for d in ICON_DIR_CANDIDATES:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return None

def _font_strict(filename: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(filename), size=size)

def _font_exo_b(size: int):   # Имя
    return _font_strict("Exo2-Bold.ttf", size)

def _font_mont_b(size: int):  # Цифры
    return _font_strict("Montserrat-Bold.ttf", size)

def _font_mont_sb(size: int): # Подписи
    return _font_strict("Montserrat-SemiBold.ttf", size)

# ---------------------------------------------------------------------------
# Layout & helpers
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080
PADDING = 32

# высота плашки умеренная (не выше), но авто-подстройка по текстам ниже
BAR_H_BASE = 236

# фиксированные градиенты (как в примерах; без привязки к командам)
GRAD_ORANGE = ("#FF8A00", "#FFC54D")             # card / cards (левая часть)
GRAD_DUO_LEFT = ("#4C2E8D", "#2F1E62")           # фиолетовый (card2 левая)
GRAD_DUO_RIGHT = ("#154D8C", "#0E3F70")          # синий (card2 правая)
GRAD_RIGHTPANEL = ("#121212", "#262626")         # правая колонка cards
GRAD_BAD = ("#4E342E", "#3E2723")                # cardbad

def _to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

def _grad_lr(size: Tuple[int,int], c1: str, c2: str) -> Image.Image:
    w,h = size
    base = Image.new("RGB", (w,h), c1)
    top  = Image.new("RGB", (w,h), c2)
    mask = Image.new("L", (w,1))
    md = ImageDraw.Draw(mask)
    for x in range(w):
        md.point((x,0), int(255*x/max(1,w-1)))
    mask = mask.resize((w,h))
    return Image.composite(top, base, mask)

def _white_disc(diam: int) -> Image.Image:
    im = Image.new("RGBA", (diam,diam), (0,0,0,0))
    ImageDraw.Draw(im).ellipse((0,0,diam,diam), fill=(255,255,255,255))
    return im

def _load_icon(name_wo_ext: str) -> Optional[Image.Image]:
    p = _icon_path(f"{name_wo_ext}.png")
    if not p: return None
    return Image.open(p).convert("RGBA")

def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_loader, max_w: int,
              start_sz: int, min_sz: int=28) -> ImageFont.FreeTypeFont:
    sz = start_sz
    while sz >= min_sz:
        f = font_loader(sz)
        w = draw.textbbox((0,0), text, font=f)[2]
        if w <= max_w:
            return f
        sz -= 2
    return font_loader(min_sz)

def _circle_crop_bottom(img: Image.Image, diam: int, top_safe_px: int = 18) -> Image.Image:
    """
    Круглая вырезка. 'Якоримся' вниз, но оставляем запас сверху,
    чтобы макушка не резалась.
    """
    target_h = int(diam*1.15)  # стал чуть выше, чтобы больше головы вошло
    scale = target_h / max(1, img.height)
    new_w, new_h = max(1, int(img.width*scale)), max(1, int(img.height*scale))
    head = img.resize((new_w, new_h), Image.LANCZOS)

    # центрируем по X в рамке diam
    if new_w < diam:
        pad = (diam - new_w)//2
        pad_im = Image.new("RGBA", (diam, new_h), (0,0,0,0))
        pad_im.alpha_composite(head, (pad,0))
        head = pad_im
    else:
        left = (new_w - diam)//2
        head = head.crop((left, 0, left+diam, new_h))

    # берём нижнюю часть высотой diam, но с безопасным верхним запасом
    if head.height <= diam:
        # добиваем прозрачностью сверху
        pad = Image.new("RGBA", (diam, diam), (0,0,0,0))
        pad.alpha_composite(head, (0, diam - head.height))
        head = pad
    else:
        # сдвиг вверх так, чтобы сверху оставался небольшой запас
        top = max(0, head.height - diam - top_safe_px)
        head = head.crop((0, top, diam, top + diam))

    mask = Image.new("L", (diam,diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam,diam), (0,0,0,0))
    out.paste(head, (0,0), mask)
    return out

def _stats_block_size(draw: ImageDraw.ImageDraw,
                      stats: List[Tuple[str,str]],
                      num_font, cap_font, col_w: int, max_cols: int = 3) -> Tuple[int,int]:
    """Габариты блока статов (ширина, высота) без рисования."""
    shown = stats[:max_cols]
    if not shown: return (0,0)
    num_h = draw.textbbox((0,0), "68", font=num_font)[3]
    cap_h = draw.textbbox((0,0), "Hg", font=cap_font)[3]
    w = col_w*len(shown)
    h = num_h + 8 + cap_h
    return (w, h)

def _draw_stats_centered(draw: ImageDraw.ImageDraw, center_x: int, top_y: int,
                         stats: List[Tuple[str,str]],
                         num_loader, cap_loader,
                         num_sz: int, cap_sz: int, col_w: int = 200,
                         color=(255,255,255,255), max_cols: int = 3):
    shown = stats[:max_cols]
    if not shown: return
    f_num = num_loader(num_sz)
    f_cap = cap_loader(cap_sz)
    block_w, _ = _stats_block_size(draw, shown, f_num, f_cap, col_w, max_cols)
    start_x = center_x - block_w//2
    for i, (val, lbl) in enumerate(shown):
        x = start_x + i*col_w
        # число
        tw, th = draw.textbbox((0,0), str(val), font=f_num)[2:]
        draw.text((x, top_y), str(val), font=f_num, fill=color)
        # подпись
        draw.text((x, top_y + th + 8), lbl.upper(), font=f_cap, fill=color)

# ---------------------------------------------------------------------------
# Single card
# ---------------------------------------------------------------------------
def render_card(
    mode: str,                      # "single"
    player_name_ru: str,
    subtitle: str,                  # не используется
    team_logo_img: Optional[Image.Image],
    colors_unused: Tuple[str,str,str],  # цвет команды игнорируем — фиксированный градиент
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)

    # Параметры: размеры элементов
    FACE_D = 280                      # лицо крупнее, но с безопасной обрезкой
    LOGO_DISC = 88                    # белый круг компактнее
    LOGO_INSIDE = int(LOGO_DISC*0.86) # сам логотип крупнее внутри круга

    # Считаем ширину контента, чтобы бар не был "во всю ширину"
    # Имя + 3 колонки статов по 200
    # Сначала прикинем шрифты (подгоним под доступную ширину позже)
    name_max_w_soft = CANVAS_W - (PADDING + LOGO_DISC + 90 + FACE_D + 3*200 + 2*PADDING)
    f_name = _fit_text(draw, player_name_ru.upper(), _font_exo_b, max(420, name_max_w_soft), start_sz=72, min_sz=40)

    # Бар — «чуть больше по ширине, чтобы все влезло»
    col_w = 200
    f_num = _font_mont_b(56)
    f_cap = _font_mont_sb(24)
    stats_w, stats_h = _stats_block_size(draw, stats[:3], f_num, f_cap, col_w, 3)
    name_w = draw.textbbox((0,0), player_name_ru.upper(), font=f_name)[2]
    content_w = (PADDING + LOGO_DISC + 90 + FACE_D + 24 + name_w + 32 + stats_w + PADDING)
    bar_w = int(min(CANVAS_W - PADDING, max(content_w, 980)))
    bar_h = BAR_H_BASE

    # Рисуем оранжевый бар нужной ширины
    bar = _grad_lr((bar_w, bar_h), *GRAD_ORANGE).convert("RGBA")
    y_bar = CANVAS_H - bar_h
    canvas.alpha_composite(bar, (PADDING, y_bar))

    # Логотип (слева, рядом с игроком)
    if team_logo_img is not None:
        disc = _white_disc(LOGO_DISC)
        disc_x = PADDING + 16
        disc_y = CANVAS_H - PADDING - LOGO_DISC
        canvas.alpha_composite(disc, (disc_x, disc_y))
        lg = team_logo_img.convert("RGBA").resize((LOGO_INSIDE, LOGO_INSIDE), Image.LANCZOS)
        off = (disc_x + (LOGO_DISC-LOGO_INSIDE)//2, disc_y + (LOGO_DISC-LOGO_INSIDE)//2)
        canvas.alpha_composite(lg, off)

    # Фото
    face = _circle_crop_bottom(headshot_img, FACE_D, top_safe_px=22)
    face_x = PADDING + LOGO_DISC + 90
    face_y = CANVAS_H - PADDING - FACE_D + 8  # чуть ниже в бар
    canvas.alpha_composite(face, (face_x, face_y))

    # Имя по центру отводимой зоны
    name_x_left = face_x + FACE_D + 24
    name_y = y_bar + 36
    max_name_w = bar_w - (name_x_left - PADDING) - PADDING
    # Переподгоним имя, если нужно
    f_name = _fit_text(draw, player_name_ru.upper(), _font_exo_b, max(420, max_name_w), start_sz=72, min_sz=40)
    name_text = player_name_ru.upper()
    name_bbox = draw.textbbox((0,0), name_text, font=f_name)
    name_w = name_bbox[2]
    # центрируем имя в доступной области по X
    name_area_cx = name_x_left + max_name_w//2
    name_draw_x = int(name_area_cx - name_w/2)
    draw.text((name_draw_x, name_y), name_text, font=f_name, fill=(255,255,255,255))

    # Статы — строго по центру относительно имени (общий блок по центру той же области)
    stats_top = name_y + (name_bbox[3] - name_bbox[1]) + 14
    _draw_stats_centered(draw, center_x=name_area_cx, top_y=stats_top,
                         stats=stats, num_loader=_font_mont_b, cap_loader=_font_mont_sb,
                         num_sz=56, cap_sz=24, col_w=200)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Duo card (двойная, оба градиента фиксированные, одинаковые размеры шрифтов)
# ---------------------------------------------------------------------------
def render_card2(
    name1: str, logo1: Optional[Image.Image], colors1_unused, head1: Image.Image, stats1: List[Tuple[str,str]],
    name2: str, logo2: Optional[Image.Image], colors2_unused, head2: Image.Image, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)

    half_w = CANVAS_W // 2
    bar_h = BAR_H_BASE

    # Левый и правый бар — вплотную друг к другу, без зазора
    left_bar  = _grad_lr((half_w, bar_h), *GRAD_DUO_LEFT).convert("RGBA")
    right_bar = _grad_lr((half_w, bar_h), *GRAD_DUO_RIGHT).convert("RGBA")
    y_bar = CANVAS_H - bar_h
    canvas.alpha_composite(left_bar,  (0,      y_bar))
    canvas.alpha_composite(right_bar, (half_w, y_bar))

    # Единые размеры шрифтов (по худшему случаю)
    name_start_sz = 64
    name_min_sz   = 40
    num_sz, cap_sz = 54, 22
    col_w = 180

    def measure_name(name: str, max_w: int) -> Tuple[ImageFont.FreeTypeFont, int, int]:
        f = _fit_text(draw, name, _font_exo_b, max_w, start_sz=name_start_sz, min_sz=name_min_sz)
        bbox = draw.textbbox((0,0), name, font=f)
        return f, bbox[2], bbox[3]-bbox[1]

    # Ширина зон для имени/статов слева и справа
    def side_zone_x0(is_left: bool) -> int:
        return 0 if is_left else half_w

    def side_layout(x0: int, name: str, logo: Optional[Image.Image], head: Image.Image, stats: List[Tuple[str,str]]):
        # Левая колонка элементов
        FACE_D = 256
        LOGO_DISC = 88
        LOGO_INSIDE = int(LOGO_DISC*0.86)

        # логотип рядом с игроком
        if logo is not None:
            disc = _white_disc(LOGO_DISC)
            disc_x = x0 + PADDING + 16
            disc_y = CANVAS_H - PADDING - LOGO_DISC
            canvas.alpha_composite(disc, (disc_x, disc_y))
            lg = logo.convert("RGBA").resize((LOGO_INSIDE, LOGO_INSIDE), Image.LANCZOS)
            canvas.alpha_composite(lg, (disc_x+(LOGO_DISC-LOGO_INSIDE)//2,
                                        disc_y+(LOGO_DISC-LOGO_INSIDE)//2))

        face = _circle_crop_bottom(head, FACE_D, top_safe_px=20)
        face_x = x0 + PADDING + LOGO_DISC + 86
        face_y = CANVAS_H - PADDING - FACE_D + 8
        canvas.alpha_composite(face, (face_x, face_y))

        # зона имени/статов
        name_area_left = face_x + FACE_D + 20
        name_area_w    = half_w - (name_area_left - x0) - PADDING
        f_name, name_w, name_h = measure_name(name.upper(), name_area_w)
        name_cx = name_area_left + name_area_w//2
        name_y  = y_bar + 32
        draw.text((int(name_cx - name_w/2), name_y), name.upper(), font=f_name, fill=(255,255,255,255))

        stats_top = name_y + name_h + 14
        _draw_stats_centered(draw, name_cx, stats_top, stats, _font_mont_b, _font_mont_sb,
                             num_sz=num_sz, cap_sz=cap_sz, col_w=col_w)

    # Рисуем обе стороны
    side_layout(side_zone_x0(True),  name1, logo1, head1, stats1)
    side_layout(side_zone_x0(False), name2, logo2, head2, stats2)
    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Special card (cards): левая основа + правая узкая колонка с иконкой звезды (без белого круга)
# ---------------------------------------------------------------------------
def render_card_special(
    name: str, logo: Optional[Image.Image], colors_unused, head: Image.Image,
    stats: List[Tuple[str,str]], right_text: str, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)

    # Параметры
    left_min_w = 980
    bar_h = BAR_H_BASE
    FACE_D = 256
    LOGO_DISC = 88
    LOGO_INSIDE = int(LOGO_DISC*0.86)

    # прикидка ширины под имя+статы
    f_name = _fit_text(draw, name.upper(), _font_exo_b, CANVAS_W//2, start_sz=68, min_sz=40)
    col_w = 200
    f_num = _font_mont_b(56)
    f_cap = _font_mont_sb(24)
    stats_w, stats_h = _stats_block_size(draw, stats[:3], f_num, f_cap, col_w, 3)
    name_w = draw.textbbox((0,0), name.upper(), font=f_name)[2]
    main_w = max(left_min_w, PADDING + LOGO_DISC + 86 + FACE_D + 24 + max(name_w, stats_w) + PADDING)

    right_w = 520  # узкая колонка
    total_w = min(CANVAS_W - PADDING, main_w + right_w)

    # Левая оранжевая часть
    left_bar = _grad_lr((int(main_w), bar_h), *GRAD_ORANGE).convert("RGBA")
    y_bar = CANVAS_H - bar_h
    canvas.alpha_composite(left_bar, (PADDING, y_bar))

    # Правая колонка (тёмно-серая), вплотную
    right_bar = _grad_lr((int(right_w), bar_h), *GRAD_RIGHTPANEL).convert("RGBA")
    canvas.alpha_composite(right_bar, (PADDING + int(main_w), y_bar))

    # Логотип команды
    if logo is not None:
        disc = _white_disc(LOGO_DISC)
        disc_x = PADDING + 16
        disc_y = CANVAS_H - PADDING - LOGO_DISC
        canvas.alpha_composite(disc, (disc_x, disc_y))
        lg = logo.convert("RGBA").resize((LOGO_INSIDE, LOGO_INSIDE), Image.LANCZOS)
        canvas.alpha_composite(lg, (disc_x+(LOGO_DISC-LOGO_INSIDE)//2,
                                    disc_y+(LOGO_DISC-LOGO_INSIDE)//2))

    # Фото
    face = _circle_crop_bottom(head, FACE_D, top_safe_px=20)
    face_x = PADDING + LOGO_DISC + 86
    face_y = CANVAS_H - PADDING - FACE_D + 8
    canvas.alpha_composite(face, (face_x, face_y))

    # Имя (центр левой части)
    name_area_left = face_x + FACE_D + 20
    name_area_w    = int(main_w) - (name_area_left - PADDING) - PADDING
    f_name = _fit_text(draw, name.upper(), _font_exo_b, name_area_w, start_sz=68, min_sz=40)
    name_bbox = draw.textbbox((0,0), name.upper(), font=f_name)
    name_w = name_bbox[2]
    name_h = name_bbox[3] - name_bbox[1]
    name_cx = name_area_left + name_area_w//2
    name_y  = y_bar + 32
    draw.text((int(name_cx - name_w/2), name_y), name.upper(), font=f_name, fill=(255,255,255,255))

    # Статы под именем, по центру той же зоны
    stats_top = name_y + name_h + 14
    _draw_stats_centered(draw, name_cx, stats_top, stats, _font_mont_b, _font_mont_sb,
                         num_sz=56, cap_sz=24, col_w=200)

    # Правая колонка: текст + звезда (без белого круга)
    tx_left = PADDING + int(main_w) + 24
    tx_top  = y_bar + 28
    tx_w    = int(right_w) - 24 - 24
    f_txt   = _font_mont_sb(30)

    # звезда
    star = _load_icon("star")
    if star is not None:
        # небольшая иконка, без подложки
        scale = 0.8
        s_w = int(48*scale); s_h = int(48*scale)
        star_res = star.resize((s_w, s_h), Image.LANCZOS)
        canvas.alpha_composite(star_res, (tx_left, tx_top))
        tx_left_text = tx_left + s_w + 12
    else:
        tx_left_text = tx_left

    # многострочный правый текст
    draw = ImageDraw.Draw(canvas)
    x, y = tx_left_text, tx_top
    words = (right_text or "").split()
    line = ""
    line_h = draw.textbbox((0,0), "Hg", font=f_txt)[3]
    for w in words:
        test = (line + " " + w).strip()
        wpx = draw.textbbox((0,0), test, font=f_txt)[2]
        if wpx <= tx_w:
            line = test
        else:
            if line:
                draw.text((x, y), line, font=f_txt, fill=(255,255,255,255))
                y += line_h + 10
            line = w
            if y > CANVAS_H - 40: break
    if line and y <= CANVAS_H - 40:
        draw.text((x, y), line, font=f_txt, fill=(255,255,255,255))

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# BAD card: коричневый бар, какашка без круга, логотип рядом с игроком
# ---------------------------------------------------------------------------
def render_card_bad(
    name: str, head: Image.Image, stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(canvas)

    bar_h = BAR_H_BASE
    bar = _grad_lr((CANVAS_W - 2*PADDING, bar_h), *GRAD_BAD).convert("RGBA")
    y_bar = CANVAS_H - bar_h
    canvas.alpha_composite(bar, (PADDING, y_bar))

    # Какашка слева без белого круга
    poop = _load_icon("poop")
    if poop is not None:
        scale = 0.9
        p_w = int(40*scale); p_h = int(40*scale)
        p = poop.resize((p_w,p_h), Image.LANCZOS)
        canvas.alpha_composite(p, (PADDING + 8, y_bar + 36))

    # Фото и логотип рядом
    FACE_D = 256
    face = _circle_crop_bottom(head, FACE_D, top_safe_px=20)
    face_x = PADDING + 64
    face_y = CANVAS_H - PADDING - FACE_D + 8
    canvas.alpha_composite(face, (face_x, face_y))

    if team_logo_img is not None:
        LOGO_DISC = 88
        LOGO_INSIDE = int(LOGO_DISC*0.86)
        disc = _white_disc(LOGO_DISC)
        disc_x = face_x - LOGO_DISC - 24
        disc_y = CANVAS_H - PADDING - LOGO_DISC
        canvas.alpha_composite(disc, (disc_x, disc_y))
        lg = team_logo_img.convert("RGBA").resize((LOGO_INSIDE, LOGO_INSIDE), Image.LANCZOS)
        canvas.alpha_composite(lg, (disc_x+(LOGO_DISC-LOGO_INSIDE)//2,
                                    disc_y+(LOGO_DISC-LOGO_INSIDE)//2))

    # Имя
    name_area_left = face_x + FACE_D + 24
    name_area_w    = CANVAS_W - PADDING - name_area_left
    f_name = _fit_text(draw, name.upper(), _font_exo_b, name_area_w, start_sz=64, min_sz=38)
    name_bbox = draw.textbbox((0,0), name.upper(), font=f_name)
    name_w = name_bbox[2]; name_h = name_bbox[3]-name_bbox[1]
    name_cx = name_area_left + name_area_w//2
    name_y  = y_bar + 32
    draw.text((int(name_cx - name_w/2), name_y), name.upper(), font=f_name, fill=(255,255,255,255))

    # Статы под именем, по центру
    _draw_stats_centered(draw, name_cx, name_y + name_h + 14, stats,
                         _font_mont_b, _font_mont_sb, num_sz=54, cap_sz=22, col_w=180)

    return _to_png_bytes(canvas)
