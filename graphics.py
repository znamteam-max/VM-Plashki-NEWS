# graphics.py
# Рендер плашек NEWS: render_card / render_card2 / render_card_bad / render_card_special
# Канвас 1920x1080 RGBA (прозрачный). Градиенты из team colors. Фото/лого в белых кружках.

from __future__ import annotations
import os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------- Канвас и базовые константы ----------
CANVAS_W, CANVAS_H = 1920, 1080
MARGIN = 40

# Геометрия панелей (умеренные, «в 1.5–2 раза меньше» прежних)
CARD_H    = 200   # card / cardbad / левая часть cards
CARDS_RH  = 180   # правая доп-плашка в cards
CARD2_H   = 220   # card2 (во всю ширину снизу)

# Скругления
RADIUS_RIGHT = 28
RADIUS_BOTH  = 28

# Цвета
WHITE = (255,255,255,255)
BLACK = (0,0,0,255)
SEMI_BLACK = (0,0,0,180)
BROWN_BAD = (90,58,44,255)

# Фото/лого в кружках
LOGO_CIRCLE_DIAM = 120
LOGO_OFFSET = (-30, -30)

HEAD_DIAM_SMALL = 156   # на card/cardbad/cards
HEAD_DIAM_CARD2 = 168   # на card2

# Иконка 💩 (для cardbad). Если нет PNG — используем emoji
POOP_ICON_PATH = os.getenv("POOP_ICON_PATH", "").strip()

# ---------- Шрифты ----------
_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}

# можно прокинуть твой «наш шрифт» через переменные окружения
FONT_REGULAR_PATH = os.getenv("FONT_REGULAR_PATH", "").strip() or None
FONT_BOLD_PATH    = os.getenv("FONT_BOLD_PATH", "").strip() or None

TRY_BOLD = [
    FONT_BOLD_PATH,
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/exo2/Exo2-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "Montserrat-Bold.ttf", "Exo2-Bold.ttf", "DejaVuSans-Bold.ttf",
    "Arial-Bold.ttf", "Arialbd.ttf"
]
TRY_REG = [
    FONT_REGULAR_PATH,
    "/usr/share/fonts/truetype/montserrat/Montserrat-Regular.ttf",
    "/usr/share/fonts/truetype/exo2/Exo2-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "Montserrat-Regular.ttf", "Exo2-Regular.ttf", "DejaVuSans.ttf",
    "Arial.ttf"
]

