# graphics.py — совместимо со старыми вызовами telegram.py, возвращает PNG bytes
from __future__ import annotations
import os, io
from typing import Optional, Tuple, List, Iterable, Union
from PIL import Image, ImageDraw, ImageFont

# ====== РАЗМЕТКА / КОНСТАНТЫ =================================================
W, H = 1920, 1080          # общий холст
CARD_H = 220               # ниже, как просили
MARGIN = 24
GAP_CARDS = 10             # для правого модуля в /cards (render_card_special)

# Геометрия головы/лого (с учётом пожеланий)
HEAD_R        = 138        # круг для головы — больше, чтобы не резало макушку
HEAD_SHIFT_Y  = 10         # голова на 5–10 px выше нижней кромки
HEAD_SHIFT_X  = 36         # и левее на 30–40 px
TEAM_LOGO_D   = 96         # логотип крупнее ~1.5x
TEAM_LOGO_Y_PAD = 18       # почти у нижней кромки

# Размеры шрифтов: имя < статистика (ещё меньше)
NAME_SIZE = 64
STAT_NUM  = 46
STAT_LAB  = 24

WHITE = (255, 255, 255)

# Градиенты (фиксированные, НЕ по цветам команды)
GRAD_ORANGE = ((255, 143, 26), (255, 209, 74))   # card / main
GRAD_BROWN  = ((78, 52, 48), (54, 36, 33))       # cardbad
GRAD_DARK   = ((32, 32, 32), (20, 20, 20))       # правый модуль в cards
GRAD_PURPLE = ((61, 34, 116), (42, 26, 90))      # card2 слева
GRAD_BLUE   = ((27, 73, 132), (17, 55, 104))     # card2 справа

# ====== ПОИСК ШРИФТОВ ========================================================
def _candidate_font_dirs() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.environ.get("FONTS_DIR") or "",                # явное указание
        os.path.join(here, "fonts"),
        os.path.join(here, "api", "fonts"),
        "/var/task/fonts",
        "/var/task/api/fonts",
    ]

def _find_font(filename: str) -> Optional[str]:
    for d in _candidate_font_dirs():
        if not d: continue
        p = os.path.join(d, filename)
        if os.path.exists(p): return p
    return None

def _font(path_or_name: str, size: int) -> ImageFont.FreeTypeFont:
    # допускаем вызов с "Exo2-Bold.ttf" и с полным путём
    path = path_or_name if os.path.exists(path_or_name) else _find_font(path_or_name)
    if not path:
        raise OSError(f"Font not found: {path_or_name}")
    return ImageFont.truetype(path, size)

def _f_name(size: int) -> ImageFont.FreeTypeFont:
    return _font("Montserrat-Bold.ttf", size)

def _f_num(size: int) -> ImageFont.FreeTypeFont:
    return _font("Exo2-Bold.ttf", size)

def _f_lab(size: int) -> ImageFont.FreeTypeFont:
    return _font("Montserrat-SemiBold.ttf", size)

# ====== ИКОНКИ ===============================================================
def _icon(name: str) -> Optional[Image.Image]:
    # ищем в /assets/icons и /api/assets/icons
    here = os.path.dirname(os.path.abspath(__file__))
    for d in [
        os.path.join(here, "assets", "icons"),
        os.path.join(here, "api", "assets", "icons"),
        "/var/task/assets/icons",
        "/var/task/api/assets/icons",
    ]:
        p = os.path.join(d, name)
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except Exception:
                pass
    return None

# ====== УТИЛИТЫ РИСОВАНИЯ ====================================================
def _linear_gradient(w: int, h: int, c1: Tuple[int,int,int], c2: Tuple[int,int,int]) -> Image.Image:
    im = Image.new("RGB", (w, h), c1)
    dr = ImageDraw.Draw(im)
    for x in range(w):
        t = x / max(1, w-1)
        r = int(c1[0] + (c2[0]-c1[0])*t)
        g = int(c1[1] + (c2[1]-c1[1])*t)
        b = int(c1[2] + (c2[2]-c1[2])*t)
        dr.line([(x,0),(x,h)], fill=(r,g,b))
    return im

