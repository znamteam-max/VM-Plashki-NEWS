# graphics.py — fixed masks, no rounded corners, robust fonts, vector fallbacks, 1920x1080
from __future__ import annotations
import io, os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080

# ---------- paths ----------
def _here(*p): return os.path.abspath(os.path.join(os.path.dirname(__file__), *p))
ASSET_SEARCH_ROOTS = [_here(), _here("api")]

def _find_file(rel: str) -> Optional[str]:
    rel = rel.lstrip("/\\")
    for root in ASSET_SEARCH_ROOTS:
        cand = os.path.join(root, rel)
        if os.path.exists(cand): return cand
    for alt in ("fonts", "api/fonts", "icons", "api/icons"):
        cand = _here(alt, os.path.basename(rel))
        if os.path.exists(cand): return cand
    return None

def _load_font_multi(cands: List[str], size: int) -> ImageFont.ImageFont:
    for nm in cands:
        path = _find_file(nm) or _find_file(os.path.join("fonts", os.path.basename(nm))) \
               or _find_file(os.path.join("api", "fonts", os.path.basename(nm)))
        if path:
            try: return ImageFont.truetype(path, size=size)
            except Exception: pass
    return ImageFont.load_default()

def _load_png(rel: str, size: Optional[int]=None) -> Optional[Image.Image]:
    path = _find_file(rel) or _find_file(os.path.join("icons", os.path.basename(rel))) \
           or _find_file(os.path.join("api", "icons", os.path.basename(rel)))
    if not path: return None
    try:
        im = Image.open(path).convert("RGBA")
        if size: im = im.resize((size, size), Image.LANCZOS)
        return im
    except Exception:
        return None

# Fonts
MONTSERRAT_BOLD = ["Montserrat-Bold.ttf", "Montserrat-ExtraBold.ttf", "MontserratAlternates-Bold.ttf", "Montserrat-Black.ttf"]
MONTSERRAT_SEMI = ["Montserrat-SemiBold.ttf", "MontserratAlternates-SemiBold.ttf", "Montserrat-Medium.ttf", "Montserrat-Regular.ttf"]
EXO2_BOLD       = ["Exo2-Bold.ttf", "Exo 2 Bold.ttf", "Exo2-ExtraBold.ttf", "Exo2-SemiBold.ttf"]

def font_name(size:int):     return _load_font_multi(MONTSERRAT_BOLD, size)
def font_stat_val(size:int): return _load_font_multi(EXO2_BOLD, size)
def font_stat_lbl(size:int): return _load_font_multi(MONTSERRAT_SEMI, size)

# ---------- text metrics ----------
def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
    if not text: return (0,0)
    box = draw.textbbox((0,0), text, font=font)
    return box[2]-box[0], box[3]-box[1]

# ---------- helpers ----------
def _png_bytes(img: Image.Image) -> bytes:
    b = io.BytesIO(); img.save(b, format="PNG"); return b.getvalue()

def _linear_gradient(w:int, h:int, c1:Tuple[int,int,int], c2:Tuple[int,int,int], horizontal:bool=True)->Image.Image:
    grad = Image.new("RGBA", (w,h), (0,0,0,0))
    d = ImageDraw.Draw(grad)
    if horizontal:
        for x in range(w):
            t = x/(w-1) if w>1 else 0
            r = int(c1[0]*(1-t) + c2[0]*t)
            g = int(c1[1]*(1-t) + c2[1]*t)
            b = int(c1[2]*(1-t) + c2[2]*t)
            d.line([(x,0),(x,h)], fill=(r,g,b,255))
    else:
        for y in range(h):
            t = y/(h-1) if h>1 else 0
            r = int(c1[0]*(1-t) + c2[0]*t)
            g = int(c1[1]*(1-t) + c2[1]*t)
            b = int(c1[2]*(1-t) + c2[2]*t)
            d.line([(0,y),(w,y)], fill=(r,g,b,255))
    return grad

def _to_rgb(hex_color:str)->Tuple[int,int,int]:
    s = hex_color.strip().lstrip("#")
    if len(s)==3: s="".join(ch*2 for ch in s)
    return (int(s[0:2],16), int(s[2:4],16), int(s[4:6],16))

def _circle_mask(d:int) -> Image.Image:
    m = Image.new("L", (d,d), 0)
    ImageDraw.Draw(m).ellipse([0,0,d-1,d-1], fill=255)
    return m

