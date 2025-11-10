# graphics.py — 1920x1080, bottom-anchored cards, Cyrillic fonts, gradients
from __future__ import annotations
import os, io, math
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
BAR_H_SINGLE = 220          # /card, /cardbad
BAR_H_DUO = 220             # /card2
BAR_H_SPECIAL = 240         # /cards (с правой колонкой)
PAD = 32

# --------- шрифты ---------
_DEF_FONT = None
_DEF_FONT_B = None

def _font_path_candidates():
    # можно переопределить путём через ENV
    p1 = os.getenv("FONT_PATH_PRIMARY")
    p2 = os.getenv("FONT_PATH_SECONDARY")
    cands = []
    for p in (p1, p2):
        if p and os.path.exists(p): cands.append(p)
    # несколько распространённых кириллических
    cands += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/ttf-dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    return cands

def _load_fonts():
    global _DEF_FONT, _DEF_FONT_B
    # по умолчанию — DejaVuSans (крилица)
    reg_path = None
    bold_path = None
    # если заданы FONT_PATH_* — берём их
    fp = os.getenv("FONT_PATH_PRIMARY")
    fb = os.getenv("FONT_PATH_BOLD") or os.getenv("FONT_PATH_SECONDARY")
    if fp and os.path.exists(fp): reg_path = fp
    if fb and os.path.exists(fb): bold_path = fb
    # иначе ищем среди кандидатов
    if not reg_path or not os.path.exists(reg_path):
        for c in _font_path_candidates():
            if c.lower().endswith("dejavusans.ttf") and os.path.exists(c):
                reg_path = c; break
    if not bold_path or not os.path.exists(bold_path):
        for c in _font_path_candidates():
            lc = c.lower()
            if ("dejavusans-bold.ttf" in lc or "dejavusans.ttf" in lc) and os.path.exists(c):
                bold_path = c; break
    # финально
    _DEF_FONT = reg_path
    _DEF_FONT_B = bold_path or reg_path

def _font(size:int, bold:bool=False) -> ImageFont.FreeTypeFont:
    if _DEF_FONT is None: _load_fonts()
    path = _DEF_FONT_B if bold else _DEF_FONT
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        # жёсткий фолбэк
        return ImageFont.load_default()

# --------- утилиты рисования ----------
def _hex_to_rgb(hx:str)->Tuple[int,int,int]:
    hx = (hx or "#000000").strip()
    if not hx.startswith("#"): hx = "#" + hx
    hx = hx.lstrip("#")
    if len(hx)==3:
        hx = "".join([c*2 for c in hx])
    r = int(hx[0:2],16); g=int(hx[2:4],16); b=int(hx[4:6],16)
    return (r,g,b)

def _darken(c:Tuple[int,int,int], k:float=0.75)->Tuple[int,int,int]:
    return (int(c[0]*k), int(c[1]*k), int(c[2]*k))

def _h_grad(size:Tuple[int,int], c1:str, c2:Optional[str]=None) -> Image.Image:
    """Горизонтальный градиент слева направо."""
    w,h = size
    base = Image.new("RGB", (w,h), (0,0,0))
    if not c2: c2 = "#000000"
    a = _hex_to_rgb(c1); b = _hex_to_rgb(c2)
    grad = Image.new("RGB", (w,1), 0)
    dr = (b[0]-a[0])/max(1,w-1)
    dg = (b[1]-a[1])/max(1,w-1)
    db = (b[2]-a[2])/max(1,w-1)
    px = grad.load()
    r,g,bl = a
    for x in range(w):
        px[x,0] = (int(r), int(g), int(bl))
        r += dr; g += dg; bl += db
    return grad.resize((w,h))

