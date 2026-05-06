# graphics.py - NBA lower-third cards, 1920x1080 transparent PNG
from __future__ import annotations

import io
import math
import os
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080


def _here(*p: str) -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), *p))


ASSET_SEARCH_ROOTS = [_here(), _here("api")]


def _find_file(rel: str) -> Optional[str]:
    rel = rel.lstrip("/\\")
    for root in ASSET_SEARCH_ROOTS:
        cand = os.path.join(root, rel)
        if os.path.exists(cand):
            return cand
    for alt in ("fonts", "api/fonts", "icons", "api/icons", "assets/icons"):
        cand = _here(alt, os.path.basename(rel))
        if os.path.exists(cand):
            return cand
    return None


def _load_font_multi(cands: List[str], size: int) -> ImageFont.ImageFont:
    for nm in cands:
        path = (
            _find_file(nm)
            or _find_file(os.path.join("fonts", os.path.basename(nm)))
            or _find_file(os.path.join("api", "fonts", os.path.basename(nm)))
        )
        if path:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _load_png(rel: str, size: Optional[int] = None) -> Optional[Image.Image]:
    path = (
        _find_file(rel)
        or _find_file(os.path.join("icons", os.path.basename(rel)))
        or _find_file(os.path.join("assets", "icons", os.path.basename(rel)))
        or _find_file(os.path.join("api", "icons", os.path.basename(rel)))
    )
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGBA")
        if size:
            im = im.resize((size, size), Image.LANCZOS)
        return im
    except Exception:
        return None


MONTSERRAT_BOLD = [
    "Montserrat-Bold.ttf",
    "Montserrat-ExtraBold.ttf",
    "MontserratAlternates-Bold.ttf",
    "Montserrat-Black.ttf",
]
MONTSERRAT_SEMI = [
    "Montserrat-SemiBold.ttf",
    "MontserratAlternates-SemiBold.ttf",
    "Montserrat-Medium.ttf",
    "Montserrat-Regular.ttf",
]
EXO2_BOLD = ["Exo2-Bold.ttf", "Exo 2 Bold.ttf", "Exo2-ExtraBold.ttf", "Exo2-SemiBold.ttf"]


def font_name(size: int) -> ImageFont.ImageFont:
    return _load_font_multi(MONTSERRAT_BOLD, size)


def font_stat_val(size: int) -> ImageFont.ImageFont:
    return _load_font_multi(EXO2_BOLD, size)


def font_stat_lbl(size: int) -> ImageFont.ImageFont:
    return _load_font_multi(MONTSERRAT_SEMI, size)


CARD_H = 220
CARD_TOP = H - CARD_H
RADIUS = 22

LOGO_PLATE_D = 140
LOGO_INNER_D = 100
LOGO_X = 44
LOGO_Y = H - LOGO_PLATE_D

HEAD_LEFT = 110
HEAD_TOP = 760
HEAD_H = 320
HEAD_ASPECT = 0.72

STATS_AREA_X = 350
CARD_RIGHT_PAD = 36
BAD_RIGHT_PAD = 107
MIN_STATS_AREA_W = 540
STAT_COL_W = 165

NAME_SIZE = 52
NAME_MIN_SIZE = 38
NAME_TOP = CARD_TOP + 24
STAT_VAL = 48
STAT_LBL = 27
STAT_VALUE_TOP = CARD_TOP + 114
STAT_LABEL_TOP = CARD_TOP + 166

SIDE_GAP = 10
SIDE_W = 404

WHITE = (255, 255, 255, 255)
BROWN_LEFT = (76, 53, 45)
BROWN_RIGHT = (109, 76, 65)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    if not text:
        return (0, 0)
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    return text_size(draw, text, font)[0]


def _font_fit(cands: List[str], text: str, size: int, max_w: int, min_size: int) -> ImageFont.ImageFont:
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    s = size
    while s > min_size:
        f = _load_font_multi(cands, s)
        if _text_width(draw, text, f) <= max_w:
            return f
        s -= 2
    return _load_font_multi(cands, min_size)


