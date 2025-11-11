# api/telegram.py
from __future__ import annotations
import os, io, json, uuid
from typing import Any, Dict, Tuple, Optional
from urllib.request import Request as UrlRequest, urlopen as http_urlopen

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

import data
import graphics

DEBUG = os.getenv("DEBUG", "1") in ("1","true","yes")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""

app = FastAPI()

# -------- bytes helpers + multipart (чинит «Image found») --------------------
try:
    from PIL import Image
except Exception:
    Image = None  # type: ignore

def _as_bytes(value) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if Image is not None:
        from PIL.Image import Image as PILImage  # type: ignore
        if isinstance(value, PILImage):
            bio = io.BytesIO()
            value.save(bio, format="PNG")
            return bio.getvalue()
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"cannot convert {type(value)} to bytes")

def _build_multipart(fields: Dict[str,str], files: Dict[str, tuple]):
    boundary = f"----vm{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for k, v in (fields or {}).items():
        parts.append(_as_bytes(f"--{boundary}\r\n"))
        parts.append(_as_bytes(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'))
        parts.append(_as_bytes(v))
        parts.append(b"\r\n")
    for name, spec in (files or {}).items():
        if len(spec) == 2:
            filename, content = spec
            mime = "application/octet-stream"
        else:
            filename, content, mime = spec
        parts.append(_as_bytes(f"--{boundary}\r\n"))
        parts.append(_as_bytes(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        ))
        parts.append(_as_bytes(f"Content-Type: {mime}\r\n\r\n"))
        parts.append(_as_bytes(content))
        parts.append(b"\r\n")
    parts.append(_as_bytes(f"--{boundary}--\r\n"))
    body = b"".join(parts)
    return f"multipart/form-data; boundary={boundary}", body

def _tg_send_photo(chat_id: int, image, caption: str = "") -> bool:
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    ctype, body = _build_multipart(fields, {"photo": ("card.png", image, "image/png")})
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    with http_urlopen(req, timeout=25) as r:
        r.read()
    return True

# -------------------------- HELPERS ------------------------------------------
def _parse_cmd(s: str) -> Tuple[str, str]:
    """
    Возвращает (cmd, payload). cmd — без слеша.
    Пример: "/card lebron | 30 очков" -> ("card", "lebron | 30 очков")
    """
    s = (s or "").strip()
    if not s.startswith("/"):
        return "", s
    parts = s.split(maxsplit=1)
    cmd = parts[0][1:].lower()
    payload = parts[1] if len(parts) > 1 else ""
    return cmd, payload

def _split_payload(payload: str) -> Tuple[str,str,str]:
    """
    "lebron | 30 очков, 11 подборов, 11-14 с игры | молодец"
    -> ("lebron", "30 очков, 11 подборов, 11-14 с игры", "молодец")
    """
    arr = [p.strip() for p in payload.split("|")]
    while len(arr) < 3:
        arr.append("")
    return arr[0], arr[1], arr[2]

def _stats3_from_text(t: str) -> Tuple[str,str,str]:
    # Грубый парсер: ищем три блока через запятую
    # Примеры: "30 очков, 11 подборов, 11-14 с игры"
    parts = [p.strip() for p in t.split(",")]
    if len(parts) >= 3:
        return parts[0].split()[0], parts[1].split()[0], parts[2].split()[0]
    # fallback — пусть вернутся как есть
    if len(parts) == 2:
        return parts[0], parts[1], ""
    if len(parts) == 1:
        return parts[0], "", ""
    return "", "", ""

def _ru_name_for(player: Dict[str,Any]) -> str:
    pid = str(player.get("personId") or "")
    ov = data.overrides_get_name_ru(pid)
    if ov:
        return ov
    # если override нет — берем displayName и транслит не делаем
    return data.display_name_for(player)

def _ensure_assets(player: Dict[str,Any]) -> Tuple[bytes, Optional[str]]:
    head_png = data.ensure_headshot_png(player) or b""
    logo_path = data.ensure_team_logo_png(player.get("teamId"))
    return head_png, logo_path

# -------------------------- ROUTES -------------------------------------------
@app.get("/api/telegram")
async def health(action: Optional[str] = None, secret: Optional[str] = None):
    if action == "refresh":
        cnt, src = data.refresh_players()
        return JSONResponse({"ok": True, "players": cnt, "source": src})
    return JSONResponse({"ok": True})

@app.post("/api/telegram")
async def hook(req: Request):
    body = await req.body()
    try:
        upd = json.loads(body.decode("utf-8","ignore"))
    except Exception:
        return PlainTextResponse("ok")

    msg = (upd.get("message") or upd.get("edited_message") or {})
    chat = (msg.get("chat") or {})
    text = (msg.get("text") or "").strip()
    chat_id = int(chat.get("id") or 0)

    # обработка ответов "[setname:PID]"
    reply = msg.get("reply_to_message")
    if reply and "setname:" in (reply.get("text") or "") and text:
        rep_text = reply.get("text") or ""
        try:
            marker = rep_text.split("[setname:",1)[1]
            pid = marker.split("]",1)[0].strip()
            if data.overrides_save_name_ru(pid, text):
                _tg_send_photo(chat_id, _ok_png("Имя сохранено"), "")
        except Exception:
            pass
        return PlainTextResponse("ok")

    cmd, payload = _parse_cmd(text)
    if not cmd:
        return PlainTextResponse("ok")

    # На всякий случай
    player_q, stats_text, extra = _split_payload(payload)

    # ищем игрока
    hits = data.find_player_by_name(player_q)
    if not hits:
        # ничего не нашли
        _tg_send_photo(chat_id, _err_png("Игрок не найден"), "")
        return PlainTextResponse("ok")

    p = hits[0]
    name_ru = _ru_name_for(p)
    head_png, logo_path = _ensure_assets(p)

    # три числа для карточки
    s1, s2, s3 = _stats3_from_text(stats_text or "")
    stats3 = (s1, s2, s3)

    # Рендер в зависимости от команды
    if cmd == "card":
        im = graphics.render_card(name_ru, stats3, head_png, logo_path)
    elif cmd == "cardbad":
        im = graphics.render_cardbad(name_ru, stats3, head_png, logo_path)
    elif cmd == "cards":
        im = graphics.render_cards(name_ru, stats3, extra, head_png, logo_path)
    elif cmd == "card2":
        # для card2 нужен второй игрок: payload формата "left | 30,11,11-14 || right | 34,10,13-18"
        # но чтобы не усложнять — возьмём того же игрока с обеих сторон если второй не задан
        right_q = extra or player_q
        r_hits = data.find_player_by_name(right_q)
        r = r_hits[0] if r_hits else p
        r_name = _ru_name_for(r)
        r_head, r_logo = _ensure_assets(r)
        im = graphics.render_card2(name_ru, stats3, head_png, logo_path,
                                   r_name, stats3, r_head, r_logo)
    else:
        return PlainTextResponse("ok")

    _tg_send_photo(chat_id, im, "")
    return PlainTextResponse("ok")

# простые «картинки-ответы» для статусов
from PIL import Image, ImageDraw, ImageFont

def _status_png(text: str, color=(32,180,90)) -> bytes:
    im = Image.new("RGBA", (800, 220), (0,0,0,0))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0,0,800,220], fill=color+(255,))
    f = ImageFont.truetype(os.path.join(ROOT_DIR,"fonts","Montserrat-Bold.ttf"), 44)
    w, h = draw.textbbox((0,0), text, font=f)[2:]
    draw.text(((800-w)//2, (220-h)//2), text, font=f, fill=(255,255,255))
    bio = io.BytesIO(); im.save(bio, format="PNG")
    return bio.getvalue()

def _ok_png(text: str) -> bytes:
    return _status_png(text, (32,180,90))

def _err_png(text: str) -> bytes:
    return _status_png(text, (200,70,70))
