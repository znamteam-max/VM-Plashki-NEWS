# app.py  — FastAPI вебхук под Vercel
import os, io, re, requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from graphics import render_card
from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "hook")

STAT_PAIR_RE = re.compile(r"(?P<num>[-+]?\d+(?:[.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})")

def parse_card(text: str):
    # /card Имя | 25 очков, 12 подборов | impact | необязательная подпись
    if not text.lower().startswith("/card"):
        return None
    body = text.split(" ", 1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2:
        return None
    name = parts[0]
    raw = parts[1]
    template = (parts[2].strip().lower() if len(parts) >= 3 and parts[2] else "single")
    note = (parts[3].strip() if len(parts) >= 4 and parts[3] else None)
    stats = []
    for token in re.split(r"[,;/\n]", raw):
        m = STAT_PAIR_RE.search(token.strip())
        if m:
            num = m.group("num").replace(",", ".")
            label = (m.group("label") or "").strip()
            if m.group(2) == "%" and not label:
                label = "%"
            stats.append((num, label))
    return name, stats[:6], template, note

def tg_send_photo(chat_id: int, png_bytes: bytes, caption: str = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("card.png", png_bytes, "image/png")}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    r = requests.post(url, data=data, files=files, timeout=30)
    return r.ok, r.text

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/webhook/{secret}")
async def webhook(request: Request, secret: str):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404)
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")

    update = await request.json()
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
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": hint})
        return JSONResponse({"ok": True})

    name, stats, template, note = parsed
    player = find_player_by_name(name)
    if not player:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": "Игрок не найден. Уточните имя."})
        return JSONResponse({"ok": True})

    try:
        logo_path, team_colors = ensure_team_logo_png(player["team_id"])
    except Exception:
        logo_path, team_colors = None, ("#FF6A00", "#1A1A1A", "#FFFFFF")

    head_path = ensure_headshot_png(player["id"], player["full_name"])
    png_bytes = render_card(
        template=template,
        player_name=player["display"] or player["full_name"],
        team_name=player["team_name"],
        team_logo_path=logo_path or "assets/icons/star.png",
        team_colors=team_colors,
        headshot_path=head_path,
        stats=stats,
        note=note,
    )
    ok, resp = tg_send_photo(chat_id, png_bytes)
    if not ok:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": f"Ошибка отправки: {resp}"})
    return JSONResponse({"ok": True})
