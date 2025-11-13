# graphics.py — bottom-left anchored cards, Pillow>=10 safe, proper fonts/icons, 1920x1080

from __future__ import annotations
import io, os, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080

# ---------- paths & assets lookup ----------
def _here(*p): return os.path.abspath(os.path.join(os.path.dirname(__file__), *p))

# try multiple roots (repo root, /api, current)
ASSET_SEARCH_ROOTS = [
    _here(),                           # .
    _here("api"),                      # ./api
]

def _find_file(rel: str) -> Optional[str]:
    rel = rel.lstrip("/\\")
    for root in ASSET_SEARCH_ROOTS:
        cand = os.path.join(root, rel)
        if os.path.exists(cand): return cand
    # also try sibling folders "fonts" / "icons" directly near this file
    for alt in ("fonts", "api/fonts", "icons", "api/icons"):
        cand = _here(alt, os.path.basename(rel))
        if os.path.exists(cand): return cand
    return None

def _load_font(rel: str, size: int) -> ImageFont.FreeTypeFont:
    path = _find_file(rel) or _find_file(os.path.join("fonts", os.path.basename(rel))) \
           or _find_file(os.path.join("api", "fonts", os.path.basename(rel)))
    if not path:
        # last resort
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()

def _load_png(rel: str, size: Optional[int]=None) -> Optional[Image.Image]:
    path = _find_file(rel) or _find_file(os.path.join("icons", os.path.basename(rel))) \
           or _find_file(os.path.join("api", "icons", os.path.basename(rel)))
    if not path: return None
    try:
        im = Image.open(path).convert("RGBA")
        if size:
            im = im.resize((size, size), Image.LANCZOS)
        return im
    except Exception:
        return None

# Fonts (prefer Montserrat / Exo2; fallback to default)
def font_name(size:int): return _load_font("Montserrat-Bold.ttf", size)
def font_stat_val(size:int): return _load_font("Exo2-Bold.ttf", size)
def font_stat_lbl(size:int): return _load_font("Montserrat-SemiBold.ttf", size)

# ---------- text metrics (Pillow 10+ safe) ----------
def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
    if not text: return (0,0)
    box = draw.textbbox((0,0), text, font=font)
    return box[2]-box[0], box[3]-box[1]

# ---------- helpers ----------
def _png_bytes(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

def _rounded_rect(size: Tuple[int,int], radius: int, fill):
    w,h = size
    base = Image.new("RGBA", (w,h), (0,0,0,0))
    m = Image.new("L", (w,h), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0,0,w-1,h-1], radius=radius, fill=255)
    base.paste(fill, (0,0), m) if isinstance(fill, Image.Image) else ImageDraw.Draw(base).rounded_rectangle([0,0,w-1,h-1], radius, fill=fill)
    return base, m

def _linear_gradient(w:int, h:int, c1:Tuple[int,int,int], c2:Tuple[int,int,int], horizontal:bool=True)->Image.Image:
    grad = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(grad)
    if horizontal:
        for x in range(w):
            t = x/(w-1) if w>1 else 0
            r = int(c1[0]*(1-t) + c2[0]*t)
            g = int(c1[1]*(1-t) + c2[1]*t)
            b = int(c1[2]*(1-t) + c2[2]*t)
            draw.line([(x,0),(x,h)], fill=(r,g,b,255))
    else:
        for y in range(h):
            t = y/(h-1) if h>1 else 0
            r = int(c1[0]*(1-t) + c2[0]*t)
            g = int(c1[1]*(1-t) + c2[1]*t)
            b = int(c1[2]*(1-t) + c2[2]*t)
            draw.line([(0,y),(w,y)], fill=(r,g,b,255))
    return grad

def _to_rgb(hex_color: str) -> Tuple[int,int,int]:
    s = hex_color.strip().lstrip("#")
    if len(s)==3: s="".join(ch*2 for ch in s)
    r = int(s[0:2],16); g=int(s[2:4],16); b=int(s[4:6],16)
    return (r,g,b)

def _circle_mask(d:int) -> Image.Image:
    m = Image.new("L", (d,d), 0)
    ImageDraw.Draw(m).ellipse([0,0,d-1,d-1], fill=255)
    return m

