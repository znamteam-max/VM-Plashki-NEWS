# /api/telegram.py  — Vercel Serverless Function (FastAPI) для Telegram webhook
# Маршруты:
#   GET  /api/telegram                      — health-check (JSON)
#   POST /api/telegram?secret=<WEBHOOK_SECRET> — вебхук Telegram (команда /card ...)
#
# Требуемые переменные окружения в Vercel (Production):
#   BOT_TOKEN       — токен бота от @BotFather
#   WEBHOOK_SECRET  — любая строка, должна совпадать с ?secret= в URL вебхука
#
# Пример установки вебхука:
#   https://api.telegram.org/bot<ТОКЕН>/deleteWebhook
#   https://api.telegram.org/bot<ТОКЕН>/setWebhook?url=https://<project>.vercel.app/api/telegram?secret=<WEBHOOK_SECRET>
#
# Формат команды:
#   /card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | (необязательная подпись)
#   Шаблоны: single, pair, single_note, impact, bad

import os
import re
import json
import traceback
import requests
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "hook")

# мачим число + необязательный % + короткую подпись
STAT_PAIR_RE = re.compile(r"(?P<num>[-+]?\d+(?:[.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})")


def parse_card(text: str):
    """
    Ожидаем:
      /card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | подпись
    Возвращаем: (name, stats_list[:6], template, note_or_None)
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
        if not m:
            continue
        num = m.group("num").replace(",", ".")
        label = (m.group("label") or "").strip()
        if m.group(2) == "%" and not label:
            label = "%"
        stats.append((num, label))
    return name, stats[:6], template, note


def tg_send_photo(chat_id: int, png_bytes: bytes, caption: str | None = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("card.png", png_bytes, "image/png")}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    r = requests.post(url, data=data, files=files, timeout=30)
    return r.ok, r.text


@app.get("/")
def health():
    # Открой в браузере: https://<project>.vercel.app/api/telegram
    return {"ok": True, "endpoint": "/api/telegram", "webhook": "POST ?secret=<WEBHOOK_SECRET>"}


@app.post("/")
async def webhook(request: Request, secret: str = Query(default="")):
    # 1) базовые проверки
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET:
        # маскируем как 404 (чтобы нельзя было дергать без секрета)
        raise HTTPException(status_code=404, detail="Not found")

    try:
        update = await request.json()
    except Exception:
        # если пришел не-JSON — просто игнор
        return JSONResponse({"ok": True})

    # 2) вытаскиваем сообщение
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return JSONResponse({"ok": True})

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    # 3) парсим команду
    parsed = parse_card(text)
    if not parsed:
        hint = ("Формат:\n"
                "/card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | подпись\n"
                "Шаблоны: single, pair, single_note, impact, bad")
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": hint}, timeout=15)
        except Exception:
            pass
        return JSONResponse({"ok": True})

    name, stats, template, note = parsed

    try:
        # 4) ленивые импорты тяжёлых частей (чтобы не падать при warm-up)
        from graphics import render_card
        from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png
    except Exception as e:
        # если неожиданно сломались импорты — отдадим текстом
        err = f"Import error: {e}"
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": err}, timeout=15)
        except Exception:
            pass
        # пробросим 200, чтобы телега не ддосила ретраями
        return JSONResponse({"ok": True, "error": "import"})

    # 5) находим игрока (лёгкий индекс в data.py)
    player = None
    try:
        player = find_player_by_name(name)
    except Exception:
        player = None

    if not player:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": "Игрок не найден. Уточните имя."}, timeout=15)
        except Exception:
            pass
        return JSONResponse({"ok": True})

    # 6) ассеты (логотип — PNG из репо или плейсхолдер; headshot — в /tmp)
    try:
        logo_path, team_colors = ensure_team_logo_png(player["team_id"])
    except Exception:
        logo_path, team_colors = "assets/icons/star.png", ("#FF6A00", "#1A1A1A", "#FFFFFF")

    try:
        head_path = ensure_headshot_png(player["id"], player["full_name"])
    except Exception:
        # совсем аварийный плейсхолдер головы
        head_path = "assets/icons/star.png"

    # 7) рендер PNG и отправка
    try:
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
        ok, resp = tg_send_photo(chat_id, png_bytes)
        if not ok:
            # сообщим текстом, если отправка фото не удалась
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": f"Ошибка отправки изображения: {resp}"}, timeout=15)
    except Exception as e:
        # на случай любой ошибки — текст + не роняем функцию
        err_txt = "Ошибка генерации карточки"
        try:
            # приложим краткий текст ошибки (без длинного трейсбэка)
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": f"{err_txt}: {e.__class__.__name__}"}, timeout=15)
        except Exception:
            pass

    return JSONResponse({"ok": True})
