# api/telegram.py
from __future__ import annotations

import os
import json
import asyncio
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

# ==== ENV / CONFIG ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEGRAM_SECRET = (
    os.getenv("WEBHOOK_SECRET") or
    os.getenv("TELEGRAM_SECRET") or
    "hook-123"
).strip()

TG_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

# ==== FASTAPI APP =============================================================
app = FastAPI(title="Telegram Bot Webhook", version="1.0.0")

def _ok(payload: Dict[str, Any], code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=code, headers={"Cache-Control": "no-store"})

def _extract_secret(request: Request) -> str:
    # 1) официальный способ: в заголовке
    hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if hdr:
        return hdr
    # 2) совместимость с прежним форматом: ?secret=...
    return request.query_params.get("secret") or ""

def _check_secret(request: Request) -> None:
    sec = _extract_secret(request)
    if TELEGRAM_SECRET and sec != TELEGRAM_SECRET:
        raise HTTPException(status_code=401, detail="bad secret")

# ==== MINIMAL TG API CLIENT (без внешних зависимостей) =======================
async def tg_call(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Отправка запроса в Telegram Bot API.
    Сделано через asyncio.to_thread + urllib, чтобы не тянуть httpx/aiohttp.
    """
    if not TG_API_BASE:
        return {"ok": False, "error": "BOT_TOKEN is empty"}

    import urllib.request, urllib.parse

    url = f"{TG_API_BASE}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")

    def _do() -> Dict[str, Any]:
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Connection": "close",
            }
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return json.loads(raw)

    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        return {"ok": False, "error": repr(e)}

async def tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None) -> None:
    params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to:
        params["reply_to_message_id"] = reply_to
        params["allow_sending_without_reply"] = True
    await tg_call("sendMessage", params)

# ==== BUSINESS LOGIC ==========================================================
# Импортируем нужные функции из data.py (ваш обновлённый модуль)
from data import (
    refresh_players, find_player_by_name, ensure_headshot_png
)

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /find <имя/фамилия> — найти игрока по имени (например: /find Doncic)\n"
    "• /help — это сообщение\n"
)

async def handle_update(update: Dict[str, Any]) -> None:
    """
    Простая обработка /start, /help и /find.
    Важно: не бросать исключения — чтобы вебхук не получал 500.
    """
    try:
        msg = update.get("message") or update.get("edited_message") or {}
        if not msg:
            # например callback_query и т.п. — можно расширить позже
            return

        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id:
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return

        if text == "/start":
            await tg_send_message(chat_id, "Бот на связи ✅\n\n" + HELP_TEXT, reply_to=msg.get("message_id"))
            return

        if text == "/help":
            await tg_send_message(chat_id, HELP_TEXT, reply_to=msg.get("message_id"))
            return

        if text.lower().startswith("/find"):
            parts = text.split(maxsplit=1)
            if len(parts) == 1 or not parts[1].strip():
                await tg_send_message(chat_id, "Использование: /find <имя/фамилия>", reply_to=msg.get("message_id"))
                return
            query = parts[1].strip()
            res = find_player_by_name(query)
            if not res:
                await tg_send_message(chat_id, f"Ничего не найдено по запросу: {query}", reply_to=msg.get("message_id"))
                return

            # Отдадим топ-10
            lines: List[str] = []
            for p in res[:10]:
                pid = p.get("personId", "")
                dn = p.get("displayName") or (p.get("firstName","") + " " + p.get("lastName","")).strip()
                photo = ensure_headshot_png(p, size="260x190")
                lines.append(f"• {dn} (id: {pid})\n  {photo}")
            await tg_send_message(chat_id, "Найдено:\n" + "\n".join(lines), reply_to=msg.get("message_id"))
            return

        # Иначе — подсказка
        await tg_send_message(chat_id, "Не понял команду.\n\n" + HELP_TEXT, reply_to=msg.get("message_id"))

    except Exception as e:
        # Логируем в stdout, но не падаем
        print("handle_update error:", repr(e), flush=True)

# ==== ROUTES =================================================================
@app.get("/api/telegram/health")
async def health() -> JSONResponse:
    return _ok({"ok": True})

@app.get("/api/telegram")
async def get_router(request: Request) -> JSONResponse:
    """
    Совместимость: /api/telegram?action=refresh&secret=...
    """
    _check_secret(request)
    action = request.query_params.get("action") or ""
    if action == "refresh":
        try:
            count, meta = refresh_players(drop_cache=False)
            return _ok({"ok": True, "refreshed": True, "players_indexed": count, **meta})
        except Exception as e:
            return _ok({"ok": False, "error": repr(e)}, code=500)
    # по умолчанию — просто health
    return _ok({"ok": True, "route": "telegram-get"})

@app.post("/api/telegram")
async def webhook_query(request: Request, background: BackgroundTasks) -> JSONResponse:
    """
    Основной вебхук: быстро возвращаем 200, обработку пускаем в фон.
    """
    _check_secret(request)
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")

    # быстрый ответ, чтобы Telegram не ретраил
    background.add_task(handle_update, update)
    return _ok({"ok": True})
