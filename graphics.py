# api/graphics.py
# Рендер «плашек» без скруглений. Градиенты фиксированные. Выравнивание и размеры как в ТЗ.
from __future__ import annotations
import os
from typing import Tuple, List, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------- ПУТИ И ШРИФТЫ ----------

# Ищем шрифты строго в api/fonts (локально и в serverless /var/task/)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FONTS_CANDIDATES = [
    os.path.join(_THIS_DIR, "fonts"),
    os.path.join(os.path.dirname(_THIS_DIR), "api", "fonts"),
    "/var/task/api/fonts",
    "api/fonts",
]
def _find_font(name: str) -> str:
    for base in _FONTS_CANDIDATES:
        path = os.path.join(base, name)
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"Font not found: {name} (looked in {', '.join(_FONTS_CANDIDATES)})")

FONT_EXO_BOLD = _find_font("Exo2-Bold.ttf")
FONT_MONTSERRAT_BOLD = _find_font("Montserrat-Bold.ttf")
FONT_MONTSERRAT_SEMI = _find_font("Montserrat-SemiBold.ttf")

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size, layout_engine=ImageFont.LAYOUT_BASIC)

# ---------- УТИЛИТЫ ----------

def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def _draw_gradient_rect(im: Image.Image, box: Tuple[int,int,int,int], c1: Tuple[int,int,int], c2: Tuple[int,int,int]):
    """Горизонтальный градиент без скруглений."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    grad = Image.new("RGB", (w, 1))
    p = grad.load()
    for x in range(w):
        t = x / max(1, w-1)
        p[x, 0] = (
            int(c1[0] + (c2[0]-c1[0])*t),
            int(c1[1] + (c2[1]-c1[1])*t),
            int(c1[2] + (c2[2]-c1[2])*t),
        )
    grad = grad.resize((w, h))
    im.paste(grad, (x0, y0))

def _circle_crop(image: Image.Image, diameter: int) -> Image.Image:
    """Обрезает картинку по кругу с заданным диаметром."""
    img = image.convert("RGBA")
    img = ImageOps.fit(img, (diameter, diameter), method=Image.LANCZOS, bleed=0.0, centering=(0.5, 0.45))
    # centering y=0.45 — чуть ниже центра, чтобы макушка не резалась
    mask = Image.new("L", (diameter, diameter), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, diameter, diameter), fill=255)
    img.putalpha(mask)
    return img

def _paste_rgba(bg: Image.Image, fg: Image.Image, xy: Tuple[int,int]):
    bg.alpha_composite(fg, dest=xy)

def _load_icon(relative_path: str, size: int, no_circle: bool = True) -> Image.Image:
    # assets/icons/*
    candidates = [
        os.path.join(_THIS_DIR, "..", "assets", "icons", relative_path),
        os.path.join(os.path.dirname(_THIS_DIR), "assets", "icons", relative_path),
        os.path.join("assets", "icons", relative_path),
        os.path.join("/var/task/assets/icons", relative_path),
    ]
    for p in candidates:
        if os.path.exists(p):
            icon = Image.open(p).convert("RGBA")
            icon = icon.resize((size, size), Image.LANCZOS)
            return icon
    # fallback: простая звезда/кружок
    icon = Image.new("RGBA", (size, size), (0,0,0,0))
    d = ImageDraw.Draw(icon)
    if "star" in relative_path.lower():
        # простая 5-конечная
        from math import cos, sin, pi
        r1, r2 = size*0.48, size*0.2
        cx = cy = size/2
        pts = []
        for i in range(10):
            ang = -pi/2 + i*pi/5
            r = r1 if i%2==0 else r2
            pts.append((cx + r*cos(ang), cy + r*sin(ang)))
        d.polygon(pts, fill=(255,187,0,255))
    else:
        d.ellipse((0,0,size,size), fill=(255,255,255,255))
    return icon

# ---------- КОНСТАНТЫ РАЗМЕРОВ (1080p ориентир) ----------

CANVAS_W = 1920
CANVAS_H = 1080
SAFE_W_RATIO = 0.92  # максимальная ширина плашки относительно канвы

PADDING_X = 36
PADDING_Y = 24
BAR_H = 220  # высота основной полосы

HEAD_D = 360        # Диаметр «головы» (крупнее, чтобы макушка влезала)
HEAD_OVER_BAR = 140 # Насколько «голова» заходит вверх из полосы

LOGO_CIRCLE_D = 108  # белый кружок под логотип
LOGO_IMG_D = 96       # сама пиктограмма внутри (крупнее при меньшем белом круге)

STAT_GAP = 56         # расстояние между колонками статистики
GROUP_GAP = 40        # отступ между именем и блоком статистики

NAME_SIZE = 84        # имя игрока (чуть меньше)
NUM_SIZE  = 58        # цифры статистики сильно меньше имени
LBL_SIZE  = 28        # подписи (ОЧКИ/ПОДБОРЫ/С ИГРЫ)

# Градиенты (фикс) — подправь при желании
GRAD_ORANGE_L = (255,137,26)
GRAD_ORANGE_R = (255,206,76)

GRAD_PURPLE_L = (78,42,136)
GRAD_PURPLE_R = (44,21,81)

GRAD_BLUE_L   = (30,85,150)
GRAD_BLUE_R   = (14,47,95)

GRAD_BAD_L    = (76,52,47)
GRAD_BAD_R    = (59,38,35)

# ---------- ВСПОМОГАТЕЛЬНЫЕ РИСОВАЛКИ ТЕКСТА ----------

def _draw_text_center(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font: ImageFont.FreeTypeFont, fill=(255,255,255,255)) -> Tuple[int,int,int,int]:
    w, h = _measure(draw, text, font)
    draw.text((x - w//2, y - h//2), text, font=font, fill=fill)
    return (x - w//2, y - h//2, x + w//2, y + h//2)

# ---------- КОМПОНОВКА ЛЕВОГО МОДУЛЯ (общая для card/cardBAD/cards/card2) ----------

def _build_left_module(
    im: Image.Image,
    y_bottom: int,
    player_img: Image.Image,
    team_logo_img: Image.Image,
    name_ru: str,
    stats: List[Tuple[str, str]],
    grad_l: Tuple[int,int,int],
    grad_r: Tuple[int,int,int],
    extra_right_w: int = 0
) -> Tuple[int,int,int,int]:
    """
    Рисует левую часть плашки (адаптивная ширина) и возвращает (x0,y0,x1,y1) занятой области.
    extra_right_w — сколько надо зарезервировать справа (например, для правого блока у cards).
    """
    draw = ImageDraw.Draw(im)
    # Шрифты
    f_name = _font(FONT_MONTSERRAT_BOLD, NAME_SIZE)
    f_num  = _font(FONT_EXO_BOLD, NUM_SIZE)
    f_lbl  = _font(FONT_MONTSERRAT_SEMI, LBL_SIZE)

    # Посчитаем минимально нужную ширину
    # Блок имени
    tmp_img = Image.new("RGBA", (10,10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    name_w, name_h = _measure(tmp_draw, name_ru, f_name)

    # Блок статистики (три колонки по умолчанию)
    stat_cols_w = 0
    col_ws = []
    for num, lbl in stats:
        num_w, num_h = _measure(tmp_draw, num, f_num)
        lbl_w, lbl_h = _measure(tmp_draw, lbl, f_lbl)
        col_w = max(num_w, lbl_w)
        col_ws.append(col_w)
    if col_ws:
        stat_cols_w = sum(col_ws) + STAT_GAP*(len(col_ws)-1)

    inner_left = PADDING_X + LOGO_CIRCLE_D + 20 + HEAD_D + 28  # (логотип) + (зазор) + (голова) + зазор до текста
    block_w = max(name_w, stat_cols_w)  # ширина центрального «столба»
    total_w = inner_left + block_w + PADDING_X + extra_right_w

    max_w = int(CANVAS_W * SAFE_W_RATIO)
    bar_w = min(total_w, max_w)

    bar_x0 = PADDING_X
    bar_y1 = y_bottom
    bar_y0 = bar_y1 - BAR_H
    bar_x1 = bar_x0 + bar_w

    # Градиент
    _draw_gradient_rect(im, (bar_x0, bar_y0, bar_x1, bar_y1), grad_l, grad_r)

    # Голова (круг больше и с центровкой чуть ниже)
    head = _circle_crop(player_img, HEAD_D)
    head_x = bar_x0 + LOGO_CIRCLE_D + 20  # логотип слева, потом голова
    head_y = bar_y1 - HEAD_D + HEAD_OVER_BAR - BAR_H//2  # выступает вверх, но без срезанной макушки
    _paste_rgba(im, head, (head_x, head_y))

    # Белый круг + логотип команды — ближе к левому краю
    logo_bg = Image.new("RGBA", (LOGO_CIRCLE_D, LOGO_CIRCLE_D), (0,0,0,0))
    d = ImageDraw.Draw(logo_bg)
    d.ellipse((0,0,LOGO_CIRCLE_D,LOGO_CIRCLE_D), fill=(255,255,255,255))
    team_logo_resized = team_logo_img.convert("RGBA").resize((LOGO_IMG_D, LOGO_IMG_D), Image.LANCZOS)
    _paste_rgba(logo_bg, team_logo_resized, ((LOGO_CIRCLE_D-LOGO_IMG_D)//2, (LOGO_CIRCLE_D-LOGO_IMG_D)//2))
    logo_x = bar_x0 + PADDING_X//2
    logo_y = bar_y1 - int(LOGO_CIRCLE_D*0.9)  # чуть заходит вниз
    _paste_rgba(im, logo_bg, (logo_x, logo_y))

    # Текстовые блоки — выравнивание по центру вертикально и по оси X общего столба
    text_center_x = head_x + HEAD_D + 28 + block_w//2
    baseline_y = bar_y0 + 62

    _draw_text_center(draw, text_center_x, baseline_y, name_ru, f_name)
    # Статистика под именем: делим доступную ширину на колонки и центрируем группу
    if stats:
        cols = len(stats)
        cols_w = col_ws
        total_cols_w = sum(cols_w) + STAT_GAP*(cols-1)
        start_x = text_center_x - total_cols_w//2
        # первая линия — цифры
        num_y = baseline_y + 64
        lbl_y = num_y + 48
        x = start_x
        for i, (num, lbl) in enumerate(stats):
            col_w = cols_w[i]
            _draw_text_center(draw, x + col_w//2, num_y, num, f_num)
            _draw_text_center(draw, x + col_w//2, lbl_y, lbl, f_lbl)
            x += col_w + STAT_GAP

    return (bar_x0, bar_y0, bar_x1, bar_y1)

# ---------- ПРЕСЕТЫ ----------

def render_card(
    canvas: Optional[Image.Image],
    player_img: Image.Image,
    team_logo_img: Image.Image,
    name_ru: str,
    stats: List[Tuple[str,str]],
) -> Image.Image:
    """card: оранжевый градиент, одна левая плашка адаптивной ширины."""
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,255))
    _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        player_img, team_logo_img, name_ru, stats,
        GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=0
    )
    return canvas

def render_cardbad(
    canvas: Optional[Image.Image],
    player_img: Image.Image,
    team_logo_img: Image.Image,
    name_ru: str,
    stats: List[Tuple[str,str]],
) -> Image.Image:
    """cardBAD: коричневый градиент, какашка без белого круга рядом с именем, логотип команды слева у головы."""
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,255))
    x0,y0,x1,y1 = _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        player_img, team_logo_img, name_ru, stats,
        GRAD_BAD_L, GRAD_BAD_R, extra_right_w=0
    )
    # «Какашка» рядом с ИМЕНЕМ (слева от центра имени)
    draw = ImageDraw.Draw(canvas)
    f_name = _font(FONT_MONTSERRAT_BOLD, NAME_SIZE)
    name_w, name_h = _measure(draw, name_ru, f_name)
    # вычислим центр имени так же, как в _build_left_module
    head_x = x0 + LOGO_CIRCLE_D + 20 + HEAD_D
    block_w = max(name_w, 1)
    text_center_x = (head_x + 28) + block_w//2
    name_center_y = y1 - BAR_H + 62
    name_left = text_center_x - name_w//2

    poop = _load_icon("poop.png", size=34, no_circle=True)
    _paste_rgba(canvas, poop, (max(x0+PADDING_X, name_left - 40), name_center_y - 22))
    return canvas

def render_cards(
    canvas: Optional[Image.Image],
    player_img: Image.Image,
    team_logo_img: Image.Image,
    name_ru: str,
    stats: List[Tuple[str,str]],
    right_text: str = "молодец"
) -> Image.Image:
    """
    cards: слева — оранжевая плашка (адаптивная), справа — чёрно-серая секция вплотную, без зазора; ⭐ без белого круга.
    """
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,255))
    # зарезервируем для правого блока ширину текста + отступы
    draw = ImageDraw.Draw(canvas)
    f_right = _font(FONT_MONTSERRAT_SEMI, 40)
    txt_w, txt_h = _measure(draw, right_text, f_right)
    right_w = max(320, txt_w + 120)  # минимум 320, иначе тесно

    x0,y0,x1,y1 = _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        player_img, team_logo_img, name_ru, stats,
        GRAD_ORANGE_L, GRAD_ORANGE_R, extra_right_w=right_w
    )
    # правая секция без зазора
    r_x0 = x1 - right_w
    r_x1 = r_x0 + right_w
    _draw_gradient_rect(canvas, (r_x0, y0, r_x1, y1), (36,36,36), (20,20,20))
    # ⭐ без белого круга
    star = _load_icon("star.png", size=34, no_circle=True)
    _paste_rgba(canvas, star, (r_x0 + 28, y0 + BAR_H//2 - 18))
    draw.text((r_x0 + 28 + 44, y0 + BAR_H//2 - 18), right_text, font=f_right, fill=(255,255,255,255))
    return canvas

def render_card2(
    canvas: Optional[Image.Image],
    left_player_img: Image.Image,
    left_team_logo: Image.Image,
    left_name_ru: str,
    left_stats: List[Tuple[str,str]],
    right_player_img: Image.Image,
    right_team_logo: Image.Image,
    right_name_ru: str,
    right_stats: List[Tuple[str,str]],
) -> Image.Image:
    """
    card2: две половины; слева фиолетовый градиент, справа тёмно-синий; одинаковые размеры шрифтов/блоков, без зазора.
    """
    if canvas is None:
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,255))

    # Сначала нарисуем левую половину, вернём ширину
    x0,y0,x1,y1 = _build_left_module(
        canvas, CANVAS_H - PADDING_Y,
        left_player_img, left_team_logo, left_name_ru, left_stats,
        GRAD_PURPLE_L, GRAD_PURPLE_R, extra_right_w=0
    )
    left_w = x1 - x0
    # Правая половина — зеркалим по центру и ставим вплотную (без разрыва)
    # Рисуем её как отдельное изображение и вставляем вплотную справа от левой.
    right_canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    rx0, ry0, rx1, ry1 = _build_left_module(
        right_canvas, CANVAS_H - PADDING_Y,
        right_player_img, right_team_logo, right_name_ru, right_stats,
        GRAD_BLUE_L, GRAD_BLUE_R, extra_right_w=0
    )
    # обрежем по ширине фактической правой плашки
    right_w = rx1 - rx0
    # Вставляем на общий канвас вплотную к левой плашке
    # Если совместная ширина > SAFE_W_RATIO, немного подожмём: уменьшим обе на одинаковую долю
    total_w = left_w + right_w
    max_w = int(CANVAS_W * SAFE_W_RATIO)
    scale = 1.0
    if total_w > max_w:
        scale = max_w / total_w

    # Масштабируем обе части одинаково
    if scale < 0.999:
        def _scale_part(im_part: Image.Image, box: Tuple[int,int,int,int]) -> Image.Image:
            x0,y0,x1,y1 = box
            crop = im_part.crop((x0, y0, x1, y1))
            new_w = int((x1-x0) * scale)
            new_h = int((y1-y0) * scale)
            return crop.resize((new_w, new_h), Image.LANCZOS)
        left_part  = _scale_part(canvas, (x0,y0,x1,y1))
        right_part = _scale_part(right_canvas, (rx0,ry0,rx1,ry1))
        # Очистим зону
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, CANVAS_H-BAR_H-PADDING_Y-40, CANVAS_W, CANVAS_H), fill=(0,0,0,255))
        # Центрируем обе части совместно и стыкуем
        total_w_new = left_part.width + right_part.width
        start_x = (CANVAS_W - total_w_new) // 2
        _paste_rgba(canvas, left_part, (start_x, CANVAS_H - PADDING_Y - left_part.height))
        _paste_rgba(canvas, right_part, (start_x + left_part.width, CANVAS_H - PADDING_Y - right_part.height))
    else:
        # без скейла — просто вплотную слева направо
        _paste_rgba(canvas, right_canvas.crop((rx0,ry0,rx1,ry1)), (x1, y0))
    return canvas
