# graphics.py — NEWS cards renderer (1920x1080 RGBA, pinned to bottom), Cyrillic-safe
from __future__ import annotations
import os, io, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------- Canvas ----------
CANVAS_W, CANVAS_H = 1920, 1080
MARGIN = 40

# Heights (compact; ≈1.5–2x ниже «больших»)
CARD_H    = 200   # /card, /cardbad, левая часть /cards
CARDS_RH  = 180   # правая колонка /cards
CARD2_H   = 220   # /card2 pinned снизу, на всю ширину

# Radii
RADIUS_RIGHT = 28
RADIUS_BOTH  = 28

# Colors
WHITE = (255,255,255,255)
BLACK = (0,0,0,255)
SEMI_BLACK = (0,0,0,180)
BROWN_BAD = (90,58,44,255)

# Logo & head circles
LOGO_DIAM = 120
LOGO_OFFSET = (-30, -30)

HEAD_DIAM_SMALL = 156   # card / cardbad / cards (левая)
HEAD_DIAM_CARD2 = 168   # card2

# Optional poop icon (cardbad)
POOP_ICON_PATH = os.getenv("POOP_ICON_PATH", "").strip()

# ---------- Fonts (Cyrillic-safe) ----------
_FONT_CACHE = {}

FONT_REGULAR_PATH = (os.getenv("FONT_REGULAR_PATH") or "").strip() or None
FONT_BOLD_PATH    = (os.getenv("FONT_BOLD_PATH") or "").strip() or None

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

def _load_font(paths, size: int):
    for p in paths:
        if not p: continue
        try:
            return ImageFont.truetype(p, int(size))
        except Exception:
            pass
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(size)) if os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") else ImageFont.load_default()

def _font(size: int, bold=False):
    key = (int(size), bool(bold))
    if key in _FONT_CACHE: return _FONT_CACHE[key]
    f = _load_font(TRY_BOLD if bold else TRY_REG, int(size))
    _FONT_CACHE[key] = f
    return f