def _circle_paste(canvas: Image.Image, avatar: Image.Image, center: Tuple[int,int], radius: int):
    avatar = avatar.convert("RGBA")
    d = radius*2
    avatar = avatar.resize((d, d), Image.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0,0,d,d), fill=255)
    x = center[0]-radius
    y = center[1]-radius
    canvas.paste(avatar, (x, y), mask)

def _as_image(obj: Union[Image.Image, bytes, str, None]) -> Optional[Image.Image]:
    if obj is None: return None
    if isinstance(obj, Image.Image): return obj.convert("RGBA")
    if isinstance(obj, (bytes, bytearray)):
        try: return Image.open(io.BytesIO(obj)).convert("RGBA")
        except Exception: return None
    if isinstance(obj, str) and os.path.exists(obj):
        try: return Image.open(obj).convert("RGBA")
        except Exception: return None
    return None

def _png_bytes(im: Image.Image) -> bytes:
    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue()

def _draw_stats_row(draw: ImageDraw.ImageDraw, x: int, baseline_y: int,
                    items: Iterable[Tuple[str,str]],
                    f_num: ImageFont.FreeTypeFont, f_lab: ImageFont.FreeTypeFont,
                    color=(255,255,255), gap=84) -> int:
    cur_x = x
    for num, lab in items:
        num = str(num or "")
        lab = str(lab or "")
        # число
        w_num, h_num = draw.textbbox((0,0), num, font=f_num)[2:]
        draw.text((cur_x, baseline_y - h_num), num, font=f_num, fill=color)
        # подпись ниже
        w_lab, h_lab = draw.textbbox((0,0), lab, font=f_lab)[2:]
        draw.text((cur_x, baseline_y + 8), lab, font=f_lab, fill=color)
        block_w = max(w_num, w_lab)
        cur_x += block_w + gap
    return cur_x - x

