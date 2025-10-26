# /api/telegram.py — Vercel Serverless (FastAPI) для Telegram webhook
# Работают пути:
#  GET  /api/telegram
#  GET  /api/telegram/healthz
#  POST /api/telegram?secret=...             (рекомендуемый вебхук)
#  POST /api/telegram/webhook/<secret>       (альтернатива)
#
# Требуемые переменные окружения (Vercel → Settings → Environment Variables, Production):
#   BOT_TOKEN       — токен бота от @BotFather
#   WEBHOOK_SECRET  — строка-секрет, должна совпадать с тем, что в URL вебхука

import os
import re
import requests
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "hook")

# число + (опц.)% + короткая подпись
STAT_PAIR_RE = re.compile(
    r"(?P<num>[-+]?\d+(?:[.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})"
)

# ---------- разбор команды ----------
def parse_card(text: str):
    """
    Ожидается:
      /card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | подпись(опц.)
    Возвращает: (name, stats[:6], template, note)
    """
    if not text or not text.lower().startswith("/card"):
        return None
    body = text.split(" ", 1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2:
        return None

    name = parts[0]
    raw_stats = parts[1]
    template = (parts[2].strip().lower() if len(parts) >= 3 and parts[2] else "single")
    note = (parts[3].strip() if len(parts) >= 4 and parts[3] else None)

    stats = []
    for token in re.split(r"[,;/\n]", raw_stats):
        m = STAT_PAIR_RE.search(token.strip())
        if m:
            num = m.group("num").replace(",", ".")
            label = (m.group("label") or "").strip()
            if m.group(2) == "%" and not label:
                label = "%"
            stats.append((num, label))
    return name, stats[:6], template, note

# ---------- отправка PNG как файла (без сжатия) ----------
def tg_send_png(chat_id: int, png_bytes: bytes, caption: str | None = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    files = {"document": ("card.png", png_bytes, "image/png")}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    r = requests.post(url, data=data, files=files, timeout=30)
    return r.ok, r.text

# ---------- общий обработчик апдейта ----------
async def handle_update(update: dict) -> JSONResponse:
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return JSONResponse({"ok": True})

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    parsed = parse_card(text)
    if not parsed:
        hint = ("Формат:\n"
                "/card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | подпись\n"
                "Шаблоны: single, pair, single_note, impact, bad")
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": hint},
                timeout=15,
            )
        except Exception:
            pass
        return JSONResponse({"ok": True})

    # ленивые импорты, чтобы не падать при холодном старте
    from graphics import render_card
    from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png

    name, stats, template, note = parsed
    player = find_player_by_name(name)
    if not player:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": "Игрок не найден. Уточните имя."},
                timeout=15,
            )
        except Exception:
            pass
        return JSONResponse({"ok": True})

    # ассеты (логотип — PNG из репо/плейсхолдер; headshot — кэш в /tmp)
    logo_path, team_colors = ensure_team_logo_png(player["team_id"])
    head_path = ensure_headshot_png(player["id"], player["full_name"])

    # рендер и отправка
    png_bytes = render_card(
        template=template,
        player_name=player["display"] or player["full_name"],
        team_name=player["team_name"],
        team_logo_path=logo_path,
        team_colors=team_colors,
        headshot_path=head_path,
        stats=stats,
        note=note,
    )

    ok, resp = tg_send_png(chat_id, png_bytes)
    if not ok:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage}",
            json={"chat_id": chat_id, "text": f"Ошибка отправки изображения: {resp}"},
            timeout=15,
        )
    return JSONResponse({"ok": True})

# ---------- health ----------
@app.get("/")
@app.get("/api/telegram")
@app.get("/api/telegram/healthz")
def health():
    return {
        "ok": True,
        "endpoints": [
            "GET  /api/telegram",
            "GET  /api/telegram/healthz",
            "POST /api/telegram?secret=...",
            "POST /api/telegram/webhook/<secret>",
        ],
    }

# ---------- webhook: секрет в query ----------
@app.post("/")
@app.post("/api/telegram")
async def webhook_query(request: Request, secret: str = Query(default="")):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET:
        # маскируем как 404, чтобы нельзя было дёргать без секрета
        raise HTTPException(status_code=404, detail="Not found")
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    return await handle_update(update)

# ---------- webhook: секрет в path ----------
@app.post("/webhook/{secret}")
@app.post("/api/telegram/webhook/{secret}")
async def webhook_path(request: Request, secret: str):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    return await handle_update(update)