def _load_font(paths: List[Optional[str]], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if not p: continue
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()

def _font(size: int, bold: bool=False) -> ImageFont.FreeTypeFont:
    key = (int(size), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    f = _load_font(TRY_BOLD if bold else TRY_REG, int(size))
    _FONT_CACHE[key] = f
    return f

def _text_size(text: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
    if not text: return (0,0)
    try:
        bbox = font.getbbox(text)
        return (int(bbox[2]-bbox[0]), int(bbox[3]-bbox[1]))
    except Exception:
        return font.getsize(text)

# ---------- Цвета / градиенты ----------
def _to_rgba(c) -> Tuple[int,int,int,255]:
    # вход может быть '#112233' или ('#112233', '#0a0a0a', ...)
    if isinstance(c, (list, tuple)):
        c = c[0]
    if isinstance(c, str):
        s = c.strip()
        if s.startswith("#"): s = s[1:]
        if len(s)==3:
            r = int(s[0]*2,16); g=int(s[1]*2,16); b=int(s[2]*2,16)
        else:
            r = int(s[0:2],16); g=int(s[2:4],16); b=int(s[4:6],16)
        return (r,g,b,255)
    if isinstance(c, tuple):
        if len(c)==3: return (int(c[0]),int(c[1]),int(c[2]),255)
        if len(c)==4: return (int(c[0]),int(c[1]),int(c[2]),int(c[3]))
    return (20,28,36,255)

def _make_hgrad(w: int, h: int, left, right) -> Image.Image:
    w, h = int(w), int(h)
    l = _to_rgba(left)
    r = _to_rgba(right)
    img = Image.new("RGBA", (w,h))
    draw = ImageDraw.Draw(img)
    for x in range(w):
        t = x/(w-1) if w>1 else 0
        col = (
            int(l[0]*(1-t) + r[0]*t),
            int(l[1]*(1-t) + r[1]*t),
            int(l[2]*(1-t) + r[2]*t),
            255
        )
        draw.line([(x,0),(x,h)], fill=col)
    return img

def _rounded_mask(w: int, h: int, r_tl: int, r_tr: int, r_br: int, r_bl: int) -> Image.Image:
    w,h = int(w),int(h)
    r_tl,r_tr,r_br,r_bl = map(int,(r_tl,r_tr,r_br,r_bl))
    m = Image.new("L", (w,h), 0)
    d = ImageDraw.Draw(m)
    # центр
    d.rectangle([r_tl,0,w-r_tr,h], fill=255)
    d.rectangle([0,r_tl,w,h-r_bl], fill=255)
    # углы
    if r_tl>0: d.pieslice([0,0,2*r_tl,2*r_tl], 180,270, fill=255)
    if r_tr>0: d.pieslice([w-2*r_tr,0,w,2*r_tr], 270,360, fill=255)
    if r_br>0: d.pieslice([w-2*r_br,h-2*r_br,w,h], 0,90, fill=255)
    if r_bl>0: d.pieslice([0,h-2*r_bl,2*r_bl,h], 90,180, fill=255)
    return m

def _panel_gradient(base: Image.Image, x:int, y:int, w:int, h:int,
                    colors, corners:Tuple[int,int,int,int]):
    # colors: ('#112233','#0a1b2c', ...)
    left  = colors[0] if isinstance(colors,(list,tuple)) and colors else colors
    right = colors[1] if (isinstance(colors,(list,tuple)) and len(colors)>1) else colors
    grad = _make_hgrad(int(w), int(h), left, right)
    tl,tr,br,bl = map(int, corners)
    if any(corners):
        mask = _rounded_mask(int(w),int(h), tl,tr,br,bl)
        base.paste(grad, (int(x),int(y)), mask)
    else:
        base.alpha_composite(grad, (int(x),int(y)))

# ---------- Вспомогательные отрисовки ----------
def _circle_image(img: Image.Image, diam: int, border_px: int=4, border_color=WHITE) -> Image.Image:
    """Обрезает изображение в круг диаметра diam, рисует белую обводку."""
    diam = int(diam)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    # вписываем
    scale = diam / max(1, max(img.width, img.height))
    nw = max(1, int(img.width*scale))
    nh = max(1, int(img.height*scale))
    img = img.resize((nw,nh), Image.LANCZOS)
    # центрируем на квадрате diam×diam
    square = Image.new("RGBA", (diam,diam), (0,0,0,0))
    ox = (diam - nw)//2
    oy = (diam - nh)//2
    square.alpha_composite(img, (ox,oy))
    # круглая маска
    mask = Image.new("L", (diam,diam), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([0,0,diam,diam], fill=255)
    circle = Image.new("RGBA", (diam,diam), (0,0,0,0))
    circle.paste(square, (0,0), mask)
    # обводка
    if border_px>0:
        d2 = ImageDraw.Draw(circle)
        bb = border_px/2
        d2.ellipse([bb,bb,diam-bb,diam-bb], outline=border_color, width=int(border_px))
    return circle

def _draw_logo_in_circle(base: Image.Image, logo: Optional[Image.Image], cx:int, cy:int, diam:int):
    # белый круг
    circle = Image.new("RGBA", (diam,diam), (0,0,0,0))
    d = ImageDraw.Draw(circle)
    d.ellipse([0,0,diam,diam], fill=WHITE)
    base.alpha_composite(circle, (int(cx-diam/2), int(cy-diam/2)))
    if logo is None: return
    lg = logo.convert("RGBA")
    pad = int(diam*0.14)
    tw = diam - pad*2
    th = diam - pad*2
    sc = min(tw/max(1,lg.width), th/max(1,lg.height))
    nw, nh = max(1,int(lg.width*sc)), max(1,int(lg.height*sc))
    lg = lg.resize((nw,nh), Image.LANCZOS)
    base.alpha_composite(lg, (int(cx-nw/2), int(cy-nh/2)))

def _wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.ImageFont, max_w:int) -> List[str]:
    words = (text or "").split()
    if not words: return []
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        t = (" ".join(cur+[w])).strip()
        tw,_ = _text_size(t, font)
        if tw<=max_w or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur: lines.append(" ".join(cur))
    return lines

def _fit_name_and_stats(draw: ImageDraw.Draw, name:str, stats:List[Tuple[str,str]],
                        max_name_w:int, base_name:int, delta:int) -> Tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    name_sz = int(base_name)
    while name_sz>14:
        f_name = _font(name_sz, bold=True)
        w,_ = _text_size(name, f_name)
        if w <= max_name_w: break
        name_sz -= 2
    f_val = _font(max(10, name_sz - delta), bold=True)
    return f_name, f_val

def _draw_stats_row(draw: ImageDraw.Draw, x:int, y_center:int, stats:List[Tuple[str,str]],
                    f_val:ImageFont.ImageFont, f_lab:ImageFont.ImageFont, max_w:int, gap:int=28):
    pairs=[]
    for val,label in stats:
        vw,vh=_text_size(val,f_val)
        lw,lh=_text_size(label,f_lab)
        w=max(vw,lw); h=vh+6+lh
        pairs.append((val,label,vw,vh,lw,lh,w,h))
    if not pairs: return
    total_w=sum(p[6] for p in pairs)+gap*(len(pairs)-1)
    if total_w>max_w:
        vs=f_val.size
        while total_w>max_w and vs>10:
            vs-=2
            f_val=_font(vs, bold=True)
            f_lab=_font(max(10,vs-12), bold=False)
            pairs=[]
            for val,label in stats:
                vw,vh=_text_size(val,f_val)
                lw,lh=_text_size(label,f_lab)
                w=max(vw,lw); h=vh+6+lh
                pairs.append((val,label,vw,vh,lw,lh,w,h))
            total_w=sum(p[6] for p in pairs)+gap*(len(pairs)-1)
    cur_x=int(x)
    y_top=int(y_center - max(p[7] for p in pairs)/2)
    for val,label,vw,vh,lw,lh,w,h in pairs:
        vx=cur_x+(w-vw)//2; vy=y_top
        draw.text((vx,vy), val, font=f_val, fill=WHITE)
        lx=cur_x+(w-lw)//2; ly=vy+vh+6
        draw.text((lx,ly), label, font=f_lab, fill=WHITE)
        cur_x += w+gap

# ---------- CARD ----------
def render_card(name_ru:str, _team_unused, logo_img:Optional[Image.Image],
                colors, head_img:Optional[Image.Image], stats:List[Tuple[str,str]]) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W,CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    # ширина панели адаптивная, но не меньше 980
    min_w, max_w = 980, 1320
    est_stats_w = 560 if stats else 0
    f_probe = _font(68, bold=True)
    nmw,_ = _text_size(name_ru, f_probe)
    panel_w = int(max(min_w, min(max_w, nmw + 380 + est_stats_w)))

    # панель (градиент) + скругление только справа
    _panel_gradient(base, x, y, panel_w, CARD_H, colors, (0, RADIUS_RIGHT, RADIUS_RIGHT, 0))

    # лого в белом кружке со сдвигом
    cx = int(x + LOGO_CIRCLE_DIAM/2 + 28 + LOGO_OFFSET[0])
    cy = int(y + LOGO_CIRCLE_DIAM/2 + 28 + LOGO_OFFSET[1])
    _draw_logo_in_circle(base, logo_img, cx, cy, LOGO_CIRCLE_DIAM)

    # фото игрока в белом круге, единая позиция
    head_circle = _circle_image(head_img, HEAD_DIAM_SMALL, border_px=6) if head_img else None
    if head_circle:
        hx = int(x + 24)
        hy = int(y + CARD_H - HEAD_DIAM_SMALL - 16)
        base.alpha_composite(head_circle, (hx,hy))
        text_x = int(hx + HEAD_DIAM_SMALL + 28)
    else:
        text_x = int(x + 40)

    center_y = int(y + CARD_H/2)

    # имя + статы: имя не меньше цифр, цифры на 10pt меньше имени
    f_name, f_val = _fit_name_and_stats(draw, name_ru, stats, panel_w - (text_x - x) - 40, 68, 10)
    f_lab = _font(max(10, f_val.size - 12), bold=False)

    # имя
    wname,_ = _text_size(name_ru, f_name)
    draw.text((text_x, int(center_y - f_name.size*0.60)), name_ru, font=f_name, fill=WHITE)

    # статы справа от имени
    stats_x = int(text_x + wname + 32)
    stats_w = int(panel_w - (stats_x - x) - 32)
    if stats and stats_w>80:
        _draw_stats_row(draw, stats_x, center_y, stats, f_val, f_lab, stats_w, gap=32)

    return base

# ---------- CARD BAD ----------
def render_card_bad(name_ru:str, _team_unused, _logo_unused, _colors_unused,
                    head_img:Optional[Image.Image], stats:List[Tuple[str,str]]) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W,CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    panel_w = 1180
    _panel_gradient(base, x, y, panel_w, CARD_H, (BROWN_BAD, BROWN_BAD), (0, RADIUS_RIGHT, RADIUS_RIGHT, 0))

    # фото
    head_circle = _circle_image(head_img, HEAD_DIAM_SMALL, border_px=6) if head_img else None
    if head_circle:
        hx = int(x + 24)
        hy = int(y + CARD_H - HEAD_DIAM_SMALL - 16)
        base.alpha_composite(head_circle, (hx,hy))
        text_x = int(hx + HEAD_DIAM_SMALL + 28)
    else:
        text_x = int(x + 40)

    center_y = int(y + CARD_H/2)

    f_name = _font(68, bold=True)
    draw.text((text_x, int(center_y - f_name.size*0.60)), name_ru, font=f_name, fill=WHITE)
    wname,_ = _text_size(name_ru, f_name)

    # 💩
    poop_x = int(text_x + wname + 16)
    poop_size = int(f_name.size*0.9)
    if POOP_ICON_PATH and os.path.exists(POOP_ICON_PATH):
        try:
            icon = Image.open(POOP_ICON_PATH).convert("RGBA")
            sc = poop_size / max(1, icon.height)
            icon = icon.resize((max(1,int(icon.width*sc)), poop_size), Image.LANCZOS)
            base.alpha_composite(icon, (poop_x, int(center_y + f_name.size*0.60) - icon.height))
        except Exception:
            draw.text((poop_x, int(center_y - f_name.size*0.60)), "💩", font=_font(poop_size, bold=False), fill=WHITE)
    else:
        draw.text((poop_x, int(center_y - f_name.size*0.60)), "💩", font=_font(poop_size, bold=False), fill=WHITE)

    # статы
    stats_x = int(poop_x + 56)
    stats_w = int(panel_w - (stats_x - x) - 32)
    if stats and stats_w>80:
        f_val = _font(56, bold=True)
        f_lab = _font(42, bold=False)
        _draw_stats_row(draw, stats_x, center_y, stats, f_val, f_lab, stats_w, gap=28)

    return base

# ---------- CARDS (левая стандарт + правая доп) ----------
def render_card_special(name_ru:str, _team_unused, logo_img:Optional[Image.Image],
                        colors, head_img:Optional[Image.Image],
                        stats:List[Tuple[str,str]], right_text:str) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W,CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    # левая панель
    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    left_w = 1120
    _panel_gradient(base, x, y, left_w, CARD_H, colors, (0, RADIUS_RIGHT, RADIUS_RIGHT, 0))

    # лого
    cx = int(x + LOGO_CIRCLE_DIAM/2 + 26 + LOGO_OFFSET[0])
    cy = int(y + LOGO_CIRCLE_DIAM/2 + 26 + LOGO_OFFSET[1])
    _draw_logo_in_circle(base, logo_img, cx, cy, LOGO_CIRCLE_DIAM)

    # фото
    head_circle = _circle_image(head_img, HEAD_DIAM_SMALL, border_px=6) if head_img else None
    if head_circle:
        hx = int(x + 24)
        hy = int(y + CARD_H - HEAD_DIAM_SMALL - 16)
        base.alpha_composite(head_circle, (hx,hy))
        text_x = int(hx + HEAD_DIAM_SMALL + 28)
    else:
        text_x = int(x + 40)

    center_y = int(y + CARD_H/2)

    f_name, f_val = _fit_name_and_stats(draw, name_ru, stats, left_w - (text_x - x) - 36, 66, 10)
    f_lab = _font(max(10, f_val.size - 12), bold=False)

    draw.text((text_x, int(center_y - f_name.size*0.60)), name_ru, font=f_name, fill=WHITE)
    wname,_ = _text_size(name_ru, f_name)

    stats_x = int(text_x + wname + 28)
    stats_w = int(left_w - (stats_x - x) - 28)
    if stats and stats_w>80:
        _draw_stats_row(draw, stats_x, center_y, stats, f_val, f_lab, stats_w, gap=28)

    # правая доп-плашка
    rx = int(x + left_w + 10)
    ry = int(CANVAS_H - MARGIN - CARDS_RH)
    right_w = 520
    # скругления слева и справа
    msk = _rounded_mask(right_w, CARDS_RH, RADIUS_BOTH, RADIUS_BOTH, RADIUS_BOTH, RADIUS_BOTH)
    right = Image.new("RGBA", (right_w, CARDS_RH), SEMI_BLACK)
    base.paste(right, (rx, ry), msk)

    # текст справа (⭐ + переносы + пустая строка внизу)
    pad = 24
    f_right = _font(36, bold=True)
    txt = ("⭐ " + (right_text or "").strip()).strip()
    lines = _wrap_text(draw, txt, f_right, right_w - pad*2)
    if not lines: lines = ["⭐"]
    lines.append("")  # пустая строка

    line_h = max(30, int(f_right.size*1.18))
    total_h = int(line_h*len(lines))
    top = int(ry + (CARDS_RH - total_h)//2)
    for i,ln in enumerate(lines):
        draw.text((rx+pad, top + i*line_h), ln, font=f_right, fill=WHITE)

    return base

# ---------- CARD2 (во всю ширину снизу) ----------
def render_card2(ruA:str, _teamA_unused, logoA:Optional[Image.Image], colorsA,
                 headA:Optional[Image.Image], statsA:List[Tuple[str,str]],
                 ruB:str, _teamB_unused, logoB:Optional[Image.Image], colorsB,
                 headB:Optional[Image.Image], statsB:List[Tuple[str,str]]) -> Image.Image:
    base = Image.new("RGBA", (CANVAS_W,CANVAS_H), (0,0,0,0))
    draw = ImageDraw.Draw(base)

    y = CANVAS_H - MARGIN - CARD2_H
    x = 0; w = CANVAS_W; h = CARD2_H
    half = int(w//2)

    # подложки-градиенты без скруглений
    _panel_gradient(base, x, y, half, h, colorsA, (0,0,0,0))
    _panel_gradient(base, x+half, y, half, h, colorsB, (0,0,0,0))

    # лого-кружки по сторонам
    cxA = int(x + half*0.03 + LOGO_CIRCLE_DIAM/2 + LOGO_OFFSET[0])
    cyA = int(y + LOGO_CIRCLE_DIAM/2 + 20 + LOGO_OFFSET[1])
    _draw_logo_in_circle(base, logoA, cxA, cyA, LOGO_CIRCLE_DIAM)

    cxB = int(x + half + half*0.03 + LOGO_CIRCLE_DIAM/2 + LOGO_OFFSET[0])
    cyB = int(y + LOGO_CIRCLE_DIAM/2 + 20 + LOGO_OFFSET[1])
    _draw_logo_in_circle(base, logoB, cxB, cyB, LOGO_CIRCLE_DIAM)

    # фото игроков в белых кружках (фиксированные позиции)
    headA_c = _circle_image(headA, HEAD_DIAM_CARD2, border_px=6) if headA else None
    headB_c = _circle_image(headB, HEAD_DIAM_CARD2, border_px=6) if headB else None
    if headA_c:
        hxA = int(x + half*0.10)
        hyA = int(y + h - HEAD_DIAM_CARD2 - 14)
        base.alpha_composite(headA_c, (hxA,hyA))
    if headB_c:
        hxB = int(x + w - half*0.10 - HEAD_DIAM_CARD2)
        hyB = int(y + h - HEAD_DIAM_CARD2 - 14)
        base.alpha_composite(headB_c, (hxB,hyB))

    # текстовые зоны
    pad = 24
    # левая
    left_x = int(x + half*0.10 + (HEAD_DIAM_CARD2 if headA_c else 0) + 28)
    left_w = int(half - (left_x - x) - pad)
    center_y = int(y + h/2)

    # правая
    right_x = int(x + half + half*0.10 + (HEAD_DIAM_CARD2 if headB_c else 0) + 28)
    right_w = int(half - (right_x - (x+half)) - pad)

    # имя на 2pt больше цифр
    f_nameA, f_valA = _fit_name_and_stats(draw, ruA, statsA, left_w, 76, 2)
    if f_valA.size > f_nameA.size - 2:
        f_valA = _font(max(10, f_nameA.size - 2), bold=True)
    f_labA = _font(max(10, f_valA.size - 12), bold=False)

    f_nameB, f_valB = _fit_name_and_stats(draw, ruB, statsB, right_w, 76, 2)
    if f_valB.size > f_nameB.size - 2:
        f_valB = _font(max(10, f_nameB.size - 2), bold=True)
    f_labB = _font(max(10, f_valB.size - 12), bold=False)

    # имя A
    draw.text((left_x, int(center_y - f_nameA.size*0.60)), ruA, font=f_nameA, fill=WHITE)
    wA,_ = _text_size(ruA, f_nameA)
    sAx = int(left_x + wA + 28)
    sAw = int(left_w - wA - 28)
    if statsA and sAw>80:
        _draw_stats_row(draw, sAx, center_y, statsA, f_valA, f_labA, sAw, gap=24)

    # имя B
    draw.text((right_x, int(center_y - f_nameB.size*0.60)), ruB, font=f_nameB, fill=WHITE)
    wB,_ = _text_size(ruB, f_nameB)
    sBx = int(right_x + wB + 28)
    sBw = int(right_w - wB - 28)
    if statsB and sBw>80:
        _draw_stats_row(draw, sBx, center_y, statsB, f_valB, f_labB, sBw, gap=24)

    return base