def _text_size(text: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
    if not text: return (0,0)
    try:
        b = font.getbbox(text)
        return (int(b[2]-b[0]), int(b[3]-b[1]))
    except Exception:
        return font.getsize(text)

# ---------- Helpers ----------
def _to_png_bytes(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

def _rgba(c) -> Tuple[int,int,int,int]:
    if isinstance(c, (list, tuple)):
        if len(c)==4: return (int(c[0]),int(c[1]),int(c[2]),int(c[3]))
        if len(c)==3: return (int(c[0]),int(c[1]),int(c[2]),255)
        c = c[0]
    if isinstance(c, str):
        s = c.strip()
        if s.startswith("#"): s = s[1:]
        if len(s)==3:
            r = int(s[0]*2,16); g=int(s[1]*2,16); b=int(s[2]*2,16)
        else:
            r = int(s[0:2],16); g=int(s[2:4],16); b=int(s[4:6],16)
        return (r,g,b,255)
    return (20,28,36,255)

def _grad_horz(w:int,h:int,left,right)->Image.Image:
    w,h = int(w), int(h)
    L,R = _rgba(left), _rgba(right)
    img = Image.new("RGBA",(w,h))
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x/(w-1) if w>1 else 0
        col = (
            int(L[0]*(1-t)+R[0]*t),
            int(L[1]*(1-t)+R[1]*t),
            int(L[2]*(1-t)+R[2]*t),
            255
        )
        d.line([(x,0),(x,h)], fill=col)
    return img

def _round_mask(w:int,h:int,tl:int,tr:int,br:int,bl:int)->Image.Image:
    w,h = int(w),int(h)
    tl,tr,br,bl = map(int,(tl,tr,br,bl))
    m = Image.new("L",(w,h),0); d=ImageDraw.Draw(m)
    d.rectangle([tl,0,w-tr,h], fill=255)
    d.rectangle([0,tl,w,h-bl], fill=255)
    if tl>0: d.pieslice([0,0,2*tl,2*tl],180,270,fill=255)
    if tr>0: d.pieslice([w-2*tr,0,w,2*tr],270,360,fill=255)
    if br>0: d.pieslice([w-2*br,h-2*br,w,h],0,90,fill=255)
    if bl>0: d.pieslice([0,h-2*bl,2*bl,h],90,180,fill=255)
    return m

def _panel(base: Image.Image, x:int,y:int,w:int,h:int, colors, corners):
    x,y,w,h = map(int,(x,y,w,h))
    left = colors[0] if isinstance(colors,(list,tuple)) and colors else colors
    right = colors[1] if (isinstance(colors,(list,tuple)) and len(colors)>1) else colors
    g = _grad_horz(w,h,left,right)
    tl,tr,br,bl = corners
    if any(corners):
        base.paste(g, (x,y), _round_mask(w,h,int(tl),int(tr),int(br),int(bl)))
    else:
        base.alpha_composite(g,(x,y))

def _circle_image(img: Optional[Image.Image], diam:int, border:int=4)->Optional[Image.Image]:
    if img is None: return None
    diam = int(diam)
    if img.mode!="RGBA": img=img.convert("RGBA")
    sc = diam / max(1,max(img.width,img.height))
    nw,nh = max(1,int(img.width*sc)), max(1,int(img.height*sc))
    rs = img.resize((nw,nh), Image.LANCZOS)
    sq = Image.new("RGBA",(diam,diam),(0,0,0,0))
    sq.alpha_composite(rs, ((diam-nw)//2, (diam-nh)//2))
    m = Image.new("L",(diam,diam),0); d=ImageDraw.Draw(m)
    d.ellipse([0,0,diam,diam], fill=255)
    circ = Image.new("RGBA",(diam,diam),(0,0,0,0))
    circ.paste(sq,(0,0),m)
    if border>0:
        d2=ImageDraw.Draw(circ)
        bb=border/2
        d2.ellipse([bb,bb,diam-bb,diam-bb], outline=WHITE, width=int(border))
    return circ

def _logo_in_white_circle(base: Image.Image, logo: Optional[Image.Image], cx:int,cy:int,diam:int):
    cx,cy,diam = int(cx),int(cy),int(diam)
    # white base
    disk = Image.new("RGBA",(diam,diam),(0,0,0,0))
    d=ImageDraw.Draw(disk)
    d.ellipse([0,0,diam,diam], fill=WHITE)
    base.alpha_composite(disk, (cx-diam//2, cy-diam//2))
    if logo is None: return
    l = logo.convert("RGBA")
    pad = int(diam*0.14)
    tw,th = diam-pad*2, diam-pad*2
    sc = min(tw/max(1,l.width), th/max(1,l.height))
    nw,nh = max(1,int(l.width*sc)), max(1,int(l.height*sc))
    l = l.resize((nw,nh), Image.LANCZOS)
    base.alpha_composite(l, (cx-nw//2, cy-nh//2))

def _wrap(draw: ImageDraw.ImageDraw, text:str, font:ImageFont.ImageFont, max_w:int)->List[str]:
    words = (text or "").split()
    if not words: return []
    lines=[]; cur=[]
    for w in words:
        test=" ".join(cur+[w])
        if _text_size(test, font)[0] <= max_w or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur)); cur=[w]
    if cur: lines.append(" ".join(cur))
    return lines

def _draw_stats(draw: ImageDraw.ImageDraw, x:int, y_center:int, stats:List[Tuple[str,str]],
                f_val:ImageFont.ImageFont, f_lab:ImageFont.ImageFont, max_w:int, gap:int=28):
    pairs=[]
    for val,lbl in stats:
        vw,vh=_text_size(val,f_val)
        lw,lh=_text_size(lbl,f_lab)
        w=max(vw,lw); h=vh+6+lh
        pairs.append((val,lbl,vw,vh,lw,lh,w,h))
    if not pairs: return
    total = sum(p[6] for p in pairs)+gap*(len(pairs)-1)
    if total>max_w:
        vs=f_val.size
        while total>max_w and vs>10:
            vs-=2
            f_val=_font(vs, True)
            f_lab=_font(max(10,vs-12), False)
            pairs=[]
            for val,lbl in stats:
                vw,vh=_text_size(val,f_val)
                lw,lh=_text_size(lbl,f_lab)
                w=max(vw,lw); h=vh+6+lh
                pairs.append((val,lbl,vw,vh,lw,lh,w,h))
            total = sum(p[6] for p in pairs)+gap*(len(pairs)-1)
    cur_x=int(x)
    y_top=int(y_center - max(p[7] for p in pairs)/2)
    for val,lbl,vw,vh,lw,lh,w,h in pairs:
        vx=cur_x+(w-vw)//2; vy=y_top
        draw.text((vx,vy), val, font=f_val, fill=WHITE)
        lx=cur_x+(w-lw)//2; ly=vy+vh+6
        draw.text((lx,ly), lbl, font=f_lab, fill=WHITE)
        cur_x += w+gap

def _fit_name(draw, name:str, base:int, max_w:int, delta:int):
    sz=int(base)
    while sz>14:
        f=_font(sz, True)
        if _text_size(name,f)[0] <= max_w: break
        sz-=2
    f_name=_font(sz, True)
    f_val=_font(max(10, sz-delta), True)
    f_lab=_font(max(10, f_val.size-12), False)
    return f_name,f_val,f_lab

# ---------- /card ----------
# signature must match calls from api/telegram.py:
# render_card("single", name_ru, team_name_ru, logo_img, colors, head_img, stats, **kwargs)
def render_card(_preset: str, name_ru: str, _team_ru: str,
                logo_img: Optional[Image.Image], colors,
                head_img: Optional[Image.Image], stats: List[Tuple[str,str]], **_kw) -> bytes:
    base = Image.new("RGBA",(CANVAS_W,CANVAS_H),(0,0,0,0))
    draw = ImageDraw.Draw(base)

    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H

    # width adaptive but bounded
    min_w, max_w = 980, 1320
    f_probe=_font(68, True)
    nmw,_ = _text_size(name_ru, f_probe)
    est_stats_w = 560 if stats else 0
    panel_w = max(min_w, min(max_w, nmw + 380 + est_stats_w))

    # gradient panel with right-only rounding
    _panel(base, x, y, panel_w, CARD_H, colors, (0, RADIUS_RIGHT, RADIUS_RIGHT, 0))

    # logo in white disk
    cx = int(x + LOGO_DIAM/2 + 28 + LOGO_OFFSET[0])
    cy = int(y + LOGO_DIAM/2 + 28 + LOGO_OFFSET[1])
    _logo_in_white_circle(base, logo_img, cx, cy, LOGO_DIAM)

    # headshot circle
    head_c = _circle_image(head_img, HEAD_DIAM_SMALL, border=6)
    if head_c:
        hx = int(x + 24)
        hy = int(y + CARD_H - HEAD_DIAM_SMALL - 16)
        base.alpha_composite(head_c, (hx,hy))
        text_x = int(hx + HEAD_DIAM_SMALL + 28)
    else:
        text_x = int(x + 40)

    center_y = int(y + CARD_H/2)

    f_name,f_val,f_lab = _fit_name(draw, name_ru, base=68, max_w=int(panel_w - (text_x - x) - 40), delta=10)

    # name
    draw.text((text_x, int(center_y - f_name.size*0.60)), name_ru, font=f_name, fill=WHITE)

    # stats
    wname,_ = _text_size(name_ru, f_name)
    stats_x = int(text_x + wname + 32)
    stats_w = int(panel_w - (stats_x - x) - 32)
    if stats and stats_w>80:
        _draw_stats(draw, stats_x, center_y, stats, f_val, f_lab, stats_w, gap=32)

    return _to_png_bytes(base)

# ---------- /cardbad ----------
# render_card_bad(name_ru, head_img, stats, team_logo_img=logo, **kwargs)
def render_card_bad(name_ru: str, head_img: Optional[Image.Image],
                    stats: List[Tuple[str,str]], team_logo_img: Optional[Image.Image]=None, **_kw) -> bytes:
    base = Image.new("RGBA",(CANVAS_W,CANVAS_H),(0,0,0,0))
    draw = ImageDraw.Draw(base)

    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    panel_w = 1180

    _panel(base, x, y, panel_w, CARD_H, (BROWN_BAD,BROWN_BAD), (0,RADIUS_RIGHT,RADIUS_RIGHT,0))

    # headshot
    head_c = _circle_image(head_img, HEAD_DIAM_SMALL, border=6)
    if head_c:
        hx=int(x+24); hy=int(y + CARD_H - HEAD_DIAM_SMALL - 16)
        base.alpha_composite(head_c,(hx,hy))
        text_x = int(hx + HEAD_DIAM_SMALL + 28)
    else:
        text_x = int(x + 40)

    center_y = int(y + CARD_H/2)

    f_name=_font(68, True)
    draw.text((text_x, int(center_y - f_name.size*0.60)), name_ru, font=f_name, fill=WHITE)
    wname,_ = _text_size(name_ru, f_name)

    # 💩
    poop_x = int(text_x + wname + 16)
    poop_size = int(max(28, f_name.size*0.9))
    if POOP_ICON_PATH and os.path.exists(POOP_ICON_PATH):
        try:
            po = Image.open(POOP_ICON_PATH).convert("RGBA")
            sc = poop_size / max(1, po.height)
            po = po.resize((max(1,int(po.width*sc)), poop_size), Image.LANCZOS)
            base.alpha_composite(po, (poop_x, int(center_y + f_name.size*0.60) - po.height))
        except Exception:
            draw.text((poop_x, int(center_y - f_name.size*0.60)), "💩", font=_font(poop_size, False), fill=WHITE)
    else:
        draw.text((poop_x, int(center_y - f_name.size*0.60)), "💩", font=_font(poop_size, False), fill=WHITE)

    # stats
    stats_x = int(poop_x + 56)
    stats_w = int(panel_w - (stats_x - x) - 32)
    if stats and stats_w>80:
        f_val=_font(56, True)
        f_lab=_font(42, False)
        _draw_stats(draw, stats_x, center_y, stats, f_val, f_lab, stats_w, gap=28)

    # optional team logo in white disk (top-left offset)
    if team_logo_img:
        cx = int(x + LOGO_DIAM/2 + 26 + LOGO_OFFSET[0])
        cy = int(y + LOGO_DIAM/2 + 26 + LOGO_OFFSET[1])
        _logo_in_white_circle(base, team_logo_img, cx, cy, LOGO_DIAM)

    return _to_png_bytes(base)

# ---------- /cards (left + right note) ----------
# render_card_special(name_ru, logo_img, colors, head_img, stats, right_text, **kwargs)
def render_card_special(name_ru: str, logo_img: Optional[Image.Image], colors,
                        head_img: Optional[Image.Image], stats: List[Tuple[str,str]],
                        right_text: str, **_kw) -> bytes:
    base = Image.new("RGBA",(CANVAS_W,CANVAS_H),(0,0,0,0))
    draw = ImageDraw.Draw(base)

    # left panel
    x = MARGIN
    y = CANVAS_H - MARGIN - CARD_H
    left_w = 1120
    _panel(base, x, y, left_w, CARD_H, colors, (0,RADIUS_RIGHT,RADIUS_RIGHT,0))

    # logo
    cx = int(x + LOGO_DIAM/2 + 26 + LOGO_OFFSET[0])
    cy = int(y + LOGO_DIAM/2 + 26 + LOGO_OFFSET[1])
    _logo_in_white_circle(base, logo_img, cx, cy, LOGO_DIAM)

    # headshot
    head_c = _circle_image(head_img, HEAD_DIAM_SMALL, border=6)
    if head_c:
        hx=int(x+24); hy=int(y + CARD_H - HEAD_DIAM_SMALL - 16)
        base.alpha_composite(head_c,(hx,hy))
        text_x = int(hx + HEAD_DIAM_SMALL + 28)
    else:
        text_x = int(x + 40)

    center_y = int(y + CARD_H/2)

    f_name,f_val,f_lab = _fit_name(draw, name_ru, base=66, max_w=int(left_w - (text_x - x) - 36), delta=10)
    draw.text((text_x, int(center_y - f_name.size*0.60)), name_ru, font=f_name, fill=WHITE)
    wname,_ = _text_size(name_ru, f_name)

    stats_x = int(text_x + wname + 28)
    stats_w = int(left_w - (stats_x - x) - 28)
    if stats and stats_w>80:
        _draw_stats(draw, stats_x, center_y, stats, f_val, f_lab, stats_w, gap=28)

    # right panel (semi transparent black, both rounded)
    rx = int(x + left_w + 10)
    ry = int(CANVAS_H - MARGIN - CARDS_RH)
    right_w = 520
    m = _round_mask(right_w, CARDS_RH, RADIUS_BOTH, RADIUS_BOTH, RADIUS_BOTH, RADIUS_BOTH)
    right = Image.new("RGBA",(right_w,CARDS_RH),SEMI_BLACK)
    base.paste(right,(rx,ry),m)

    # right text (⭐ + wrap + extra bottom empty line)
    pad = 24
    f_r = _font(36, True)
    txt = ("⭐ " + (right_text or "").strip()).strip()
    lines = _wrap(draw, txt, f_r, max_w=right_w - pad*2)
    if not lines: lines = ["⭐"]
    lines.append("")  # bottom padding line

    line_h = max(30, int(f_r.size*1.18))
    total_h = line_h*len(lines)
    top = int(ry + (CARDS_RH - total_h)//2)
    for i,ln in enumerate(lines):
        draw.text((rx+pad, top + i*line_h), ln, font=f_r, fill=WHITE)

    return _to_png_bytes(base)

# ---------- /card2 (full width bottom) ----------
# render_card2(ruA, logoA, colorsA, headA, statsA, ruB, logoB, colorsB, headB, statsB, **kwargs)
def render_card2(ruA: str, logoA: Optional[Image.Image], colorsA,
                 headA: Optional[Image.Image], statsA: List[Tuple[str,str]],
                 ruB: str, logoB: Optional[Image.Image], colorsB,
                 headB: Optional[Image.Image], statsB: List[Tuple[str,str]], **_kw) -> bytes:
    base = Image.new("RGBA",(CANVAS_W,CANVAS_H),(0,0,0,0))
    draw = ImageDraw.Draw(base)

    y = CANVAS_H - MARGIN - CARD2_H
    x = 0; w = CANVAS_W; h = CARD2_H
    half = int(w//2)

    # two half gradients, no rounding, pinned to bottom
    _panel(base, x, y, half, h, colorsA, (0,0,0,0))
    _panel(base, x+half, y, half, h, colorsB, (0,0,0,0))

    # logos (white disks)
    cxA = int(x + half*0.03 + LOGO_DIAM/2 + LOGO_OFFSET[0])
    cyA = int(y + LOGO_DIAM/2 + 20 + LOGO_OFFSET[1])
    _logo_in_white_circle(base, logoA, cxA, cyA, LOGO_DIAM)

    cxB = int(x + half + half*0.03 + LOGO_DIAM/2 + LOGO_OFFSET[0])
    cyB = int(y + LOGO_DIAM/2 + 20 + LOGO_OFFSET[1])
    _logo_in_white_circle(base, logoB, cxB, cyB, LOGO_DIAM)

    # heads (fixed positions)
    headA_c = _circle_image(headA, HEAD_DIAM_CARD2, border=6)
    headB_c = _circle_image(headB, HEAD_DIAM_CARD2, border=6)
    if headA_c:
        hxA=int(x + half*0.10); hyA=int(y + h - HEAD_DIAM_CARD2 - 14)
        base.alpha_composite(headA_c,(hxA,hyA))
    if headB_c:
        hxB=int(x + w - half*0.10 - HEAD_DIAM_CARD2); hyB=int(y + h - HEAD_DIAM_CARD2 - 14)
        base.alpha_composite(headB_c,(hxB,hyB))

    pad = 24
    center_y = int(y + h/2)

    left_x  = int(x + half*0.10 + (HEAD_DIAM_CARD2 if headA_c else 0) + 28)
    left_w  = int(half - (left_x - x) - pad)
    right_x = int(x + half + half*0.10 + (HEAD_DIAM_CARD2 if headB_c else 0) + 28)
    right_w = int(half - (right_x - (x+half)) - pad)

    # name must be 2pt bigger than stats
    fA_name,fA_val,fA_lab = _fit_name(draw, ruA, base=76, max_w=left_w,  delta=2)
    fB_name,fB_val,fB_lab = _fit_name(draw, ruB, base=76, max_w=right_w, delta=2)
    # ensure name >= stats
    if fA_val.size > fA_name.size-2: fA_val=_font(max(10, fA_name.size-2), True)
    if fB_val.size > fB_name.size-2: fB_val=_font(max(10, fB_name.size-2), True)
    fA_lab=_font(max(10, fA_val.size-12), False)
    fB_lab=_font(max(10, fB_val.size-12), False)

    draw.text((left_x,  int(center_y - fA_name.size*0.60)),  ruA, font=fA_name, fill=WHITE)
    draw.text((right_x, int(center_y - fB_name.size*0.60)),  ruB, font=fB_name, fill=WHITE)

    wA,_ = _text_size(ruA, fA_name); sAx = int(left_x + wA + 28); sAw = int(left_w - wA - 28)
    wB,_ = _text_size(ruB, fB_name); sBx = int(right_x + wB + 28); sBw = int(right_w - wB - 28)

    if statsA and sAw>80: _draw_stats(draw, sAx, center_y, statsA, fA_val, fA_lab, sAw, gap=24)
    if statsB and sBw>80: _draw_stats(draw, sBx, center_y, statsB, fB_val, fB_lab, sBw, gap=24)

    return _to_png_bytes(base)
