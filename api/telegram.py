# api/telegram.py  — FastAPI webhook для Vercel (Free)
import os, io, re, json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import requests

# используем наши модули генерации
from graphics import render_card
from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "hook")  # часть пути, чтобы не светить токен

STAT_PAIR_RE = re.compile(r"(?P<num>[-+]?\d+(?:[.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})")

def parse_card_command(text: str):
    """
    Ожидаем формат:
    /card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | (необязательное уточнение)
    """
    if not text.lower().startswith("/card"):
        return None
    body = text.split(" ", 1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2:
        return None
    name = parts[0]
    raw_stats = parts[1]

    # шаблон
    template = "single"
    if len(parts) >= 3 and parts[2]:
        template = parts[2].strip().lower()

    note = None
    if len(parts) >= 4 and parts[3]:
        note = parts[3].strip()

    # парсим статистику
    stats = []
    for token in re.split(r"[,;/\n]", raw_stats):
        m = STAT_PAIR_RE.search(token.strip())
        if not m:
            continue
        num = m.group("num").replace(",", ".")
        label = m.group("label").strip() or ""
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

@app.get("/api/telegram/healthz")
def healthz():
    return {"ok": True}

@app.post(f"/api/telegram/{{secret}}")
async def webhook(request: Request, secret: str):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not set")

    update = await request.json()
    message = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not message:
        return JSONResponse({"ok": True})

    chat_id = message["chat"]["id"]
    text = message.get("text") or ""

    parsed = parse_card_command(text)
    if not parsed:
        # Подсказка по формату
        hint = (
            "Используйте формат:\n"
            "/card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | Доп. подпись\n\n"
            "Шаблоны: single, pair, single_note, impact, bad"
        )
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": hint})
        return JSONResponse({"ok": True})

    name, stats, template, note = parsed

    player = find_player_by_name(name)
    if not player:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": "Игрок не найден. Уточните имя."})
        return JSONResponse({"ok": True})

    # ассеты (без CairoSVG: если SVG недоступен — вернём простой плейсхолдер)
    try:
        logo_path, team_colors = ensure_team_logo_png(player["team_id"])
    except Exception:
        # fallback: нейтральные цвета и без логотипа
        logo_path, team_colors = None, ("#FF6A00", "#1A1A1A", "#FFFFFF")

    head_path = ensure_headshot_png(player["id"], player["full_name"])

    png_bytes = render_card(
        template=template,
        player_name=player["display"] or player["full_name"],
        team_name=player["team_name"],
        team_logo_path=logo_path if logo_path else "assets/icons/star.png",  # плейсхолдер
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