def _paste_center(base:Image.Image, part:Image.Image, cx:int, cy:int):
    x = cx - part.width//2
    y = cy - part.height//2
    base.alpha_composite(part, (x,y))

def _ensure_rgba(img_or_none) -> Optional[Image.Image]:
    if img_or_none is None: return None
    return img_or_none.convert("RGBA") if img_or_none.mode!="RGBA" else img_or_none

# ---------- common layout constants ----------
# heights and sizes tuned per request (−10% vs. previous typical 210–220)
CARD_H = 190
RADIUS  = 22
PADDING = 26
GAP     = 20

NAME_SIZE = 70        # larger than stats
STAT_VAL  = 48
STAT_LBL  = 22

LOGO_D    = 68        # team logo circle
HEAD_D    = 152       # base, will +20%
HEAD_SCALE = 1.2      # +20%

# additional requested offsets for headshot
HEAD_SHIFT_X = +50
HEAD_SHIFT_Y = -10

# colors
ORANGE_1 = (255,138,0)   # #FF8A00
ORANGE_2 = (255,211,77)  # #FFD34D
BROWN_1  = (70,46,37)
BROWN_2  = (42,34,32)

BLACK_1  = (32,32,32)
BLACK_2  = (16,16,16)

WHITE = (255,255,255,255)

