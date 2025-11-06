# graphics.py — компактные плашки, уменьшенные шрифты, BAD с какашкой, card2-логотип сдвинут
from __future__ import annotations
from typing import List, Tuple, Optional, Dict, Any
from PIL import Image, ImageDraw, ImageFont
import io, os, json, re

# Холст
W, H = 1920, 1080

# Панель — стала ниже
BAR_H          = 220
PAD_L          = 56
PAD_R          = 56
TOP_IN         = 20
BOT_IN         = 18

NAME_STATS_GAP = 26
BLOCK_HGAP     = 50
INNER_VGAP     = 18

NAME_PAD_TOP    = 3
NAME_PAD_BOTTOM = 5
BLOCK_PAD_TOP   = 3
BLOCK_PAD_BOTTOM= 5

# Головы/лого — SINGLE теперь как DUO
HEAD_SIZE   = 300
DUO_HEAD    = 300
LOGO_D      = 140
LOGO_SIZE   = 124
LOGO_OFFSET_X = 18
LOGO_OFFSET_Y = 210

# Шрифты — чуть меньше
F_BOLD_PATH = "assets/fonts/Montserrat-Bold.ttf"
F_SB_PATH   = "assets/fonts/Montserrat-SemiBold.ttf"
F_EXO_PATH  = "assets/fonts/Exo2-Bold.ttf"

BASE_NAME = 54
BASE_VAL  = 46
BASE_LBL  = 24

POOP_ICON = "assets/icons/poop.png"

def _load_font(path: str, size: int):
    try: return ImageFont.truetype(path, size)
    except Exception: return ImageFont.load_default()

def _text_img(text: str, font: ImageFont.FreeTypeFont, fill=(255,255,255,255)) -> Image.Image:
    probe = Image.new("RGBA", (1,1))
    d = ImageDraw.Draw(probe)
    l,t,r,b = d.textbbox((0,0), text, font=font)
    w,h = r-l, b-t
    img = Image.new("RGBA", (max(1,w), max(1,h)), (0,0,0,0))
    ImageDraw.Draw(img).text((-l,-t), text, font=font, fill=fill)
    return img

def _pad_v(img: Image.Image, top: int, bottom: int) -> Image.Image:
    if top<=0 and bottom<=0: return img
    out = Image.new("RGBA", (img.width, img.height+max(0,top)+max(0,bottom)), (0,0,0,0))
    out.alpha_composite(img, (0, max(0,top)))
    return out

def _fit_text_to_width(text: str, font_path: str, max_w: int, max_size: int, min_size: int = 22):
    lo, hi = min_size, max_size
    probe = Image.new("RGBA", (1,1))
    d = ImageDraw.Draw(probe)
    best = _load_font(font_path, lo)
    while lo <= hi:
        mid = (lo+hi)//2
        f = _load_font(font_path, mid)
        l,t,r,b = d.textbbox((0,0), text, font=f)
        if r-l <= max_w:
            best = f; lo = mid+1
        else:
            hi = mid-1
    return _text_img(text, best), best

def _circle_crop_img(img: Image.Image, d: int) -> Image.Image:
    im = img.convert("RGBA")
    s = min(im.size)
    left = (im.width - s) // 2
    top  = max(0, im.height - s)
    im   = im.crop((left, top, left+s, top+s)).resize((d, d), Image.LANCZOS)
    mask = Image.new("L", (d,d), 0)
    ImageDraw.Draw(mask).ellipse((0,0,d,d), fill=255)
    out = Image.new("RGBA", (d,d), (0,0,0,0))
    out.paste(im, (0,0), mask)
    return out

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    DEFAULT = (0, 122, 204)
    if not isinstance(h, str): return DEFAULT
    s = h.strip()
    m = re.match(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", s)
    if m:
        r = max(0, min(255, int(m.group(1))))
        g = max(0, min(255, int(m.group(2))))
        b = max(0, min(255, int(m.group(3))))
        return (r, g, b)
    m = re.search(r'#?([0-9A-Fa-f]{6})', s)
    if not m: return DEFAULT
    hex6 = m.group(1)
    try:
        return (int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16))
    except Exception:
        return DEFAULT

