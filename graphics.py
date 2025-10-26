# graphics.py — правильные bbox (без налезаний) + нормальные отступы + динамическая ширина

from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import io

# Холст
W, H = 1920, 1080

# Панель
BAR_H          = 360     # побольше воздуха по вертикали
PAD_L          = 56
PAD_R          = 56
TOP_IN         = 36
BOT_IN         = 32

# Интервалы
NAME_STATS_GAP = 32      # между ИМЕНЕМ и метриками
BLOCK_HGAP     = 64      # между блоками метрик
INNER_VGAP     = 18      # между числом и подписью внутри блока

# Размеры головы/логотипа
HEAD_SIZE      = 380
LOGO_D         = 170
LOGO_SIZE      = 150

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
BASE_NAME = 60
BASE_VAL  = 50
BASE_LBL  = 36
TAG_MAX_W = 280
TAG_MAX_S = 42

def _text_img(text: str, font: ImageFont.FreeTypeFont, fill=(255,255,255,255)) -> Image.Image:
    """Рисует текст БЕЗ обрезаний: учитывает отрицательные отступы bbox."""
    # сначала считаем bbox через textbbox (даёт реальные границы)
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    w, h = right - left, bottom - top
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-left, -top), text, font=font, fill=fill)
    return img

def _fit_text_to_width(text: str, font_path: str, max_w: int, max_size: int, min_size: int = 26):
    """Подбираем размер шрифта по ширине и возвращаем готовую картинку текста."""
    lo, hi = min_size, max_size
    best_font = _load_font(font_path, lo)

    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)

    while lo <= hi:
        mid = (lo + hi) // 2
        f = _load_font(font_path, mid)
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        w = r - l
        if w <= max_w:
            best_font = f
            lo = mid + 1
        else:
            hi = mid - 1

    return _text_img(text, best_font), best_font

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

        # вертикальная сборка: значение сверху, подпись снизу, между ними vgap
        w = max(val_img.width, lbl_img.width)
        h = val_img.height + vgap + lbl_img.height
        block = Image.new("RGBA", (w, h), (0,0,0,0))
        block.alpha_composite(val_img, ((w - val_img.width)//2, 0))
        block.alpha_composite(lbl_img, ((w - lbl_img.width)//2, val_img.height + vgap))

        blocks.append(block)
        total_w += w
        max_h = max(max_h, h)

    total_w += hgap * (len(blocks) - 1) if blocks else 0
    line = Image.new("RGBA", (max(1,total_w), max_h), (0,0,0,0))
    x = 0
    for b in blocks:
        line.alpha_composite(b, (x, (max_h - b.height)//2))
        x += b.width + hgap
    return line

def render_card(
    template: str,
    player_name: str,
    team_name: str,
    team_logo_path: str,
    team_colors: Tuple[str, str, str],
    headshot_path: str,
    stats: List[Tuple[str, str]],
    note: Optional[str] = None,
) -> bytes:
    primary, dark, light = team_colors
    canvas = Image.new("RGBA", (W, H), (0,0,0,0))

    # -------- элементы заранее (чтобы посчитать ширину панели) --------
    head = _circle_crop(headshot_path, HEAD_SIZE)

    logo_raw = Image.open(team_logo_path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    logo_circle = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
    m = Image.new("L", (LOGO_D, LOGO_D), 0); ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
    logo_circle.putalpha(m)
    logo_circle.alpha_composite(logo_raw, ((LOGO_D - LOGO_SIZE)//2, (LOGO_D - LOGO_SIZE)//2))

    # Имя (с подгонкой по ширине)
    name_area_x = PAD_L + head.width + 36
    name_max_w  = W - name_area_x - PAD_R
    name_img, name_font = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, name_max_w, BASE_NAME, 28)

    # Метрики (значение+подпись) — уже без обрезаний
    f_val = _load_font(F_EXO_PATH, BASE_VAL)
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
    stats_line = _metric_line(stats, f_val, f_lbl)

    # Высотный лимит для метрик
    avail_h_for_stats = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
    if avail_h_for_stats < 1: avail_h_for_stats = 1
    if stats_line.height > avail_h_for_stats:
        k = avail_h_for_stats / stats_line.height
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)

    # Тег impact справа от имени
    star_img = tag_img = None
    if template == "impact":
        try:
            star_img = Image.open("assets/icons/star.png").convert("RGBA").resize((64,64), Image.LANCZOS)
        except Exception:
            star_img = None
        tag_img, _ = _fit_text_to_width("ДЕЛАЕТ РАЗНИЦУ", F_SB_PATH, TAG_MAX_W, TAG_MAX_S, 22)

    # Бокс заметки справа (single_note)
    note_box_w = 0
    note_img = None
    if template == "single_note" and note:
        note_box_w = 520
        note_img, _ = _fit_text_to_width(note, F_SB_PATH, note_box_w - 40, 40, 22)

    # Правая граница по содержимому
    right_by_name = name_area_x + name_img.width
    if template == "impact":
        right_by_name += 16 + (star_img.width if star_img else 0) + (12 if star_img else 0) + (tag_img.width if tag_img else 0)
    right_by_stats = name_area_x + stats_line.width
    content_right  = max(right_by_name, right_by_stats)
    bar_w = min(W, content_right + PAD_R)
    if template == "single_note" and note_box_w:
        bar_w = max(bar_w, PAD_L + head.width + 36 + name_img.width + 120, W - PAD_R)  # панель растягиваем до правого края для бокса

    # -------- рисуем --------
    bar_y = H - BAR_H
    panel = Image.new("RGBA", (bar_w, BAR_H), (0,0,0,0))
    ImageDraw.Draw(panel).rounded_rectangle((0,0,bar_w,BAR_H), 28, fill=primary)
    canvas.alpha_composite(panel, (0, bar_y))

    # Головa/логотип
    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(head, (head_x, head_y))
    canvas.alpha_composite(logo_circle, (PAD_L + head.width - 120, bar_y + 28))

    # Имя
    name_x = name_area_x
    name_y = bar_y + TOP_IN
    canvas.alpha_composite(name_img, (name_x, name_y))

    # Impact-tag справа от имени
    if template == "impact":
        cur_x = name_x + name_img.width + 16
        if star_img:
            canvas.alpha_composite(star_img, (cur_x, name_y - 2))
            cur_x += star_img.width + 12
        if tag_img:
            canvas.alpha_composite(tag_img, (cur_x, name_y + 6))

    # Метрики (строго ниже имени, с явным зазором)
    stats_x = name_x
    stats_y = name_y + name_img.height + NAME_STATS_GAP
    canvas.alpha_composite(stats_line, (stats_x, stats_y))

    # Бокс заметки справа
    if template == "single_note" and note_box_w and note_img:
        box = Image.new("RGBA", (note_box_w, BAR_H), (0,0,0,0))
        ImageDraw.Draw(box).rounded_rectangle((0,0,note_box_w,BAR_H), 24, fill=(255,255,255,35))
        box.alpha_composite(note_img, ((note_box_w - note_img.width)//2, (BAR_H - note_img.height)//2))
        canvas.alpha_composite(box, (bar_w - PAD_R - note_box_w, bar_y))

    # PNG
    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()