# ---------- rendering primitives ----------
def _draw_team_logo(base:Image.Image, logo_img:Optional[Image.Image], x:int, y:int):
    if logo_img is None: return
    logo_img = _ensure_rgba(logo_img)
    # fit into circle LOGO_D
    lg = logo_img.copy()
    # square fit
    side = min(lg.width, lg.height)
    lg = lg.crop(((lg.width-side)//2, (lg.height-side)//2, (lg.width+side)//2, (lg.height+side)//2))
    lg = lg.resize((LOGO_D, LOGO_D), Image.LANCZOS)
    # white circle plate
    plate = Image.new("RGBA", (LOGO_D+14, LOGO_D+14), (255,255,255,255))
    mask = _circle_mask(LOGO_D+14)
    # make subtle shadow
    shadow = Image.new("RGBA", (LOGO_D+18, LOGO_D+18), (0,0,0,0))
    shmask = _circle_mask(LOGO_D+18)
    shadow_draw = Image.new("RGBA", shadow.size, (0,0,0,160))
    shadow.alpha_composite(shadow_draw, (0,0), shmask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(2))
    base.alpha_composite(shadow, (x-2,y-2))
    base.paste(plate, (x,y), mask)
    base.paste(lg, (x+7,y+7), _circle_mask(LOGO_D))

def _draw_headshot(base:Image.Image, head_img:Image.Image, cx:int, cy:int, d:int):
    head = _ensure_rgba(head_img).copy()
    # center crop to square
    side = min(head.width, head.height)
    head = head.crop(((head.width-side)//2, (head.height-side)//2, (head.width+side)//2, (head.height+side)//2))
    head = head.resize((d,d), Image.LANCZOS)
    mask = _circle_mask(d)
    # subtle ring
    ring = Image.new("RGBA", (d+8, d+8), (255,255,255,255))
    ring_mask = _circle_mask(d+8)
    base.paste(ring, (cx-(d+8)//2, cy-(d+8)//2), ring_mask)
    base.paste(head, (cx-d//2, cy-d//2), mask)

def _draw_name_and_stats(base:Image.Image, x:int, y:int, w:int, name_ru:str, stats:List[Tuple[str,str]]):
    d = ImageDraw.Draw(base)
    # Name
    f_name = font_name(NAME_SIZE)
    name_w, name_h = text_size(d, name_ru, f_name)
    d.text((x, y), name_ru, font=f_name, fill=WHITE)

    # stats centered under name, in columns
    cols = max(1, len(stats))
    area_x = x
    area_y = y + name_h + 10
    area_w = max(w - 10, name_w)  # ensure space
    col_w  = area_w // cols

    f_val = font_stat_val(STAT_VAL)
    f_lbl = font_stat_lbl(STAT_LBL)

    for i,(val,lbl) in enumerate(stats):
        cx = area_x + col_w*i + col_w//2
        # measure widths
        vw, vh = text_size(d, str(val), f_val)
        lw, lh = text_size(d, str(lbl), f_lbl)
        d.text((cx - vw//2, area_y), str(val), font=f_val, fill=WHITE)
        d.text((cx - lw//2, area_y + vh + 6), str(lbl), font=f_lbl, fill=WHITE)

def _main_bar_gradient(width:int, height:int, left_color:Tuple[int,int,int], right_color:Tuple[int,int,int]) -> Image.Image:
    grad = _linear_gradient(width, height, left_color, right_color, horizontal=True)
    card, m = _rounded_rect((width,height), RADIUS, grad)
    return card

# ---------- single ----------
def render_card(mode: str,
                name_ru: str,
                team_name_ru: str,
                team_logo_img: Optional[Image.Image],
                team_colors: Tuple[str,str,str],
                head_img: Image.Image,
                stats: List[Tuple[str,str]]) -> bytes:
    """
    mode is ignored (kept for backward compat).
    team_colors is (primary, secondary, dark) in hex strings.
    """
    img = Image.new("RGBA", (W,H), (0,0,0,0))
    # bar anchored bottom-left
    CARD_W = 1180
    bar_x, bar_y = 0, H - CARD_H
    primary, secondary, _ = team_colors
    c1, c2 = _to_rgb(primary), _to_rgb(secondary)
    bar = _main_bar_gradient(CARD_W, CARD_H, c1, c2)
    img.alpha_composite(bar, (bar_x, bar_y))

    # logo (left), head (right from logo)
    logo_x = bar_x + PADDING
    logo_y = bar_y + CARD_H - PADDING - LOGO_D
    _draw_team_logo(img, team_logo_img, logo_x, logo_y)

    # head
    hd = int(HEAD_D * HEAD_SCALE)
    head_cx = logo_x + LOGO_D + 60 + HEAD_SHIFT_X
    head_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head_img, head_cx, head_cy, hd)

    # name + stats (to the right of head), centered area
    text_x = head_cx + hd//2 + 28
    text_y = bar_y + 24
    avail_w = CARD_W - (text_x - bar_x) - PADDING
    _draw_name_and_stats(img, text_x, text_y, avail_w, name_ru, stats)

    return _png_bytes(img)

# ---------- duo (1080 total, 540 each) ----------
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
    BAR_W = 1080
    HALF = 540
    bar_x, bar_y = 0, H - CARD_H

    # left half gradient: team1 primary -> secondary
    c1a, c1b, _ = team1_colors
    left = _main_bar_gradient(HALF, CARD_H, _to_rgb(c1a), _to_rgb(c1b))
    # right half gradient: team2 secondary -> primary (зеркалим)
    c2a, c2b, _ = team2_colors
    right = _main_bar_gradient(HALF, CARD_H, _to_rgb(c2b), _to_rgb(c2a))

    base = Image.new("RGBA", (BAR_W, CARD_H), (0,0,0,0))
    base.alpha_composite(left, (0,0))
    base.alpha_composite(right, (HALF,0))

    img.alpha_composite(base, (bar_x, bar_y))

    # divider
    d = ImageDraw.Draw(img)
    d.rectangle([bar_x + HALF - 1, bar_y + 8, bar_x + HALF + 1, bar_y + CARD_H - 8], fill=(255,255,255,80))

    # LEFT side content
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

    # RIGHT side content
    logo2_x = bar_x + HALF + PADDING
    logo2_y = bar_y + CARD_H - PADDING - LOGO_D
    _draw_team_logo(img, team2_logo_img, logo2_x, logo2_y)

    head2_cx = logo2_x + LOGO_D + 60 + HEAD_SHIFT_X
    head2_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head2_img, head2_cx, head2_cy, hd)

    text2_x = head2_cx + hd//2 + 28
    text2_y = bar_y + 24
    avail2_w = (bar_x + BAR_W) - text2_x - PADDING
    _draw_name_and_stats(img, text2_x, text2_y, avail2_w, name2_ru, stats2)

    return _png_bytes(img)

# ---------- special (main + side block) ----------
def render_card_special(name_ru: str,
                        team_logo_img: Optional[Image.Image],
                        team_colors: Tuple[str,str,str],
                        head_img: Image.Image,
                        stats: List[Tuple[str,str]],
                        info_text: str) -> bytes:

    img = Image.new("RGBA", (W,H), (0,0,0,0))

    # main bar
    MAIN_W = 1180
    main_x, main_y = 0, H - CARD_H
    c1, c2, _ = team_colors
    main = _main_bar_gradient(MAIN_W, CARD_H, _to_rgb(c1), _to_rgb(c2))
    img.alpha_composite(main, (main_x, main_y))

    # side block 10 px to the right
    SIDE_W = 440
    side_x = main_x + MAIN_W + 10
    side_y = main_y
    side_grad = _main_bar_gradient(SIDE_W, CARD_H, BLACK_1, BLACK_2)
    img.alpha_composite(side_grad, (side_x, side_y))

    # left part content (like single)
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

    # side content with star icon
    star_sz = 28
    star = _load_png("star.png", star_sz)
    d = ImageDraw.Draw(img)
    f = font_stat_lbl(26)
    tx = side_x + PADDING + (star_sz + 12 if star else 0)
    ty = side_y + CARD_H//2 - 14
    if star:
        img.alpha_composite(star, (side_x + PADDING, ty-2))
    else:
        # draw vector star fallback
        cx, cy, r = side_x + PADDING + 12, ty+12, 12
        pts=[]
        for i in range(10):
            ang = math.pi/2 + i*math.pi/5
            rr = r if i%2==0 else r*0.45
            pts.append((cx+rr*math.cos(ang), cy-rr*math.sin(ang)))
        d.polygon(pts, outline=(255,205,0,255), fill=None, width=3)
    # wrap text (short info)
    info = (info_text or "").strip()
    if info:
        d.text((tx, ty), info, font=f, fill=WHITE)

    return _png_bytes(img)

# ---------- bad (brown, poop after name) ----------
def render_card_bad(name_ru: str,
                    head_img: Image.Image,
                    stats: List[Tuple[str,str]],
                    team_logo_img: Optional[Image.Image]=None) -> bytes:

    img = Image.new("RGBA", (W,H), (0,0,0,0))
    CARD_W = 1180
    bar_x, bar_y = 0, H - CARD_H
    bar = _main_bar_gradient(CARD_W, CARD_H, BROWN_1, BROWN_2)
    img.alpha_composite(bar, (bar_x, bar_y))

    # optional team logo (still to the left)
    if team_logo_img is not None:
        _draw_team_logo(img, team_logo_img, bar_x + PADDING, bar_y + CARD_H - PADDING - LOGO_D)
        head_left_base = bar_x + PADDING + LOGO_D + 60
    else:
        head_left_base = bar_x + PADDING

    # head
    hd = int(HEAD_D * HEAD_SCALE)
    head_cx = head_left_base + HEAD_SHIFT_X
    head_cy = bar_y + CARD_H//2 + HEAD_SHIFT_Y
    _draw_headshot(img, head_img, head_cx, head_cy, hd)

    # name + poop icon after name
    d = ImageDraw.Draw(img)
    f_name = font_name(NAME_SIZE)
    text_x = head_cx + hd//2 + 28
    text_y = bar_y + 24
    d.text((text_x, text_y), name_ru, font=f_name, fill=WHITE)
    name_w, name_h = text_size(d, name_ru, f_name)

    poop_base = 28
    poop = _load_png("poop.png", poop_base*2)  # «в 2 раза крупнее»
    if poop:
        img.alpha_composite(poop, (text_x + name_w + 14, text_y + max(0, (name_h - poop.height)//2)))
    else:
        # fallback: simple brown blob
        px, py = text_x + name_w + 14, text_y + 4
        d.rounded_rectangle([px,py, px+poop_base*2, py+poop_base*2], radius=12, outline=(210,160,90,255), width=4)

    # stats under the name
    avail_w = CARD_W - (text_x - bar_x) - PADDING
    _draw_name_and_stats(img, text_x, text_y, avail_w, "", stats)  # name already drawn

    return _png_bytes(img)