# ====== БАЗОВЫЕ СБОРЩИКИ =====================================================
def _render_single_core(name_ru: str,
                        stats: List[Tuple[str,str]],
                        head_img: Image.Image,
                        team_logo_img: Optional[Image.Image],
                        grad=((255, 143, 26),(255,209,74))) -> Image.Image:
    """Одна плашка в левом нижнем углу на всю ширину."""
    name_ru = (name_ru or "").upper()

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    bg = _linear_gradient(W, CARD_H, grad[0], grad[1]).convert("RGBA")
    canvas.paste(bg, (0, H - CARD_H))

    dr = ImageDraw.Draw(canvas)

    # ЛОГО — крупнее и ближе к нижней кромке
    if team_logo_img:
        team = team_logo_img.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        # тонкая белая подложка
        pad = 9
        bgw = Image.new("RGBA", (TEAM_LOGO_D+pad*2, TEAM_LOGO_D+pad*2), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - pad
        canvas.paste(bgw, (bx-pad, by-pad), bgw)
        canvas.paste(team, (bx, by), team)

    # ГОЛОВА — левее и чуть выше низа
    cx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    cy = H - HEAD_SHIFT_Y - HEAD_R
    _circle_paste(canvas, head_img, (cx, cy), HEAD_R)

    # ИМЯ
    f_name = _f_name(NAME_SIZE)
    name_x = cx + HEAD_R + 28
    name_y = H - CARD_H + 44
    dr.text((name_x, name_y), name_ru, font=f_name, fill=WHITE)

    # СТАТИСТИКА — меньше имени
    f_num = _f_num(STAT_NUM)
    f_lab = _f_lab(STAT_LAB)
    stats_y = name_y + 78
    _draw_stats_row(dr, name_x, stats_y, stats, f_num, f_lab)

    return canvas

def _render_cards_extra(canvas: Image.Image, start_x: int, text: str):
    """Правый модуль для /cards: звезда БЕЗ белого круга + текст."""
    ex = _linear_gradient(W - start_x, CARD_H, GRAD_DARK[0], GRAD_DARK[1]).convert("RGBA")
    canvas.paste(ex, (start_x, H - CARD_H))
    dr = ImageDraw.Draw(canvas)

    x = start_x + 28
    y = H - CARD_H + 54
    star = _icon("star.png")
    if star:
        h = 40
        w = int(star.width * h / star.height)
        star = star.resize((w, h), Image.LANCZOS)
        canvas.paste(star, (x, y), star)
        x += w + 14
    dr.text((x, y), str(text or ""), font=_f_name(32), fill=WHITE)

# ====== ПУБЛИЧНЫЕ ФУНКЦИИ (СОВМЕСТИМЫЕ С ТЕЛЕГРАМ-ФАЙЛОМ) ===================
def render_card(*args, **kwargs) -> bytes:
    """
    Совместимая сигнатура:
      render_card("single", ru, "", team_logo_img, colors, head_img, stats_list)
    Можно звать и «новым» стилем:
      render_card(ru, ("30","11","11-14"), head_png_bytes, team_logo_path)
    Возвращает PNG bytes.
    """
    # Распознаём варианты аргументов
    if args and isinstance(args[0], str) and args[0].lower() in ("single",""):
        # старый стиль
        # ("single", ru, "", team_logo_img, colors, head_img, stats_list)
        _, ru, _team_txt, team_logo_img, _colors, head_img, stats_list = (list(args) + [None]*7)[:7]
        head = _as_image(head_img)
        logo = _as_image(team_logo_img)
        stats = []
        for it in (stats_list or []):
            v, l = (it if isinstance(it, (list, tuple)) and len(it)>=2 else (str(it), ""))[:2]
            stats.append((str(v), str(l)))
        im = _render_single_core(str(ru), stats, head, logo, GRAD_ORANGE)
        return _png_bytes(im)

    # новый стиль: (name_ru, (n1,n2,n3), head_png_bytes, team_logo_path)
    name_ru = str(args[0])
    triple = args[1] if len(args) > 1 else ("","","")
    head_png = args[2] if len(args) > 2 else None
    team_logo_path = args[3] if len(args) > 3 else None
    head = _as_image(head_png)
    logo = _as_image(team_logo_path)
    n1, n2, n3 = (list(triple) + ["","",""])[:3]
    stats = [(str(n1), "ОЧКИ"), (str(n2), "ПОДБОРЫ"), (str(n3), "С ИГРЫ")]
    im = _render_single_core(name_ru, stats, head, logo, GRAD_ORANGE)
    return _png_bytes(im)

def render_card_bad(*args, **kwargs) -> bytes:
    """
    Совместимая сигнатура:
      render_card_bad(ru, head_img, stats_list, team_logo_img=logo)
    """
    if args:
        ru = args[0]
        head_img = args[1] if len(args) > 1 else None
        stats_list = args[2] if len(args) > 2 else []
        team_logo_img = kwargs.get("team_logo_img")
        head = _as_image(head_img)
        logo = _as_image(team_logo_img)
        stats = []
        for it in (stats_list or []):
            v, l = (it if isinstance(it, (list, tuple)) and len(it)>=2 else (str(it), ""))[:2]
            stats.append((str(v), str(l)))

        # базовый рендер
        im = _render_single_core(str(ru), stats, head, logo, GRAD_BROWN)

        # добавляем «poop» в 2× после имени
        dr = ImageDraw.Draw(im)
        f_name = _f_name(NAME_SIZE)
        name_txt = str(ru or "").upper()
        # координаты имени такие же, как в core:
        cx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
        name_x = cx + HEAD_R + 28
        name_y = H - CARD_H + 44
        x1, y1, x2, y2 = dr.textbbox((name_x, name_y), name_txt, font=f_name)
        poop = _icon("poop.png")
        if poop:
            h = int(NAME_SIZE * 0.9)
            w = int(poop.width * h / poop.height)
            poop = poop.resize((w, h), Image.LANCZOS)
            im.paste(poop, (x2 + 16, name_y - int(h*0.1)), poop)
        return _png_bytes(im)

    # на всякий случай — если позвали «новым» стилем:
    name_ru = kwargs.get("name_ru") or ""
    triple = kwargs.get("stats") or ("","","")
    head_png = kwargs.get("head_png")
    team_logo_path = kwargs.get("team_logo_path")
    head = _as_image(head_png)
    logo = _as_image(team_logo_path)
    n1, n2, n3 = (list(triple) + ["","",""])[:3]
    stats = [(str(n1), "ОЧКИ"), (str(n2), "ПОДБОРЫ"), (str(n3), "С ИГРЫ")]
    im = _render_single_core(str(name_ru), stats, head, logo, GRAD_BROWN)
    return _png_bytes(im)

def render_card_special(*args, **kwargs) -> bytes:
    """
    Совместимая сигнатура:
      render_card_special(ru, team_logo_img, colors, head_img, stats_list, info_text)
      — это «/cards»: отдельный правый модуль на 10px отступе.
    """
    ru = args[0] if len(args) > 0 else ""
    team_logo_img = args[1] if len(args) > 1 else None
    # colors = args[2] (игнорируем — у нас фикс. градиенты)
    head_img = args[3] if len(args) > 3 else None
    stats_list = args[4] if len(args) > 4 else []
    info_text = args[5] if len(args) > 5 else ""

    head = _as_image(head_img)
    logo = _as_image(team_logo_img)
    stats = []
    for it in (stats_list or []):
        v, l = (it if isinstance(it, (list, tuple)) and len(it)>=2 else (str(it), ""))[:2]
        stats.append((str(v), str(l)))

    # main часть — как обычный card, но не на всю ширину: возьмём ~58%
    main_w = int(W * 0.58)

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    main_bg = _linear_gradient(main_w, CARD_H, GRAD_ORANGE[0], GRAD_ORANGE[1]).convert("RGBA")
    canvas.paste(main_bg, (0, H - CARD_H))

    # логотип/голова/имя/статы — как в core, только считаем, что «правая граница» дальше не нужна
    # ЛОГО
    if logo:
        team = logo.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        pad = 9
        bgw = Image.new("RGBA", (TEAM_LOGO_D+pad*2, TEAM_LOGO_D+pad*2), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - pad
        canvas.paste(bgw, (bx-pad, by-pad), bgw)
        canvas.paste(team, (bx, by), team)

    # ГОЛОВА
    cx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    cy = H - HEAD_SHIFT_Y - HEAD_R
    _circle_paste(canvas, head, (cx, cy), HEAD_R)

    # ТЕКСТЫ
    dr = ImageDraw.Draw(canvas)
    f_name = _f_name(NAME_SIZE)
    f_num  = _f_num(STAT_NUM)
    f_lab  = _f_lab(STAT_LAB)
    name_x = cx + HEAD_R + 28
    name_y = H - CARD_H + 44
    dr.text((name_x, name_y), str(ru or "").upper(), font=f_name, fill=WHITE)
    stats_y = name_y + 78
    _draw_stats_row(dr, name_x, stats_y, stats, f_num, f_lab)

    # ПРАВЫЙ МОДУЛЬ: строго отдельно, с отступом 10 px
    _render_cards_extra(canvas, main_w + GAP_CARDS, str(info_text or ""))
    return _png_bytes(canvas)

def render_card2(*args, **kwargs) -> bytes:
    """
    Совместима с вызовом:
      render_card2(ru1, logo1, colors1, head1, stats1, ru2, logo2, colors2, head2, stats2)
    Имена/статы обеих сторон выровнены по общей линии.
    """
    ru1   = args[0] if len(args) > 0 else ""
    logo1 = args[1] if len(args) > 1 else None
    # colors1 = args[2] — игнорируем (фикс. градиенты)
    head1 = args[3] if len(args) > 3 else None
    st1   = args[4] if len(args) > 4 else []
    ru2   = args[5] if len(args) > 5 else ""
    logo2 = args[6] if len(args) > 6 else None
    # colors2 = args[7]
    head2 = args[8] if len(args) > 8 else None
    st2   = args[9] if len(args) > 9 else []

    Llogo = _as_image(logo1); Rlogo = _as_image(logo2)
    Lhead = _as_image(head1); Rhead = _as_image(head2)
    Lstats, Rstats = [], []
    for it in (st1 or []):
        v, l = (it if isinstance(it, (list, tuple)) and len(it)>=2 else (str(it), ""))[:2]
        Lstats.append((str(v), str(l)))
    for it in (st2 or []):
        v, l = (it if isinstance(it, (list, tuple)) and len(it)>=2 else (str(it), ""))[:2]
        Rstats.append((str(v), str(l)))

    canvas = Image.new("RGBA", (W, H), (0,0,0,0))
    half = W // 2

    # фоновые половины
    Lbg = _linear_gradient(half, CARD_H, GRAD_PURPLE[0], GRAD_PURPLE[1]).convert("RGBA")
    Rbg = _linear_gradient(W - half, CARD_H, GRAD_BLUE[0], GRAD_BLUE[1]).convert("RGBA")
    canvas.paste(Lbg, (0, H - CARD_H))
    canvas.paste(Rbg, (half, H - CARD_H))

    dr = ImageDraw.Draw(canvas)
    f_name = _f_name(NAME_SIZE)
    f_num  = _f_num(STAT_NUM)
    f_lab  = _f_lab(STAT_LAB)

    # общий baseline
    name_y  = H - CARD_H + 44
    stats_y = name_y + 78

    # ЛЕВАЯ СТОРОНА
    if Llogo:
        team = Llogo.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        pad = 9
        bgw = Image.new("RGBA", (TEAM_LOGO_D+pad*2, TEAM_LOGO_D+pad*2), (255,255,255,240))
        bx = MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - pad
        canvas.paste(bgw, (bx-pad, by-pad), bgw)
        canvas.paste(team, (bx, by), team)

    Lcx = MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    Lcy = H - HEAD_SHIFT_Y - HEAD_R
    _circle_paste(canvas, Lhead, (Lcx, Lcy), HEAD_R)

    Lname_x = Lcx + HEAD_R + 28
    dr.text((Lname_x, name_y), str(ru1 or "").upper(), font=f_name, fill=WHITE)
    _draw_stats_row(dr, Lname_x, stats_y, Lstats, f_num, f_lab)

    # ПРАВАЯ СТОРОНА
    if Rlogo:
        team = Rlogo.resize((TEAM_LOGO_D, TEAM_LOGO_D), Image.LANCZOS)
        pad = 9
        bgw = Image.new("RGBA", (TEAM_LOGO_D+pad*2, TEAM_LOGO_D+pad*2), (255,255,255,240))
        bx = half + MARGIN
        by = H - CARD_H + CARD_H - TEAM_LOGO_D - TEAM_LOGO_Y_PAD - pad
        canvas.paste(bgw, (bx-pad, by-pad), bgw)
        canvas.paste(team, (bx, by), team)

    Rcx = half + MARGIN + TEAM_LOGO_D + 28 + HEAD_SHIFT_X
    Rcy = H - HEAD_SHIFT_Y - HEAD_R
    _circle_paste(canvas, Rhead, (Rcx, Rcy), HEAD_R)

    Rname_x = Rcx + HEAD_R + 28
    dr.text((Rname_x, name_y), str(ru2 or "").upper(), font=f_name, fill=WHITE)
    _draw_stats_row(dr, Rname_x, stats_y, Rstats, f_num, f_lab)

    return _png_bytes(canvas)

# Совместимость с альтернативным именем
def render_cardbad(*a, **kw) -> bytes:
    return render_card_bad(*a, **kw)
