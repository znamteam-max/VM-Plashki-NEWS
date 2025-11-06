# graphics.py — универсальные рендеры плашек
from __future__ import annotations
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import io, textwrap

# Холст
W, H = 1920, 1080

# Панель
BAR_H          = 250
PAD_L          = 56
PAD_R          = 56
TOP_IN         = 22
BOT_IN         = 20

NAME_STATS_GAP = 30
BLOCK_HGAP     = 56
INNER_VGAP     = 20

NAME_PAD_TOP    = 4
NAME_PAD_BOTTOM = 6
BLOCK_PAD_TOP   = 4
BLOCK_PAD_BOTTOM= 6

# Головы/лого
HEAD_SIZE   = 360
DUO_HEAD    = 300
LOGO_D      = 140
LOGO_SIZE   = 124
LOGO_OFFSET_X = 18
LOGO_OFFSET_Y = 210

# Шрифты
F_BOLD_PATH = "assets/fonts/Montserrat-Bold.ttf"
F_SB_PATH   = "assets/fonts/Montserrat-SemiBold.ttf"
F_EXO_PATH  = "assets/fonts/Exo2-Bold.ttf"

def _load_font(path: str, size: int):
    try: return ImageFont.truetype(path, size)
    except Exception: return ImageFont.load_default()

BASE_NAME = 60
BASE_VAL  = 50
BASE_LBL  = 28

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

def _fit_text_to_width(text: str, font_path: str, max_w: int, max_size: int, min_size: int = 26):
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

def _hex_to_rgb(h: str) -> Tuple[int,int,int]:
    h = h.strip().lstrip("#")
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

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

    name_area_x = PAD_L + HEAD_SIZE + 36
    name_max_w  = W - name_area_x - PAD_R
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, name_max_w, BASE_NAME, 28)
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

    panel = _rounded_horizontal_gradient(bar_w, BAR_H, 24, left_rgb, right_rgb)
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
            star_img = Image.open("assets/icons/star.png").convert("RGBA").resize((56,56), Image.LANCZOS)
            canvas.alpha_composite(star_img, (name_x + name_img.width + 14, name_y - 2))
            tag_img, _ = _fit_text_to_width("ДЕЛАЕТ РАЗНИЦУ", F_SB_PATH, 260, 40, 22)
            canvas.alpha_composite(tag_img, (name_x + name_img.width + 14 + 56 + 10, name_y + 4))
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
    # половины
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
        panel = _rounded_horizontal_gradient(w_half, BAR_H, 24, left_rgb, right_rgb)
        canvas.alpha_composite(panel, (x0, bar_y))

        head = _circle_crop_img(head_img, DUO_HEAD)
        head_x = x0 + 36
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
            logo_x = head_x + LOGO_OFFSET_X
            logo_y = head_y + LOGO_OFFSET_Y
            canvas.alpha_composite(shadow, (logo_x-3, logo_y-3))
            canvas.alpha_composite(logo_circle, (logo_x, logo_y))

        # адаптивный размер имени и метрик — под ширину половины
        name_area_x = head_x + DUO_HEAD + 28
        max_w = x0 + w_half - name_area_x - 28
        # сначала найдём общий минимальный размер имени и для левой, и для правой сторон
        # (реализуем простым пересчётом внутри цикла, но одинаковая логика — ок)
        name_img, f_name = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, max_w, BASE_NAME, 24)
        f_val = _load_font(F_EXO_PATH, BASE_VAL)
        f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
        stats_line = _metric_line(stats, f_val, f_lbl)
        avail_h = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
        if avail_h < 1: avail_h = 1
        if stats_line.height > avail_h:
            k = avail_h / stats_line.height
            stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)

        name_y = bar_y + TOP_IN
        canvas.alpha_composite(name_img, (name_area_x, name_y))
        stats_y = name_y + name_img.height + NAME_STATS_GAP
        canvas.alpha_composite(stats_line, (name_area_x, stats_y))

    # выравнивание размеров имени для обеих половин:
    # упрощённо: мы уже обрезали по ширине каждой половины бинарным поиском, этого хватает визуально.

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# ---------- BAD ----------
def render_card_bad(player_name: str, head_img: Image.Image, stats: List[Tuple[str,str]]) -> bytes:
    # фиксированный «неприятный» градиент
    primary = "#6D4C41"
    rgb = _hex_to_rgb(primary)
    left_rgb, right_rgb = _shade(rgb, 0.7), rgb

    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    head = _circle_crop_img(head_img, HEAD_SIZE)
    bar_y = H - BAR_H
    panel = _rounded_horizontal_gradient(W, BAR_H, 24, left_rgb, right_rgb)
    canvas.alpha_composite(panel, (0, bar_y))

    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(head, (head_x, head_y))

    # 💩 после имени
    name_area_x = PAD_L + HEAD_SIZE + 36
    max_w = W - name_area_x - PAD_R
    name_text = f"{player_name.upper()}  💩"
    name_img, _ = _fit_text_to_width(name_text, F_BOLD_PATH, max_w, BASE_NAME, 28)
    name_img = _pad_v(name_img, NAME_PAD_TOP, NAME_PAD_BOTTOM)
    canvas.alpha_composite(name_img, (name_area_x, bar_y + TOP_IN))

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

