# api/graphics.py
from __future__ import annotations
import io, os, math, textwrap
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Paths & fonts
# ---------------------------------------------------------------------------
HERE = os.path.dirname(__file__)
FONT_DIR = os.path.join(HERE, "fonts")  # ожидаем шрифты в api/fonts

def _font_try(path: str, size: int):
    """
    Пытаемся открыть переданный путь, затем пробуем assets/fonts как запасной вариант,
    затем — системный DejaVuSans-Bold.
    """
    # 1) как есть
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        pass
    # 2) запасной путь в assets/fonts
    try:
        alt = os.path.join(HERE, "assets", "fonts", os.path.basename(path))
        return ImageFont.truetype(alt, size=size)
    except Exception:
        pass
    # 3) системный фоллбэк
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()

def _font_exo_b(size: int):  # Имя игрока
    return _font_try(os.path.join(FONT_DIR, "Exo2-Bold.ttf"), size)

def _font_mont_b(size: int):  # Цифры
    return _font_try(os.path.join(FONT_DIR, "Montserrat-Bold.ttf"), size)

def _font_mont_sb(size: int):  # Лейблы
    return _font_try(os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf"), size)

# ---------------------------------------------------------------------------
# Helpers: geometry, drawing, gradients
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080
BAR_H = 300               # высота «шторы» снизу
RADIUS = 32               # радиус (не используется в no-round версии, оставлен для совместимости)
PADDING = 32

def _to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

def _grad_lr(size: Tuple[int,int], c1: str, c2: str) -> Image.Image:
    """Простой горизонтальный градиент слева-направо."""
    w,h = size
    base = Image.new("RGB", (w,h), c1)
    top = Image.new("RGB", (w,h), c2)
    mask = Image.new("L", (w,1))
    md = ImageDraw.Draw(mask)
    for x in range(w):
        md.point((x,0), int(255*x/(w-1)))
    mask = mask.resize((w,h))
    return Image.composite(top, base, mask)

def _rounded_rect(w: int, h: int, r: int, round_left=True, round_right=True) -> Image.Image:
    """
    NO-ROUND версия: возвращает полностью белую маску того же размера.
    Любые «скругления» выключены. Эта заглушка нужна, чтобы
    все карточки были с прямыми углами и чтобы избежать NameError.
    """
    return Image.new("L", (w, h), 255)

def _paste_card(canvas: Image.Image, card: Image.Image, bottom: int = CANVAS_H) -> Tuple[int,int]:
    """Приклеить плашку к нижней границе."""
    x = 0
    y = bottom - card.height
    canvas.alpha_composite(card, (x,y))
    return (x,y)

def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_loader, max_w: int, start_sz: int, min_sz: int=24):
    """Уменьшаем шрифт до влезания в ширину."""
    sz = start_sz
    while sz >= min_sz:
        f = font_loader(sz)
        w, _ = draw.textbbox((0,0), text, font=f)[2:]
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
    Круглая вырезка лица БЕЗ обводки.
    Кадрируем по нижней кромке круга — «якоримся» вниз, чтобы плечи попадали в нижнюю дугу.
    """
    # Скейлим по высоте так, чтобы нижняя часть (плечи) легла в круг
    target_h = int(diam*1.25)  # чуть выше круга, чтобы лицо централизовалось
    scale = target_h / img.height
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    head = img.resize((new_w, new_h), Image.LANCZOS)

    # вырезаем прямоугольник по ширине круга; центрируем по X
    if new_w < diam:
        pad = (diam - new_w)//2
        head_pad = Image.new("RGBA", (diam, new_h), (0,0,0,0))
        head_pad.alpha_composite(head, (pad,0))
        head = head_pad
    else:
        left = (new_w - diam)//2
        head = head.crop((left, 0, left+diam, new_h))

    # берём только нижнюю часть высотой diam
    if head.height < diam:
        # добиваем прозрачностью сверху
        pad = Image.new("RGBA", (diam, diam), (0,0,0,0))
        pad.alpha_composite(head, (0, diam - head.height))
        head = pad
    else:
        head = head.crop((0, head.height - diam, diam, head.height))

    # круглая маска
    mask = Image.new("L", (diam,diam), 0)
    ImageDraw.Draw(mask).ellipse((0,0,diam,diam), fill=255)
    out = Image.new("RGBA", (diam,diam), (0,0,0,0))
    out.paste(head, (0,0), mask)
    return out

def _stats_layout(draw: ImageDraw.ImageDraw, base_x: int, base_y: int, col_w: int,
                  stats: List[Tuple[str,str]], num_font_loader, cap_font_loader,
                  num_sz: int, cap_sz: int, color=(255,255,255,255), max_cols: int=3):
    """Рисуем до 3 метрик: число крупно, подпись ниже мелко."""
    for i, (value, label) in enumerate(stats[:max_cols]):
        x = base_x + i*col_w
        # число
        f_num = num_font_loader(num_sz)
        tw, th = draw.textbbox((0,0), str(value), font=f_num)[2:]
        draw.text((x, base_y), str(value), font=f_num, fill=color)
        # подпись
        f_cap = cap_font_loader(cap_sz)
        draw.text((x, base_y + th + 8), label.upper(), font=f_cap, fill=color)

# ---------------------------------------------------------------------------
# Single card
# ---------------------------------------------------------------------------
def render_card(
    mode: str,            # "single"
    player_name_ru: str,
    subtitle: str,        # не используем (совместимость)
    team_logo_img: Optional[Image.Image],
    colors: Tuple[str,str,str],   # (primary, darker, accent) — используем первые два
    headshot_img: Image.Image,
    stats: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))

    # нижняя шторка во всю ширину (без скруглений)
    c1, c2 = colors[0], colors[1] if len(colors) > 1 else colors[0]
    bar = _grad_lr((CANVAS_W, BAR_H), c1, c2).convert("RGBA")
    # маска — сплошной прямоугольник
    mask = _rounded_rect(CANVAS_W, BAR_H, RADIUS, round_left=False, round_right=True)
    bar_rgba = Image.new("RGBA", (CANVAS_W, BAR_H), (0,0,0,0))
    bar_rgba.paste(bar, (0,0), mask)

    # накладываем
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(bar_rgba, (0, y_bar))

    draw = ImageDraw.Draw(canvas)

    # логотип команды в белом круге
    if team_logo_img is not None:
        LOGO_D = 128
        disc = _white_disc(LOGO_D)
        canvas.alpha_composite(disc, (PADDING, CANVAS_H - PADDING - LOGO_D))
        # впишем логотип внутрь
        lg = team_logo_img.convert("RGBA")
        lg_d = int(LOGO_D*0.76)
        lg = lg.resize((lg_d, lg_d), Image.LANCZOS)
        off = (PADDING + (LOGO_D-lg_d)//2, CANVAS_H - PADDING - LOGO_D + (LOGO_D-lg_d)//2)
        canvas.alpha_composite(lg, off)

    # фото игрока — круг без обводки, «сидит» на нижней кромке
    FACE_D = 260
    face = _circle_crop_bottom(headshot_img, FACE_D)
    face_x = PADDING + 96  # немного после логотипа
    face_y = CANVAS_H - PADDING - FACE_D + 8  # чутка опускаем внутрь бара
    canvas.alpha_composite(face, (face_x, face_y))

    # имя игрока
    name_x = face_x + FACE_D + 32
    name_y = CANVAS_H - BAR_H + 40
    max_name_w = CANVAS_W - name_x - PADDING - 24
    f_name = _fit_text(draw, player_name_ru.upper(), _font_exo_b, max_name_w, start_sz=88, min_sz=48)
    draw.text((name_x, name_y), player_name_ru.upper(), font=f_name, fill=(255,255,255,255))

    # числа/подписи
    stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 20
    col_w = 220
    _stats_layout(draw, base_x=name_x, base_y=stats_y, col_w=col_w,
                  stats=stats, num_font_loader=_font_mont_b, cap_font_loader=_font_mont_sb,
                  num_sz=64, cap_sz=26, color=(255,255,255,255), max_cols=3)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Duo card (двойная)
# ---------------------------------------------------------------------------
def render_card2(
    name1: str, logo1: Optional[Image.Image], colors1: Tuple[str,str,str], head1: Image.Image, stats1: List[Tuple[str,str]],
    name2: str, logo2: Optional[Image.Image], colors2: Tuple[str,str,str], head2: Image.Image, stats2: List[Tuple[str,str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))

    half_w = CANVAS_W // 2
    # левая половина (без скруглений)
    bar1 = _grad_lr((half_w, BAR_H), colors1[0], colors1[1] if len(colors1)>1 else colors1[0]).convert("RGBA")
    mask1 = _rounded_rect(half_w, BAR_H, RADIUS, round_left=True, round_right=False)
    bar1_rgba = Image.new("RGBA", (half_w, BAR_H), (0,0,0,0))
    bar1_rgba.paste(bar1, (0,0), mask1)
    canvas.alpha_composite(bar1_rgba, (0, CANVAS_H - BAR_H))

    # правая половина (без скруглений)
    bar2 = _grad_lr((half_w, BAR_H), colors2[0], colors2[1] if len(colors2)>1 else colors2[0]).convert("RGBA")
    mask2 = _rounded_rect(half_w, BAR_H, RADIUS, round_left=False, round_right=True)
    bar2_rgba = Image.new("RGBA", (half_w, BAR_H), (0,0,0,0))
    bar2_rgba.paste(bar2, (0,0), mask2)
    canvas.alpha_composite(bar2_rgba, (half_w, CANVAS_H - BAR_H))

    draw = ImageDraw.Draw(canvas)

    def side(x0: int, name: str, logo: Optional[Image.Image], head: Image.Image, stats: List[Tuple[str,str]]):
        # логотип
        if logo is not None:
            LOGO_D = 112
            disc = _white_disc(LOGO_D)
            canvas.alpha_composite(disc, (x0 + PADDING, CANVAS_H - PADDING - LOGO_D))
            lg = logo.convert("RGBA").resize((int(LOGO_D*0.76), int(LOGO_D*0.76)), Image.LANCZOS)
            off = (x0 + PADDING + (LOGO_D-lg.width)//2, CANVAS_H - PADDING - LOGO_D + (LOGO_D-lg.height)//2)
            canvas.alpha_composite(lg, off)

        FACE_D = 236
        face = _circle_crop_bottom(head, FACE_D)
        face_x = x0 + PADDING + 96
        face_y = CANVAS_H - PADDING - FACE_D + 8
        canvas.alpha_composite(face, (face_x, face_y))

        name_x = face_x + FACE_D + 24
        name_y = CANVAS_H - BAR_H + 36
        max_w = x0 + half_w - PADDING - name_x
        f_name = _fit_text(draw, name.upper(), _font_exo_b, max_w, start_sz=72, min_sz=44)
        draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))

        stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 16
        col_w = 200
        _stats_layout(draw, name_x, stats_y, col_w, stats, _font_mont_b, _font_mont_sb, 56, 24)

    side(0, name1, logo1, head1, stats1)
    side(half_w, name2, logo2, head2, stats2)
    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Special card (с правой колонкой)
# ---------------------------------------------------------------------------
def render_card_special(
    name: str, logo: Optional[Image.Image], colors: Tuple[str,str,str], head: Image.Image,
    stats: List[Tuple[str,str]], right_text: str, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))

    # основная шторка слева 2/3 + правая панель 1/3
    main_w = int(CANVAS_W*0.66)
    right_w = CANVAS_W - main_w

    # левая часть (без скруглений)
    left_bar = _grad_lr((main_w, BAR_H), colors[0], colors[1] if len(colors)>1 else colors[0]).convert("RGBA")
    left_mask = _rounded_rect(main_w, BAR_H, RADIUS, round_left=False, round_right=True)
    left_rgba = Image.new("RGBA", (main_w, BAR_H), (0,0,0,0))
    left_rgba.paste(left_bar, (0,0), left_mask)
    canvas.alpha_composite(left_rgba, (0, CANVAS_H - BAR_H))

    # правая колонка — отдельный блок (без скруглений)
    right_bar = _grad_lr((right_w, BAR_H), colors[1] if len(colors)>1 else colors[0], colors[0]).convert("RGBA")
    right_mask = _rounded_rect(right_w, BAR_H, RADIUS, round_left=True, round_right=True)
    right_rgba = Image.new("RGBA", (right_w, BAR_H), (0,0,0,0))
    right_rgba.paste(right_bar, (0,0), right_mask)
    canvas.alpha_composite(right_rgba, (main_w, CANVAS_H - BAR_H))

    draw = ImageDraw.Draw(canvas)

    # логотип
    if logo is not None:
        LOGO_D = 112
        disc = _white_disc(LOGO_D)
        canvas.alpha_composite(disc, (PADDING, CANVAS_H - PADDING - LOGO_D))
        lg = logo.convert("RGBA").resize((int(LOGO_D*0.76), int(LOGO_D*0.76)), Image.LANCZOS)
        off = (PADDING + (LOGO_D-lg.width)//2, CANVAS_H - PADDING - LOGО_D + (LOGО_D-lg.height)//2)
        canvas.alpha_composite(lg, off)

    # лицо
    FACE_D = 236
    face = _circle_crop_bottom(head, FACE_D)
    face_x = PADDING + 96
    face_y = CANVAS_H - PADDING - FACE_D + 8
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = CANVAS_H - BAR_H + 36
    max_w = main_w - name_x - PADDING
    f_name = _fit_text(draw, name.upper(), _font_exo_b, max_w, start_sz=72, min_sz=44)
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))

    stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 16
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 56, 24)

    # правая колонка текстом
    rpad = 28
    tx = main_w + rpad
    ty = CANVAS_H - BAR_H + 36
    tw = right_w - rpad*2
    f_txt = _font_mont_sb(30)
    # мягкие переносы
    lines = []
    for paragraph in (right_text or "").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            wpx = draw.textbbox((0,0), test, font=f_txt)[2]
            if wpx <= tw:
                cur = test
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    for i, line in enumerate(lines[:8]):
        draw.text((tx, ty + i*38), line, font=f_txt, fill=(255,255,255,255))

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# BAD card (коричневая «плохая»)
# ---------------------------------------------------------------------------
def render_card_bad(
    name: str, head: Image.Image, stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    # коричневый градиент
    bar = _grad_lr((CANVAS_W, BAR_H), "#4E342E", "#3E2723").convert("RGBA")
    mask = _rounded_rect(CANVAS_W, BAR_H, RADIUS, round_left=False, round_right=True)
    bar_rgba = Image.new("RGBA", (CANVAS_W, BAR_H), (0,0,0,0))
    bar_rgba.paste(bar, (0,0), mask)
    canvas.alpha_composite(bar_rgba, (0, CANVAS_H - BAR_H))

    draw = ImageDraw.Draw(canvas)

    # маркер слева
    d = ImageDraw.Draw(canvas)
    d.ellipse((PADDING, CANVAS_H - BAR_H + 32, PADDING+18, CANVAS_H - BAR_H + 50), fill=(255,200,0,255))

    # лицо
    FACE_D = 236
    face = _circle_crop_bottom(head, FACE_D)
    face_x = PADDING + 36
    face_y = CANVAS_H - PADDING - FACE_D + 8
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = CANVAS_H - BAR_H + 36
    max_w = CANVAS_W - name_x - PADDING
    f_name = _fit_text(draw, name.upper(), _font_exo_b, max_w, start_sz=72, min_sz=44)
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255,255,255,255))

    stats_y = name_y + draw.textbbox((0,0), "Hg", font=f_name)[3] - name_y + 16
    _stats_layout(draw, name_x, stats_y, 200, stats, _font_mont_b, _font_mont_sb, 56, 24)

    # логотип (необязательно)
    if team_logo_img is not None:
        LOGO_D = 112
        disc = _white_disc(LOGO_D)
        canvas.alpha_composite(disc, (CANVAS_W - PADDING - LOGО_D, CANVAS_H - PADDING - LOGО_D))
        lg = team_logo_img.convert("RGBA").resize((int(LOGО_D*0.76), int(LOGО_D*0.76)), Image.LANCZOS)
        off = (CANVAS_W - PADDING - LOGО_D + (LOGО_D-lg.width)//2,
               CANVAS_H - PADDING - LOGО_D + (LOGО_D-lg.height)//2)
        canvas.alpha_composite(lg, off)

    return _to_png_bytes(canvas)
