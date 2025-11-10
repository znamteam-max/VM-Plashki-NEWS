# api/graphics.py
from __future__ import annotations
import io, os
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Paths & fonts
# ---------------------------------------------------------------------------
HERE = os.path.dirname(__file__)
FONT_DIR = os.path.join(HERE, "fonts")  # api/fonts

def _font_try(path: str, size: int):
    """
    1) api/fonts, 2) assets/fonts, 3) DejaVu, 4) PIL default
    """
    # 1) как есть
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        pass
    # 2) запасной — assets/fonts/<basename>
    try:
        alt = os.path.join(HERE, "assets", "fonts", os.path.basename(path))
        return ImageFont.truetype(alt, size=size)
    except Exception:
        pass
    # 3) системный
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()

def _font_exo_b(size: int):   # Имя игрока
    return _font_try(os.path.join(FONT_DIR, "Exo2-Bold.ttf"), size)

def _font_mont_b(size: int):  # Цифры
    return _font_try(os.path.join(FONT_DIR, "Montserrat-Bold.ttf"), size)

def _font_mont_sb(size: int): # Лейблы
    return _font_try(os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf"), size)

# ---------------------------------------------------------------------------
# Layout & helpers
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080
BAR_H = 180                 # ниже, как в твоих примерах
PADDING = 32

# фиксированные градиенты под твой стиль
GRAD_CARD = ("#FF7A18", "#FFC22E")          # card / cards (оранжевый)
GRAD_CARD2_LEFT  = ("#462066", "#2A0F46")   # левая половина card2 (фиолет)
GRAD_CARD2_RIGHT = ("#194B93", "#0E2F6D")   # правая половина card2 (синий)

def _to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

def _grad_lr(size: Tuple[int,int], c1: str, c2: str) -> Image.Image:
    """Горизонтальный градиент слева-направо."""
    w,h = size
    base = Image.new("RGB", (w,h), c1)
    top  = Image.new("RGB", (w,h), c2)
    mask = Image.new("L", (w,1))
    md = ImageDraw.Draw(mask)
    for x in range(w):
        md.point((x,0), int(255*x/(w-1)))
    mask = mask.resize((w,h))
    return Image.composite(top, base, mask)

def _rect_mask(w: int, h: int) -> Image.Image:
    """Полная белая маска — прямые углы, без скруглений."""
    return Image.new("L", (w, h), 255)

def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_loader, max_w: int, start_sz: int, min_sz: int=24):
    """Уменьшаем шрифт до влезания в ширину."""
    sz = start_sz
    while sz >= min_sz:
        f = font_loader(sz)
        w = draw.textbbox((0,0), text, font=f)[2]
        if w <= max_w:
            return f
        sz -= 2
    return font_loader(min_sz)

def _white_disc(diam: int) -> Image.Image:
    im = Image.new("RGBA", (diam,diam), (0,0,0,0))
    d = ImageDraw.Draw(im)
    d.ellipse((0,0,diam,diam), fill=(255,255,255,255))
    return im

def _circle_crop_bottom(img: Image.Image, diam: int) -> Image.Image:
    """
    Круг без обводки, «якорим» по нижней кромке, но мягче, чтобы не резать макушку.
    """
    target_h = int(diam*1.12)  # было 1.25 — резало верх
    scale = target_h / max(1, img.height)
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    head = img.resize((new_w, new_h), Image.LANCZOS)

    # подгонка под ширину круга
    if new_w < diam:
        pad = (diam - new_w)//2
        tmp = Image.new("RGBA", (diam, new_h), (0,0,0,0))
        tmp.alpha_composite(head, (pad,0))
        head = tmp
    else:
        left = (new_w - diam)//2
        head = head.crop((left, 0, left+diam, new_h))

    # берём нижние diam пикселей
    if head.height < diam:
        pad = Image.new("RGBA", (diam, diam), (0,0,0,0))
        pad.alpha_composite(head, (0, diam - head.height))
        head = pad
    else:
        head = head.crop((0, head.height - diam, diam, head.height))

    mask = Image.new("L", (diam,diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam,diam), (0,0,0,0))
    out.paste(head, (0,0), mask)
    return out