# ---------- DR (шаблон) ----------
def render_card_dr(player_name: str,
                   head_img: Image.Image,
                   stats: List[Tuple[str,str]],
                   template_path: Optional[str] = "assets/templates/card_dr_base.png") -> bytes:
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    # фон-шаблон, если есть
    try:
        if template_path and isinstance(template_path, str) and len(template_path)>0 and \
           (template_path.endswith(".png") or template_path.endswith(".PNG")) and \
           (template_path and template_path != "none") and \
           (template_path and os.path.exists(template_path)):
            base = Image.open(template_path).convert("RGBA").resize((W,H), Image.LANCZOS)
            canvas.alpha_composite(base, (0,0))
    except Exception:
        pass

    # стандартная нижняя панель, чтобы текст был читаем
    primary = "#1E1E1E"
    left_rgb, right_rgb = (20,20,20), (34,34,34)
    bar_y = H - BAR_H
    panel = _rounded_horizontal_gradient(W, BAR_H, 24, left_rgb, right_rgb)
    canvas.alpha_composite(panel, (0, bar_y))

    head = _circle_crop_img(head_img, HEAD_SIZE)
    head_x = PAD_L
    head_y = bar_y - head.height//3
    canvas.alpha_composite(head, (head_x, head_y))

    name_area_x = PAD_L + HEAD_SIZE + 36
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, W - name_area_x - PAD_R, BASE_NAME, 28)
    name_img = _pad_v(name_img, NAME_PAD_TOP, NAME_PAD_BOTTOM)
    canvas.alpha_composite(name_img, (name_area_x, bar_y + TOP_IN))

    f_val = _load_font(F_EXO_PATH, BASE_VAL)
    f_lbl = _load_font(F_SB_PATH,  BASE_LBL)
    stats_line = _metric_line(stats, f_val, f_lbl)
    avail_h = BAR_H - TOP_IN - name_img.height - NAME_STATS_GAP - BOT_IN
    if stats_line.height > avail_h:
        k = avail_h / stats_line.height
        stats_line = stats_line.resize((max(1,int(stats_line.width*k)), max(1,int(stats_line.height*k))), Image.LANCZOS)
    canvas.alpha_composite(stats_line, (name_area_x, bar_y + TOP_IN + name_img.height + NAME_STATS_GAP))

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()

# ---------- SPECIAL (боковая вставка) ----------
def _wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> Image.Image:
    # простой перенос по словам
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
    panel = _rounded_horizontal_gradient(W, BAR_H, 24, left_rgb, right_rgb)
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

    name_area_x = PAD_L + HEAD_SIZE + 36
    name_img, _ = _fit_text_to_width(player_name.upper(), F_BOLD_PATH, W - name_area_x - PAD_R - 520 - 10, BASE_NAME, 28)
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

    # правая вставка: такая же высота, отступ 10px, скругления
    info_w = 520
    info_x = W - PAD_R - info_w
    info_y = bar_y
    info_panel = _rounded_horizontal_gradient(info_w, BAR_H, 24, left_rgb, right_rgb)
    canvas.alpha_composite(info_panel, (info_x, info_y))

    f_info = _load_font(F_SB_PATH, 30)
    wrapped = _wrap_text_to_width(info_text, f_info, info_w - 32)
    canvas.alpha_composite(wrapped, (info_x + 16, info_y + TOP_IN))

    bio = io.BytesIO()
    canvas.save(bio, format="PNG")
    return bio.getvalue()
