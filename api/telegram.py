# api/telegram.py
from __future__ import annotations

import os
import re
import json
import uuid
import asyncio
from typing import Any, Dict, Optional, List, Tuple

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
app = FastAPI(title="Telegram Bot Webhook", version="1.2.1")

def _ok(payload: Dict[str, Any], code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=code, headers={"Cache-Control": "no-store"})

def _extract_secret(request: Request) -> str:
    # Telegram может прислать секрет в заголовке (официально) или у вас в query (?secret=)
    hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if hdr:
        return hdr
    return request.query_params.get("secret") or ""

def _check_secret(request: Request) -> None:
    sec = _extract_secret(request)
    if TELEGRAM_SECRET and sec != TELEGRAM_SECRET:
        raise HTTPException(status_code=401, detail="bad secret")

# ==== TG API helpers ==========================================================
async def tg_call(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
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
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return json.loads(raw)

    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        return {"ok": False, "error": repr(e)}

def _multipart_body(fields: Dict[str, str], files: List[Tuple[str, bytes, str, str]]) -> Tuple[bytes, str]:
    """
    files: list of (fieldname, file_bytes, filename, content_type)
    """
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    CRLF = b"\r\n"
    body = bytearray()
    bnd = ("--" + boundary).encode()

    # fields
    for k, v in fields.items():
        body += bnd + CRLF
        body += f'Content-Disposition: form-data; name="{k}"'.encode() + CRLF + CRLF
        body += (v if isinstance(v, str) else str(v)).encode("utf-8") + CRLF

    # files
    for field, content, filename, content_type in files:
        body += bnd + CRLF
        body += f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode() + CRLF
        body += f"Content-Type: {content_type}".encode() + CRLF + CRLF
        body += content + CRLF

    # end
    body += bnd + b"--" + CRLF
    return bytes(body), boundary

async def tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None, parse_mode: Optional[str] = None) -> None:
    params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_to:
        params["reply_to_message_id"] = reply_to
        params["allow_sending_without_reply"] = True
    await tg_call("sendMessage", params)

async def tg_send_photo_bytes(chat_id: int, png_bytes: bytes, caption: Optional[str] = None,
                              reply_to: Optional[int] = None, parse_mode: Optional[str] = "HTML") -> Dict[str, Any]:
    if not TG_API_BASE:
        return {"ok": False, "error": "BOT_TOKEN is empty"}
    import urllib.request
    url = f"{TG_API_BASE}/sendPhoto"
    fields: Dict[str, str] = {"chat_id": str(chat_id)}
    if caption: fields["caption"] = caption
    if parse_mode: fields["parse_mode"] = parse_mode
    if reply_to:
        fields["reply_to_message_id"] = str(reply_to)
        fields["allow_sending_without_reply"] = "true"
    body, boundary = _multipart_body(fields, files=[
        ("photo", png_bytes, "card.png", "image/png")
    ])

    def _do() -> Dict[str, Any]:
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "Connection": "close",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return json.loads(raw)

    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        return {"ok": False, "error": repr(e)}

# ==== BUSINESS LOGIC ==========================================================
from data import (
    refresh_players, find_player_by_name, ensure_headshot_png
)

# ленивый импорт графики, чтобы refresh не падал, если Pillow не установлен
try:
    from graphics import render_card  # Pillow внутри graphics
    HAVE_GRAPHICS = True
except Exception as e:
    print("graphics import failed:", repr(e), flush=True)
    render_card = None  # type: ignore
    HAVE_GRAPHICS = False

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /find <имя/фамилия> — найти игрока (например: /find Doncic)\n"
    "• /card <имя> | <метрики через запятую> | [impact|single]\n"
    "  пример: /card wembanyama | 10 очков, 12 передач, 5 перехватов | impact\n"
    "• /help — это сообщение\n"
)

_CARD_CMD_RE = re.compile(r"^/card(?:@[A-Za-z0-9_]+)?\s*(.*)$", re.IGNORECASE)

def _parse_card(text: str) -> Optional[Dict[str, str]]:
    m = _CARD_CMD_RE.match(text.strip())
    if not m: return None
    body = m.group(1).strip()
    if not body:
        return {"name": "", "stats": "", "tpl": "single"}
    parts = [p.strip() for p in body.split("|")]
    name = parts[0] if len(parts) >= 1 else ""
    stats = parts[1] if len(parts) >= 2 else ""
    tpl = (parts[2] if len(parts) >= 3 else "single").lower()
    if tpl not in ("single", "impact"):
        tpl = "single"
    return {"name": name, "stats": stats, "tpl": tpl}