def _stats_layout(draw: ImageDraw.ImageDraw, base_x: int, base_y: int, col_w: int,
                  stats: List[Tuple[str,str]], num_font_loader, cap_font_loader,
                  num_sz: int, cap_sz: int, color=(255,255,255,255), max_cols: int=3):
    """До 3 метрик: число крупно, подпись ниже мелко."""
    for i, (value, label) in enumerate(stats[:max_cols]):
        x = base_x + i*col_w
        f_num = num_font_loader(num_sz)
        _, _, tw, th = draw.textbbox((0,0), str(value), font=f_num)
        draw.text((x, base_y), str(value), font=f_num, fill=color)
        f_cap = cap_font_loader(cap_sz)
        draw.text((x, base_y + th + 8), str(label).upper(), font=f_cap, fill=color)

# ---------------------------------------------------------------------------
# Single card (/card)
# ---------------------------------------------------------------------------
def render_card(
    mode: str,            # "single"
    player_name_ru: str,
    subtitle: str,        # не используем (совместимость)
    team_logo_img: Optional[Image.Image],
    colors: Tuple[str,str,str],   # игнорируем, используем фиксированный градиент
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))

    # нижняя шторка во всю ширину (фикс. оранжевый градиент)
    c1, c2 = GRAD_CARD
    bar = _grad_lr((CANVAS_W, BAR_H), c1, c2).convert("RGBA")
    mask = _rect_mask(CANVAS_W, BAR_H)
    bar_rgba = Image.new("RGBA", (CANVAS_W, BAR_H), (0,0,0,0))
    bar_rgba.paste(bar, (0,0), mask)
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(bar_rgba, (0, y_bar))

    draw = ImageDraw.Draw(canvas)

    # логотип
    if team_logo_img is not None:
        LOGO_D = 116
        disc = _white_disc(LOGO_D)
        canvas.alpha_composite(disc, (PADDING, CANVAS_H - PADDING - LOGO_D))
        lg = team_logo_img.convert("RGBA").resize((int(LOGO_D*0.76), int(LOGO_D*0.76)), Image.LANCZOS)
        off = (PADDING + (LOGO_D-lg.width)//2, CANVAS_H - PADDING - LOGO_D + (LOGO_D-lg.height)//2)
        canvas.alpha_composite(lg, off)

    # лицо
    FACE_D = 228
    face = _circle_crop_bottom(headshot_img, FACE_D)
    face_x = PADDING + 92
    face_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 28
    name_y = CANVAS_H - BAR_H + 28
    max_name_w = CANVAS_W - name_x - PADDING - 24
    f_name = _fit_text(draw, player_name_ru.upper(), _font_exo_b, max_name_w, start_sz=84, min_sz=46)
    draw.text((name_x, name_y), player_name_ru.upper(), font=f_name, fill=(255,255,255,255))

    # статы (ВНИМАНИЕ: правильная формула Y)
    name_h = draw.textbbox((0,0), "Hg", font=f_name)[3]
    stats_y = name_y + name_h + 18
    _stats_layout(draw, base_x=name_x, base_y=stats_y, col_w=216,
                  stats=stats, num_font_loader=_font_mont_b, cap_font_loader=_font_mont_sb,
                  num_sz=60, cap_sz=26, color=(255,255,255,255), max_cols=3)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Duo card (/card2)
# ---------------------------------------------------------------------------
def render_card2(
    name1: str, logo1: Optional[Image.Image], colors1: Tuple[str,str,str], head1: Image.Image, stats1: List[Tuple[str,str]],
    name2: str, logo2: Optional[Image.Image], colors2: Tuple[str,str,str], head2: Image.Image, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    half_w = CANVAS_W // 2

    # левая половина — фикс. фиолетовый
    bar1 = _grad_lr((half_w, BAR_H), GRAD_CARD2_LEFT[0], GRAD_CARD2_LEFT[1]).convert("RGBA")
    bar1_rgba = Image.new("RGBA", (half_w, BAR_H), (0,0,0,0))
    bar1_rgba.paste(bar1, (0,0), _rect_mask(half_w, BAR_H))
    canvas.alpha_composite(bar1_rgba, (0, CANVAS_H - BAR_H))

    # правая половина — фикс. синий
    bar2 = _grad_lr((half_w, BAR_H), GRAD_CARD2_RIGHT[0], GRAD_CARD2_RIGHT[1]).convert("RGBA")
    bar2_rgba = Image.new("RGBA", (half_w, BAR_H), (0,0,0,0))
    bar2_rgba.paste(bar2, (0,0), _rect_mask(half_w, BAR_H))
    canvas.alpha_composite(bar2_rgba, (half_w, CANVAS_H - BAR_H))

    draw = ImageDraw.Draw(canvas)

    def side(x0: int, name: str, logo: Optional[Image.Image], head: Image.Image, stats: List[Tuple[str,str]]):
        # логотип
        if logo is not None:
            LOGO_D = 108
            disc = _white_disc(LOGO_D)
            canvas.alpha_composite(disc, (x0 + PADDING, CANVAS_H - PADDING - LOGO_D))
            lg = logo.convert("RGBA").resize((int(LOGO_D*0.76), int(LOGO_D*0.76)), Image.LANCZOS)
            off = (x0 + PADDING + (LOGO_D-lg.width)//2,
                   CANVAS_H - PADDING - LOGO_D + (LOGO_D-lg.height)//2)
            canvas.alpha_composite(lg, off)

        # лицо
        FACE_D = 216
        face = _circle_crop_bottom(head, FACE_D)
        face_x = x0 + PADDING + 90
        face_y = CANVAS_H - PADDING - FACE_D + 6
        canvas.alpha_composite(face, (face_x, face_y))

        # имя
        name_x = face_x + FACE_D + 24
        name_y = CANVAS_H - BAR_H + 26
        max_w = x0 + half_w - PADDING - name_x
        f_name = _fit_text(draw, name.upper(), _font_exo_b, max_w, start_sz=70, min_sz=42)
        draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))

        # статы (правильный Y)
        name_h = draw.textbbox((0,0), "Hg", font=f_name)[3]
        stats_y = name_y + name_h + 14
        _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 54, 24)

    side(0, name1, logo1, head1, stats1)
    side(half_w, name2, logo2, head2, stats2)
    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Special card (/cards — с правой колонкой)
