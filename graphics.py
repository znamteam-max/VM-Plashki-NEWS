# graphics.py — аккуратные отступы + авто-масштаб + динамическая ширина плашки

from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import io

# Размеры холста
W, H = 1920, 1080

# Геометрия панели
BAR_H          = 340      # высота нижней плашки
PAD_L          = 56       # левый внешний отступ панели
PAD_R          = 56       # правый внешний отступ панели (к краю панели, не холста)
TOP_IN         = 28       # верхний внутренний отступ внутри панели
BOT_IN         = 26       # нижний внутренний отступ внутри панели
NAME_STATS_GAP = 28       # между именем и строкой метрик
BLOCK_HGAP     = 56       # горизонтальный зазор между блоками метрик
INNER_VGAP     = 14       # вертикальный зазор между числом и подписью
HEAD_SIZE      = 380      # диаметр головы
LOGO_D         = 170      # круг с логотипом
LOGO_SIZE      = 150      # размер логотипа внутри круга

# Пути шрифтов (если их нет — используем системный дефолт)
F_BOLD_PATH = "assets/fonts/Montserrat-Bold.ttf"
F_SB_PATH   = "assets/fonts/Montserrat-SemiBold.ttf"
F_EXO_PATH  = "assets/fonts/Exo2-Bold.ttf"

def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

# Базовые размеры шрифтов (будут ещё подгоняться)
BASE_NAME = 60
BASE_VAL  = 48
BASE_LBL  = 34
TAG_MAX_W = 260
TAG_MAX_S = 40

