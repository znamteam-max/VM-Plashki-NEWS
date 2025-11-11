# api/graphics.py
from __future__ import annotations
import os, io, math, re
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

# =========================
# TUNABLE CONSTANTS
# =========================
# Базовый размер холста (если извне не передали w/h)
CANVAS_W, CANVAS_H = 1920, 1080

# Геометрия общей плашки
MARGIN_L = 24          # отступ слева
MARGIN_B = 24          # отступ снизу
CARD_H   = 220         # высота всех нижних плашек (делаем ниже по просьбе)
CARD_W   = 1180        # ширина основной плашки (card / cards left / cardbad)
GAP_2    = 10          # зазор между основной и доп.плашкой в cards

# Фото игрока (в круге)
HEAD_D            = 280   # диаметр круга с портретом
HEAD_X_SHIFT      = -36   # сдвиг влево (30–40px)
HEAD_BOTTOM_SHIFT = 8     # отступ от нижней кромки до круга (5–10px)

# Логотип команды
LOGO_D   = 96            # увеличено ~1.5x
LOGO_PAD = 24            # отступы логотипа от краёв плашки

# Типографика
NAME_SIZE        = 66     # имя игрока (чуть меньше, чем было)
ST_NUM_SIZE      = 44     # число статистики (явно меньше имени)
ST_LABEL_SIZE    = 22     # подпись статистики
LINE_SPACING     = 8

# card2 (двойная) — общая ширина 1080px, 2 половины без зазора
CARD2_W    = 1080
CARD2_H    = CARD_H
# выравнивания имени/статистики в обеих половинах
NAME_Y_UP  = 26
STATS_Y_UP = 88

# Цвета/градиенты
ORANGE_GRAD = ("#FF8A00", "#FFC532")    # основной фирменный
DARK_GRAD   = ("#1F1F1F", "#2B2B2B")    # тёмная доп.плашка для cards
BAD_GRAD    = ("#4B332E", "#3A2A27")    # коричневый для cardbad
PURPLE_GRAD = ("#4B2D7C", "#3A1E5F")    # если понадобится
BLUE_GRAD   = ("#194C86", "#163B69")    # если понадобится

# Пути к ассетам
HERE       = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR  = os.path.join(HERE, "fonts")
ICONS_DIR  = os.path.join(HERE, "assets", "icons")
STAR_PNG   = os.path.join(ICONS_DIR, "star.png")
POOP_PNG   = os.path.join(ICONS_DIR, "poop.png")

# Шрифты (используем ТОЛЬКО эти файлы)
FONT_EXO_BOLD   = os.path.join(FONTS_DIR, "Exo2-Bold.ttf")
FONT_MON_BOLD   = os.path.join(FONTS_DIR, "Montserrat-Bold.ttf")
FONT_MON_SEMIB  = os.path.join(FONTS_DIR, "Montserrat-SemiBold.ttf")

# =========================
# ВСПОМОГАТЕЛЬНЫЕ
# =========================
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)

def _load_headshot(img_or_bytes: Any) -> Optional[Image.Image]:
    if img_or_bytes is None:
        return None
    try:
        if isinstance(img_or_bytes, Image.Image):
            return img_or_bytes.convert("RGBA")
        if isinstance(img_or_bytes, (bytes, bytearray)):
            return Image.open(io.BytesIO(img_or_bytes)).convert("RGBA")
        # путь к файлу
        if isinstance(img_or_bytes, str) and os.path.exists(img_or_bytes):
            return Image.open(img_or_bytes).convert("RGBA")
    except Exception:
        return None
    return None

def _load_icon(path: str, size: int) -> Optional[Image.Image]:
    try:
        im = Image.open(path).convert("RGBA")
        return im.resize((size, size), Image.LANCZOS)
    except Exception:
        return None

