# api/telegram.py
from __future__ import annotations

import os
import re
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
app = FastAPI(title="Telegram Bot Webhook", version="1.1.0")

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

# ==== MINIMAL TG API CLIENT ===================================================
async def tg_call(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Простой вызов Telegram Bot API.
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

async def tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None, parse_mode: Optional[str] = None) -> None:
    params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_to:
        params["reply_to_message_id"] = reply_to
        params["allow_sending_without_reply"] = True
    await tg_call("sendMessage", params)

async def tg_send_photo(chat_id: int, photo: str, caption: Optional[str] = None,
                        reply_to: Optional[int] = None, parse_mode: Optional[str] = "HTML") -> None:
    params: Dict[str, Any] = {"chat_id": chat_id, "photo": photo}
    if caption:
        params["caption"] = caption
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_to:
        params["reply_to_message_id"] = reply_to
        params["allow_sending_without_reply"] = True
    await tg_call("sendPhoto", params)

# ==== BUSINESS LOGIC ==========================================================
from data import (
    refresh_players, find_player_by_name, ensure_headshot_png
)

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /find <имя/фамилия> — найти игрока по имени (например: /find Doncic)\n"
    "• /card <имя> | <текст> | single — карточка игрока с фото (пример: /card wembanyama | 10 очков, 12 передач | single)\n"
    "• /help — это сообщение\n"
)

_CARD_CMD_RE = re.compile(r"^/card(?:@[A-Za-z0-9_]+)?\s*(.*)$", re.IGNORECASE)

def _parse_card(text: str) -> Optional[Dict[str, str]]:
    """
    Разбор строки после /card:
      "<name> | <caption> | <mode?>"
    Возвращает dict с ключами: name, caption, mode
    """
    m = _CARD_CMD_RE.match(text.strip())
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return {"name": "", "caption": "", "mode": "single"}
    parts = [p.strip() for p in body.split("|")]
    name = parts[0] if len(parts) >= 1 else ""
    caption = parts[1] if len(parts) >= 2 else ""
    mode = (parts[2] if len(parts) >= 3 else "single").lower()
    if mode not in ("single", "team", "grid", "solo"):
        mode = "single"
    return {"name": name, "caption": caption, "mode": mode}

def _best_player_match(results: List[Dict[str, Any]], q: str) -> Dict[str, Any]:
    """
    Простой выбор «лучшего» совпадения:
      1) активные игроки сверху
      2) короче displayName — чуть выше
      3) базовый — первый
    """
    ql = (q or "").strip().lower()
    def key(p: Dict[str, Any]):
        dn = (p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}".strip()).lower()
        return (
            0 if p.get("isActive", True) else 1,
            abs(len(dn) - len(ql)),  # грубая эвристика близости
            len(dn),
        )
    return sorted(results, key=key)[0]

async def handle_update(update: Dict[str, Any]) -> None:
    """
    Обработка /start, /help, /find, /card.
    """
    try:
        msg = update.get("message") or update.get("edited_message") or {}
        if not msg:
            return

        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id:
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return

        # /start
        if text == "/start":
            await tg_send_message(chat_id, "Бот на связи ✅\n\n" + HELP_TEXT, reply_to=msg.get("message_id"))
            return

        # /help
        if text == "/help":
            await tg_send_message(chat_id, HELP_TEXT, reply_to=msg.get("message_id"))
            return

        # /find <query>
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

            lines: List[str] = []
            for p in res[:10]:
                pid = p.get("personId", "")
                dn = p.get("displayName") or (p.get("firstName","") + " " + p.get("lastName","")).strip()
                photo = ensure_headshot_png(p, size="260x190")
                lines.append(f"• {dn} (id: {pid})\n  {photo}")
            await tg_send_message(chat_id, "Найдено:\n" + "\n".join(lines), reply_to=msg.get("message_id"))
            return

        # /card <name> | <caption> | <mode?>
        if text.lower().startswith("/card"):
            parsed = _parse_card(text)
            if not parsed or not parsed["name"]:
                await tg_send_message(
                    chat_id,
                    "Использование: /card <имя> | <текст> | single\nНапример: /card wembanyama | 10 очков, 12 передач | single",
                    reply_to=msg.get("message_id"),
                )
                return

            query = parsed["name"]
            caption_text = parsed["caption"]
            mode = parsed["mode"]  # сейчас поддерживаем только single (один игрок/фото)

            res = find_player_by_name(query)
            if not res:
                await tg_send_message(chat_id, f"Не нашёл игрока: {query}", reply_to=msg.get("message_id"))
                return

            p = _best_player_match(res, query)
            dn = p.get("displayName") or (p.get("firstName","") + " " + p.get("lastName","")).strip()
            photo = ensure_headshot_png(p, size="520x380")  # крупнее, для карточки

            # Подпись — HTML
            caption = f"<b>{dn}</b>"
            if caption_text:
                caption += f"\n{caption_text}"

            await tg_send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_to=msg.get("message_id"),
                parse_mode="HTML",
            )
            return

        # Иначе — подсказка
        await tg_send_message(chat_id, "Не понял команду.\n\n" + HELP_TEXT, reply_to=msg.get("message_id"))

    except Exception as e:
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

    background.add_task(handle_update, update)
    return _ok({"ok": True})