def _fit_text_to_width(text: str, font_path: str, max_w: int, max_size: int, min_size: int = 24):
    size_lo, size_hi = min_size, max_size
    best = _load_font(font_path, size_lo)
    # бинарный поиск размера
    while size_lo <= size_hi:
        mid = (size_lo + size_hi) // 2
        f = _load_font(font_path, mid)
        w = f.getbbox(text)[2] - f.getbbox(text)[0]
        if w <= max_w:
            best = f
            size_lo = mid + 1
        else:
            size_hi = mid - 1
    # картинка текста
    bbox = best.getbbox(text)
    img  = Image.new("RGBA", (bbox[2]-bbox[0], bbox[3]-bbox[1]), (0,0,0,0))
    ImageDraw.Draw(img).text((0,0), text, font=best, fill=(255,255,255,255))
    return img, best

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
        v = str(v)
        lab = (lab or "").upper().strip()

        vb  = f_val.getbbox(v)
        vimg= Image.new("RGBA", (vb[2]-vb[0], vb[3]-vb[1]), (0,0,0,0))
        ImageDraw.Draw(vimg).text((0,0), v, font=f_val, fill=color)

        if lab:
            lb  = f_lbl.getbbox(lab)
            limg= Image.new("RGBA", (lb[2]-lb[0], lb[3]-lb[1]), (0,0,0,0))
            ImageDraw.Draw(limg).text((0,0), lab, font=f_lbl, fill=color)
        else:
            limg= Image.new("RGBA", (1,1), (0,0,0,0))

        h = vimg.height + vgap + limg.height
        w = max(vimg.width, limg.width)

        block = Image.new("RGBA", (w, h), (0,0,0,0))
        block.alpha_composite(vimg, ((w - vimg.width)//2, 0))
        block.alpha_composite(limg, ((w - limg.width)//2, vimg.height + vgap))

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

    # ===== заранее собираем элементы, чтобы посчитать нужную ширину панели =====
    head = _circle_crop(headshot_path, HEAD_SIZE)

    # Лого в круге
    logo_raw = Image.open(team_logo_path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    logo_circle = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
    m = Image.new("L", (LOGO_D, LOGO_D), 0); ImageDraw.Draw(m).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
    logo_circle.putalpha(m)
    logo_circle.alpha_composite(logo_raw, ((LOGO_D - LOGO_SIZE)//2, (LOGO_D - LOGO_SIZE)//2))

    # Имя с подбором размера под ширину
    name_area_x = PAD_L + head.width + 36
    # пока не знаем ширину панели, предполагаем максимум до правого края холста
    name_max_w = W - name_area_x - PAD_R
    name_img, name_font = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, name_max_w, BASE_NAME, 26)

    # Строка метрик
    f_val = _load_font(F_EXO_PATH, BASE_VAL)
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
    stats_line = _metric_line(stats, f_val, f_lbl)

    # По высоте метрики должны влезть в панель
    avail_h_for_stats = BAR_H - TOP_IN - (name_img.height + NAME_STATS_GAP) - BOT_IN
    if avail_h_for_stats < 1:
        avail_h_for_stats = 1
    if stats_line.height > avail_h_for_stats:
        k = avail_h_for_stats / stats_line.height
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)

    # Элементы шаблонов справа от имени
    star_img = tag_img = None
    extra_right = 0
    if template == "impact":
        try:
            star_img = Image.open("assets/icons/star.png").convert("RGBA").resize((64,64), Image.LANCZOS)
        except Exception:
            star_img = None
        tag_text = "ДЕЛАЕТ РАЗНИЦУ"
        tag_img, _ = _fit_text_to_width(tag_text, F_SB_PATH, TAG_MAX_W, TAG_MAX_S, 22)
        extra_right = (star_img.width + 16 + tag_img.width) if star_img else tag_img.width

    # Для single_note справа может быть прямоугольник — учитываем его как правую границу
    note_box_w = 0
    note_img = None
    if template == "single_note" and note:
        note_box_w = 520
        note_img, _ = _fit_text_to_width(note, F_SB_PATH, note_box_w - 40, 40, 22)

    # Правая граница по содержимому (имя + звезда/тег, метрики, бокс заметки)
    right_by_name  = name_area_x + name_img.width + (16 + (star_img.width if star_img else 0) + (tag_img.width if tag_img else 0)) if template == "impact" else name_area_x + name_img.width
    right_by_stats = name_area_x + stats_line.width
    right_by_note  = W - PAD_R if note_box_w else 0  # бокс прижмём справа панели, его ширина учтётся в самой панели
    content_right  = max(right_by_name, right_by_stats, right_by_note)

    # Ширина панели = контент + правый внутренний отступ, но не больше ширины холста
    bar_w = min(W, content_right + PAD_R)
    if template == "single_note" and note:
        # добавляем пространство под правый бокс
        bar_w = min(W, max(bar_w, W - PAD_R))  # панель дотянем до позиции бокса (он прижат к правому краю панели)

    # ===== рисуем панель нужной ширины и раскладываем элементы =====
    bar_y = H - BAR_H
    panel = Image.new("RGBA", (bar_w, BAR_H), (0,0,0,0))
    ImageDraw.Draw(panel).rounded_rectangle((0,0,bar_w,BAR_H), 28, fill=primary)

    # Куда ставим элементы на панели
    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(panel, (0, bar_y))
    canvas.alpha_composite(head, (head_x, head_y))

    # Лого
    logo_x = PAD_L + head.width - 120
    logo_y = bar_y + 28
    canvas.alpha_composite(logo_circle, (logo_x, logo_y))

    # Имя
    name_x = name_area_x
    name_y = bar_y + TOP_IN
    canvas.alpha_composite(name_img, (name_x, name_y))

    # Тег impact (звезда + текст) — справа от имени, не заезжает на метрики
    if template == "impact":
        cur_x = name_x + name_img.width + 16
        if star_img:
            canvas.alpha_composite(star_img, (cur_x, name_y - 2))
            cur_x += star_img.width + 12
        if tag_img:
            # ставим на уровне имени с небольшим смещением
            canvas.alpha_composite(tag_img, (cur_x, name_y + 6))

    # Метрики
    stats_x = name_x
    stats_y = name_y + name_img.height + NAME_STATS_GAP
    canvas.alpha_composite(stats_line, (stats_x, stats_y))

    # Бокс заметки (single_note) — прижимаем к правому краю панели
    if template == "single_note" and note and note_img:
        box_w = 520
        box = Image.new("RGBA", (box_w, BAR_H), (0,0,0,0))
        ImageDraw.Draw(box).rounded_rectangle((0,0,box_w,BAR_H), 24, fill=(255,255,255,35))
        box.alpha_composite(note_img, ((box_w - note_img.width)//2, (BAR_H - note_img.height)//2))
        box_x = bar_w - PAD_R - box_w
        box_y = bar_y
        canvas.alpha_composite(box, (box_x, box_y))

    # Выход — PNG с прозрачностью
    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()
