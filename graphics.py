# api/graphics.py
from __future__ import annotations
import io, os, math, textwrap
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Paths, assets, fonts (STRICT)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(__file__)
FONT_DIR = os.path.join(HERE, "fonts")            # <- кладём Exo2/Montserrat сюда
ASSETS_DIR = os.path.join(HERE, "assets")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

def _must_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Грузим только указанный TTF. Никаких фолбэков."""
    full = os.path.join(FONT_DIR, path)
    if not os.path.exists(full):
        raise FileNotFoundError(f"Font not found: {full}")
    return ImageFont.truetype(full, size=size)

def _font_exo_b(size: int):      # Имя игрока
    return _must_font("Exo2-Bold.ttf", size)

def _font_mont_b(size: int):     # Цифры
    return _must_font("Montserrat-Bold.ttf", size)

def _font_mont_sb(size: int):    # Лейблы/правый текст
    return _must_font("Montserrat-SemiBold.ttf", size)

def _load_icon(name: str, size: int) -> Optional[Image.Image]:
    p = os.path.join(ICONS_DIR, name)
    if not os.path.exists(p):
        return None
    im = Image.open(p).convert("RGBA")
    if size and (im.width != size or im.height != size):
        im = im.resize((size, size), Image.LANCZOS)
    return im

# ---------------------------------------------------------------------------
# Geometry, drawing, gradients
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080

BAR_H = 176         # ниже и тоньше
PADDING = 28

# Градиенты (фиксированная палитра — не от команды)
GRAD_ORANGE = ("#FF8A00", "#FFC437")               # card / cards
GRAD_DUO_LEFT = ("#4A1E7C", "#2E135C")             # card2 left
GRAD_DUO_RIGHT = ("#164C91", "#0B356C")            # card2 right
GRAD_BAD = ("#4E342E", "#3E2723")                  # cardbad

def _to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO(); img.save(bio, format="PNG"); return bio.getvalue()

def _grad_lr(size: Tuple[int,int], c1: str, c2: str) -> Image.Image:
    w,h = size
    base = Image.new("RGB", (w,h), c1)
    top = Image.new("RGB", (w,h), c2)
    mask = Image.new("L", (w,1))
    md = ImageDraw.Draw(mask)
    for x in range(w):
        md.point((x,0), int(255*x/max(1,(w-1))))
    mask = mask.resize((w,h))
    return Image.composite(top, base, mask)

def _white_disc(d: int) -> Image.Image:
    im = Image.new("RGBA", (d,d), (0,0,0,0))
    ImageDraw.Draw(im).ellipse((0,0,d,d), fill=(255,255,255,255))
    return im

def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_loader, max_w: int,
              start_sz: int, min_sz: int=30):
    sz = start_sz
    while sz >= min_sz:
        f = font_loader(sz)
        tw = draw.textbbox((0,0), text, font=f)[2]
        if tw <= max_w:
            return f
        sz -= 2
    return font_loader(min_sz)

def _circle_crop_bottom(img: Image.Image, diam: int) -> Image.Image:
    """
    Круглая вырезка, «якорь» по нижней кромке (плечи в нижней дуге), крупнее,
    чтобы макушка не обрезалась.
    """
    target_h = int(diam * 1.30)   # чуть выше круга
    scale = target_h / img.height
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    head = img.resize((new_w, new_h), Image.LANCZOS)

    if new_w < diam:
        pad = (diam - new_w)//2
        pad_im = Image.new("RGBA", (diam, new_h), (0,0,0,0))
        pad_im.alpha_composite(head, (pad,0)); head = pad_im
    else:
        left = (new_w - diam)//2
        head = head.crop((left,0,left+diam,new_h))

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
    for i, (value, label) in enumerate(stats[:max_cols]):
        x = base_x + i*col_w
        f_num = num_font_loader(num_sz)
        num_w, num_h = draw.textbbox((0,0), str(value), font=f_num)[2:]
        draw.text((x, base_y), str(value), font=f_num, fill=color)
        f_cap = cap_font_loader(cap_sz)
        draw.text((x, base_y + num_h + 10), label.upper(), font=f_cap, fill=color)

# ---------------------------------------------------------------------------
# Helpers: compute dynamic widths
# ---------------------------------------------------------------------------
def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return draw.textbbox((0,0), text, font=font)[2]

def _compute_single_width(name_text: str, stats: List[Tuple[str,str]],
                          logo_d: int, face_d: int, fonts_tuple) -> int:
    tmp = Image.new("RGBA", (10,10)); d = ImageDraw.Draw(tmp)
    f_name = fonts_tuple[0]
    cols = min(3, len(stats))
    col_w = 200
    needed = (PADDING + logo_d + 18 + face_d + 24 +
              _measure(d, name_text, f_name) + 24 +
              cols*col_w + PADDING)
    return max(820, min(CANVAS_W - PADDING*2, needed))

def _compute_duo_side_width(name_text: str, stats: List[Tuple[str,str]],
                            logo_d: int, face_d: int, fonts_tuple) -> int:
    tmp = Image.new("RGBA", (10,10)); d = ImageDraw.Draw(tmp)
    f_name = fonts_tuple[0]
    col_w = 180
    needed = (PADDING + logo_d + 16 + face_d + 20 +
              _measure(d, name_text, f_name) + 20 +
              min(3,len(stats))*col_w + PADDING)
    return max(740, min(CANVAS_W - PADDING*2, needed))

# ---------------------------------------------------------------------------
# Single card
# ---------------------------------------------------------------------------
def render_card(
    mode: str,            # "single"
    player_name_ru: str,
    subtitle: str,        # не используем (совместимость)
    team_logo_img: Optional[Image.Image],
    colors_unused: Tuple[str,str,str],   # игнорируем, у нас фикс. градиент
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    name = player_name_ru.upper()

    # размеры элементов (логотип меньше по кругу, сам логотип крупнее в круге)
    LOGO_D = 84
    FACE_D = 288

    # заранее подберём шрифты
    draw_tmp = ImageDraw.Draw(Image.new("RGBA", (10,10)))
    f_name = _fit_text(draw_tmp, name, _font_exo_b, 1100, start_sz=82, min_sz=46)
    fonts = (f_name,)

    bar_w = _compute_single_width(name, stats, LOGO_D, FACE_D, fonts)
    bar = _grad_lr((bar_w, BAR_H), GRAD_ORANGE[0], GRAD_ORANGE[1]).convert("RGBA")
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(bar, (PADDING, y_bar))

    draw = ImageDraw.Draw(canvas)

    # логотип команды в небольшом белом круге, сам логотип крупнее (0.9)
    if team_logo_img is not None:
        disc = _white_disc(LOGO_D)
        disc_x = PADDING + PADDING
        disc_y = y_bar + BAR_H - PADDING - LOGO_D
        canvas.alpha_composite(disc, (disc_x, disc_y))
        lg = team_logo_img.convert("RGBA")
        lg_sz = int(LOGO_D * 0.90)
        lg = lg.resize((lg_sz, lg_sz), Image.LANCZOS)
        canvas.alpha_composite(lg, (disc_x + (LOGO_D-lg_sz)//2, disc_y + (LOGO_D-lg_sz)//2))

    # лицо
    face = _circle_crop_bottom(headshot_img, FACE_D)
    face_x = PADDING + PADDING + LOGO_D + 18
    face_y = y_bar + BAR_H - PADDING - FACE_D + 10
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = y_bar + 32
    draw.text((name_x, name_y), name, font=f_name, fill=(255,255,255,255))

    # статы
    stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 16
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 58, 24)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Duo card (двойная, без зазора, фикс. градиенты)
# ---------------------------------------------------------------------------
def render_card2(
    name1: str, logo1: Optional[Image.Image], colors1_unused: Tuple[str,str,str], head1: Image.Image, stats1: List[Tuple[str,str]],
    name2: str, logo2: Optional[Image.Image], colors2_unused: Tuple[str,str,str], head2: Image.Image, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))

    # размеры стороны
    LOGO_D = 80
    FACE_D = 260
    n1, n2 = name1.upper(), name2.upper()

    dtmp = ImageDraw.Draw(Image.new("RGBA",(10,10)))
    f1 = _fit_text(dtmp, n1, _font_exo_b, 900, start_sz=72, min_sz=44)
    f2 = _fit_text(dtmp, n2, _font_exo_b, 900, start_sz=72, min_sz=44)

    w1 = _compute_duo_side_width(n1, stats1, LOGO_D, FACE_D, (f1,))
    w2 = _compute_duo_side_width(n2, stats2, LOGO_D, FACE_D, (f2,))
    total_w = min(CANVAS_W - PADDING*2, w1 + w2)
    # если не влазит, слегка ужмём правую
    if w1 + w2 > total_w:
        overflow = (w1 + w2) - total_w
        w2 = max(680, w2 - overflow)

    x_left = PADDING
    x_right = x_left + w1  # вплотную, без зазора
    y_bar = CANVAS_H - BAR_H

    # левая полоса
    bar1 = _grad_lr((w1, BAR_H), GRAD_DUO_LEFT[0], GRAD_DUO_LEFT[1]).convert("RGBA")
    canvas.alpha_composite(bar1, (x_left, y_bar))
    # правая полоса
    bar2 = _grad_lr((w2, BAR_H), GRAD_DUO_RIGHT[0], GRAD_DUO_RIGHT[1]).convert("RGBA")
    canvas.alpha_composite(bar2, (x_right, y_bar))

    draw = ImageDraw.Draw(canvas)

    def side(x0: int, name: str, logo: Optional[Image.Image], head: Image.Image,
             stats: List[Tuple[str,str]], f_name):
        # логотип
        if logo is not None:
            disc = _white_disc(LOGO_D)
            disc_x = x0 + PADDING
            disc_y = y_bar + BAR_H - PADDING - LOGO_D
            canvas.alpha_composite(disc, (disc_x, disc_y))
            lg = logo.convert("RGBA").resize((int(LOGO_D*0.90), int(LOGO_D*0.90)), Image.LANCZOS)
            canvas.alpha_composite(lg, (disc_x + (LOGO_D-lg.width)//2, disc_y + (LOGO_D-lg.height)//2))
        # лицо
        face = _circle_crop_bottom(head, FACE_D)
        face_x = x0 + PADDING + LOGO_D + 16
        face_y = y_bar + BAR_H - PADDING - FACE_D + 8
        canvas.alpha_composite(face, (face_x, face_y))
        # имя
        name_x = face_x + FACE_D + 20
        name_y = y_bar + 30
        draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))
        # статы
        stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 14
        _stats_layout(draw, name_x, stats_y, 180, stats, _font_mont_b, _font_mont_sb, 52, 22)

    side(x_left, n1, logo1, head1, stats1, f1)
    side(x_right, n2, logo2, head2, stats2, f2)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Special card (с правой колонкой и звездой без круга)
# ---------------------------------------------------------------------------
def render_card_special(
    name: str, logo: Optional[Image.Image], colors_unused: Tuple[str,str,str],
    head: Image.Image, stats: List[Tuple[str,str]], right_text: str, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    nm = name.upper()

    LOGO_D = 84
    FACE_D = 288

    dtmp = ImageDraw.Draw(Image.new("RGBA",(10,10)))
    f_name = _fit_text(dtmp, nm, _font_exo_b, 1000, start_sz=78, min_sz=44)

    # ширина левой основной части
    left_w = _compute_single_width(nm, stats, LOGO_D, FACE_D, (f_name,))
    # правая панель уже и фиксированная
    right_w = 520
    total_w = min(CANVAS_W - PADDING*2, left_w + right_w)

    y_bar = CANVAS_H - BAR_H
    x_left = PADDING
    x_right = x_left + left_w

    # левая часть (оранжевый)
    left_bar = _grad_lr((left_w, BAR_H), GRAD_ORANGE[0], GRAD_ORANGE[1]).convert("RGBA")
    canvas.alpha_composite(left_bar, (x_left, y_bar))
    # правая колонка — тёмно-серая
    right_bar = _grad_lr((right_w, BAR_H), "#2B2B2B", "#1F1F1F").convert("RGBA")
    canvas.alpha_composite(right_bar, (x_right, y_bar))

    draw = ImageDraw.Draw(canvas)

    # логотип команды — небольшой круг слева
    if logo is not None:
        disc = _white_disc(LOGO_D)
        disc_x = x_left + PADDING
        disc_y = y_bar + BAR_H - PADDING - LOGO_D
        canvas.alpha_composite(disc, (disc_x, disc_y))
        lg = logo.convert("RGBA").resize((int(LOGO_D*0.90), int(LOGO_D*0.90)), Image.LANCZOS)
        canvas.alpha_composite(lg, (disc_x + (LOGO_D-lg.width)//2, disc_y + (LOGO_D-lg.height)//2))

    # лицо
    face = _circle_crop_bottom(head, FACE_D)
    face_x = x_left + PADDING + LOGO_D + 18
    face_y = y_bar + BAR_H - PADDING - FACE_D + 10
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = y_bar + 30
    draw.text((name_x, name_y), nm, font=f_name, fill=(255,255,255,255))

    # статы
    stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 16
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 58, 24)

    # правая колонка текстом + звезда без круга
    star = _load_icon("star.png", 36)   # без белого фона
    tx = x_right + PADDING + (40 if star else 0) + 8
    ty = y_bar + 30
    if star:
        canvas.alpha_composite(star, (x_right + PADDING, ty + 2))

    tw = right_w - (tx - x_right) - PADDING
    f_txt = _font_mont_sb(28)

    # мягкие переносы
    lines: List[str] = []
    for para in (right_text or "").split("\n"):
        para = para.strip()
        if not para:
            lines.append(""); continue
        cur = ""
        for w in para.split():
            test = (cur + " " + w).strip()
            wpx = draw.textbbox((0,0), test, font=f_txt)[2]
            if wpx <= tw:
                cur = test
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)
    for i, line in enumerate(lines[:8]):
        draw.text((tx, ty + i*36), line, font=f_txt, fill=(255,255,255,255))

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# BAD card (какашка без круга, логотип команды у игрока)
# ---------------------------------------------------------------------------
def render_card_bad(
    name: str, head: Image.Image, stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    nm = name.upper()

    LOGO_D = 84
    FACE_D = 268

    dtmp = ImageDraw.Draw(Image.new("RGBA",(10,10)))
    f_name = _fit_text(dtmp, nm, _font_exo_b, 1100, start_sz=78, min_sz=44)

    # ширина считаем как single
    bar_w = _compute_single_width(nm, stats, LOGO_D, FACE_D, (f_name,))
    y_bar = CANVAS_H - BAR_H
    x_left = PADDING

    bar = _grad_lr((bar_w, BAR_H), GRAD_BAD[0], GRAD_BAD[1]).convert("RGBA")
    canvas.alpha_composite(bar, (x_left, y_bar))
    draw = ImageDraw.Draw(canvas)

    # Логотип команды — слева у игрока (в белом кружке)
    if team_logo_img is not None:
        disc = _white_disc(LOGO_D)
        disc_x = x_left + PADDING
        disc_y = y_bar + BAR_H - PADDING - LOGO_D
        canvas.alpha_composite(disc, (disc_x, disc_y))
        lg = team_logo_img.convert("RGBA").resize((int(LOGO_D*0.90), int(LOGO_D*0.90)), Image.LANCZOS)
        canvas.alpha_composite(lg, (disc_x + (LOGO_D-lg.width)//2, disc_y + (LOGO_D-lg.height)//2))

    # лицо
    face = _circle_crop_bottom(head, FACE_D)
    face_x = x_left + PADDING + LOGO_D + 16
    face_y = y_bar + BAR_H - PADDING - FACE_D + 8
    canvas.alpha_composite(face, (face_x, face_y))

    # имя + какашка без белого круга
    poop = _load_icon("poop.png", 28)
    name_x = face_x + FACE_D + 24 + (poop.width + 8 if poop else 0)
    name_y = y_bar + 30
    if poop:
        canvas.alpha_composite(poop, (name_x - (poop.width + 8), name_y + 4))
    draw.text((name_x, name_y), nm, font=f_name, fill=(255,255,255,255))

    stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 16
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 56, 24)

    return _to_png_bytes(canvas)
