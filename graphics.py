# api/graphics.py
from __future__ import annotations
import os, io, re
from typing import Any, Tuple, List, Optional
from PIL import Image, ImageDraw, ImageFont

# =========================
# TUNABLE CONSTANTS
# =========================
CANVAS_W, CANVAS_H = 1920, 1080

# Геометрия плашек
MARGIN_L = 24
MARGIN_B = 24
CARD_H   = 200          # пониже, чтобы «не нависали»
CARD_W   = 1180
GAP_2    = 10

# Фото игрока
HEAD_D            = 260   # диаметр круга
HEAD_X_SHIFT      = -36   # сдвиг левее на 30–40px
HEAD_BOTTOM_SHIFT = 8     # 5–10px выше низа

# Лого команды
LOGO_D   = 96
LOGO_PAD = 24

# Типографика
NAME_SIZE     = 60   # имя меньше
ST_NUM_SIZE   = 40   # цифры ещё меньше имени
ST_LABEL_SIZE = 20

# card2
CARD2_W    = 1080
CARD2_H    = CARD_H
NAME_Y_UP  = 24
STATS_Y_UP = 84

# Градиенты
ORANGE_GRAD = ("#FF8A00", "#FFC532")
DARK_GRAD   = ("#1F1F1F", "#2B2B2B")
BAD_GRAD    = ("#4B332E", "#3A2A27")

# Пути
HERE      = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(HERE, "fonts")
ICONS_DIR = os.path.join(HERE, "assets", "icons")
STAR_PNG  = os.path.join(ICONS_DIR, "star.png")
POOP_PNG  = os.path.join(ICONS_DIR, "poop.png")

# Имёна файлов шрифтов (только эти)
EXO_BOLD_FILE  = "Exo2-Bold.ttf"
MON_BOLD_FILE  = "Montserrat-Bold.ttf"
MON_SEMI_FILE  = "Montserrat-SemiBold.ttf"

# ---------- robust font loader ----------
def _font_any(filename: str, size: int) -> ImageFont.FreeTypeFont:
    """
    Ищет шрифт в нескольких местах:
    - api/fonts/...
    - /var/task/api/fonts/...
    - /var/task/fonts/... (если кто-то положил не туда)
    Можно переопределить путём через ENV: FONT_EXO_PATH, FONT_MON_BOLD_PATH, FONT_MON_SEMI_PATH
    """
    env_key = None
    if filename == EXO_BOLD_FILE: env_key = "FONT_EXO_PATH"
    if filename == MON_BOLD_FILE: env_key = "FONT_MON_BOLD_PATH"
    if filename == MON_SEMI_FILE: env_key = "FONT_MON_SEMI_PATH"

    candidates: List[str] = []
    if env_key and os.getenv(env_key):
        candidates.append(os.getenv(env_key))  # абсолютный путь

    # стандартные места
    candidates += [
        os.path.join(FONTS_DIR, filename),
        os.path.join("/var/task/api/fonts", filename),
        os.path.join("/var/task/fonts", filename),
        os.path.join(HERE, filename),  # вдруг лежит рядом
    ]
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    raise OSError(f"Font not found. Tried: {', '.join([p for p in candidates])}")

def _font_exo(size: int) -> ImageFont.FreeTypeFont:
    return _font_any(EXO_BOLD_FILE, size)

def _font_mon_b(size: int) -> ImageFont.FreeTypeFont:
    return _font_any(MON_BOLD_FILE, size)

def _font_mon_semi(size: int) -> ImageFont.FreeTypeFont:
    return _font_any(MON_SEMI_FILE, size)

# ---------- misc ----------
def _imgcolor(rgb_hex: str) -> Tuple[int,int,int]:
    s = rgb_hex.strip().lstrip("#")
    if len(s)==3: s = "".join(ch*2 for ch in s)
    return tuple(int(s[i:i+2],16) for i in (0,2,4))

