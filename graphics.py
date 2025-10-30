# graphics.py — логотип рядом с фото, мягкий градиент, мелкие подписи метрик

from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import io

# Холст
W, H = 1920, 1080

# Панель (компактная)
BAR_H          = 250
PAD_L          = 56
PAD_R          = 56
TOP_IN         = 22
BOT_IN         = 20

# Интервалы
NAME_STATS_GAP = 18
BLOCK_HGAP     = 56
INNER_VGAP     = 10

# Доп. вертикальные паддинги (тонкие, чтобы текст не «лип»)
NAME_PAD_TOP       = 4
NAME_PAD_BOTTOM    = 6
BLOCK_PAD_TOP      = 4
BLOCK_PAD_BOTTOM   = 6

# Размеры головы/логотипа
HEAD_SIZE   = 360
LOGO_D      = 150
LOGO_SIZE   = 132
LOGO_GAP    = 6     # логотип почти вплотную к фото (уменьшено)

# Шрифты
F_BOLD_PATH = "assets/fonts/Montserrat-Bold.ttf"
F_SB_PATH   = "assets/fonts/Montserrat-SemiBold.ttf"
F_EXO_PATH  = "assets/fonts/Exo2-Bold.ttf"

def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

# Базовые размеры шрифтов
BASE_NAME = 60   # ИМЯ — без изменений
BASE_VAL  = 50   # ЧИСЛО — без изменений
BASE_LBL  = 28   # ПОДПИСИ («ОЧКИ», «ПОДБОРЫ») — МЕНЬШЕ
TAG_MAX_W = 260
TAG_MAX_S = 40

# ---------- текст без «срезов» ----------
def _text_img(text: str, font: ImageFont.FreeTypeFont, fill=(255,255,255,255)) -> Image.Image:
    probe = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(probe)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0,0,0,0))
    ImageDraw.Draw(img).text((-l, -t), text, font=font, fill=fill)
    return img

def _pad_v(img: Image.Image, top: int, bottom: int) -> Image.Image:
    if top <= 0 and bottom <= 0:
        return img
    out = Image.new("RGBA", (img.width, img.height + max(0, top) + max(0, bottom)), (0,0,0,0))
    out.alpha_composite(img, (0, max(0, top)))
    return out

def _fit_text_to_width(text: str, font_path: str, max_w: int, max_size: int, min_size: int = 26):
    lo, hi = min_size, max_size
    probe = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(probe)
    best = _load_font(font_path, lo)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _load_font(font_path, mid)
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        if r - l <= max_w:
            best = f
            lo = mid + 1
        else:
            hi = mid - 1
    return _text_img(text, best), best

def _circle_crop(path: str, d: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    s = min(im.size)
    left = (im.width - s) // 2
    top  = max(0, im.height - s)
    im   = im.crop((left, top, left + s, top + s)).resize((d, d), Image.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0,0,d,d), fill=255)
    out = Image.new("RGBA", (d, d), (0,0,0,0))
    out.paste(im, (0,0), mask)
    return out

# ---------- цвет и градиент ----------
def _hex_to_rgb(h: str) -> Tuple[int,int,int]:
    h = h.strip()
    if h.startswith("#"): h = h[1:]
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))

def _clamp(c: int) -> int:
    return max(0, min(255, c))

def _shade(rgb: Tuple[int,int,int], k: float) -> Tuple[int,int,int]:
    # k<1 — темнее на |1-k|*100%, k>1 — светлее
    return (_clamp(int(rgb[0]*k)), _clamp(int(rgb[1]*k)), _clamp(int(rgb[2]*k)))

def _rounded_horizontal_gradient(width: int, height: int, radius: int,
                                 left_rgb: Tuple[int,int,int], right_rgb: Tuple[int,int,int]) -> Image.Image:
    grad = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(grad)
    for x in range(width):
        t = x / max(1, width-1)
        r = int(left_rgb[0] + (right_rgb[0]-left_rgb[0]) * t)
        g = int(left_rgb[1] + (right_rgb[1]-left_rgb[1]) * t)
        b = int(left_rgb[2] + (right_rgb[2]-left_rgb[2]) * t)
        draw.line([(x,0),(x,height)], fill=(r,g,b,255))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,width,height), radius, fill=255)
    out = Image.new("RGBA", (width, height), (0,0,0,0))
    out.paste(grad, (0,0), mask)
    return out

