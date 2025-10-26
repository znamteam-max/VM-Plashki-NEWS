
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import io

W, H = 1920, 1080
BAR_H = 320
PAD = 64

def _load_font(path, size, fallback=24):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

F_BOLD = _load_font("assets/fonts/Montserrat-Bold.ttf", 88)
F_SB   = _load_font("assets/fonts/Montserrat-SemiBold.ttf", 48)
F_EXO  = _load_font("assets/fonts/Exo2-Bold.ttf", 56)

def _circle_crop(path: str, diameter: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    s = min(im.size)
    left = (im.width - s) // 2
    top = max(0, im.height - s)
    im = im.crop((left, top, left + s, top + s)).resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    d = ImageDraw.Draw(mask); d.ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out

def _metric_blocks(stats: List[Tuple[str, str]], color_text=(255, 255, 255, 255)) -> Image.Image:
    padd = 48
    blocks = []
    total_w = 0
    for val, label in stats:
        val_bbox = F_EXO.getbbox(val)
        val_img = Image.new("RGBA", (val_bbox[2]-val_bbox[0], val_bbox[3]-val_bbox[1]), (0,0,0,0))
        ImageDraw.Draw(val_img).text((0,0), val, font=F_EXO, fill=color_text)

        label = (label or "").upper().strip()
        lb_bbox = F_SB.getbbox(label if label else "")
        lb_img = Image.new("RGBA", (max(1, lb_bbox[2]-lb_bbox[0]), max(1, lb_bbox[3]-lb_bbox[1])), (0,0,0,0))
        ImageDraw.Draw(lb_img).text((0,0), label, font=F_SB, fill=color_text)

        h = val_img.height + 8 + lb_img.height
        w = max(val_img.width, lb_img.width)
        block = Image.new("RGBA", (w, h), (0,0,0,0))
        block.alpha_composite(val_img, ((w - val_img.width)//2, 0))
        block.alpha_composite(lb_img, ((w - lb_img.width)//2, val_img.height + 8))
        blocks.append(block); total_w += w

    total_w += padd * (len(blocks) - 1)
    line = Image.new("RGBA", (total_w, max(b.height for b in blocks)), (0,0,0,0))
    x = 0
    for b in blocks:
        line.alpha_composite(b, (x, (line.height - b.height)//2))
        x += b.width + padd
    return line

def render_card(
    template: str,
    player_name: str,
    team_name: str,
    team_logo_path: str,
    team_colors: Tuple[str, str, str],
    headshot_path: str,
    stats: List[Tuple[str, str]],
    note: Optional[str] = None,
) -> bytes:
    primary, dark, light = team_colors
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    bar = Image.new("RGBA", (W, BAR_H), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle((0, 0, W, BAR_H), 32, fill=primary)

    logo = Image.open(team_logo_path).convert("RGBA").resize((170, 170), Image.LANCZOS)
    logo_circle = Image.new("RGBA", (190, 190), (255, 255, 255, 255))
    mask = Image.new("L", (190, 190), 0); ImageDraw.Draw(mask).ellipse((0, 0, 190, 190), fill=255)
    logo_circle.putalpha(mask)
    logo_circle.alpha_composite(logo, ((190 - logo.width)//2, (190 - logo.height)//2))

    head = _circle_crop(headshot_path, 420)

    name_txt = player_name.upper()
    nb = F_BOLD.getbbox(name_txt)
    name_img = Image.new("RGBA", (nb[2]-nb[0], nb[3]-nb[1]), (0,0,0,0))
    ImageDraw.Draw(name_img).text((0,0), name_txt, font=F_BOLD, fill=(255,255,255,255))

    stats_line = _metric_blocks(stats, color_text=(255,255,255,255))

    bar_y = H - BAR_H
    im.alpha_composite(bar, (0, bar_y))
    im.alpha_composite(head, (PAD, bar_y - head.height//3))
    im.alpha_composite(logo_circle, (PAD + head.width - 140, bar_y + 30))

    nx = PAD + head.width + 48
    ny = bar_y + 36
    im.alpha_composite(name_img, (nx, ny))
    im.alpha_composite(stats_line, (nx, ny + name_img.height + 24))

    if template == "impact":
        # простая звезда + подпись
        try:
            star = Image.open("assets/icons/star.png").convert("RGBA").resize((80, 80), Image.LANCZOS)
            im.alpha_composite(star, (nx + name_img.width + 24, ny - 8))
        except Exception:
            pass
        tag = "ДЕЛАЕТ РАЗНИЦУ"
        tb = F_SB.getbbox(tag)
        tag_img = Image.new("RGBA", (tb[2]-tb[0], tb[3]-tb[1]), (0,0,0,0))
        ImageDraw.Draw(tag_img).text((0,0), tag, font=F_SB, fill=(255,255,255,255))
        im.alpha_composite(tag_img, (nx + name_img.width + 24 + 90, ny + 8))

    elif template == "bad":
        try:
            poop = Image.open("assets/icons/poop.png").convert("RGBA").resize((80, 80), Image.LANCZOS)
            im.alpha_composite(poop, (nx - 92, ny - 6))
        except Exception:
            pass
        darken = Image.new("RGBA", (W, BAR_H), (0, 0, 0, 60))
        im.alpha_composite(darken, (0, bar_y))

    elif template == "single_note" and note:
        rb_w = 560
        rb = Image.new("RGBA", (rb_w, BAR_H), (0, 0, 0, 0))
        ImageDraw.Draw(rb).rounded_rectangle((0, 0, rb_w, BAR_H), 32, fill=(255, 255, 255, 40))
        nb = F_SB.getbbox(note)
        nimg = Image.new("RGBA", (nb[2]-nb[0], nb[3]-nb[1]), (0, 0, 0, 0))
        ImageDraw.Draw(nimg).text((0, 0), note, font=F_SB, fill=(255, 255, 255, 255))
        rb.alpha_composite(nimg, ((rb_w - nimg.width)//2, (BAR_H - nimg.height)//2))
        im.alpha_composite(rb, (W - rb_w - PAD, bar_y))

    # pair: база готова, в будущем добавить второго игрока

    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue()