def _draw_text_left_top(
    draw: ImageDraw.ImageDraw,
    x: int,
    visual_top: int,
    text: str,
    font: ImageFont.ImageFont,
    fill=WHITE,
) -> Tuple[int, int, int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((int(x - box[0]), int(visual_top - box[1])), text, font=font, fill=fill)
    return (int(x), int(visual_top), int(x + box[2] - box[0]), int(visual_top + box[3] - box[1]))


def _draw_text_center_top(
    draw: ImageDraw.ImageDraw,
    cx: int,
    visual_top: int,
    text: str,
    font: ImageFont.ImageFont,
    fill=WHITE,
) -> Tuple[int, int, int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    w = box[2] - box[0]
    x = int(cx - w / 2)
    return _draw_text_left_top(draw, x, visual_top, text, font, fill)


def _png_bytes(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


def _to_rgb(hex_color: str) -> Tuple[int, int, int]:
    s = str(hex_color or "#000000").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        s = "000000"
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _ensure_rgba(img: Optional[Image.Image]) -> Optional[Image.Image]:
    return None if img is None else (img.convert("RGBA") if img.mode != "RGBA" else img)


def _circle_mask(d: int) -> Image.Image:
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).ellipse([0, 0, d - 1, d - 1], fill=255)
    return m


def _linear_gradient(
    w: int,
    h: int,
    c1: Tuple[int, int, int],
    c2: Tuple[int, int, int],
    horizontal: bool = True,
) -> Image.Image:
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(grad)
    if horizontal:
        for x in range(w):
            t = x / (w - 1) if w > 1 else 0
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            d.line([(x, 0), (x, h)], fill=(r, g, b, 255))
    else:
        for y in range(h):
            t = y / (h - 1) if h > 1 else 0
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            d.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return grad


def _rounded_mask(width: int, height: int, radius: int, corners: str) -> Image.Image:
    if corners == "none" or radius <= 0:
        return Image.new("L", (width, height), 255)

    scale = 4
    w4, h4, r4 = width * scale, height * scale, radius * scale
    mask = Image.new("L", (w4, h4), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, w4 - 1, h4 - 1], radius=r4, fill=255)

    if corners == "right":
        d.rectangle([0, 0, r4 + 2, h4], fill=255)
    elif corners == "left":
        d.rectangle([w4 - r4 - 2, 0, w4, h4], fill=255)

    return mask.resize((width, height), Image.LANCZOS)


def _main_bar(
    width: int,
    height: int,
    c_left: Tuple[int, int, int],
    c_right: Tuple[int, int, int],
    corners: str = "right",
) -> Image.Image:
    grad = _linear_gradient(width, height, c_left, c_right, horizontal=True)
    mask = _rounded_mask(width, height, RADIUS, corners)
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def _alpha_bbox(img: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img.split()[3].getbbox()


def _draw_team_logo(base: Image.Image, logo_img: Optional[Image.Image], x: int = LOGO_X, y: int = LOGO_Y) -> None:
    if logo_img is None:
        return

    logo = _ensure_rgba(logo_img).copy()
    bbox = _alpha_bbox(logo)
    if bbox:
        logo = logo.crop(bbox)
    logo.thumbnail((LOGO_INNER_D, LOGO_INNER_D), Image.LANCZOS)

    shadow = Image.new("RGBA", (LOGO_PLATE_D + 10, LOGO_PLATE_D + 10), (0, 0, 0, 0))
    sh = Image.new("RGBA", shadow.size, (0, 0, 0, 110))
    shadow.paste(sh, (0, 0), _circle_mask(LOGO_PLATE_D + 10))
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    base.alpha_composite(shadow, (x - 5, y - 5))

    plate = Image.new("RGBA", (LOGO_PLATE_D, LOGO_PLATE_D), (255, 255, 255, 255))
    base.paste(plate, (x, y), _circle_mask(LOGO_PLATE_D))

    lx = x + (LOGO_PLATE_D - logo.width) // 2
    ly = y + (LOGO_PLATE_D - logo.height) // 2
    base.alpha_composite(logo, (lx, ly))


def _draw_headshot(base: Image.Image, head_img: Image.Image, left: int = HEAD_LEFT, top: int = HEAD_TOP) -> None:
    im = _ensure_rgba(head_img).copy()
    crop_h = im.height
    crop_w = min(im.width, int(crop_h * HEAD_ASPECT))
    if crop_w < im.width:
        crop_x = (im.width - crop_w) // 2
        im = im.crop((crop_x, 0, crop_x + crop_w, crop_h))

    out_w = max(1, int(round(HEAD_H * im.width / im.height)))
    im = im.resize((out_w, HEAD_H), Image.LANCZOS)
    base.alpha_composite(im, (left, top))


def _stats_area_width(stats: List[Tuple[str, str]], min_w: int = MIN_STATS_AREA_W) -> int:
    cols = max(1, len(stats or []))
    return max(min_w, cols * STAT_COL_W)


def _card_width(name_ru: str, stats: List[Tuple[str, str]], *, bad: bool = False) -> Tuple[int, int]:
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    display_name = (name_ru or "").strip().upper()
    name_font = font_name(NAME_SIZE)
    name_w = _text_width(draw, display_name, name_font) if display_name else 0
    stats_w = _stats_area_width(stats)
    if bad:
        stats_w = max(stats_w, name_w + 64 + 96)
        width = STATS_AREA_X + stats_w + BAD_RIGHT_PAD
    else:
        stats_w = max(stats_w, name_w + 80)
        width = STATS_AREA_X + stats_w + CARD_RIGHT_PAD
    return min(W, int(width)), int(stats_w)


def _draw_stats(base: Image.Image, area_x: int, area_w: int, stats: List[Tuple[str, str]]) -> None:
    draw = ImageDraw.Draw(base)
    stats = [(str(v), str(l).upper()) for v, l in (stats or [])]
    if not stats:
        return

    value_font = font_stat_val(STAT_VAL)
    label_font_default = font_stat_lbl(STAT_LBL)
    col_w = area_w / max(1, len(stats))
    for i, (val, lbl) in enumerate(stats):
        cx = int(area_x + col_w * (i + 0.5))
        _draw_text_center_top(draw, cx, STAT_VALUE_TOP, val, value_font)

        label_font = label_font_default
        if lbl:
            while _text_width(draw, lbl, label_font) > max(64, int(col_w) - 6) and getattr(label_font, "size", STAT_LBL) > 21:
                label_font = font_stat_lbl(getattr(label_font, "size", STAT_LBL) - 1)
            _draw_text_center_top(draw, cx, STAT_LABEL_TOP, lbl, label_font)


def _draw_name_and_stats(
    base: Image.Image,
    area_x: int,
    area_w: int,
    name_ru: str,
    stats: List[Tuple[str, str]],
) -> None:
    draw = ImageDraw.Draw(base)
    display_name = (name_ru or "").strip().upper()
    if display_name:
        f_name = _font_fit(MONTSERRAT_BOLD, display_name, NAME_SIZE, area_w, NAME_MIN_SIZE)
        name_w = _text_width(draw, display_name, f_name)
        name_x = area_x + max(0, (area_w - name_w) // 2)
        _draw_text_left_top(draw, name_x, NAME_TOP, display_name, f_name)
    _draw_stats(base, area_x, area_w, stats)


def _draw_player_stack(
    img: Image.Image,
    logo_img: Optional[Image.Image],
    head_img: Image.Image,
    *,
    x_offset: int = 0,
) -> None:
    _draw_headshot(img, head_img, x_offset + HEAD_LEFT, HEAD_TOP)
    _draw_team_logo(img, logo_img, x_offset + LOGO_X, LOGO_Y)


def _draw_star(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else int(r * 0.45)
        pts.append((cx + rr * math.cos(ang), cy - rr * math.sin(ang)))
    draw.polygon(pts, fill=(255, 205, 0, 255))


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> List[str]:
    words = [w for w in (text or "").strip().upper().split() if w]
    lines: List[str] = []
    line = ""
    for word in words:
        test = word if not line else f"{line} {word}"
        if line and _text_width(draw, test, font) > max_w:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    return lines or []


def _preferred_info_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> List[str]:
    words = [w for w in (text or "").strip().upper().split() if w]
    if "ПО" in words:
        idx = words.index("ПО")
        if 0 < idx < len(words) - 1:
            semantic = [" ".join(words[:idx]), "ПО", " ".join(words[idx + 1 :])]
            return semantic
    return _wrap_lines(draw, text, font, max_w)


def _draw_side_info(base: Image.Image, x: int, y: int, width: int, info_text: str) -> None:
    draw = ImageDraw.Draw(base)
    info = (info_text or "").strip().upper()
    if not info:
        return

    star = _load_png("star.png", 36)
    star_x = x + 22
    star_top = y + 27
    if star:
        base.alpha_composite(star, (star_x, star_top))
    else:
        _draw_star(draw, star_x + 18, star_top + 19, 18)

    text_x = x + 68
    text_top = y + 28
    max_w = width - 72
    size = 50
    while size > 34:
        f = font_name(size)
        lines = _preferred_info_lines(draw, info, f, max_w)
        line_h = int(size * 1.08)
        fits_width = all(_text_width(draw, line, f) <= max_w for line in lines)
        if len(lines) <= 3 and fits_width and text_top + line_h * len(lines) <= y + CARD_H - 20:
            break
        size -= 2
    f = font_name(size)
    lines = _preferred_info_lines(draw, info, f, max_w)[:3]
    line_h = int(size * 1.08)
    for i, line in enumerate(lines):
        _draw_text_left_top(draw, text_x, text_top + i * line_h, line, f)


def render_card(
    mode: str,
    name_ru: str,
    team_name_ru: str,
    team_logo_img: Optional[Image.Image],
    team_colors: Tuple[str, str, str],
    head_img: Image.Image,
    stats: List[Tuple[str, str]],
) -> bytes:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card_w, area_w = _card_width(name_ru, stats)

    primary, dark, _ = team_colors
    bar = _main_bar(card_w, CARD_H, _to_rgb(dark), _to_rgb(primary), corners="right")
    img.alpha_composite(bar, (0, CARD_TOP))

    _draw_player_stack(img, team_logo_img, head_img)
    _draw_name_and_stats(img, STATS_AREA_X, area_w, name_ru, stats)
    return _png_bytes(img)


def render_card2(
    name1_ru: str,
    team1_logo_img: Optional[Image.Image],
    team1_colors: Tuple[str, str, str],
    head1_img: Image.Image,
    stats1: List[Tuple[str, str]],
    name2_ru: str,
    team2_logo_img: Optional[Image.Image],
    team2_colors: Tuple[str, str, str],
    head2_img: Image.Image,
    stats2: List[Tuple[str, str]],
) -> bytes:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    half = W // 2

    p1, d1, _ = team1_colors
    p2, d2, _ = team2_colors
    left = _main_bar(half, CARD_H, _to_rgb(d1), _to_rgb(p1), corners="none")
    right = _main_bar(half, CARD_H, _to_rgb(d2), _to_rgb(p2), corners="none")
    img.alpha_composite(left, (0, CARD_TOP))
    img.alpha_composite(right, (half, CARD_TOP))

    draw = ImageDraw.Draw(img)
    draw.rectangle([half - 1, CARD_TOP + 14, half + 1, H - 14], fill=(255, 255, 255, 70))

    _draw_player_stack(img, team1_logo_img, head1_img, x_offset=0)
    _draw_name_and_stats(img, STATS_AREA_X, half - STATS_AREA_X - CARD_RIGHT_PAD, name1_ru, stats1)

    _draw_player_stack(img, team2_logo_img, head2_img, x_offset=half)
    _draw_name_and_stats(img, half + STATS_AREA_X, half - STATS_AREA_X - CARD_RIGHT_PAD, name2_ru, stats2)

    return _png_bytes(img)


def render_card_special(
    name_ru: str,
    team_logo_img: Optional[Image.Image],
    team_colors: Tuple[str, str, str],
    head_img: Image.Image,
    stats: List[Tuple[str, str]],
    info_text: str,
) -> bytes:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    main_w, area_w = _card_width(name_ru, stats)
    side_x = min(W - SIDE_W, main_w + SIDE_GAP)

    primary, dark, _ = team_colors
    main = _main_bar(main_w, CARD_H, _to_rgb(dark), _to_rgb(primary), corners="all")
    side = _main_bar(SIDE_W, CARD_H, _to_rgb(dark), _to_rgb(primary), corners="all")
    img.alpha_composite(main, (0, CARD_TOP))
    img.alpha_composite(side, (side_x, CARD_TOP))

    _draw_player_stack(img, team_logo_img, head_img)
    _draw_name_and_stats(img, STATS_AREA_X, area_w, name_ru, stats)
    _draw_side_info(img, side_x, CARD_TOP, SIDE_W, info_text)

    return _png_bytes(img)


def render_card_bad(
    name_ru: str,
    head_img: Image.Image,
    stats: List[Tuple[str, str]],
    team_logo_img: Optional[Image.Image] = None,
) -> bytes:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card_w, area_w = _card_width(name_ru, stats, bad=True)

    bar = _main_bar(card_w, CARD_H, BROWN_LEFT, BROWN_RIGHT, corners="all")
    img.alpha_composite(bar, (0, CARD_TOP))

    _draw_player_stack(img, team_logo_img, head_img)

    draw = ImageDraw.Draw(img)
    display_name = (name_ru or "").strip().upper()
    f_name = _font_fit(MONTSERRAT_BOLD, display_name, NAME_SIZE, max(1, area_w - 86), NAME_MIN_SIZE)
    name_w = _text_width(draw, display_name, f_name)
    icon_size = 64
    group_w = name_w + 18 + icon_size
    name_x = max(430, STATS_AREA_X + max(0, (area_w - group_w) // 2))
    if display_name:
        _draw_text_left_top(draw, name_x, NAME_TOP, display_name, f_name)

    poop = _load_png("poop.png", icon_size)
    poop_x = int(name_x + name_w + 18)
    poop_y = NAME_TOP - 8
    if poop:
        img.alpha_composite(poop, (poop_x, poop_y))
    else:
        _draw_star(draw, poop_x + icon_size // 2, poop_y + icon_size // 2, icon_size // 2)

    _draw_stats(img, STATS_AREA_X, area_w, stats)
    return _png_bytes(img)


GRAPHICS_ACCESS_CHECK = "layout-v2-reference-cards"