def _grad_lr(draw: ImageDraw.ImageDraw, box: Tuple[int,int,int,int], c1: str, c2: str):
    x0,y0,x1,y1 = box
    w = max(1, x1-x0)
    r1,g1,b1 = _imgcolor(c1)
    r2,g2,b2 = _imgcolor(c2)
    for i in range(w):
        t = i/float(w-1)
        r = int(r1 + (r2-r1)*t)
        g = int(g1 + (g2-g1)*t)
        b = int(b1 + (b2-b1)*t)
        draw.line([(x0+i, y0), (x0+i, y1)], fill=(r,g,b))

def _box_bottom_left(W:int,H:int,w:int,h:int)->Tuple[int,int,int,int]:
    x0 = MARGIN_L
    y1 = H - MARGIN_B
    return (x0, y1-h, x0+w, y1)

def _circle_fit(img: Optional[Image.Image], diameter: int) -> Image.Image:
    if img is None:
        return Image.new("RGBA", (diameter,diameter), (0,0,0,0))
    w,h = img.size
    k = diameter / max(w,h)
    im = img.resize((int(w*k), int(h*k)), Image.LANCZOS)
    sq = Image.new("RGBA", (diameter,diameter), (0,0,0,0))
    off = ((diameter-im.size[0])//2, (diameter-im.size[1])//2)
    sq.alpha_composite(im, dest=off)
    m = Image.new("L",(diameter,diameter),0)
    ImageDraw.Draw(m).ellipse([0,0,diameter,diameter], fill=255)
    sq.putalpha(m)
    return sq

def _load_img(x: Any) -> Optional[Image.Image]:
    try:
        if isinstance(x, Image.Image): return x.convert("RGBA")
        if isinstance(x, (bytes, bytearray)): return Image.open(io.BytesIO(x)).convert("RGBA")
        if isinstance(x, str) and os.path.exists(x): return Image.open(x).convert("RGBA")
    except Exception:
        return None
    return None

def _parse_stats(s: Any) -> List[Tuple[str,str]]:
    if isinstance(s, list):
        out=[]
        for v in s:
            if isinstance(v,(list,tuple)) and len(v)>=2: out.append((str(v[0]),str(v[1])))
            else: out.append((str(v), ""))
        return out
    s = str(s or "").strip()
    if not s: return []
    parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
    out=[]
    for p in parts:
        m = re.match(r"^(\S+)\s+(.*)$", p)
        out.append((m.group(1), m.group(2)) if m else (p,""))
    return out

def _draw_panel(img: Image.Image, box: Tuple[int,int,int,int], grad: Tuple[str,str]):
    _grad_lr(ImageDraw.Draw(img), box, grad[0], grad[1])

def _draw_center(d: ImageDraw.ImageDraw, cx: int, y: int, text: str, font: ImageFont.FreeTypeFont):
    w,h = d.textbbox((0,0), text, font=font)[2:]
    d.text((cx-w//2, y), text, font=font, fill=(255,255,255))

# =========================
# RENDERERS
# =========================
def render_card(*args, **kwargs) -> Image.Image:
    W,H = kwargs.get("w") or kwargs.get("width") or CANVAS_W, kwargs.get("h") or kwargs.get("height") or CANVAS_H
    name  = kwargs.get("name_ru") or kwargs.get("name") or (args[0] if args else "")
    stats = kwargs.get("stats") or (args[1] if len(args)>1 else "")
    head  = _load_img(kwargs.get("head") or kwargs.get("headshot") or (args[2] if len(args)>2 else None))
    logoP = kwargs.get("logo") or kwargs.get("team_logo") or (args[3] if len(args)>3 else None)

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    box = _box_bottom_left(W,H,CARD_W,CARD_H)
    _draw_panel(img, box, ORANGE_GRAD)

    head_c = _circle_fit(head, HEAD_D)
    img.alpha_composite(head_c, dest=(box[0]+HEAD_X_SHIFT, box[3]-HEAD_D-HEAD_BOTTOM_SHIFT))

    if isinstance(logoP,str) and os.path.exists(logoP):
        logo = Image.open(logoP).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
        lx = box[0]+LOGO_PAD; ly = box[3]-LOGO_D-LOGO_PAD
        white = Image.new("RGBA",(LOGO_D,LOGO_D),(255,255,255,255))
        m = Image.new("L",(LOGO_D,LOGO_D),0); ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
        white.putalpha(m); img.alpha_composite(white, dest=(lx,ly)); img.alpha_composite(logo, dest=(lx,ly))

    d = ImageDraw.Draw(img)
    f_name = _font_exo(NAME_SIZE)
    f_num  = _font_mon_b(ST_NUM_SIZE)
    f_lab  = _font_mon_semi(ST_LABEL_SIZE)

    name_txt = str(name or "").upper()
    x_text_l = box[0] + LOGO_PAD + LOGO_D + 36
    x_text_r = box[2] - 24
    d.text((x_text_l, box[1]+24), name_txt, font=f_name, fill=(255,255,255))

    pairs = _parse_stats(stats)
    cols  = max(1,len(pairs))
    col_w = max(1,(x_text_r-x_text_l)//cols)
    y_num = box[1] + 24 + f_name.size + 16
    y_lab = y_num + f_num.size + 2
    for i,(num,lab) in enumerate(pairs):
        cx = x_text_l + i*col_w + col_w//2
        _draw_center(d, cx, y_num, str(num), f_num)
        _draw_center(d, cx, y_lab,  str(lab).upper(), f_lab)
    return img

def render_cardbad(*args, **kwargs) -> Image.Image:
    W,H = kwargs.get("w") or kwargs.get("width") or CANVAS_W, kwargs.get("h") or kwargs.get("height") or CANVAS_H
    name  = kwargs.get("name_ru") or kwargs.get("name") or (args[0] if args else "")
    stats = kwargs.get("stats") or (args[1] if len(args)>1 else "")
    head  = _load_img(kwargs.get("head") or kwargs.get("headshot") or (args[2] if len(args)>2 else None))
    logoP = kwargs.get("logo") or kwargs.get("team_logo") or (args[3] if len(args)>3 else None)

    img = Image.new("RGBA",(W,H),(0,0,0,0))
    box = _box_bottom_left(W,H,CARD_W,CARD_H)
    _draw_panel(img, box, BAD_GRAD)

    head_c = _circle_fit(head, HEAD_D)
    img.alpha_composite(head_c, dest=(box[0]+HEAD_X_SHIFT, box[3]-HEAD_D-HEAD_BOTTOM_SHIFT))

    if isinstance(logoP,str) and os.path.exists(logoP):
        logo = Image.open(logoP).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
        lx = box[0]+LOGO_PAD; ly = box[3]-LOGO_D-LOGO_PAD
        white = Image.new("RGBA",(LOGO_D,LOGO_D),(255,255,255,255))
        m = Image.new("L",(LOGO_D,LOGO_D),0); ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
        white.putalpha(m); img.alpha_composite(white, dest=(lx,ly)); img.alpha_composite(logo, dest=(lx,ly))

    d = ImageDraw.Draw(img)
    f_name = _font_exo(NAME_SIZE)
    f_num  = _font_mon_b(ST_NUM_SIZE)
    f_lab  = _font_mon_semi(ST_LABEL_SIZE)

    name_txt = str(name or "").upper()
    x_text_l = box[0] + LOGO_PAD + LOGO_D + 36
    name_y   = box[1] + 24
    d.text((x_text_l, name_y), name_txt, font=f_name, fill=(255,255,255))

    poop = None
    try:
        if os.path.exists(POOP_PNG):
            poop = Image.open(POOP_PNG).convert("RGBA")
            scale = int(NAME_SIZE*0.9)*2
            poop = poop.resize((scale,scale), Image.LANCZOS)
    except Exception:
        poop = None
    if poop:
        w_name = d.textbbox((0,0), name_txt, font=f_name)[2]
        img.alpha_composite(poop, dest=(x_text_l + w_name + 18, name_y - 6))

    pairs = _parse_stats(stats)
    cols  = max(1,len(pairs))
    x_text_r = box[2] - 24
    col_w = max(1,(x_text_r-x_text_l)//cols)
    y_num = box[1] + 24 + f_name.size + 16
    y_lab = y_num + f_num.size + 2
    for i,(num,lab) in enumerate(pairs):
        cx = x_text_l + i*col_w + col_w//2
        _draw_center(d, cx, y_num, str(num), f_num)
        _draw_center(d, cx, y_lab,  str(lab).upper(), f_lab)
    return img

def render_cards(*args, **kwargs) -> Image.Image:
    W,H = kwargs.get("w") or kwargs.get("width") or CANVAS_W, kwargs.get("h") or kwargs.get("height") or CANVAS_H
    name  = kwargs.get("name_ru") or kwargs.get("name") or (args[0] if args else "")
    stats = kwargs.get("stats") or (args[1] if len(args)>1 else "")
    head  = _load_img(kwargs.get("head") or kwargs.get("headshot") or (args[2] if len(args)>2 else None))
    logoP = kwargs.get("logo") or kwargs.get("team_logo") or (args[3] if len(args)>3 else None)
    extra = kwargs.get("extra") or kwargs.get("note") or (args[4] if len(args)>4 else "")

    img = Image.new("RGBA",(W,H),(0,0,0,0))
    left = _box_bottom_left(W,H,CARD_W,CARD_H)
    small_w = 420
    right = (left[2] + GAP_2, left[1], left[2] + GAP_2 + small_w, left[3])

    _draw_panel(img, left, ORANGE_GRAD)
    _draw_panel(img, right, DARK_GRAD)

    head_c = _circle_fit(head, HEAD_D)
    img.alpha_composite(head_c, dest=(left[0]+HEAD_X_SHIFT, left[3]-HEAD_D-HEAD_BOTTOM_SHIFT))

    if isinstance(logoP,str) and os.path.exists(logoP):
        logo = Image.open(logoP).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
        lx = left[0]+LOGO_PAD; ly = left[3]-LOGO_D-LOGO_PAD
        white = Image.new("RGBA",(LOGO_D,LOGO_D),(255,255,255,255))
        m = Image.new("L",(LOGO_D,LOGO_D),0); ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
        white.putalpha(m); img.alpha_composite(white, dest=(lx,ly)); img.alpha_composite(logo, dest=(lx,ly))

    d = ImageDraw.Draw(img)
    f_name = _font_exo(NAME_SIZE)
    f_num  = _font_mon_b(ST_NUM_SIZE)
    f_lab  = _font_mon_semi(ST_LABEL_SIZE)

    name_txt = str(name or "").upper()
    x_text_l = left[0] + LOGO_PAD + LOGO_D + 36
    x_text_r = left[2] - 24
    d.text((x_text_l, left[1]+24), name_txt, font=f_name, fill=(255,255,255))

    pairs = _parse_stats(stats)
    cols  = max(1,len(pairs))
    col_w = max(1,(x_text_r-x_text_l)//cols)
    y_num = left[1] + 24 + f_name.size + 16
    y_lab = y_num + f_num.size + 2
    for i,(num,lab) in enumerate(pairs):
        cx = x_text_l + i*col_w + col_w//2
        _draw_center(d, cx, y_num, str(num), f_num)
        _draw_center(d, cx, y_lab,  str(lab).upper(), f_lab)

    # правая карточка: звезда без белого круга + текст
    rx = right[0] + 24
    ry = right[1] + 24
    try:
        if os.path.exists(STAR_PNG):
            star = Image.open(STAR_PNG).convert("RGBA").resize((28,28), Image.LANCZOS)
            img.alpha_composite(star, dest=(rx, ry))
            rx += 28 + 12
    except Exception:
        pass
    f_extra = _font_mon_semi(28)
    d.text((rx, ry-2), str(extra or "").strip(), font=f_extra, fill=(255,255,255))
    return img

def render_card2(*args, **kwargs) -> Image.Image:
    W,H = kwargs.get("w") or kwargs.get("width") or CANVAS_W, kwargs.get("h") or kwargs.get("height") or CANVAS_H
    name1  = kwargs.get("name1")  or kwargs.get("name_ru") or kwargs.get("name") or (args[0] if args else "")
    stats1 = kwargs.get("stats1") or kwargs.get("stats") or (args[1] if len(args)>1 else "")
    head1  = _load_img(kwargs.get("head1") or kwargs.get("head") or (args[2] if len(args)>2 else None))
    logo1  = kwargs.get("logo1") or kwargs.get("logo") or (args[3] if len(args)>3 else None)

    name2  = kwargs.get("name2")  or ""
    stats2 = kwargs.get("stats2") or ""
    head2  = _load_img(kwargs.get("head2"))
    logo2  = kwargs.get("logo2")

    img  = Image.new("RGBA",(W,H),(0,0,0,0))
    card = _box_bottom_left(W,H,CARD2_W,CARD2_H)
    mid  = (card[0]+card[2])//2
    left, right = (card[0],card[1],mid,card[3]), (mid,card[1],card[2],card[3])
    _draw_panel(img, left, ORANGE_GRAD)
    _draw_panel(img, right, ORANGE_GRAD)

    img.alpha_composite(_circle_fit(head1, HEAD_D), dest=(left[0]+HEAD_X_SHIFT,  left[3]-HEAD_D-HEAD_BOTTOM_SHIFT))
    img.alpha_composite(_circle_fit(head2, HEAD_D), dest=(right[0]+HEAD_X_SHIFT, right[3]-HEAD_D-HEAD_BOTTOM_SHIFT))

    def _logo(panel, lp):
        if isinstance(lp,str) and os.path.exists(lp):
            logo = Image.open(lp).convert("RGBA").resize((LOGO_D,LOGO_D), Image.LANCZOS)
            lx = panel[0]+LOGO_PAD; ly = panel[3]-LOGO_D-LOGO_PAD
            white = Image.new("RGBA",(LOGO_D,LOGO_D),(255,255,255,255))
            m = Image.new("L",(LOGO_D,LOGO_D),0); ImageDraw.Draw(m).ellipse([0,0,LOGO_D,LOGO_D], fill=255)
            white.putalpha(m); img.alpha_composite(white, dest=(lx,ly)); img.alpha_composite(logo, dest=(lx,ly))
    _logo(left, logo1); _logo(right, logo2)

    d = ImageDraw.Draw(img)
    f_name = _font_exo(NAME_SIZE)
    f_num  = _font_mon_b(ST_NUM_SIZE)
    f_lab  = _font_mon_semi(ST_LABEL_SIZE)

    def _block(panel, name, stats):
        name_txt = str(name or "").upper()
        x_text = panel[0] + LOGO_PAD + LOGO_D + 36
        d.text((x_text, panel[1]+NAME_Y_UP), name_txt, font=f_name, fill=(255,255,255))
        pairs = _parse_stats(stats)
        cols  = max(1,len(pairs))
        x_right = panel[2]-24
        col_w = max(1,(x_right-x_text)//cols)
        y_num = panel[1]+STATS_Y_UP
        y_lab = y_num + f_num.size + 2
        for i,(num,lab) in enumerate(pairs):
            cx = x_text + i*col_w + col_w//2
            _draw_center(d, cx, y_num, str(num), f_num)
            _draw_center(d, cx, y_lab,  str(lab).upper(), f_lab)

    _block(left, name1, stats1)
    _block(right, name2, stats2)
    return img

# ---- алиасы для обратной совместимости ----
def render_card_bad(*a, **k):   return render_cardbad(*a, **k)
def render_card_special(*a, **k): return render_cards(*a, **k)