def _clamp(x:int)->int: return max(0,min(255,x))
def _shade(rgb: Tuple[int,int,int], k: float) -> Tuple[int,int,int]:
    r,g,b = rgb; return (_clamp(int(r*k)), _clamp(int(g*k)), _clamp(int(b*k)))

def _rounded_horizontal_gradient(width:int, height:int, radius:int,
                                 left_rgb:Tuple[int,int,int], right_rgb:Tuple[int,int,int]) -> Image.Image:
    grad = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(grad)
    for x in range(width):
        t = x / max(1, width-1)
        r = int(left_rgb[0] + (right_rgb[0]-left_rgb[0])*t)
        g = int(left_rgb[1] + (right_rgb[1]-left_rgb[1])*t)
        b = int(left_rgb[2] + (right_rgb[2]-left_rgb[2])*t)
        draw.line([(x,0),(x,height)], fill=(r,g,b,255))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,width,height), radius, fill=255)
    out = Image.new("RGBA", (width, height), (0,0,0,0))
    out.paste(grad, (0,0), mask)
    return out

def _metric_line(stats: List[Tuple[str,str]], f_val, f_lbl, color=(255,255,255,255),
                 hgap=BLOCK_HGAP, vgap=INNER_VGAP) -> Image.Image:
    blocks, total_w, max_h = [], 0, 0
    for v, lab in stats:
        v = str(v); lab = (lab or "").upper().strip()
        val_img = _text_img(v, f_val, color)
        lbl_img = _text_img(lab, f_lbl, color) if lab else Image.new("RGBA", (1,1), (0,0,0,0))
        w = max(val_img.width, lbl_img.width)
        h = val_img.height + vgap + lbl_img.height
        block = Image.new("RGBA", (w, h), (0,0,0,0))
        block.alpha_composite(val_img, ((w-val_img.width)//2, 0))
        block.alpha_composite(lbl_img, ((w-lbl_img.width)//2, val_img.height+vgap))
        block = _pad_v(block, BLOCK_PAD_TOP, BLOCK_PAD_BOTTOM)
        blocks.append(block)
        total_w += block.width
        max_h = max(max_h, block.height)
    total_w += hgap * (len(blocks)-1) if blocks else 0
    line = Image.new("RGBA", (max(1,total_w), max_h), (0,0,0,0))
    x=0
    for b in blocks:
        line.alpha_composite(b, (x, (max_h-b.height)//2))
        x += b.width + hgap
    return line

# ---------- SINGLE ----------
def render_card(template: str,
                player_name: str,
                team_name: str,
                team_logo_img: Optional[Image.Image],
                team_colors: Tuple[str,str,str],
                head_img: Image.Image,
                stats: List[Tuple[str,str]],
                note: Optional[str] = None) -> bytes:
    primary_hex, dark_hex, light_hex = team_colors
    primary_rgb = _hex_to_rgb(primary_hex)
    left_rgb  = _shade(primary_rgb, 0.65)
    right_rgb = primary_rgb

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    head = _circle_crop_img(head_img, HEAD_SIZE)

    name_area_x = PAD_L + HEAD_SIZE + 32
    name_max_w  = W - name_area_x - PAD_R
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, name_max_w, BASE_NAME, 24)
    name_img    = _pad_v(name_img, NAME_PAD_TOP, NAME_PAD_BOTTOM)

    f_val = _load_font(F_EXO_PATH, BASE_VAL)
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
    stats_line = _metric_line(stats, f_val, f_lbl)

    avail_h_for_stats = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
    if avail_h_for_stats < 1: avail_h_for_stats = 1
    if stats_line.height > avail_h_for_stats:
        k = avail_h_for_stats / stats_line.height
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)

    bar_y = H - BAR_H
    content_right = max(name_area_x + name_img.width, name_area_x + stats_line.width)
    bar_w = min(W, content_right + PAD_R)

    panel = _rounded_horizontal_gradient(bar_w, BAR_H, 22, left_rgb, right_rgb)
    canvas.alpha_composite(panel, (0, bar_y))

    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(head, (head_x, head_y))

    if team_logo_img:
        logo_raw = team_logo_img.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
        shadow = Image.new("RGBA", (LOGO_D+6, LOGO_D+6), (0,0,0,0))
        sh_mask = Image.new("L", (LOGO_D+6, LOGO_D+6), 0)
        ImageDraw.Draw(sh_mask).ellipse((3,3,LOGO_D+3,LOGO_D+3), fill=90)
        shadow.putalpha(sh_mask)
        logo_circle = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
        mask = Image.new("L", (LOGO_D, LOGO_D), 0)
        ImageDraw.Draw(mask).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
        logo_circle.putalpha(mask)
        logo_circle.alpha_composite(logo_raw, ((LOGO_D-LOGO_SIZE)//2, (LOGO_D-LOGO_SIZE)//2))
        logo_x = head_x + LOGO_OFFSET_X
        logo_y = head_y + LOGO_OFFSET_Y
        canvas.alpha_composite(shadow, (logo_x-3, logo_y-3))
        canvas.alpha_composite(logo_circle, (logo_x, logo_y))

    name_x = name_area_x
    name_y = bar_y + TOP_IN
    canvas.alpha_composite(name_img, (name_x, name_y))

    if template == "impact":
        try:
            star_img = Image.open("assets/icons/star.png").convert("RGBA").resize((52,52), Image.LANCZOS)
            canvas.alpha_composite(star_img, (name_x + name_img.width + 12, name_y - 2))
            tag_img, _ = _fit_text_to_width("ДЕЛАЕТ РАЗНИЦУ", F_SB_PATH, 240, 36, 20)
            canvas.alpha_composite(tag_img, (name_x + name_img.width + 12 + 52 + 8, name_y + 2))
        except Exception:
            pass

    stats_x = name_x
    stats_y = name_y + name_img.height + NAME_STATS_GAP
    canvas.alpha_composite(stats_line, (stats_x, stats_y))

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# ---------- DUO ----------
def render_card2(
    player1_name: str, team1_logo: Optional[Image.Image], colors1: Tuple[str,str,str], head1: Image.Image, stats1: List[Tuple[str,str]],
    player2_name: str, team2_logo: Optional[Image.Image], colors2: Tuple[str,str,str], head2: Image.Image, stats2: List[Tuple[str,str]]
) -> bytes:
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    w_half = W//2
    for side, player_name, team_logo, colors, head_img, stats in [
        ("left",  player1_name, team1_logo, colors1, head1, stats1),
        ("right", player2_name, team2_logo, colors2, head2, stats2),
    ]:
        primary = colors[0]
        rgb = _hex_to_rgb(primary)
        left_rgb, right_rgb = _shade(rgb, 0.65), rgb
        bar_y = H - BAR_H
        x0 = 0 if side=="left" else w_half
        panel = _rounded_horizontal_gradient(w_half, BAR_H, 22, left_rgb, right_rgb)
        canvas.alpha_composite(panel, (x0, bar_y))

        head = _circle_crop_img(head_img, DUO_HEAD)
        head_x = x0 + 32
        head_y = bar_y - head.height//3
        canvas.alpha_composite(head, (head_x, head_y))

        if team_logo:
            logo_raw = team_logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
            shadow = Image.new("RGBA", (LOGO_D+6, LOGO_D+6), (0,0,0,0))
            sh_mask = Image.new("L", (LOGO_D+6, LOGO_D+6), 0)
            ImageDraw.Draw(sh_mask).ellipse((3,3,LOGO_D+3,LOGO_D+3), fill=90)
            shadow.putalpha(sh_mask)
            logo_circle = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
            mask = Image.new("L", (LOGO_D, LOGO_D), 0)
            ImageDraw.Draw(mask).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
            logo_circle.putalpha(mask)
            logo_circle.alpha_composite(logo_raw, ((LOGO_D-LOGO_SIZE)//2, (LOGO_D-LOGO_SIZE)//2))
            # Сдвиг: выше и левее на 20px
            logo_x = head_x + LOGO_OFFSET_X - 20
            logo_y = head_y + LOGO_OFFSET_Y - 20
            canvas.alpha_composite(shadow, (logo_x-3, logo_y-3))
            canvas.alpha_composite(logo_circle, (logo_x, logo_y))

        name_area_x = head_x + DUO_HEAD + 24
        max_w = x0 + w_half - name_area_x - 24
        name_img, f_name = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, max_w, BASE_NAME, 22)
        f_val = _load_font(F_EXO_PATH, BASE_VAL)
        f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
        stats_line = _metric_line(stats, f_val, f_lbl)
        avail_h = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
        if avail_h < 1: avail_h = 1
        if stats_line.height > avail_h:
            k = avail_h / stats_line.height
            stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)

        name_y = H - BAR_H + TOP_IN
        canvas.alpha_composite(name_img, (name_area_x, name_y))
        stats_y = name_y + name_img.height + NAME_STATS_GAP
        canvas.alpha_composite(stats_line, (name_area_x, stats_y))

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# ---------- BAD ----------
def render_card_bad(player_name: str, head_img: Image.Image, stats: List[Tuple[str,str]]) -> bytes:
    primary = "#6D4C41"  # коричневый
    rgb = _hex_to_rgb(primary)
    left_rgb, right_rgb = _shade(rgb, 0.7), rgb

    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    head = _circle_crop_img(head_img, HEAD_SIZE)
    bar_y = H - BAR_H
    panel = _rounded_horizontal_gradient(W, BAR_H, 22, left_rgb, right_rgb)
    canvas.alpha_composite(panel, (0, bar_y))

    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(head, (head_x, head_y))

    name_area_x = PAD_L + HEAD_SIZE + 32
    max_w = W - name_area_x - PAD_R
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, max_w, BASE_NAME, 24)
    name_img = _pad_v(name_img, NAME_PAD_TOP, NAME_PAD_BOTTOM)

    # имя + какашка
    canvas.alpha_composite(name_img, (name_area_x, bar_y + TOP_IN))
    try:
        poop = Image.open(POOP_ICON).convert("RGBA").resize((52,52), Image.LANCZOS)
        px = name_area_x + name_img.width + 10
        py = bar_y + TOP_IN - 2
        canvas.alpha_composite(poop, (px, py))
    except Exception:
        pass

    f_val = _load_font(F_EXO_PATH, BASE_VAL)
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
    stats_line = _metric_line(stats, f_val, f_lbl)
    avail_h = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
    if stats_line.height > avail_h:
        k = max(0.2, avail_h / stats_line.height)
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)
    canvas.alpha_composite(stats_line, (name_area_x, bar_y + TOP_IN + name_img.height + NAME_STATS_GAP))

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# ---------- DR (detect layout) ----------
_GUIDE_COLORS = {
    "name": (255,255,0,255),
    "head": (255,0,255,255),
    "logo": (0,255,255,255),
    "stat": (0,255,0,255),
}

def _bbox_for_color(img: Image.Image, target: Tuple[int,int,int,int]) -> Optional[Tuple[int,int,int,int]]:
    px = img.convert("RGBA").load()
    w,h = img.size
    x0,y0,x1,y1 = w, h, -1, -1
    tr, tg, tb, ta = target
    for y in range(h):
        for x in range(w):
            r,g,b,a = px[x,y]
            if r==tr and g==tg and b==tb and a>=200:
                if x < x0: x0 = x
                if y < y0: y0 = y
                if x > x1: x1 = x
                if y > y1: y1 = y
    if x1 >= x0 and y1 >= y0:
        return (x0,y0,x1,y1)
    return None

def _all_bboxes_for_color(img: Image.Image, target: Tuple[int,int,int,int]) -> List[Tuple[int,int,int,int]]:
    im = img.convert("RGBA")
    w,h = im.size
    px = im.load()
    tr,tg,tb,ta = target
    vis = [[False]*w for _ in range(h)]
    boxes: List[Tuple[int,int,int,int]] = []
    for y in range(h):
        for x in range(w):
            if vis[y][x]: continue
            r,g,b,a = px[x,y]
            if r==tr and g==tg and b==tb and a>=200:
                q=[(x,y)]
                vis[y][x]=True
                minx,miny,maxx,maxy = x,y,x,y
                while q:
                    cx,cy = q.pop()
                    if cx<minx: minx=cx
                    if cy<miny: miny=cy
                    if cx>maxx: maxx=cx
                    if cy>maxy: maxy=cy
                    for nx,ny in ((cx-1,cy),(cx+1,cy),(cx,cy-1),(cx,cy+1)):
                        if 0<=nx<w and 0<=ny<h and not vis[ny][nx]:
                            rr,gg,bb,aa = px[nx,ny]
                            if rr==tr and gg==tg and bb==tb and aa>=200:
                                vis[ny][nx]=True
                                q.append((nx,ny))
                boxes.append((minx,miny,maxx,maxy))
    return boxes

def _load_json_layout(path: str) -> Optional[Dict[str,Any]]:
    if not os.path.exists(path): return None
    try:
        with open(path,"r",encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else None
    except Exception:
        return None

def _detect_layout_from_guides(guides_path: str) -> Optional[Dict[str,Any]]:
    try:
        im = Image.open(guides_path).convert("RGBA")
    except Exception:
        return None
    layout: Dict[str,Any] = {}
    bb_name = _bbox_for_color(im, _GUIDE_COLORS["name"])
    if bb_name:
        x0,y0,x1,y1 = bb_name
        layout["name"] = {"x": x0, "y": y0, "max_w": x1-x0+1, "align": "left"}
    bb_head = _bbox_for_color(im, _GUIDE_COLORS["head"])
    if bb_head:
        x0,y0,x1,y1 = bb_head
        cx = (x0+x1)//2; cy=(y0+y1)//2
        d = min(x1-x0+1, y1-y0+1)
        layout["head"] = {"cx": cx, "cy": cy, "d": d}
    bb_logo = _bbox_for_color(im, _GUIDE_COLORS["logo"])
    if bb_logo:
        x0,y0,x1,y1=bb_logo
        size = min(x1-x0+1, y1-y0+1)
        layout["logo"] = {"x": x0, "y": y0, "size": size}
    stats_boxes = _all_bboxes_for_color(im, _GUIDE_COLORS["stat"])
    if stats_boxes:
        stats_boxes.sort(key=lambda b: ( (b[1]//50), b[0] ))
        layout["stats"] = [{"x":b[0], "y":b[1], "max_w": b[2]-b[0]+1, "align":"center"} for b in stats_boxes]
    return layout if layout else None

def _load_dr_layout(n: int) -> Tuple[Optional[Image.Image], Optional[Dict[str,Any]]]:
    base_png = os.path.join(os.getenv("DR_TEMPLATES_DIR", "assets/templates"), f"dr{n}.png")
    guides_png = os.path.join(os.getenv("DR_TEMPLATES_DIR", "assets/templates"), f"dr{n}_guides.png")
    layout_json = os.path.join(os.getenv("DR_TEMPLATES_DIR", "assets/templates"), f"dr{n}.json")

    base_img = None
    if os.path.exists(base_png):
        try: base_img = Image.open(base_png).convert("RGBA").resize((W,H), Image.LANCZOS)
        except Exception: base_img = None

    layout = _load_json_layout(layout_json)
    if layout: return base_img, layout

    if os.path.exists(guides_png):
        layout = _detect_layout_from_guides(guides_png)
        if layout: return base_img, layout

    return base_img, None

def _draw_name(canvas: Image.Image, text: str, slot: Dict[str,Any]):
    x = int(slot.get("x", 0)); y = int(slot.get("y", 0))
    max_w = int(slot.get("max_w", 600))
    align = slot.get("align","left")
    name_img, _ = _fit_text_to_width(text, F_BOLD_PATH, max_w, 84, 24)
    if align == "center":
        x = x + (max_w - name_img.width)//2
    elif align == "right":
        x = x + (max_w - name_img.width)
    canvas.alpha_composite(name_img, (x, y))

def _draw_head(canvas: Image.Image, head_img: Image.Image, slot: Dict[str,Any]):
    d = int(slot.get("d", HEAD_SIZE))
    cx = int(slot.get("cx", PAD_L + d//2))
    cy = int(slot.get("cy", H - BAR_H - d//3))
    head = _circle_crop_img(head_img, d)
    canvas.alpha_composite(head, (cx - d//2, cy - d//2))

def _draw_logo(canvas: Image.Image, logo_img: Optional[Image.Image], slot: Dict[str,Any]):
    if not logo_img: return
    size = int(slot.get("size", LOGO_SIZE))
    x = int(slot.get("x", PAD_L + LOGO_OFFSET_X))
    y = int(slot.get("y", H - BAR_H - LOGO_OFFSET_Y))
    logo_raw = logo_img.resize((size, size), Image.LANCZOS)
    d = size + 16
    shadow = Image.new("RGBA", (d+6, d+6), (0,0,0,0))
    sh_mask = Image.new("L", (d+6, d+6), 0)
    ImageDraw.Draw(sh_mask).ellipse((3,3,d+3,d+3), fill=90)
    shadow.putalpha(sh_mask)
    circle = Image.new("RGBA", (d, d), (255,255,255,255))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0,0,d,d), fill=255)
    circle.putalpha(mask)
    circle.alpha_composite(logo_raw, ((d - size)//2, (d - size)//2))
    canvas.alpha_composite(shadow, (x-3, y-3))
    canvas.alpha_composite(circle, (x, y))

def _draw_stat_cell(canvas: Image.Image, v: str, lab: str, slot: Dict[str,Any]):
    max_w = int(slot.get("max_w", 320))
    align = slot.get("align","center")
    val_sz = int(slot.get("val_size", 50))
    lbl_sz = int(slot.get("lbl_size", 24))
    f_val = _load_font(F_EXO_PATH, val_sz)
    f_lbl = _load_font(F_SB_PATH,  lbl_sz)

    val_img = _text_img(str(v), f_val, (255,255,255,255))
    lbl_img = _text_img((lab or "").upper(), f_lbl, (255,255,255,220)) if lab else None
    w = max(val_img.width, lbl_img.width if lbl_img else 0)
    if w > max_w:
        val_img, _ = _fit_text_to_width(str(v), F_EXO_PATH, max_w, val_sz, 18)
        if lbl_img:
            lbl_img, _ = _fit_text_to_width((lab or "").upper(), F_SB_PATH, max_w, lbl_sz, 14)
        w = max(val_img.width, lbl_img.width if lbl_img else 0)
    total_h = val_img.height + (INNER_VGAP//2) + (lbl_img.height if lbl_img else 0)
    img = Image.new("RGBA", (w, total_h), (0,0,0,0))
    img.alpha_composite(val_img, ((w - val_img.width)//2, 0))
    if lbl_img:
        img.alpha_composite(lbl_img, ((w - lbl_img.width)//2, val_img.height + (INNER_VGAP//2)))

    x = int(slot.get("x", 0)); y = int(slot.get("y", 0))
    if align == "center": x = x + (max_w - img.width)//2
    elif align == "right": x = x + (max_w - img.width)
    canvas.alpha_composite(img, (x, y))

def render_card_drN(
    n: int,
    player_name: str,
    head_img: Image.Image,
    logo_img: Optional[Image.Image],
    stats: List[Tuple[str,str]]
) -> bytes:
    base_img, layout = _load_dr_layout(n)
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    if base_img:
        canvas.alpha_composite(base_img, (0,0))

    if not layout:
        name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, 1200, 84, 24)
        canvas.alpha_composite(name_img, ((W-name_img.width)//2, (H-name_img.height)//2))
        bio = io.BytesIO(); canvas.save(bio, format="PNG"); return bio.getvalue()

    if "name" in layout:
        _draw_name(canvas, player_name.upper(), layout["name"])
    if "head" in layout:
        _draw_head(canvas, head_img, layout["head"])
    if "logo" in layout:
        _draw_logo(canvas, logo_img, layout["logo"])
    stat_slots = layout.get("stats") or []
    for i, slot in enumerate(stat_slots):
        v, lab = ("","")
        if i < len(stats):
            v, lab = stats[i]
        _draw_stat_cell(canvas, v, lab, slot)

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# ---------- SPECIAL ----------
def _wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> Image.Image:
    words = text.split()
    lines: List[str] = []
    cur = ""
    probe = Image.new("RGBA", (1,1)); d = ImageDraw.Draw(probe)
    for w in words:
        t = (cur + " " + w).strip()
        l,tb,r,b = d.textbbox((0,0), t, font=font)
        if r-l <= max_w:
            cur = t
        else:
            if cur: lines.append(cur); cur = w
            else:   lines.append(w); cur = ""
    if cur: lines.append(cur)
    txt = "\n".join(lines)
    l,tb,r,b = d.multiline_textbbox((0,0), txt, font=font, spacing=4)
    img = Image.new("RGBA", (max(1,r-l), max(1,b-tb)), (0,0,0,0))
    ImageDraw.Draw(img).multiline_text((0,0), txt, font=font, spacing=4, fill=(255,255,255,255))
    return img

def render_card_special(
    player_name: str,
    team_logo_img: Optional[Image.Image],
    team_colors: Tuple[str,str,str],
    head_img: Image.Image,
    stats: List[Tuple[str,str]],
    info_text: str
) -> bytes:
    primary_hex, dark_hex, light_hex = team_colors
    rgb = _hex_to_rgb(primary_hex)
    left_rgb, right_rgb = _shade(rgb, 0.65), rgb

    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    bar_y = H - BAR_H
    panel = _rounded_horizontal_gradient(W, BAR_H, 22, left_rgb, right_rgb)
    canvas.alpha_composite(panel, (0, bar_y))

    head = _circle_crop_img(head_img, HEAD_SIZE)
    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(head, (head_x, head_y))

    if team_logo_img:
        logo_raw = team_logo_img.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
        shadow = Image.new("RGBA", (LOGO_D+6, LOGO_D+6), (0,0,0,0))
        sh_mask = Image.new("L", (LOGO_D+6, LOGO_D+6), 0)
        ImageDraw.Draw(sh_mask).ellipse((3,3,LOGO_D+3,LOGO_D+3), fill=90)
        shadow.putalpha(sh_mask)
        logo_circle = Image.new("RGBA", (LOGO_D, LOGO_D), (255,255,255,255))
        mask = Image.new("L", (LOGO_D, LOGO_D), 0)
        ImageDraw.Draw(mask).ellipse((0,0,LOGO_D,LOGO_D), fill=255)
        logo_circle.putalpha(mask)
        logo_circle.alpha_composite(logo_raw, ((LOGO_D-LOGO_SIZE)//2, (LOGO_D-LOGO_SIZE)//2))
        logo_x = head_x + LOGO_OFFSET_X
        logo_y = head_y + LOGO_OFFSET_Y
        canvas.alpha_composite(shadow, (logo_x-3, logo_y-3))
        canvas.alpha_composite(logo_circle, (logo_x, logo_y))

    name_area_x = PAD_L + HEAD_SIZE + 32
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, W - name_area_x - PAD_R - 520 - 10, BASE_NAME, 24)
    name_img = _pad_v(name_img, NAME_PAD_TOP, NAME_PAD_BOTTOM)
    name_y = bar_y + TOP_IN
    canvas.alpha_composite(name_img, (name_area_x, name_y))

    f_val = _load_font(F_EXO_PATH, BASE_VAL)
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
    stats_line = _metric_line(stats, f_val, f_lbl)
    avail_h = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
    if stats_line.height > avail_h:
        k = avail_h / stats_line.height
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)
    stats_y = name_y + name_img.height + NAME_STATS_GAP
    canvas.alpha_composite(stats_line, (name_area_x, stats_y))

    info_w = 520
    info_x = W - PAD_R - info_w
    info_y = bar_y
    info_panel = _rounded_horizontal_gradient(info_w, BAR_H, 22, left_rgb, right_rgb)
    canvas.alpha_composite(info_panel, (info_x, info_y))

    # размер текста близок к цифрам статистики
    f_info = _load_font(F_SB_PATH, 44)
    wrapped = _wrap_text_to_width(info_text, f_info, info_w - 32)
    canvas.alpha_composite(wrapped, (info_x + 16, info_y + TOP_IN))

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# --- Back-compat alias for older code paths ---
# Старый telegram.py может ожидать функцию render_card_dr(...)
# Делаем простой прокси на render_card_drN с первым аргументом n.
def render_card_dr(n, player_name, head_img, logo_img, stats):
    return render_card_drN(n, player_name, head_img, logo_img, stats)

