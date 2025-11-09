# graphics.py
# Рендер плашек: render_card, render_card2, render_card_bad, render_card_special
# Без зависимостей от api/telegram — чтобы не было круговых импортов.

from __future__ import annotations
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io, os, math

# ------------------------- Утилиты -------------------------

def _hex_to_rgb(h: Optional[str], default=(16, 24, 40)) -> Tuple[int,int,int]:
    if not h:
        return default
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c*2 for c in h)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0,2,4))  # type: ignore
    except Exception:
        return default

def _ensure_font(size: int, bold: bool=False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Попытка системных шрифтов; затемfallback
    candidates = []
    if bold:
        candidates += ["Arial Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    else:
        candidates += ["Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()

def _textsize(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    words = text.replace("\n", " ").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        test = w if not cur else f"{cur} {w}"
        wpx,_ = _textsize(draw, test, font)
        if wpx <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def _paste_centered(img: Image.Image, src: Image.Image, box: Tuple[int,int,int,int]):
    # Вписываем по центру со сохранением пропорций
    bx, by, bw, bh = box[0], box[1], box[2]-box[0], box[3]-box[1]
    if src.width == 0 or src.height == 0:
        return
    scale = min(bw/src.width, bh/src.height)
    sw, sh = max(1, int(src.width*scale)), max(1, int(src.height*scale))
    src2 = src.resize((sw, sh), Image.LANCZOS)
    px = bx + (bw - sw)//2
    py = by + (bh - sh)//2
    img.alpha_composite(src2, (px, py))

def _round_mask(w: int, h: int, r: int, left: bool, right: bool) -> Image.Image:
    # Маска для скруглений по сторонам (левые и/или правые углы)
    mask = Image.new("L", (w,h), 255)
    if r <= 0:
        return mask
    d = ImageDraw.Draw(mask)

    # зальём прозрачным и восстановим прямоугольник по центру — так проще
    mask.paste(0, (0,0,w,h))
    rect = Image.new("L", (w,h), 255)
    mask = ImageChops_overlay(mask, rect)  # заполнить

    # затрём углы при необходимости
    def cut_corner(cx, cy):
        corner = Image.new("L", (r*2, r*2), 0)
        cd = ImageDraw.Draw(corner)
        cd.pieslice((0,0,r*2,r*2), 0, 360, fill=255)
        mask.paste(corner, (cx, cy), corner)

    # по умолчанию все скруглены, мы хотим selectively
    # Реализуем как наложение непрозрачности: сначала прямоугольник, затем дорисуем «дырки» квадратами
    # Проще: вручную рисуем чёрные квадраты там, где НЕ нужно скругление (но мы хотим скругление справа/слева)
    # Сделаем наоборот: создадим полную маску без скруглений, затем дорисуем скругленные углы.
    # Чтобы не усложнять — воспользуемся готовой функцией с вычитанием прямых углов.
    # (Ниже — простой рабочий подход без лишних изысков)
    mask = Image.new("L", (w,h), 255)
    d = ImageDraw.Draw(mask)
    d.rectangle([0,0,w,h], fill=255)

    if right:
        # правый верхний
        corner = Image.new("L", (r*2, r*2), 0)
        cd = ImageDraw.Draw(corner)
        cd.pieslice((0,0,r*2,r*2), 180, 270, fill=255)
        mask.paste(corner, (w - 2*r, 0), corner)
        # правый нижний
        corner2 = Image.new("L", (r*2, r*2), 0)
        cd2 = ImageDraw.Draw(corner2)
        cd2.pieslice((0,0,r*2,r*2), 90, 180, fill=255)
        mask.paste(corner2, (w - 2*r, h - 2*r), corner2)
    if left:
        # левый верхний
        corner3 = Image.new("L", (r*2, r*2), 0)
        cd3 = ImageDraw.Draw(corner3)
        cd3.pieslice((0,0,r*2,r*2), 270, 360, fill=255)
        mask.paste(corner3, (0, 0), corner3)
        # левый нижний
        corner4 = Image.new("L", (r*2, r*2), 0)
        cd4 = ImageDraw.Draw(corner4)
        cd4.pieslice((0,0,r*2,r*2), 0, 90, fill=255)
        mask.paste(corner4, (0, h - 2*r), corner4)
    return mask

def ImageChops_overlay(a: Image.Image, b: Image.Image) -> Image.Image:
    # простой overlay fill; в данном файле используем как «присваивание»
    out = a.copy()
    out.paste(b)
    return out

def _ensure_rgba(img: Optional[Image.Image]) -> Optional[Image.Image]:
    if img is None: return None
    if img.mode != "RGBA":
        return img.convert("RGBA")
    return img

def _with_circle(img: Image.Image, diameter: int) -> Image.Image:
    # Обрезать в круг
    size = (diameter, diameter)
    img2 = img.resize(size, Image.LANCZOS)
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([0,0,diameter,diameter], fill=255)
    out = Image.new("RGBA", size, (0,0,0,0))
    out.paste(img2, (0,0), mask)
    return out

def _auto_font_to_fit(draw: ImageDraw.ImageDraw, text: str, min_size: int, max_size: int, max_width: int, bold=False) -> ImageFont.ImageFont:
    lo, hi = min_size, max_size
    best = _ensure_font(min_size, bold=bold)
    while lo <= hi:
        mid = (lo+hi)//2
        f = _ensure_font(mid, bold=bold)
        w,_ = _textsize(draw, text, f)
        if w <= max_width:
            best = f
            lo = mid+1
        else:
            hi = mid-1
    return best

def _compose_base(width: int, height: int, bg=(0,0,0,0)) -> Image.Image:
    return Image.new("RGBA", (width, height), bg)

# ------------------------- Общие параметры -------------------------

PAD_X = 28
PAD_Y = 22
RADIUS = 26  # базовый радиус для скруглений
LOGO_DIAM = 88
LOGO_SHIFT_X = -30  # сдвиг логотипа в кружке: левее
LOGO_SHIFT_Y = -30  # сдвиг вверх
HEAD_W = 180
HEAD_H = 180

MAIN_H = 220  # базовая высота карточек (может увеличиться для cardS при больших текстах)

# ------------------------- Рендеры -------------------------

def render_card(name_ru: str,
                subtitle: str,
                logo_img: Optional[Image.Image],
                colors: Tuple[str,str,str] | None,
                head_img: Image.Image,
                stats: List[Tuple[str,str]]) -> Image.Image:
    """
    Обычная плашка: скругления только справа; центрирование имени и статов.
    """
    # Цвета
    c1 = _hex_to_rgb((colors or ("#0A2A4A","#081E36","#0A2A4A"))[0])
    bg = c1

    # Канва
    W = 1200  # временно макс, потом обрежем по содержимому
    H = MAIN_H
    base = _compose_base(W, H)
    draw = ImageDraw.Draw(base)

    # Шрифты
    name_font = _ensure_font(52, bold=True)
    stat_val_font = _ensure_font(48, bold=True)
    stat_lab_font = _ensure_font(28, bold=False)

    # Измерим ширину по контенту
    name_w, name_h = _textsize(draw, name_ru, name_font)
    stat_blocks = []
    sb_w_total = 0
    gap_stat = 26
    for val, lab in stats[:4]:  # 4 столбца максимум для компактности
        vw, vh = _textsize(draw, str(val), stat_val_font)
        lw, lh = _textsize(draw, str(lab), stat_lab_font)
        bw = max(vw, lw) + 12
        bh = vh + 6 + lh
        stat_blocks.append((bw,bh,vw,vh,lw,lh,val,lab))
        sb_w_total += bw
    inner_w = max(name_w, sb_w_total + gap_stat*(max(0, len(stat_blocks)-1)))
    inner_w += PAD_X*2 + HEAD_W + 40 + LOGO_DIAM  # место под аватар и логотип
    W_eff = min(W, inner_w)

    # Фон с правым скруглением
    bg_layer = Image.new("RGBA", (W_eff, H), (*bg, 255))
    mask = _round_mask(W_eff, H, RADIUS, left=False, right=True)
    card = Image.new("RGBA", (W_eff, H), (0,0,0,0))
    card.paste(bg_layer, (0,0), mask)
    base.alpha_composite(card, (0,0))

    # Логотип (круг) поверх
    if logo_img:
        logo_img = _ensure_rgba(logo_img)
        circ = _with_circle(logo_img, LOGO_DIAM)
        base.alpha_composite(circ, (PAD_X + LOGO_SHIFT_X, PAD_Y + LOGO_SHIFT_Y))

    # Имя и статы: центрируем по вертикали между верхним и нижним бордерами
    content_x = PAD_X + LOGO_DIAM + 20
    head_box = (content_x, (H-HEAD_H)//2, content_x+HEAD_W, (H-HEAD_H)//2+HEAD_H)

    # Текст слева от head? Нет, по ТЗ голова справа/слева? Берём голову слева, текст — справа
    text_x = head_box[2] + 30
    top_y = PAD_Y
    draw.text((text_x, top_y), name_ru, font=name_font, fill=(255,255,255,255))
    top_y += name_h + 8

    # Рисуем столбики статов по центру строки
    x = text_x
    y_val = top_y
    for i,(bw,bh,vw,vh,lw,lh,val,lab) in enumerate(stat_blocks):
        vx = x + (bw - vw)//2
        draw.text((vx, y_val), str(val), font=stat_val_font, fill=(255,255,255,255))
        ly = y_val + vh + 6
        lx = x + (bw - lw)//2
        draw.text((lx, ly), str(lab), font=stat_lab_font, fill=(230,230,230,255))
        x += bw + gap_stat

    # Фото игрока поверх всех
    head_img = _ensure_rgba(head_img)
    if head_img:
        _paste_centered(base, head_img, head_box)

    return base.crop((0,0,W_eff,H))


def render_card2(name_a: str, subtitle_a: str, logo_a: Optional[Image.Image], colors_a: Tuple[str,str,str] | None, head_a: Image.Image, stats_a: List[Tuple[str,str]],
                 name_b: str, subtitle_b: str, logo_b: Optional[Image.Image], colors_b: Tuple[str,str,str] | None, head_b: Image.Image, stats_b: List[Tuple[str,str]]) -> Image.Image:
    """
    Двойная плашка без скруглений вообще.
    Размер шрифта имён >= размера статистики (на 2pt больше). Автоподгон имён.
    """
    H = MAIN_H
    # Цвета
    ca = _hex_to_rgb((colors_a or ("#0A2A4A","#081E36","#0A2A4A"))[0])
    cb = _hex_to_rgb((colors_b or ("#0A2A4A","#081E36","#0A2A4A"))[0])

    # Канва пока широкая
    W = 1600
    base = _compose_base(W, H)
    draw = ImageDraw.Draw(base)

    # Авто-шрифт имен: максимальная ширина каждой половины
    half_w = (W - PAD_X*2 - 40)//2
    name_font_a = _auto_font_to_fit(draw, name_a, 36, 64, half_w - HEAD_W - LOGO_DIAM - 40, bold=True)
    name_font_b = _auto_font_to_fit(draw, name_b, 36, 64, half_w - HEAD_W - LOGO_DIAM - 40, bold=True)

    # Шрифт статистики на 2pt меньше, чем имя (но не меньше 20)
    stat_val_font_a = _ensure_font(max(20, name_font_a.size - 2), bold=True)
    stat_val_font_b = _ensure_font(max(20, name_font_b.size - 2), bold=True)
    stat_lab_font = _ensure_font( max(16, min(stat_val_font_a.size, stat_val_font_b.size) - 12), bold=False )

    # Блок А фон
    Aw = half_w
    Abg = Image.new("RGBA", (Aw, H), (*ca, 255))
    base.alpha_composite(Abg, (0,0))
    # Блок B фон
    Bw = half_w
    Bbg = Image.new("RGBA", (Bw, H), (*cb, 255))
    base.alpha_composite(Bbg, (Aw, 0))

    # Логотипы
    if logo_a:
        circ_a = _with_circle(_ensure_rgba(logo_a), LOGO_DIAM)
        base.alpha_composite(circ_a, (PAD_X + LOGO_SHIFT_X, PAD_Y + LOGO_SHIFT_Y))
    if logo_b:
        circ_b = _with_circle(_ensure_rgba(logo_b), LOGO_DIAM)
        base.alpha_composite(circ_b, (Aw + PAD_X + LOGO_SHIFT_X, PAD_Y + LOGO_SHIFT_Y))

    # Текст и статы — одинаковое выравнивание по обеим сторонам
    gap_stat = 26

    def draw_side(x0: int, name: str, name_font, head_img, stats):
        draw.text((x0 + PAD_X + LOGO_DIAM + 20 + HEAD_W + 30, PAD_Y), name, font=name_font, fill=(255,255,255,255))
        # статы
        sx = x0 + PAD_X + LOGO_DIAM + 20 + HEAD_W + 30
        y0 = PAD_Y + _textsize(draw, name, name_font)[1] + 8
        # столбцы
        for val, lab in (stats or [])[:4]:
            vw, vh = _textsize(draw, str(val), stat_val_font_a if name_font is name_font_a else stat_val_font_b)
            lw, lh = _textsize(draw, str(lab), stat_lab_font)
            bw = max(vw, lw) + 12
            vx = sx + (bw - vw)//2
            draw.text((vx, y0), str(val), font=(stat_val_font_a if name_font is name_font_a else stat_val_font_b), fill=(255,255,255,255))
            draw.text((sx + (bw - lw)//2, y0 + vh + 6), str(lab), font=stat_lab_font, fill=(230,230,230,255))
            sx += bw + gap_stat

        # head поверх
        hb = (x0 + PAD_X + LOGO_DIAM + 20, (H-HEAD_H)//2, x0 + PAD_X + LOGO_DIAM + 20 + HEAD_W, (H-HEAD_H)//2 + HEAD_H)
        _paste_centered(base, _ensure_rgba(head_img), hb)

    draw_side(0, name_a, name_font_a, head_a, stats_a)
    draw_side(Aw, name_b, name_font_b, head_b, stats_b)

    return base.crop((0,0,Aw+Bw,H))


def render_card_bad(name_ru: str,
                    subtitle: str,
                    logo_img: Optional[Image.Image],
                    colors_ignored: Tuple[str,str,str] | None,
                    head_img: Image.Image,
                    stats: List[Tuple[str,str]]) -> Image.Image:
    """
    Плашка BAD: всегда коричневая; скругления только справа; рядом с именем — какашка.
    """
    H = MAIN_H
    W = 1200
    base = _compose_base(W, H)
    draw = ImageDraw.Draw(base)

    brown = (92, 64, 51)  # плохой коричневый
    bg_layer = Image.new("RGBA", (W, H), (*brown, 255))
    mask = _round_mask(W, H, RADIUS, left=False, right=True)
    card = Image.new("RGBA", (W, H), (0,0,0,0))
    card.paste(bg_layer, (0,0), mask)
    base.alpha_composite(card, (0,0))

    # имя
    name_font = _ensure_font(56, bold=True)
    name_w, name_h = _textsize(draw, name_ru, name_font)
    nx = PAD_X + LOGO_DIAM + 20 + HEAD_W + 30
    ny = PAD_Y
    draw.text((nx, ny), name_ru, font=name_font, fill=(255,255,255,255))

    # 💩 справа от имени, по нижней границе имени
    poop = "💩"
    emoji_font = _ensure_font(name_font.size, bold=False)
    pw, ph = _textsize(draw, poop, emoji_font)
    draw.text((nx + name_w + 12, ny + max(0, name_h - ph) - 18), poop, font=emoji_font, fill=(255,255,255,255))

    # статы
    stat_val_font = _ensure_font(46, bold=True)
    stat_lab_font = _ensure_font(28, bold=False)
    y0 = ny + name_h + 8
    sx = nx
    gap_stat = 26
    for val, lab in (stats or [])[:4]:
        vw, vh = _textsize(draw, str(val), stat_val_font)
        lw, lh = _textsize(draw, str(lab), stat_lab_font)
        bw = max(vw, lw) + 12
        draw.text((sx + (bw - vw)//2, y0), str(val), font=stat_val_font, fill=(255,255,255,255))
        draw.text((sx + (bw - lw)//2, y0 + vh + 6), str(lab), font=stat_lab_font, fill=(230,230,230,255))
        sx += bw + gap_stat

    # фото и логотип
    if logo_img:
        circ = _with_circle(_ensure_rgba(logo_img), LOGO_DIAM)
        base.alpha_composite(circ, (PAD_X + LOGO_SHIFT_X, PAD_Y + LOGO_SHIFT_Y))
    _paste_centered(base, _ensure_rgba(head_img), (PAD_X + LOGO_DIAM + 20, (H-HEAD_H)//2, PAD_X + LOGO_DIAM + 20 + HEAD_W, (H-HEAD_H)//2+HEAD_H))

    # обрезка по реальной ширине
    used_w = max(nx + name_w + 12 + pw + PAD_X, sx)
    used_w = min(W, max(used_w, 700))
    return base.crop((0,0,used_w,H))


def render_card_special(name_ru: str,
                        subtitle: str,
                        logo_img: Optional[Image.Image],
                        colors: Tuple[str,str,str] | None,
                        head_img: Image.Image,
                        stats: List[Tuple[str,str]],
                        right_text: str) -> Image.Image:
    """
    Левая — как card (правые скругления). Правая узкая панель со ⭐, со скруглениями слева+справа.
    Последняя строка не обрезается: добавляем нижний внутренний паддинг.
    """
    H = MAIN_H
    base_W = 1500
    base = _compose_base(base_W, H)
    draw = ImageDraw.Draw(base)

    # Левая
    left = render_card(name_ru, subtitle, logo_img, colors, head_img, stats)
    lw, lh = left.size
    base.alpha_composite(left, (0,0))

    # Правая панель
    star = "⭐"
    right_pad_x = 24
    right_pad_y = 20
    right_w = max(420, int(lw*0.42))
    right_x = lw + 10
    c2 = _hex_to_rgb((colors or ("#0A2A4A","#081E36","#0A2A4A"))[1])
    layer = Image.new("RGBA", (right_w, H), (*c2, 255))
    mask = _round_mask(right_w, H, RADIUS, left=True, right=True)
    panel = Image.new("RGBA", (right_w, H), (0,0,0,0))
    panel.paste(layer, (0,0), mask)
    base.alpha_composite(panel, (right_x, 0))

    # Текст: звёздочка, затем переносы, низ — с доп. паддингом
    title_font = _ensure_font(40, bold=True)
    body_font  = _ensure_font(32, bold=False)

    # первая строка со звёздой
    sx = right_x + right_pad_x
    sy = right_pad_y
    draw.text((sx, sy), star, font=title_font, fill=(255,255,255,255))
    sw,_ = _textsize(draw, star, title_font)
    sx_text = sx + sw + 10

    max_text_w = right_x + right_w - right_pad_x - sx_text
    lines = _wrap_text(draw, right_text, body_font, max_text_w)
    # рисуем текст; добавляем пустую строку внизу (анти-обрезание)
    ty = sy
    for i, line in enumerate(lines):
        if i == 0:
            # первую строку — рядом со звездой
            draw.text((sx_text, ty+6), line, font=body_font, fill=(255,255,255,255))
        else:
            draw.text((right_x + right_pad_x, ty), line, font=body_font, fill=(255,255,255,255))
        _, lh2 = _textsize(draw, line, body_font)
        ty += lh2 + 6
    # нижний отступ
    ty += int(body_font.size * 0.8)

    # Общее изображение
    total_w = right_x + right_w
    return base.crop((0,0,total_w,H))