# ---------------------------------------------------------------------------
def render_card_special(
    name: str, logo: Optional[Image.Image], colors: Tuple[str,str,str], head: Image.Image,
    stats: List[Tuple[str,str]], right_text: str, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))

    # левая 2/3 + правая 1/3 — обе на фикс. оранжевом
    main_w = int(CANVAS_W*0.66)
    right_w = CANVAS_W - main_w

    left_bar = _grad_lr((main_w, BAR_H), GRAD_CARD[0], GRAD_CARD[1]).convert("RGBA")
    left_rgba = Image.new("RGBA", (main_w, BAR_H), (0,0,0,0))
    left_rgba.paste(left_bar, (0,0), _rect_mask(main_w, BAR_H))
    canvas.alpha_composite(left_rgba, (0, CANVAS_H - BAR_H))

    right_bar = _grad_lr((right_w, BAR_H), GRAD_CARD[1], GRAD_CARD[0]).convert("RGBA")
    right_rgba = Image.new("RGBA", (right_w, BAR_H), (0,0,0,0))
    right_rgba.paste(right_bar, (0,0), _rect_mask(right_w, BAR_H))
    canvas.alpha_composite(right_rgba, (main_w, CANVAS_H - BAR_H))

    draw = ImageDraw.Draw(canvas)

    # логотип
    if logo is not None:
        LOGO_D = 108
        disc = _white_disc(LOGO_D)
        canvas.alpha_composite(disc, (PADDING, CANVAS_H - PADDING - LOGO_D))
        lg = logo.convert("RGBA").resize((int(LOGO_D*0.76), int(LOGO_D*0.76)), Image.LANCZOS)
        off = (PADDING + (LOGO_D-lg.width)//2,
               CANVAS_H - PADDING - LOGO_D + (LOGO_D-lg.height)//2)
        canvas.alpha_composite(lg, off)

    # лицо
    FACE_D = 216
    face = _circle_crop_bottom(head, FACE_D)
    face_x = PADDING + 90
    face_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = CANVAS_H - BAR_H + 26
    max_w = main_w - name_x - PADDING
    f_name = _fit_text(draw, name.upper(), _font_exo_b, max_w, start_sz=70, min_sz=42)
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))

    # статы (правильный Y)
    name_h = draw.textbbox((0,0), "Hg", font=f_name)[3]
    stats_y = name_y + name_h + 14
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 54, 24)

    # правая колонка текстом
    rpad = 28
    tx = main_w + rpad
    ty = CANVAS_H - BAR_H + 26
    tw = right_w - rpad*2
    f_txt = _font_mont_sb(30)

    lines: List[str] = []
    for paragraph in (right_text or "").split("\n"):
        p = paragraph.strip()
        if not p:
            lines.append("")
            continue
        words = p.split()
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            wpx = draw.textbbox((0,0), test, font=f_txt)[2]
            if wpx <= tw:
                cur = test
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)

    for i, line in enumerate(lines[:8]):
        draw.text((tx, ty + i*38), line, font=f_txt, fill=(255,255,255,255))

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# BAD card (/cardbad)
# ---------------------------------------------------------------------------
def render_card_bad(
    name: str, head: Image.Image, stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    # коричневый градиент — как было
    bar = _grad_lr((CANVAS_W, BAR_H), "#4E342E", "#3E2723").convert("RGBA")
    bar_rgba = Image.new("RGBA", (CANVAS_W, BAR_H), (0,0,0,0))
    bar_rgba.paste(bar, (0,0), _rect_mask(CANVAS_W, BAR_H))
    canvas.alpha_composite(bar_rgba, (0, CANVAS_H - BAR_H))

    draw = ImageDraw.Draw(canvas)

    # небольшой маркер слева
    d = ImageDraw.Draw(canvas)
    d.ellipse((PADDING, CANVAS_H - BAR_H + 28, PADDING+18, CANVAS_H - BAR_H + 46), fill=(255,200,0,255))

    # лицо
    FACE_D = 216
    face = _circle_crop_bottom(head, FACE_D)
    face_x = PADDING + 36
    face_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = CANVAS_H - BAR_H + 26
    max_w = CANVAS_W - name_x - PADDING
    f_name = _fit_text(draw, name.upper(), _font_exo_b, max_w, start_sz=70, min_sz=42)
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))

    # статы (правильный Y)
    name_h = draw.textbbox((0,0), "Hg", font=f_name)[3]
    stats_y = name_y + name_h + 14
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 54, 24)

    # логотип (опционально)
    if team_logo_img is not None:
        LOGO_D = 108
        disc = _white_disc(LOGO_D)
        canvas.alpha_composite(disc, (CANVAS_W - PADDING - LOGO_D, CANVAS_H - PADDING - LOGO_D))
        lg = team_logo_img.convert("RGBA").resize((int(LOGO_D*0.76), int(LOGO_D*0.76)), Image.LANCZOS)
        off = (CANVAS_W - PADDING - LOGO_D + (LOGO_D-lg.width)//2,
               CANVAS_H - PADDING - LOGO_D + (LOGO_D-lg.height)//2)
        canvas.alpha_composite(lg, off)

    return _to_png_bytes(canvas)