def _stats_from_text(s: str) -> List[Tuple[str, str]]:
    """
    '10 очков, 12 передач, 5 перехватов'
      -> [("10","очков"),("12","передач"),("5","перехватов")]
    """
    out: List[Tuple[str, str]] = []
    for chunk in [c.strip() for c in s.split(",") if c.strip()]:
        parts = chunk.split()
        if not parts: continue
        val = parts[0]
        lab = " ".join(parts[1:]) if len(parts) > 1 else ""
        out.append((val, lab))
    # максимум 6 блоков, чтобы влезало
    return out[:6] if out else [("—", "")]

async def _build_and_send_card(chat_id: int, message_id: int, query: str, stats_text: str, tpl: str) -> None:
    if not HAVE_GRAPHICS or render_card is None:
        await tg_send_message(
            chat_id,
            "Графический модуль недоступен. Установите Pillow (requirements.txt: Pillow==10.4.0) и задеплойте заново.",
            reply_to=message_id
        )
        return

    res = find_player_by_name(query)
    if not res:
        await tg_send_message(chat_id, f"Не нашёл игрока: {query}", reply_to=message_id)
        return

    # простой выбор «лучшего» совпадения: активные выше, короче имя — выше
    p = sorted(res, key=lambda x: (0 if x.get("isActive", True) else 1,
                                   len((x.get("displayName") or '').lower())))[0]
    dn = p.get("displayName") or (p.get("firstName","") + " " + p.get("lastName","")).strip()
    team_id = str(p.get("teamId") or "0")

    # корректный headshot PNG
    headshot_url = ensure_headshot_png(p, size="520x380")

    # логотип команды (если известен)
    team_logo_url = None
    if team_id and team_id != "0":
        # пример: DAL -> 1610612742; глобальный логотип L
        team_logo_url = f"https://cdn.nba.com/logos/nba/{team_id}/global/L/logo.png"

    # метрики
    stats_list = _stats_from_text(stats_text)

    # собираем карточку
    try:
        png = render_card(
            template=tpl,
            player_name=dn,
            team_name="",                       # можно вывести отдельно, пока не используется
            team_logo_path_or_url=team_logo_url,
            team_colors=None,                   # пусть подберутся из логотипа
            headshot_path_or_url=headshot_url,
            stats=stats_list,
            note=None,
        )
    except Exception as e:
        await tg_send_message(chat_id, f"Ошибка рендера: {e}", reply_to=message_id)
        return

    # отправляем PNG как файл
    resp = await tg_send_photo_bytes(chat_id, png, caption=None, reply_to=message_id, parse_mode="HTML")
    if not resp.get("ok"):
        await tg_send_message(chat_id, f"Не удалось отправить картинку: {resp.get('error','unknown')}", reply_to=message_id)

async def handle_update(update: Dict[str, Any]) -> None:
    try:
        msg = update.get("message") or update.get("edited_message") or {}
        if not msg:
            return
        chat_id = (msg.get("chat") or {}).get("id")
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
            lines: List[str] = []
            for p in res[:10]:
                pid = p.get("personId", "")
                dn = p.get("displayName") or (p.get("firstName","") + " " + p.get("lastName","")).strip()
                photo = ensure_headshot_png(p, size="260x190")
                lines.append(f"• {dn} (id: {pid})\n  {photo}")
            await tg_send_message(chat_id, "Найдено:\n" + "\n".join(lines), reply_to=msg.get("message_id"))
            return

        if text.lower().startswith("/card"):
            parsed = _parse_card(text)
            if not parsed or not parsed["name"]:
                await tg_send_message(
                    chat_id,
                    "Использование: /card <имя> | <метрики через запятую> | [impact|single]\n"
                    "Пример: /card wembanyama | 10 очков, 12 передач, 5 перехватов | impact",
                    reply_to=msg.get("message_id"),
                )
                return
            await _build_and_send_card(chat_id, msg.get("message_id"), parsed["name"], parsed["stats"], parsed["tpl"])
            return

        await tg_send_message(chat_id, "Не понял команду.\n\n" + HELP_TEXT, reply_to=msg.get("message_id"))

    except Exception as e:
        print("handle_update error:", repr(e), flush=True)

# ==== ROUTES =================================================================
@app.get("/api/telegram")
async def get_router(request: Request) -> JSONResponse:
    """
    Совместимость: /api/telegram?action=refresh&secret=...
    (доп. health не нужен)
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