def _circle(img: Image.Image, diameter: int) -> Image.Image:
    # центрируем по квадрату и маской вырезаем круг
    if img is None:  # пустая заглушка
        blank = Image.new("RGBA", (diameter, diameter), (0,0,0,0))
        return blank
    # зафитить изображение под диаметр
    w, h = img.size
    scale = diameter / max(w, h)
    new = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    # центрируем в квадрате
    sq = Image.new("RGBA", (diameter, diameter), (0,0,0,0))
    off = ((diameter - new.size[0])//2, (diameter - new.size[1])//2)
    sq.alpha_composite(new, dest=off)
    # круглая маска
    m = Image.new("L", (diameter, diameter), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([0,0,diameter,diameter], fill=255)
    sq.putalpha(m)
    return sq

def _draw_lr_gradient(draw: ImageDraw.ImageDraw, box: Tuple[int,int,int,int], c1: str, c2: str):
    x0,y0,x1,y1 = box
    w = max(1, x1-x0)
    r1,g1,b1 = ImageColor_getrgb(c1)
    r2,g2,b2 = ImageColor_getrgb(c2)
    for i in range(w):
        t = i/float(w-1)
        r = int(r1 + (r2-r1)*t)
        g = int(g1 + (g2-g1)*t)
        b = int(b1 + (b2-b1)*t)
        draw.line([(x0+i, y0), (x0+i, y1)], fill=(r,g,b))

def ImageColor_getrgb(hex_color: str) -> Tuple[int,int,int]:
    hex_color = hex_color.strip().lstrip("#")
    if len(hex_color)==3:
        hex_color = "".join(ch*2 for ch in hex_color)
    return tuple(int(hex_color[i:i+2],16) for i in (0,2,4))

def _parse_stats(stats: Any) -> List[Tuple[str,str]]:
    """
    Преобразует строку вида '30 очков, 11 подборов, 11-14 с игры'
    -> [('30','очки'), ('11','подборы'), ('11-14', 'с игры')]
    """
    if isinstance(stats, list):
        out = []
        for s in stats:
            if isinstance(s, (list, tuple)) and len(s)>=2:
                out.append((str(s[0]), str(s[1])))
            else:
                out.append((str(s), ""))  # как есть
        return out
    s = str(stats or "").strip()
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
    out: List[Tuple[str,str]] = []
    for p in parts:
        m = re.match(r"^(\S+)\s+(.*)$", p)
        if m:
            out.append((m.group(1), m.group(2)))
        else:
            out.append((p, ""))  # не удалось разделить — пишем целиком как число
    return out

def _take_args(args, kwargs, want: str) -> Any:
    """
    Гибкий парсер аргументов:
    want ∈ {'name','stats','head','logo','extra','size'}
    """
    # явные kwargs в приоритете
    if want == "name":
        v = kwargs.get("name_ru") or kwargs.get("name")
        if isinstance(v, str): return v
    if want == "stats":
        v = kwargs.get("stats")
        if v is not None: return v
    if want == "head":
        for k in ("head","headshot","player_img","photo","img"):
            if k in kwargs: return kwargs[k]
    if want == "logo":
        for k in ("logo","team_logo","logo_path"):
            if k in kwargs: return kwargs[k]
    if want == "extra":
        for k in ("extra","note","extra_text"):
            if k in kwargs: return kwargs[k]
    if want == "size":
        w = kwargs.get("w") or kwargs.get("width")
        h = kwargs.get("h") or kwargs.get("height")
        if isinstance(w,int) and isinstance(h,int): return (w,h)

    # из позиционных — пытаемся угадать по типу/содержимому
    for a in args:
        if want == "name" and isinstance(a, str) and not a.lower().endswith(".png"):
            # строка без расширения — вероятно имя
            return a
        if want == "stats":
            # список, кортеж, либо строка с запятыми/цифрами — вероятность stats
            if isinstance(a,(list,tuple)) or (isinstance(a,str) and re.search(r"\d", a)):
                return a
        if want == "head":
            if isinstance(a,(bytes,bytearray,Image.Image)) or (isinstance(a,str) and os.path.exists(a)):
                # но не логотип: если PNG путь и называется как 3-4 цифры — вероятно head
                return a
        if want == "logo":
            if isinstance(a,str) and a.lower().endswith(".png"):
                return a
        if want == "extra" and isinstance(a,str) and not re.search(r"\d",a):
            return a
        if want == "size" and isinstance(a, (tuple,list)) and len(a)>=2 and all(isinstance(x,int) for x in a[:2]):
            return (a[0],a[1])
    return None

def _box_bottom_left(w: int, h: int, card_w: int, card_h: int) -> Tuple[int,int,int,int]:
    x0 = MARGIN_L
    y1 = h - MARGIN_B
    x1 = x0 + card_w
    y0 = y1 - card_h
    return x0,y0,x1,y1

def _draw_panel(img: Image.Image, box: Tuple[int,int,int,int], grad: Tuple[str,str]):
    d = ImageDraw.Draw(img)
    _draw_lr_gradient(d, box, grad[0], grad[1])

def _draw_text_centered(draw: ImageDraw.ImageDraw, center_x: int, top_y: int, text: str, font: ImageFont.FreeTypeFont, fill=(255,255,255)):
    w,h = draw.textbbox((0,0), text, font=font)[2:]
    draw.text((center_x - w//2, top_y), text, font=font, fill=fill)

# =========================
# РЕНДЕРЫ
# =========================
def render_card(*args, **kwargs) -> Image.Image:
    """
    Базовая плашка: [круглая голова] [имя] [3 статы], всё привязано к левому нижнему углу.
    Аргументы можно передавать как раньше — функция сама разберётся.
    """
    # размеры
    size = _take_args(args, kwargs, "size")
    W,H = size if size else (CANVAS_W, CANVAS_H)

    name  = _take_args(args, kwargs, "name")  or ""
    stats = _take_args(args, kwargs, "stats") or ""
    head  = _load_headshot(_take_args(args, kwargs, "head"))
    logoP = _take_args(args, kwargs, "logo")

    # холст
    img = Image.new("RGBA", (W,H), (0,0,0,0))
    # основная плашка
    box = _box_bottom_left(W,H, CARD_W, CARD_H)
    _draw_panel(img, box, ORANGE_GRAD)

    # фото (в круг)
    head_c = _circle(head, HEAD_D)
    hx = box[0] + HEAD_X_SHIFT
    hy = box[3] - HEAD_D - HEAD_BOTTOM_SHIFT
    img.alpha_composite(head_c, dest=(hx, hy))

    # логотип команды
    if isinstance(logoP, str) and os.path.exists(logoP):
        logo = Image.open(logoP).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
        lx = box[0] + LOGO_PAD
        ly = box[3] - LOGO_D - LOGO_PAD
        # белый кружок под лого (небольшой)
        circle = Image.new("RGBA", (LOGO_D,LOGO_D), (255,255,255,255))
        m = Image.new("L",(LOGO_D,LOGO_D),0)
        ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
        circle.putalpha(m)
        img.alpha_composite(circle, dest=(lx,ly))
        img.alpha_composite(logo, dest=(lx,ly))

    draw = ImageDraw.Draw(img)
    # текстовые шрифты
    f_name = _font(FONT_EXO_BOLD, NAME_SIZE)
    f_num  = _font(FONT_MON_BOLD, ST_NUM_SIZE)
    f_lab  = _font(FONT_MON_SEMIB, ST_LABEL_SIZE)

    # имя
    name_txt = str(name or "").upper()
    # оставляем место слева под голову и лого
    text_left = box[0] + LOGO_PAD + LOGO_D + 36
    text_right= box[2] - 24
    name_y = box[1] + 26
    draw.text((text_left, name_y), name_txt, font=f_name, fill=(255,255,255))

    # статистика (центрируется по колонкам под именем)
    stats_pairs = _parse_stats(stats)
    cols = max(1, len(stats_pairs))
    avail_w = text_right - text_left
    col_w = avail_w // cols
    top_y_numbers = box[1] + 26 + f_name.size + 18
    top_y_labels  = top_y_numbers + f_num.size + 2

    for i, (num, lab) in enumerate(stats_pairs):
        cx = text_left + i*col_w + col_w//2
        _draw_text_centered(draw, cx, top_y_numbers, str(num), f_num)
        _draw_text_centered(draw, cx, top_y_labels,  str(lab).upper(), f_lab)

    return img

def render_cardbad(*args, **kwargs) -> Image.Image:
    """
    Плашка 'плохая': коричневый градиент, какашка ПОСЛЕ имени, больше в 2 раза.
    """
    size = _take_args(args, kwargs, "size")
    W,H = size if size else (CANVAS_W, CANVAS_H)

    name  = _take_args(args, kwargs, "name")  or ""
    stats = _take_args(args, kwargs, "stats") or ""
    head  = _load_headshot(_take_args(args, kwargs, "head"))
    logoP = _take_args(args, kwargs, "logo")

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    box = _box_bottom_left(W,H, CARD_W, CARD_H)
    _draw_panel(img, box, BAD_GRAD)

    # фото
    head_c = _circle(head, HEAD_D)
    hx = box[0] + HEAD_X_SHIFT
    hy = box[3] - HEAD_D - HEAD_BOTTOM_SHIFT
    img.alpha_composite(head_c, dest=(hx, hy))

    # логотип
    if isinstance(logoP, str) and os.path.exists(logoP):
        logo = Image.open(logoP).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
        lx = box[0] + LOGO_PAD
        ly = box[3] - LOGO_D - LOGO_PAD
        circle = Image.new("RGBA", (LOGO_D,LOGO_D), (255,255,255,255))
        m = Image.new("L",(LOGO_D,LOGO_D),0)
        ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
        circle.putalpha(m)
        img.alpha_composite(circle, dest=(lx,ly))
        img.alpha_composite(logo, dest=(lx,ly))

    draw = ImageDraw.Draw(img)
    f_name = _font(FONT_EXO_BOLD, NAME_SIZE)
    f_num  = _font(FONT_MON_BOLD, ST_NUM_SIZE)
    f_lab  = _font(FONT_MON_SEMIB, ST_LABEL_SIZE)

    # имя + какашка
    name_txt = str(name or "").upper()
    text_left = box[0] + LOGO_PAD + LOGO_D + 36
    name_y = box[1] + 26
    draw.text((text_left, name_y), name_txt, font=f_name, fill=(255,255,255))
    # иконка ПОСЛЕ имени
    poop = _load_icon(POOP_PNG, int(NAME_SIZE*0.9)*2)  # в 2 раза крупнее обычного
    if poop:
        w_name = draw.textbbox((0,0), name_txt, font=f_name)[2]
        img.alpha_composite(poop, dest=(text_left + w_name + 18, name_y - 6))

    # статистика
    stats_pairs = _parse_stats(stats)
    cols = max(1, len(stats_pairs))
    text_right= box[2] - 24
    avail_w = (text_right - text_left)
    col_w   = avail_w // cols
    top_y_numbers = box[1] + 26 + f_name.size + 18
    top_y_labels  = top_y_numbers + f_num.size + 2

    for i, (num, lab) in enumerate(stats_pairs):
        cx = text_left + i*col_w + col_w//2
        _draw_text_centered(draw, cx, top_y_numbers, str(num), f_num)
        _draw_text_centered(draw, cx, top_y_labels,  str(lab).upper(), f_lab)

    return img

# совместимость со старым именем
render_cardbad_alias = render_cardbad

def render_cards(*args, **kwargs) -> Image.Image:
    """
    Две плашки: слева основная (оранжевая), справа маленькая тёмная с отступом 10px.
    Правая часть: иконка 'звезда' без белого фона + короткий текст.
    """
    size = _take_args(args, kwargs, "size")
    W,H = size if size else (CANVAS_W, CANVAS_H)

    name   = _take_args(args, kwargs, "name")  or ""
    stats  = _take_args(args, kwargs, "stats") or ""
    head   = _load_headshot(_take_args(args, kwargs, "head"))
    logoP  = _take_args(args, kwargs, "logo")
    extra  = _take_args(args, kwargs, "extra") or ""

    img = Image.new("RGBA", (W,H), (0,0,0,0))

    # левая плашка
    left = _box_bottom_left(W,H, CARD_W, CARD_H)
    _draw_panel(img, left, ORANGE_GRAD)

    # правая доп.плашка
    small_w = 420
    right = (left[2] + GAP_2, left[1], left[2] + GAP_2 + small_w, left[3])
    _draw_panel(img, right, DARK_GRAD)

    # фото в левой
    head_c = _circle(head, HEAD_D)
    hx = left[0] + HEAD_X_SHIFT
    hy = left[3] - HEAD_D - HEAD_BOTTOM_SHIFT
    img.alpha_composite(head_c, dest=(hx, hy))

    # логотип в левой
    if isinstance(logoP, str) and os.path.exists(logoP):
        logo = Image.open(logoP).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
        lx = left[0] + LOGO_PAD
        ly = left[3] - LOGO_D - LOGO_PAD
        circle = Image.new("RGBA", (LOGO_D,LOGO_D), (255,255,255,255))
        m = Image.new("L",(LOGO_D,LOGO_D),0)
        ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
        circle.putalpha(m)
        img.alpha_composite(circle, dest=(lx,ly))
        img.alpha_composite(logo, dest=(lx,ly))

    draw = ImageDraw.Draw(img)
    f_name = _font(FONT_EXO_BOLD, NAME_SIZE)
    f_num  = _font(FONT_MON_BOLD, ST_NUM_SIZE)
    f_lab  = _font(FONT_MON_SEMIB, ST_LABEL_SIZE)

    # имя (слева)
    name_txt = str(name or "").upper()
    text_left = left[0] + LOGO_PAD + LOGO_D + 36
    text_right= left[2] - 24
    name_y = left[1] + 26
    draw.text((text_left, name_y), name_txt, font=f_name, fill=(255,255,255))

    # статистика (слева)
    stats_pairs = _parse_stats(stats)
    cols = max(1, len(stats_pairs))
    avail_w = text_right - text_left
    col_w   = avail_w // cols
    top_y_numbers = left[1] + 26 + f_name.size + 18
    top_y_labels  = top_y_numbers + f_num.size + 2
    for i, (num, lab) in enumerate(stats_pairs):
        cx = text_left + i*col_w + col_w//2
        _draw_text_centered(draw, cx, top_y_numbers, str(num), f_num)
        _draw_text_centered(draw, cx, top_y_labels,  str(lab).upper(), f_lab)

    # правая — звезда и текст
    star = _load_icon(STAR_PNG, 28)
    rx = right[0] + 24
    ry = right[1] + 24
    if star:
        img.alpha_composite(star, dest=(rx, ry))
        rx += star.size[0] + 12
    f_extra = _font(FONT_MON_SEMIB, 28)
    draw.text((rx, ry-2), str(extra or "").strip(), font=f_extra, fill=(255,255,255))

    return img

def render_card2(*args, **kwargs) -> Image.Image:
    """
    Сдвоенная плашка 1080px шириной, без зазора между половинами.
    Оба игрока: общая высота CARD2_H, имена и статистика на одной высоте.
    """
    size = _take_args(args, kwargs, "size")
    W,H = size if size else (CANVAS_W, CANVAS_H)

    # ожидаем два комплекта аргументов — но функция гибкая:
    # name/ stats/ head/ logo — относятся к левому игроку;
    # для правого можно передать через kwargs: name2, stats2, head2, logo2
    name1  = _take_args(args, kwargs, "name")  or kwargs.get("name1") or ""
    stats1 = _take_args(args, kwargs, "stats") or kwargs.get("stats1") or ""
    head1  = _load_headshot(_take_args(args, kwargs, "head") or kwargs.get("head1"))
    logo1  = _take_args(args, kwargs, "logo")  or kwargs.get("logo1")

    name2  = kwargs.get("name2")  or ""
    stats2 = kwargs.get("stats2") or ""
    head2  = _load_headshot(kwargs.get("head2"))
    logo2  = kwargs.get("logo2")

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    # общий прямоугольник снизу, ширина 1080
    card = _box_bottom_left(W,H, CARD2_W, CARD2_H)
    # левая/правая половины без зазора
    mid = (card[0] + card[2])//2
    left  = (card[0], card[1], mid, card[3])
    right = (mid, card[1], card[2], card[3])
    _draw_panel(img, left, ORANGE_GRAD)
    _draw_panel(img, right, ORANGE_GRAD)  # одинаковый градиент по ТЗ

    # левые/правые головы
    head_c1 = _circle(head1, HEAD_D)
    img.alpha_composite(head_c1, dest=(left[0] + HEAD_X_SHIFT, left[3] - HEAD_D - HEAD_BOTTOM_SHIFT))
    head_c2 = _circle(head2, HEAD_D)
    # у правого — тоже смещаем картинку влево внутри своей половины
    img.alpha_composite(head_c2, dest=(right[0] + HEAD_X_SHIFT, right[3] - HEAD_D - HEAD_BOTTOM_SHIFT))

    # логотипы внизу
    def _put_logo(panel, logo_path):
        if isinstance(logo_path, str) and os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
            lx = panel[0] + LOGO_PAD
            ly = panel[3] - LOGO_D - LOGO_PAD
            circle = Image.new("RGBA", (LOGO_D,LOGO_D), (255,255,255,255))
            m = Image.new("L",(LOGO_D,LOGO_D),0)
            ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
            circle.putalpha(m)
            img.alpha_composite(circle, dest=(lx,ly))
            img.alpha_composite(logo, dest=(lx,ly))
    _put_logo(left, logo1)
    _put_logo(right, logo2)

    draw = ImageDraw.Draw(img)
    f_name = _font(FONT_EXO_BOLD, NAME_SIZE)
    f_num  = _font(FONT_MON_BOLD, ST_NUM_SIZE)
    f_lab  = _font(FONT_MON_SEMIB, ST_LABEL_SIZE)

    def _block(panel, name, stats):
        name_txt = str(name or "").upper()
        x_text = panel[0] + LOGO_PAD + LOGO_D + 36
        draw.text((x_text, panel[1] + NAME_Y_UP), name_txt, font=f_name, fill=(255,255,255))
        pairs = _parse_stats(stats)
        cols  = max(1, len(pairs))
        x_right = panel[2] - 24
        avail = x_right - x_text
        col_w = avail // cols
        ty_num = panel[1] + STATS_Y_UP
        ty_lab = ty_num + f_num.size + 2
        for i,(num,lab) in enumerate(pairs):
            cx = x_text + i*col_w + col_w//2
            _draw_text_centered(draw, cx, ty_num, str(num), f_num)
            _draw_text_centered(draw, cx, ty_lab, str(lab).upper(), f_lab)

    _block(left,  name1, stats1)
    _block(right, name2, stats2)

    return img

# Совместимость с возможными старым именем
def render_cardbad_alias2(*a, **k): return render_cardbad(*a, **k)
def render_card_special(*a, **k):   return render_cards(*a, **k)
