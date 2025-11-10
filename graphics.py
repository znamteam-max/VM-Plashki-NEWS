# api/graphics.py
from __future__ import annotations
import io, os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Paths & fallbacks
# ---------------------------------------------------------------------------
HERE = os.path.dirname(__file__)
FONT_DIRS = [
    os.path.join(HERE, "fonts"),
    os.path.join(os.path.dirname(HERE), "assets", "fonts"),
    "/usr/share/fonts/truetype/dejavu",  # DejaVu fallback с кириллицей
]
ICON_DIRS = [
    os.path.join(os.path.dirname(HERE), "assets", "icons"),
    os.path.join(HERE, "assets", "icons"),
]

def _find_file(fname: str, dirs: List[str]) -> Optional[str]:
    for d in dirs:
        p = os.path.join(d, fname)
        if os.path.exists(p):
            return p
    return None

def _load_font_chain(candidates: List[str], size: int):
    last_err = None
    for fname in candidates:
        p = _find_file(fname, FONT_DIRS) if os.path.sep not in fname else fname
        if not p:
            continue
        try:
            return ImageFont.truetype(p, size=size)
        except Exception as e:
            last_err = e
    # самый надёжный fallback с кириллицей
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
        except Exception:
            return ImageFont.load_default()

# Политика шрифтов:
#  - ИМЕНА и ПОДПИСИ (есть кириллица) — DejaVu / Noto / любые с кириллицей
#  - ЦИФРЫ — Montserrat-Bold (если есть), иначе тот же fallback
def _font_name(size: int):
    return _load_font_chain([
        "Exo2-Bold.ttf",                 # если есть кириллица — ок, если нет, ниже возьмём DejaVu
        "NotoSans-SemiBold.ttf",
        "NotoSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ], size)

def _font_nums(size: int):
    return _load_font_chain([
        "Montserrat-Bold.ttf",
        "NotoSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ], size)

def _font_caps(size: int):
    return _load_font_chain([
        "Montserrat-SemiBold.ttf",
        "NotoSans-SemiBold.ttf",
        "DejaVuSans.ttf",
    ], size)

def _icon_path(name: str) -> Optional[str]:
    return _find_file(name, ICON_DIRS)

# ---------------------------------------------------------------------------
# Canvas & layout
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080

# Бар ниже и тоньше
BAR_H = 180
PADDING = 32

# Диаметры
LOGO_D = 140         # круг под логотип команды
FACE_D = 220         # круг с головой

# Базовые градиенты (фиксированные по ТЗ)
ORANGE_GRAD = ("#FF8A2B", "#FFC132")              # /card и /cards левая часть
DUO_LEFT_GRAD = ("#4C1F74", "#2F0F4A")            # /card2 левая
DUO_RIGHT_GRAD = ("#184785", "#0E2D5D")           # /card2 правая
BAD_GRAD = ("#4E342E", "#3E2723")                 # /cardbad
CARDS_RIGHT_GRAD = ("#2C2C2C", "#1E1E1E")         # правая колонка /cards

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

def _grad_lr(size: Tuple[int, int], c1: str, c2: str) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    mask = Image.new("L", (w, 1))
    md = ImageDraw.Draw(mask)
    for x in range(w):
        md.point((x, 0), int(255 * x / max(1, w - 1)))
    mask = mask.resize((w, h))
    return Image.composite(top, base, mask)

def _white_disc(d: int) -> Image.Image:
    im = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((0, 0, d, d), fill=(255, 255, 255, 255))
    return im

def _fit_font(draw: ImageDraw.ImageDraw, text: str, loader, max_w: int, start: int, min_sz: int = 28):
    sz = start
    while sz >= min_sz:
        f = loader(sz)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_w:
            return f
        sz -= 2
    return loader(min_sz)

def _head_circle(img: Image.Image, diam: int) -> Image.Image:
    """
    Аккуратно вписываем голову в круг, не обрезая макушку.
    Центрируем чуть ниже середины, чтобы плечи попадали, но голова была целой.
    """
    # масштаб по ширине
    scale = diam / img.width
    new_w = diam
    new_h = int(img.height * scale)
    head = img.resize((new_w, new_h), Image.LANCZOS)

    # вертикальное окно высотой diam, центр ~62% от верха
    center_y = int(new_h * 0.62)
    top = max(0, min(center_y - diam // 2, new_h - diam))
    head = head.crop((0, top, diam, top + diam))

    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diam, diam), fill=255)
    out = Image.new("RGBA", (diam, diam), (0, 0, 0, 0))
    out.paste(head, (0, 0), mask)
    return out

def _stats_block(draw: ImageDraw.ImageDraw, x: int, y: int, stats: List[Tuple[str, str]],
                 max_cols: int = 3, col_w: int = 200):
    shown = stats[:max_cols]
    f_num = _font_nums(56)
    f_cap = _font_caps(24)
    for i, (value, label) in enumerate(shown):
        cx = x + i * col_w
        draw.text((cx, y), str(value), font=f_num, fill=(255, 255, 255, 255))
        ty = y + draw.textbbox((0, 0), str(value), font=f_num)[3] - y + 8
        draw.text((cx, ty), label.upper(), font=f_cap, fill=(255, 255, 255, 255))

def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]

def _content_width_single(draw, name: str, stats: List[Tuple[str, str]]) -> int:
    name_font = _font_name(72)
    name_w = _text_w(draw, name.upper(), name_font)
    cols = min(3, len(stats))
    stats_w = cols * 200 if cols else 0
    text_w = max(name_w, stats_w)
    # лого + отступ + лицо + отступ + текст + правый паддинг
    width = PADDING + LOGO_D + 24 + FACE_D + 28 + text_w + PADDING
    return min(max(900, width), CANVAS_W - PADDING)  # ограничим сверху

def _paste_logo(canvas: Image.Image, logo_img: Optional[Image.Image], x: int, y_bottom: int):
    if logo_img is None:
        return
    disc = _white_disc(LOGO_D)
    canvas.alpha_composite(disc, (x, y_bottom - LOGO_D))
    lg = logo_img.convert("RGBA")
    lg_d = int(LOGO_D * 0.78)
    lg = lg.resize((lg_d, lg_d), Image.LANCZOS)
    canvas.alpha_composite(lg, (x + (LOGO_D - lg_d)//2, y_bottom - LOGO_D + (LOGO_D - lg_d)//2))

def _paste_icon(canvas: Image.Image, fname: str, x: int, y: int, d: int):
    p = _icon_path(fname)
    if not p or not os.path.exists(p):
        return
    try:
        im = Image.open(p).convert("RGBA").resize((d, d), Image.LANCZOS)
        canvas.alpha_composite(im, (x, y))
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Single card
# ---------------------------------------------------------------------------
def render_card(
    mode: str,            # "single"
    player_name_ru: str,
    subtitle: str,        # не используется
    team_logo_img: Optional[Image.Image],
    colors: Tuple[str, str, str],   # игнорируем — фиксированный градиент
    headshot_img: Image.Image,
    stats: List[Tuple[str, str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # ширина под контент
    bar_w = _content_width_single(draw, player_name_ru, stats)
    bar = _grad_lr((bar_w, BAR_H), ORANGE_GRAD[0], ORANGE_GRAD[1]).convert("RGBA")
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(bar, (0, y_bar))

    # лого рядом с лицом, «передний план»
    _paste_logo(canvas, team_logo_img, PADDING, CANVAS_H - PADDING)

    # лицо
    face = _head_circle(headshot_img, FACE_D)
    face_x = PADDING + LOGO_D + 24
    face_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 28
    name_y = y_bar + 28
    max_w = bar_w - name_x - PADDING
    f_name = _fit_font(draw, player_name_ru.upper(), _font_name, max_w, start=72, min_sz=40)
    draw.text((name_x, name_y), player_name_ru.upper(), font=f_name, fill=(255, 255, 255, 255))

    # статы
    stats_y = name_y + (draw.textbbox((0, 0), "Hg", font=f_name)[3] - name_y) + 12
    _stats_block(draw, name_x, stats_y, stats, max_cols=3, col_w=200)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Duo card
# ---------------------------------------------------------------------------
def render_card2(
    name1: str, logo1: Optional[Image.Image], colors1: Tuple[str, str, str], head1: Image.Image, stats1: List[Tuple[str, str]],
    name2: str, logo2: Optional[Image.Image], colors2: Tuple[str, str, str], head2: Image.Image, stats2: List[Tuple[str, str]],
    **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # независимые ширины половин, не «во всю»
    left_w = min(_content_width_single(draw, name1, stats1), CANVAS_W // 2 - 40)
    right_w = min(_content_width_single(draw, name2, stats2), CANVAS_W // 2 - 40)

    # левая полоса
    left_bar = _grad_lr((left_w, BAR_H), DUO_LEFT_GRAD[0], DUO_LEFT_GRAD[1]).convert("RGBA")
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(left_bar, (0, y_bar))

    # правая полоса
    right_bar = _grad_lr((right_w, BAR_H), DUO_RIGHT_GRAD[0], DUO_RIGHT_GRAD[1]).convert("RGBA")
    rx0 = CANVAS_W - right_w
    canvas.alpha_composite(right_bar, (rx0, y_bar))

    # левая сторона
    _paste_logo(canvas, logo1, PADDING, CANVAS_H - PADDING)
    face1 = _head_circle(head1, FACE_D)
    face1_x = PADDING + LOGO_D + 24
    face1_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face1, (face1_x, face1_y))

    n1_x = face1_x + FACE_D + 24
    n1_y = y_bar + 26
    n1_max = left_w - n1_x - PADDING
    f1 = _fit_font(draw, name1.upper(), _font_name, n1_max, start=66, min_sz=40)
    draw.text((n1_x, n1_y), name1.upper(), font=f1, fill=(255, 255, 255, 255))
    _stats_block(draw, n1_x, n1_y + (draw.textbbox((0, 0), "Hg", font=f1)[3] - n1_y) + 10, stats1)

    # правая сторона
    _paste_logo(canvas, logo2, rx0 + PADDING, CANVAS_H - PADDING)
    face2 = _head_circle(head2, FACE_D)
    face2_x = rx0 + PADDING + LOGO_D + 24
    face2_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face2, (face2_x, face2_y))

    n2_x = face2_x + FACE_D + 24
    n2_y = y_bar + 26
    n2_max = rx0 + right_w - n2_x - PADDING
    f2 = _fit_font(draw, name2.upper(), _font_name, n2_max, start=66, min_sz=40)
    draw.text((n2_x, n2_y), name2.upper(), font=f2, fill=(255, 255, 255, 255))
    _stats_block(draw, n2_x, n2_y + (draw.textbbox((0, 0), "Hg", font=f2)[3] - n2_y) + 10, stats2)

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# Special card (с правой колонкой)
# ---------------------------------------------------------------------------
def render_card_special(
    name: str, logo: Optional[Image.Image], colors: Tuple[str, str, str], head: Image.Image,
    stats: List[Tuple[str, str]], right_text: str, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # левая основная часть как в single
    main_w = _content_width_single(draw, name, stats)
    left_bar = _grad_lr((main_w, BAR_H), ORANGE_GRAD[0], ORANGE_GRAD[1]).convert("RGBA")
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(left_bar, (0, y_bar))

    # правая колонка фиксированной ширины
    right_w = 520
    right_bar = _grad_lr((right_w, BAR_H), CARDS_RIGHT_GRAD[0], CARDS_RIGHT_GRAD[1]).convert("RGBA")
    rx = main_w + 16  # небольшой зазор
    canvas.alpha_composite(right_bar, (rx, y_bar))

    # слева — лого / лицо / имя / статы
    _paste_logo(canvas, logo, PADDING, CANVAS_H - PADDING)

    face = _head_circle(head, FACE_D)
    face_x = PADDING + LOGO_D + 24
    face_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face, (face_x, face_y))

    name_x = face_x + FACE_D + 28
    name_y = y_bar + 26
    f_name = _fit_font(draw, name.upper(), _font_name, max_w=main_w - name_x - PADDING, start=66, min_sz=38)
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255, 255, 255, 255))

    stats_y = name_y + (draw.textbbox((0, 0), "Hg", font=f_name)[3] - name_y) + 10
    _stats_block(draw, name_x, stats_y, stats)

    # правая колонка — текст + иконка «звезда»
    # белый круг + star.png
    disc = _white_disc(104)
    canvas.alpha_composite(disc, (rx + PADDING, CANVAS_H - PADDING - 104))
    _paste_icon(canvas, "star.png", rx + PADDING + 14, CANVAS_H - PADDING - 104 + 14, 76)

    # сам текст, перенос по ширине
    tx = rx + PADDING
    tw = right_w - PADDING * 2
    ty = y_bar + 26
    f_txt = _font_caps(28)

    def _wrap(par: str) -> List[str]:
        words = par.split()
        line, out = "", []
        for w in words:
            test = (line + " " + w).strip()
            if draw.textbbox((0, 0), test, font=f_txt)[2] <= tw:
                line = test
            else:
                if line:
                    out.append(line)
                line = w
        if line:
            out.append(line)
        return out

    ycur = ty
    for p in (right_text or "").split("\n"):
        p = p.strip()
        if not p:
            ycur += 10
            continue
        for line in _wrap(p)[:8]:
            draw.text((tx, ycur), line, font=f_txt, fill=(255, 255, 255, 255))
            ycur += 34

    return _to_png_bytes(canvas)

# ---------------------------------------------------------------------------
# BAD card
# ---------------------------------------------------------------------------
def render_card_bad(
    name: str, head: Image.Image, stats: List[Tuple[str, str]],
    team_logo_img: Optional[Image.Image] = None, **kwargs
) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    bar = _grad_lr((CANVAS_W - PADDING, BAR_H), BAD_GRAD[0], BAD_GRAD[1]).convert("RGBA")
    y_bar = CANVAS_H - BAR_H
    canvas.alpha_composite(bar, (0, y_bar))

    # «какашка» слева в белом кружке
    disc = _white_disc(40)
    canvas.alpha_composite(disc, (PADDING, y_bar + 24))
    _paste_icon(canvas, "poop.png", PADDING + 6, y_bar + 30, 28)

    # лицо
    face = _head_circle(head, FACE_D)
    face_x = PADDING + 36
    face_y = CANVAS_H - PADDING - FACE_D + 6
    canvas.alpha_composite(face, (face_x, face_y))

    # имя
    name_x = face_x + FACE_D + 24
    name_y = y_bar + 26
    f_name = _fit_font(draw, name.upper(), _font_name, max_w=CANVAS_W - PADDING - name_x, start=66, min_sz=38)
    draw.text((name_x, name_y), name.upper(), font=f_name, fill=(255, 255, 255, 255))

    # статы
    stats_y = name_y + (draw.textbbox((0, 0), "Hg", font=f_name)[3] - name_y) + 10
    _stats_block(draw, name_x, stats_y, stats)

    # логотип команды справа в круге (на переднем плане)
    if team_logo_img is not None:
        _paste_logo(canvas, team_logo_img, CANVAS_W - PADDING - LOGO_D, CANVAS_H - PADDING)

    return _to_png_bytes(canvas)