def _paste_circle(img:Image.Image, source:Image.Image, center_xy:Tuple[int,int], radius:int, bottom_snap:bool=True, stroke:int=0, stroke_color=(255,255,255)):
    """Вклеивает source по круглой маске. Если bottom_snap=True — круг касается нижней кромки."""
    d = radius*2
    x,y = center_xy
    # координаты круга
    cx = int(x - radius)
    cy = int(y - radius)
    if bottom_snap:
        # сдвигаем так, чтобы круг касался нижней границы изображения
        cy = img.height - d - PAD

    # подготовка маски
    mask = Image.new("L", (d,d), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0,0,d-1,d-1), fill=255)

    # подготовка исходника (вписываем так, чтобы лицо было крупным)
    # увеличим до диаметра по меньшей стороне
    src = source.copy()
    # Немного увеличим масштаб, чтобы «срез по нижней кромке» выглядел как в примерах
    scale = max(d/src.width, d/src.height) * 1.15
    nw, nh = int(src.width*scale), int(src.height*scale)
    src = src.resize((nw, nh), Image.LANCZOS)
    # вырезаем центральную часть по X и нижнюю по Y
    left = max(0, (nw - d)//2)
    top = max(0, nh - d)  # упор снизу
    crop = src.crop((left, top, left + d, top + d))

    # подложка под круг (если нужен stroke = белое кольцо под логотипы)
    if stroke>0:
        ring = Image.new("RGBA", (d+stroke*2, d+stroke*2), (0,0,0,0))
        rd = ImageDraw.Draw(ring)
        rd.ellipse((0,0,d+stroke*2-1,d+stroke*2-1), fill=stroke_color+(255,))
        img.alpha_composite(ring, (cx-stroke, cy-stroke))

    img.paste(crop, (cx, cy), mask)

def _rounded_rect_mask(sz:Tuple[int,int], r:int)->Image.Image:
    w,h = sz
    m = Image.new("L", (w,h), 0)
    d = ImageDraw.Draw(m)
    if r<=0:
        d.rectangle((0,0,w,h), fill=255)
        return m
    d.rounded_rectangle((0,0,w-1,h-1), radius=r, fill=255)
    return m

def _text(img:Image.Image, xy:Tuple[int,int], text:str, size:int, bold=False, fill=(255,255,255,255)):
    d = ImageDraw.Draw(img)
    d.text(xy, text, font=_font(size, bold=bold), fill=fill)

def _textbox_center(img:Image.Image, cx:int, y:int, text:str, size:int, bold=False, fill=(255,255,255,255)):
    f = _font(size, bold=bold)
    tw, th = f.getbbox(text)[2:]
    _text(img, (int(cx - tw/2), int(y)), text, size, bold, fill)

def _stats_line(img:Image.Image, left:int, y:int, stats:List[Tuple[str,str]], per_cell:int=3, cell_w:int=220):
    """Рисуем числа крупно, подписи меньшим кеглем, сетка."""
    stats = stats or []
    stats = stats[:per_cell]
    x = int(left)
    for val,lbl in stats:
        v = str(val).strip()
        l = str(lbl or "").strip().upper()
        _textbox_center(img, x + cell_w//2, y, v, 64, bold=True)
        _textbox_center(img, x + cell_w//2, y + 64 + 8, l, 28, bold=False, fill=(255,255,255,200))
        x += cell_w

# --------- публичные рендеры ----------
def render_card(mode:str, ru_name:str, subtitle:str, team_logo_img:Optional[Image.Image],
                team_colors:Tuple[str,str,str], head_img:Image.Image, stats:List[Tuple[str,str]],
                round_all=False, no_round=False, round_left=False, round_right=True,
                left_radius=0, radius=0, radius_left=0, radius_right=16,
                name_stat_center=True, text_topmost=True) -> bytes:
    """
    /card — одиночная: плашка снизу слева, ширина ~1040, высота BAR_H_SINGLE.
    """
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    bar_w = 1040
    bar_h = BAR_H_SINGLE
    r = 20 if not no_round else 0
    bar = Image.new("RGBA", (bar_w, bar_h), (0,0,0,0))

    # фон-градиент
    c1 = team_colors[0]; c2 = team_colors[1] if team_colors and team_colors[1] else None
    if not c2: c2 = "#101010"
    grad = _h_grad((bar_w, bar_h), c1, c2)
    bar.paste(grad, (0,0))

    # скругления только справа (как было)
    mask = _rounded_rect_mask((bar_w, bar_h), r if round_right else (0 if no_round else r))
    bar.putalpha(mask)

    # размещаем плашку внизу слева
    bx = PAD; by = H - bar_h - PAD
    canvas.alpha_composite(bar, (bx, by))

    # логотип в белом кружке
    if team_logo_img is not None:
        lg_d = 92
        lg = team_logo_img.convert("RGBA").resize((int(lg_d*0.9), int(lg_d*0.9)), Image.LANCZOS)
        # белый круг
        circ = Image.new("RGBA", (lg_d, lg_d), (255,255,255,255))
        mask_l = _rounded_rect_mask((lg_d, lg_d), lg_d//2)
        circ.putalpha(mask_l)
        # внутрь — логотип
        tmp = Image.new("RGBA", (lg_d, lg_d), (0,0,0,0))
        tmp.alpha_composite(lg, (int((lg_d-lg.width)/2), int((lg_d-lg.height)/2)))
        circ = Image.alpha_composite(circ, tmp)
        canvas.alpha_composite(circ, (bx + PAD, by + bar_h - lg_d - PAD))

    # голова игрока — круг без кольца, «срез снизу»
    _paste_circle(canvas, head_img, center_xy=(bx + 220, 0), radius=88, bottom_snap=True, stroke=0)

    # имя и статы
    name_x = bx + 320
    name_y = by + 40
    _text(canvas, (name_x, name_y), (ru_name or "").upper(), 64, bold=True)
    _stats_line(canvas, name_x, by + 112, stats, per_cell=4, cell_w=200)

    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()

def render_card_bad(ru_name:str, head_img:Image.Image, stats:List[Tuple[str,str]],
                    team_logo_img:Optional[Image.Image]=None,
                    round_all=False, no_round=False, round_left=False, round_right=True,
                    left_radius=0, radius=0, radius_left=0, radius_right=16,
                    poop_larger=True, poop_lower=20, name_stat_center=True) -> bytes:
    """BAD-версия: коричневый градиент, прижат снизу слева."""
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    bar_w = 1040; bar_h = BAR_H_SINGLE
    bar = Image.new("RGBA", (bar_w, bar_h), (0,0,0,0))
    grad = _h_grad((bar_w, bar_h), "#7A4E2D", "#4E2F1B")
    bar.paste(grad, (0,0))
    r = 20 if not no_round else 0
    mask = _rounded_rect_mask((bar_w, bar_h), r if round_right else (0 if no_round else r))
    bar.putalpha(mask)
    bx = PAD; by = H - bar_h - PAD
    canvas.alpha_composite(bar, (bx, by))

    if team_logo_img is not None:
        lg_d = 92
        lg = team_logo_img.convert("RGBA").resize((int(lg_d*0.9), int(lg_d*0.9)), Image.LANCZOS)
        circ = Image.new("RGBA", (lg_d, lg_d), (255,255,255,255))
        mask_l = _rounded_rect_mask((lg_d, lg_d), lg_d//2)
        circ.putalpha(mask_l)
        tmp = Image.new("RGBA", (lg_d, lg_d), (0,0,0,0))
        tmp.alpha_composite(lg, (int((lg_d-lg.width)/2), int((lg_d-lg.height)/2)))
        circ = Image.alpha_composite(circ, tmp)
        canvas.alpha_composite(circ, (bx + PAD, by + bar_h - lg_d - PAD))

    _paste_circle(canvas, head_img, center_xy=(bx + 220, 0), radius=88, bottom_snap=True, stroke=0)
    _text(canvas, (bx + 320, by + 40), (ru_name or "").upper(), 64, bold=True)
    _stats_line(canvas, bx + 320, by + 112, stats, per_cell=4, cell_w=200)

    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()

def render_card2(ru1:str, logo1_img:Optional[Image.Image], colors1:Tuple[str,str,str], head1_img:Image.Image, stats1:List[Tuple[str,str]],
                 ru2:str, logo2_img:Optional[Image.Image], colors2:Tuple[str,str,str], head2_img:Image.Image, stats2:List[Tuple[str,str]],
                 round_all=False, no_round=True, round_left=False, round_right=False,
                 left_radius=0, right_radius=0, radius=0, radius_left=0, radius_right=0,
                 name_stat_center=True, duo_name_stat_center=True,
                 duo_autofit_names=True, duo_sync_fit=True, duo_lock_ratio=True,
                 duo_name_delta_plus=2, duo_stat_delta_minus=2, duo_min_name_vs_stat=True) -> bytes:
    """
    /card2 — широкая плашка на всю ширину, прижата снизу, две половины.
    """
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    bar_h = BAR_H_DUO
    # левая половина
    left_w = W//2
    left = Image.new("RGBA", (left_w, bar_h), (0,0,0,0))
    g1 = _h_grad((left_w, bar_h), colors1[0], colors1[1] if colors1 and colors1[1] else "#0E0E18")
    left.paste(g1, (0,0))
    # правая
    right_w = W - left_w
    right = Image.new("RGBA", (right_w, bar_h), (0,0,0,0))
    g2 = _h_grad((right_w, bar_h), colors2[0], colors2[1] if colors2 and colors2[1] else "#0B1930")
    right.paste(g2, (0,0))

    by = H - bar_h - PAD
    canvas.alpha_composite(left, (0, by))
    canvas.alpha_composite(right, (left_w, by))

    # центральный белый круг-делитель
    divider_d = 96
    divider = Image.new("RGBA", (divider_d, divider_d), (255,255,255,255))
    divider.putalpha(_rounded_rect_mask((divider_d,divider_d), divider_d//2))
    canvas.alpha_composite(divider, (int(W/2 - divider_d/2), int(by + (bar_h - divider_d)/2)))

    # логотипы в белых кружках по краям
    def _paste_logo(logo_img, x):
        if logo_img is None: return
        d = 92
        circ = Image.new("RGBA", (d,d), (255,255,255,255))
        circ.putalpha(_rounded_rect_mask((d,d), d//2))
        lg = logo_img.convert("RGBA").resize((int(d*0.9), int(d*0.9)), Image.LANCZOS)
        tmp = Image.new("RGBA", (d,d), (0,0,0,0))
        tmp.alpha_composite(lg, (int((d-lg.width)/2), int((d-lg.height)/2)))
        circ = Image.alpha_composite(circ, tmp)
        canvas.alpha_composite(circ, (int(x), int(by + bar_h - d - PAD)))
    _paste_logo(logo1_img, PAD)
    _paste_logo(logo2_img, W - PAD - 92)

    # головы — по кругу, снизу
    _paste_circle(canvas, head1_img, center_xy=(PAD + 220, 0), radius=88, bottom_snap=True)
    _paste_circle(canvas, head2_img, center_xy=(W - PAD - 220, 0), radius=88, bottom_snap=True)

    # имена и статы
    name_y = by + 36
    stats_y = by + 112
    # левая зона
    _text(canvas, (PAD + 320, name_y), (ru1 or "").upper(), 56, bold=True)
    _stats_line(canvas, PAD + 320, stats_y, stats1, per_cell=4, cell_w=190)
    # правая зона (выровняем справа)
    # для простоты — зеркальная разметка
    right_start = W - PAD - 320 - 4*190
    _text(canvas, (right_start, name_y), (ru2 or "").upper(), 56, bold=True)
    _stats_line(canvas, right_start, stats_y, stats2, per_cell=4, cell_w=190)

    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()

def render_card_special(ru_name:str, team_logo_img:Optional[Image.Image], team_colors:Tuple[str,str,str],
                        head_img:Image.Image, stats:List[Tuple[str,str]], right_text:str,
                        round_all=False, no_round=False, round_left=False, round_right=True,
                        left_radius=0, radius=0, radius_left=0, radius_right=16,
                        right_round_left=True, right_round_right=True,
                        right_radius_left=16, right_radius_right=16,
                        right_panel_half_width=True, right_panel_width_ratio=0.5,
                        right_wrap=True, text_topmost=True, right_text_pad_bottom=14,
                        name_stat_center=True) -> bytes:
    """
    /cards — правая колонка + левая плашка; всё прижато к низу; правая уже.
    """
    canvas = Image.new("RGBA", (W,H), (0,0,0,0))
    bar_h = BAR_H_SPECIAL
    # ширины
    right_w = int(W * (0.42 if right_panel_half_width else right_panel_width_ratio))
    left_w  = W - right_w - PAD*3

    # левая плашка
    left = Image.new("RGBA", (left_w, bar_h), (0,0,0,0))
    c1 = team_colors[0]; c2 = team_colors[1] if team_colors and team_colors[1] else None
    grad = _h_grad((left_w, bar_h), c1, c2 or "#141414")
    left.paste(grad, (0,0))
    left.putalpha(_rounded_rect_mask((left_w, bar_h), 20))
    by = H - bar_h - PAD
    bx = PAD
    canvas.alpha_composite(left, (bx, by))

    # правая колонка
    rx = bx + left_w + PAD
    right = Image.new("RGBA", (right_w, bar_h), (0,0,0,0))
    grad_r = _h_grad((right_w, bar_h), _darken(_hex_to_rgb(c1),0.85), _darken(_hex_to_rgb(c1),0.65))
    right_rgb = Image.new("RGB", (right_w, bar_h), (0,0,0))
    right_rgb.paste(grad_r, (0,0))
    right = right_rgb.convert("RGBA")
    right.putalpha(_rounded_rect_mask((right_w, bar_h), 20))
    canvas.alpha_composite(right, (rx, by))

    # логотип в белом круге на левой
    if team_logo_img is not None:
        d = 92
        circ = Image.new("RGBA", (d,d), (255,255,255,255))
        circ.putalpha(_rounded_rect_mask((d,d), d//2))
        lg = team_logo_img.convert("RGBA").resize((int(d*0.9), int(d*0.9)), Image.LANCZOS)
        tmp = Image.new("RGBA", (d,d), (0,0,0,0))
        tmp.alpha_composite(lg, (int((d-lg.width)/2), int((d-lg.height)/2)))
        circ = Image.alpha_composite(circ, tmp)
        canvas.alpha_composite(circ, (bx + PAD, by + bar_h - d - PAD))

    # лицо
    _paste_circle(canvas, head_img, center_xy=(bx + 220, 0), radius=88, bottom_snap=True)

    # левые тексты
    _text(canvas, (bx + 320, by + 42), (ru_name or "").upper(), 60, bold=True)
    _stats_line(canvas, bx + 320, by + 118, stats, per_cell=4, cell_w=200)

    # правый текст (многострочный, перенос)
    if right_text:
        rt = str(right_text).replace("\\n", "\n")
        lines = []
        # грубый перенос
        max_w = right_w - PAD*2
        words = rt.split()
        cur = ""
        f = _font(44, bold=True)
        for w in words:
            test = (cur + " " + w).strip()
            tw = f.getbbox(test)[2]
            if tw > max_w and cur:
                lines.append(cur); cur = w
            else:
                cur = test
        if cur: lines.append(cur)
        y = by + 36
        for i,ln in enumerate(lines[:3]):
            _text(canvas, (rx + PAD, y), ln.upper(), 44, bold=True)
            y += 52

    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()