# ---------- строка метрик ----------
def _metric_line(
    stats: List[Tuple[str, str]],
    f_val: ImageFont.FreeTypeFont,
    f_lbl: ImageFont.FreeTypeFont,
    color=(255,255,255,255),
    hgap=BLOCK_HGAP,
    vgap=INNER_VGAP,
) -> Image.Image:
    blocks, total_w, max_h = [], 0, 0
    for v, lab in stats:
        v   = str(v)
        lab = (lab or "").upper().strip()

        val_img = _text_img(v,   f_val, color)
        lbl_img = _text_img(lab, f_lbl, color) if lab else Image.new("RGBA", (1,1), (0,0,0,0))

        w = max(val_img.width, lbl_img.width)
        h = val_img.height + vgap + lbl_img.height

        block = Image.new("RGBA", (w, h), (0,0,0,0))
        block.alpha_composite(val_img, ((w - val_img.width)//2, 0))
        block.alpha_composite(lbl_img, ((w - lbl_img.width)//2, val_img.height + vgap))

        block = _pad_v(block, BLOCK_PAD_TOP, BLOCK_PAD_BOTTOM)

        blocks.append(block)
        total_w += block.width
        max_h = max(max_h, block.height)

    total_w += hgap * (len(blocks) - 1) if blocks else 0
    line = Image.new("RGBA", (max(1,total_w), max_h), (0,0,0,0))
    x = 0
    for b in blocks:
        line.alpha_composite(b, (x, (max_h - b.height)//2))
        x += b.width + hgap
    return line

# ---------- основной рендер ----------
def render_card(
    template: str,
    player_name: str,
    team_name: str,
    team_logo_path: str,
    team_colors: Tuple[str, str, str],  # (primary, dark, light)
    headshot_path: str,
    stats: List[Tuple[str, str]],
    note: Optional[str] = None,
) -> bytes:
    primary_hex, dark_hex, light_hex = team_colors
    primary_rgb = _hex_to_rgb(primary_hex)
    # очень мягкий градиент: слева на ~10% темнее основного
    left_rgb  = _shade(primary_rgb, 0.90)
    right_rgb = primary_rgb

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))

    # Логотип (слева) → Фото (рядом с минимальным зазором)
    logo_raw = Image.open(team_logo_path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    logo_circle = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
    m = Image.new("L", (LOGO_D, LOGO_D), 0); ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
    logo_circle.putalpha(m)
    logo_circle.alpha_composite(logo_raw, ((LOGO_D - LOGO_SIZE)//2, (LOGO_D - LOGO_SIZE)//2))

    head = _circle_crop(headshot_path, HEAD_SIZE)

    # Координаты левой колонны
    logo_x = PAD_L
    head_x = logo_x + LOGO_D + LOGO_GAP

    # Текстовая зона
    name_area_x = head_x + head.width + 36
    name_max_w  = W - name_area_x - PAD_R
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, name_max_w, BASE_NAME, 28)
    name_img    = _pad_v(name_img, NAME_PAD_TOP, NAME_PAD_BOTTOM)

    f_val = _load_font(F_EXO_PATH, BASE_VAL)   # числа — как были
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)   # подписи — МЕНЬШЕ
    stats_line = _metric_line(stats, f_val, f_lbl)

    # Высотный лимит для метрик
    avail_h_for_stats = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
    if avail_h_for_stats < 1: avail_h_for_stats = 1
    if stats_line.height > avail_h_for_stats:
        k = avail_h_for_stats / stats_line.height
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)

    # Impact tag
    star_img = tag_img = None
    if template == "impact":
        try:
            star_img = Image.open("assets/icons/star.png").convert("RGBA").resize((56,56), Image.LANCZOS)
        except Exception:
            star_img = None
        tag_img, _ = _fit_text_to_width("ДЕЛАЕТ РАЗНИЦУ", F_SB_PATH, TAG_MAX_W, TAG_MAX_S, 22)

    # Примечание (single_note)
    note_box_w = 0
    note_img = None
    if template == "single_note" and note:
        note_box_w = 480
        note_img, _ = _fit_text_to_width(note, F_SB_PATH, note_box_w - 40, 38, 20)

    # Правая граница (динамическая ширина панели)
    right_by_name = name_area_x + name_img.width
    if template == "impact":
        right_by_name += 14 + (star_img.width if star_img else 0) + (10 if star_img else 0) + (tag_img.width if tag_img else 0)
    right_by_stats = name_area_x + stats_line.width
    content_right  = max(right_by_name, right_by_stats)
    bar_w = min(W, content_right + PAD_R)
    if template == "single_note" and note_box_w:
        bar_w = max(bar_w, W - PAD_R)

    # Панель с мягким ГРАДИЕНТОМ
    bar_y = H - BAR_H
    panel = _rounded_horizontal_gradient(
        width=bar_w,
        height=BAR_H,
        radius=24,
        left_rgb=left_rgb,    # слегка темнее
        right_rgb=right_rgb,  # основной цвет
    )
    canvas.alpha_composite(panel, (0, bar_y))

    # Размещение логотипа и фото
    logo_y = bar_y + (BAR_H - LOGO_D)//2
    head_y = bar_y - head.height//3
    canvas.alpha_composite(logo_circle, (logo_x, logo_y))
    canvas.alpha_composite(head, (head_x, head_y))

    # Имя
    name_x = name_area_x
    name_y = bar_y + TOP_IN
    canvas.alpha_composite(name_img, (name_x, name_y))

    # Impact tag
    if template == "impact":
        cur_x = name_x + name_img.width + 14
        if star_img:
            canvas.alpha_composite(star_img, (cur_x, name_y - 2))
            cur_x += star_img.width + 10
        if tag_img:
            canvas.alpha_composite(tag_img, (cur_x, name_y + 4))

    # Метрики
    stats_x = name_x
    stats_y = name_y + name_img.height + NAME_STATS_GAP
    canvas.alpha_composite(stats_line, (stats_x, stats_y))

    # Примечание справа
    if template == "single_note" and note_box_w and note_img:
        box = Image.new("RGBA", (note_box_w, BAR_H), (0,0,0,0))
        ImageDraw.Draw(box).rounded_rectangle((0,0,note_box_w,BAR_H), 20, fill=(255,255,255,35))
        box.alpha_composite(note_img, ((note_box_w - note_img.width)//2, (BAR_H - note_img.height)//2))
        canvas.alpha_composite(box, (bar_w - PAD_R - note_box_w, bar_y))

    # PNG
    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()
