from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import io

# Размер холста и панель внизу
W, H = 1920, 1080
BAR_H = 300
PAD = 56

# Пути шрифтов (если файлов нет — упадём на системный дефолт)
F_BOLD_PATH = "assets/fonts/Montserrat-Bold.ttf"
F_SEMIB_PATH = "assets/fonts/Montserrat-SemiBold.ttf"
F_EXO_PATH   = "assets/fonts/Exo2-Bold.ttf"

def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

# Базовые размеры (умеренные)
BASE_NAME_SIZE = 64
BASE_VAL_SIZE  = 48
BASE_LBL_SIZE  = 34

def _fit_text_to_width(text: str, font_path: str, max_w: int, max_size: int, min_size: int = 28):
    """Подбираем размер шрифта так, чтобы строка влезла по ширине max_w."""
    size_lo, size_hi = min_size, max_size
    best_font = _load_font(font_path, size_lo)
    best_img = None

    while size_lo <= size_hi:
        mid = (size_lo + size_hi) // 2
        f = _load_font(font_path, mid)
        bbox = f.getbbox(text)
        w = bbox[2] - bbox[0]
        if w <= max_w:
            best_font = f
            size_lo = mid + 1
        else:
            size_hi = mid - 1

    # отрисуем финальную картинку
    bbox = best_font.getbbox(text)
    img = Image.new("RGBA", (bbox[2]-bbox[0], bbox[3]-bbox[1]), (0,0,0,0))
    ImageDraw.Draw(img).text((0,0), text, font=best_font, fill=(255,255,255,255))
    return img, best_font

def _circle_crop(path: str, diameter: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    s = min(im.size)
    left = (im.width - s) // 2
    top = max(0, im.height - s)
    im = im.crop((left, top, left + s, top + s)).resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    d = ImageDraw.Draw(mask); d.ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out

def _metric_blocks(stats: List[Tuple[str, str]], font_val: ImageFont.FreeTypeFont, font_lbl: ImageFont.FreeTypeFont,
                   color=(255,255,255,255)) -> Image.Image:
    padd = 40
    blocks, total_w, max_h = [], 0, 0
    for val, label in stats:
        val = str(val)
        label = (label or "").upper().strip()

        vb = font_val.getbbox(val)
        val_img = Image.new("RGBA", (vb[2]-vb[0], vb[3]-vb[1]), (0,0,0,0))
        ImageDraw.Draw(val_img).text((0,0), val, font=font_val, fill=color)

        lb = font_lbl.getbbox(label if label else "")
        lb_img = Image.new("RGBA", (max(1, lb[2]-lb[0]), max(1, lb[3]-lb[1])), (0,0,0,0))
        if label:
            ImageDraw.Draw(lb_img).text((0,0), label, font=font_lbl, fill=color)

        h = val_img.height + 8 + lb_img.height
        w = max(val_img.width, lb_img.width)
        block = Image.new("RGBA", (w, h), (0,0,0,0))
        block.alpha_composite(val_img, ((w - val_img.width)//2, 0))
        block.alpha_composite(lb_img, ((w - lb_img.width)//2, val_img.height + 8))
        blocks.append(block)
        total_w += w
        max_h = max(max_h, h)

    total_w += padd * (len(blocks) - 1)
    line = Image.new("RGBA", (total_w, max_h), (0,0,0,0))
    x = 0
    for b in blocks:
        line.alpha_composite(b, (x, (max_h - b.height)//2))
        x += b.width + padd
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
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # Низовая панель
    bar = Image.new("RGBA", (W, BAR_H), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle((0, 0, W, BAR_H), 28, fill=primary)
    bar_y = H - BAR_H
    im.alpha_composite(bar, (0, bar_y))

    # Голова + логотип
    head = _circle_crop(headshot_path, 380)
    im.alpha_composite(head, (PAD, bar_y - head.height//3))

    logo = Image.open(team_logo_path).convert("RGBA").resize((150, 150), Image.LANCZOS)
    logo_circle = Image.new("RGBA", (170, 170), (255, 255, 255, 255))
    m = Image.new("L", (170, 170), 0); ImageDraw.Draw(m).ellipse((0,0,170,170), fill=255)
    logo_circle.putalpha(m)
    logo_circle.alpha_composite(logo, ((170 - logo.width)//2, (170 - logo.height)//2))
    im.alpha_composite(logo_circle, (PAD + head.width - 120, bar_y + 28))

    # Имя (с подгонкой по ширине)
    name_area_x = PAD + head.width + 36
    name_area_w = W - name_area_x - PAD
    # оставим запас под "ДЕЛАЕТ РАЗНИЦУ" справа, если template == impact
    reserve_right = 360 if template == "impact" else 0
    name_max_w = max(300, name_area_w - reserve_right)

    name_img, name_font = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, name_max_w, BASE_NAME_SIZE, 28)
    name_y = bar_y + 28
    im.alpha_composite(name_img, (name_area_x, name_y))

    # Метрики
    font_val = _load_font(F_EXO_PATH, BASE_VAL_SIZE)
    font_lbl = _load_font(F_SEMIB_PATH, BASE_LBL_SIZE)
    stats_line = _metric_blocks(stats, font_val, font_lbl)

    # Если строка метрик шире зоны — уменьшим пропорционально
    stats_max_w = name_area_w
    if stats_line.width > stats_max_w:
        ratio = stats_max_w / stats_line.width
        new_w = max(1, int(stats_line.width * ratio))
        new_h = max(1, int(stats_line.height * ratio))
        stats_line = stats_line.resize((new_w, new_h), Image.LANCZOS)

    stats_x = name_area_x
    stats_y = name_y + name_img.height + 18
    im.alpha_composite(stats_line, (stats_x, stats_y))

    # Доп. элементы шаблонов
    draw = ImageDraw.Draw(im)
    if template == "impact":
        try:
            star = Image.open("assets/icons/star.png").convert("RGBA").resize((64, 64), Image.LANCZOS)
            im.alpha_composite(star, (name_area_x + name_img.width + 16, name_y - 4))
        except Exception:
            pass
        tag = "ДЕЛАЕТ РАЗНИЦУ"
        tag_img, _ = _fit_text_to_width(tag, F_SEMIB_PATH, 260, 40, 22)
        im.alpha_composite(tag_img, (name_area_x + name_img.width + 16 + 72, name_y + 6))

    elif template == "bad":
        try:
            poop = Image.open("assets/icons/poop.png").convert("RGBA").resize((64, 64), Image.LANCZOS)
            im.alpha_composite(poop, (name_area_x - 80, name_y - 4))
        except Exception:
            pass
        # лёгкое затемнение панели
        shade = Image.new("RGBA", (W, BAR_H), (0, 0, 0, 55))
        im.alpha_composite(shade, (0, bar_y))

    elif template == "single_note" and note:
        # лёгкая плашка справа
        box_w = 520
        box = Image.new("RGBA", (box_w, BAR_H), (0,0,0,0))
        ImageDraw.Draw(box).rounded_rectangle((0,0,box_w,BAR_H), 24, fill=(255,255,255,35))
        note_img, _ = _fit_text_to_width(note, F_SEMIB_PATH, box_w - 40, 40, 22)
        box.alpha_composite(note_img, ((box_w - note_img.width)//2, (BAR_H - note_img.height)//2))
        im.alpha_composite(box, (W - box_w - PAD, bar_y))

    # Вывод
    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue()