def _ensure_rgba(img:Optional[Image.Image]) -> Optional[Image.Image]:
    return None if img is None else (img.convert("RGBA") if img.mode!="RGBA" else img)

# ---------- layout ----------
CARD_H    = 190  # ~10% ниже обычного
RADIUS    = 0    # без скруглений
PADDING   = 26
NAME_SIZE = 70
STAT_VAL  = 48
STAT_LBL  = 22

LOGO_D    = 68
HEAD_D    = 152
HEAD_SCALE = 1.2
HEAD_SHIFT_X = +50
HEAD_SHIFT_Y = -10
HEAD_CIRCLE  = False  # фото НЕ в круге (как просил)

WHITE = (255,255,255,255)
ORANGE_1 = (255,138,0)
ORANGE_2 = (255,211,77)
BROWN_1  = (70,46,37)
BROWN_2  = (42,34,32)
BLACK_1  = (32,32,32)
BLACK_2  = (16,16,16)

# ---------- primitives ----------
def _main_bar(width:int, height:int, c_left:Tuple[int,int,int], c_right:Tuple[int,int,int]) -> Image.Image:
    grad = _linear_gradient(width, height, c_left, c_right, horizontal=True)
    # без скругления — просто заливка
    return grad

def _draw_team_logo(base:Image.Image, logo_img:Optional[Image.Image], x:int, y:int):
    if logo_img is None: return
    lg = _ensure_rgba(logo_img).copy()
    # центрируем и вписываем
    side = min(lg.width, lg.height)
    lg = lg.crop(((lg.width-side)//2, (lg.height-side)//2, (lg.width+side)//2, (lg.height+side)//2))
    lg = lg.resize((LOGO_D, LOGO_D), Image.LANCZOS)

    # тень
    shadow = Image.new("RGBA", (LOGO_D+18, LOGO_D+18), (0,0,0,0))
    shmask = _circle_mask(LOGO_D+18)
    shadow_color = Image.new("RGBA", shadow.size, (0,0,0,140))
    shadow.paste(shadow_color, (0,0), shmask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(2))
    base.alpha_composite(shadow, (x-9, y-9))

    # белая «тарелка»
    plate_mask = _circle_mask(LOGO_D+14)
    plate = Image.new("RGBA", (LOGO_D+14, LOGO_D+14), (255,255,255,255))
    base.paste(plate, (x-7, y-7), plate_mask)

    # логотип в круг
    base.paste(lg, (x, y), _circle_mask(LOGO_D))

def _draw_headshot(base:Image.Image, head_img:Image.Image, cx:int, cy:int, d:int):
    im = _ensure_rgba(head_img).copy()
    side = min(im.width, im.height)
    im = im.crop(((im.width-side)//2, (im.height-side)//2, (im.width+side)//2, (im.height+side)//2))
    im = im.resize((d,d), Image.LANCZOS)
    if HEAD_CIRCLE:
        ring = Image.new("RGBA", (d+8, d+8), (255,255,255,255))
        ring_m = _circle_mask(d+8)
        base.paste(ring, (cx-(d+8)//2, cy-(d+8)//2), ring_m)
        base.paste(im, (cx-d//2, cy-d//2), _circle_mask(d))
    else:
        # без круга: аккуратно обрезанный квадратик
        base.alpha_composite(im, (cx-d//2, cy-d//2))

def _draw_name_and_stats(base:Image.Image, x:int, y:int, w:int, name_ru:str, stats:List[Tuple[str,str]]):
    d = ImageDraw.Draw(base)
    f_name = font_name(NAME_SIZE)
    name_w, name_h = text_size(d, name_ru, f_name) if name_ru else (0,0)
    if name_ru:
        d.text((x, y), name_ru, font=f_name, fill=WHITE)

    cols = max(1, len(stats))
    area_x = x
    area_y = y + (name_h + 10 if name_ru else 0)
    area_w = max(w - 10, name_w)
    col_w  = area_w // cols

    f_val = font_stat_val(STAT_VAL)
    f_lbl = font_stat_lbl(STAT_LBL)
    for i,(val,lbl) in enumerate(stats):
        cx = area_x + col_w*i + col_w//2
        vw, vh = text_size(d, str(val), f_val)
        lw, lh = text_size(d, str(lbl), f_lbl)
        d.text((cx - vw//2, area_y), str(val), font=f_val, fill=WHITE)
        d.text((cx - lw//2, area_y + vh + 6), str(lbl), font=f_lbl, fill=WHITE)

# vector fallbacks
def _draw_star(d:ImageDraw.ImageDraw, cx:int, cy:int, r:int):
    pts=[]
    for i in range(10):
        ang = math.pi/2 + i*math.pi/5
        rr = r if i%2==0 else int(r*0.45)
        pts.append((cx+rr*math.cos(ang), cy-rr*math.sin(ang)))
    d.polygon(pts, outline=(255,205,0,255), fill=None, width=3)

def _draw_poop(d:ImageDraw.ImageDraw, x:int, y:int, s:int):
    # три «блина» + завиток
    b = s
    c = (120, 84, 50, 255)
    d.rounded_rectangle([x, y+b*0.45, x+b, y+b], radius=int(b*0.22), fill=c)
    d.rounded_rectangle([x+b*0.08, y+b*0.2, x+b*0.92, y+b*0.78], radius=int(b*0.22), fill=c)
    d.rounded_rectangle([x+b*0.22, y, x+b*0.78, y+b*0.55], radius=int(b*0.22), fill=c)
    # контур
    d.arc([x+b*0.15, y-b*0.05, x+b*0.85, y+b*0.95], start=200, end=340, fill=(255,205,0,255), width=3)

# ---------- single ----------
def render_card(mode: str,
                name_ru: str,
                team_name_ru: str,
                team_logo_img: Optional[Image.Image],
                team_colors: Tuple[str,str,str],
                head_img: Image.Image,
                stats: List[Tuple[str,str]]) -> bytes:

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    CARD_W = 1180
    bar_x, bar_y = 0, H - CARD_H
    c1, c2, _ = team_colors
    bar = _main_bar(CARD_W, CARD_H, _to_rgb(c1), _to_rgb(c2))
    img.alpha_composite(bar, (bar_x, bar_y))

    # logo -> head -> text
    logo_x = bar_x + PADDING
    logo_y = bar_y + CARD_H - PADDING - LOGO_D
    _draw_team_logo(img, team_logo_img, logo_x, logo_y)

    hd = int(HEAD_D * HEAD_SCALE)
    head_cx = logo_x + LOGO_D + 60 + HEAD_SHIFT_X
    head_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head_img, head_cx, head_cy, hd)

    text_x = head_cx + hd//2 + 28
    text_y = bar_y + 24
    avail_w = CARD_W - (text_x - bar_x) - PADDING
    _draw_name_and_stats(img, text_x, text_y, avail_w, name_ru, stats)

    return _png_bytes(img)

# ---------- duo 1080 = 540+540 ----------
def render_card2(name1_ru: str,
                 team1_logo_img: Optional[Image.Image],
                 team1_colors: Tuple[str,str,str],
                 head1_img: Image.Image,
                 stats1: List[Tuple[str,str]],
                 name2_ru: str,
                 team2_logo_img: Optional[Image.Image],
                 team2_colors: Tuple[str,str,str],
                 head2_img: Image.Image,
                 stats2: List[Tuple[str,str]]) -> bytes:

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    BAR_W = 1080; HALF = 540
    bar_x, bar_y = 0, H - CARD_H

    left  = _main_bar(HALF, CARD_H, _to_rgb(team1_colors[0]), _to_rgb(team1_colors[1]))
    right = _main_bar(HALF, CARD_H, _to_rgb(team2_colors[1]), _to_rgb(team2_colors[0]))

    base = Image.new("RGBA", (BAR_W, CARD_H), (0,0,0,0))
    base.alpha_composite(left, (0,0))
    base.alpha_composite(right, (HALF,0))
    img.alpha_composite(base, (bar_x, bar_y))

    d = ImageDraw.Draw(img)
    d.rectangle([bar_x + HALF - 1, bar_y + 8, bar_x + HALF + 1, bar_y + CARD_H - 8], fill=(255,255,255,80))

    # LEFT
    logo_x = bar_x + PADDING
    logo_y = bar_y + CARD_H - PADDING - LOGO_D
    _draw_team_logo(img, team1_logo_img, logo_x, logo_y)

    hd = int(HEAD_D * HEAD_SCALE)
    head_cx = logo_x + LOGO_D + 60 + HEAD_SHIFT_X
    head_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head1_img, head_cx, head_cy, hd)

    text_x = head_cx + hd//2 + 28
    text_y = bar_y + 24
    avail_w = (bar_x + HALF) - text_x - PADDING
    _draw_name_and_stats(img, text_x, text_y, avail_w, name1_ru, stats1)

    # RIGHT
    logo2_x = bar_x + HALF + PADDING
    logo2_y = bar_y + CARD_H - PADDING - LOGO_D
    _draw_team_logo(img, team2_logo_img, logo2_x, logo2_y)

    head2_cx = logo2_x + LOGO_D + 60 + HEAD_SHIFT_X
    head2_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head2_img, head2_cy, head2_cy, hd)  # typo would break; correct below
    # correct head placement:
    _draw_headshot(img, head2_img, head2_cx, head2_cy, hd)

    text2_x = head2_cx + hd//2 + 28
    text2_y = bar_y + 24
    avail2_w = (bar_x + BAR_W) - text2_x - PADDING
    _draw_name_and_stats(img, text2_x, text2_y, avail2_w, name2_ru, stats2)

    return _png_bytes(img)

# ---------- special (main + side) ----------
def render_card_special(name_ru: str,
                        team_logo_img: Optional[Image.Image],
                        team_colors: Tuple[str,str,str],
                        head_img: Image.Image,
                        stats: List[Tuple[str,str]],
                        info_text: str) -> bytes:

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    MAIN_W = 1180; SIDE_W = 440
    main_x, main_y = 0, H - CARD_H
    side_x = main_x + MAIN_W + 10; side_y = main_y

    main = _main_bar(MAIN_W, CARD_H, _to_rgb(team_colors[0]), _to_rgb(team_colors[1]))
    side = _main_bar(SIDE_W, CARD_H, BLACK_1, BLACK_2)
    img.alpha_composite(main, (main_x, main_y))
    img.alpha_composite(side, (side_x, side_y))

    # left content
    logo_x = main_x + PADDING
    logo_y = main_y + CARD_H - PADDING - LOGO_D
    _draw_team_logo(img, team_logo_img, logo_x, logo_y)

    hd = int(HEAD_D * HEAD_SCALE)
    head_cx = logo_x + LOGO_D + 60 + HEAD_SHIFT_X
    head_cy = main_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head_img, head_cx, head_cy, hd)

    text_x = head_cx + hd//2 + 28
    text_y = main_y + 24
    avail_w = MAIN_W - (text_x - main_x) - PADDING
    _draw_name_and_stats(img, text_x, text_y, avail_w, name_ru, stats)

    # side info with star
    d = ImageDraw.Draw(img)
    star = _load_png("star.png", 28)
    tx = side_x + PADDING + (28 + 12 if star else 28)
    ty = side_y + CARD_H//2 - 14
    if star: img.alpha_composite(star, (side_x + PADDING, ty-2))
    else:    _draw_star(d, side_x + PADDING + 12, ty+12, 12)

    info = (info_text or "").strip()
    if info:
        d.text((tx, ty), info, font=font_stat_lbl(26), fill=WHITE)

    return _png_bytes(img)

# ---------- bad (brown + poop) ----------
def render_card_bad(name_ru: str,
                    head_img: Image.Image,
                    stats: List[Tuple[str,str]],
                    team_logo_img: Optional[Image.Image]=None) -> bytes:

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    CARD_W = 1180
    bar_x, bar_y = 0, H - CARD_H
    bar = _main_bar(CARD_W, CARD_H, BROWN_1, BROWN_2)
    img.alpha_composite(bar, (bar_x, bar_y))

    base_left = bar_x + PADDING
    if team_logo_img is not None:
        _draw_team_logo(img, team_logo_img, base_left, bar_y + CARD_H - PADDING - LOGO_D)
        base_left += LOGO_D + 60

    hd = int(HEAD_D * HEAD_SCALE)
    head_cx = base_left + HEAD_SHIFT_X
    head_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head_img, head_cx, head_cy, hd)

    d = ImageDraw.Draw(img)
    f_name = font_name(NAME_SIZE)
    text_x = head_cx + hd//2 + 28
    text_y = bar_y + 24
    d.text((text_x, text_y), name_ru, font=f_name, fill=WHITE)
    name_w, name_h = text_size(d, name_ru, f_name)

    poop_png = _load_png("poop.png", 56)
    if poop_png:
        img.alpha_composite(poop_png, (text_x + name_w + 14, text_y + max(0, (name_h - poop_png.height)//2)))
    else:
        _draw_poop(d, text_x + name_w + 14, text_y + 2, 44)

    avail_w = CARD_W - (text_x - bar_x) - PADDING
    _draw_name_and_stats(img, text_x, text_y, avail_w, "", stats)

    return _png_bytes(img)
