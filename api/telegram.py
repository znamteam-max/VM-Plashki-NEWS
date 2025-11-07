# api/telegram.py
# FastAPI endpoint for Telegram webhook with robust logging, 200-OK-on-error,
# diagnostics, refresh, and a minimal /card flow with ForceReply for Russian display name.

from __future__ import annotations
import os, io, json, time, uuid, traceback, re
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from PIL import Image

# -------------------- ENV & DEBUG --------------------
DEBUG = os.getenv("DEBUG", "0") == "1"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

def _log(*args: Any) -> None:
    try:
        print("[tg]", *args, flush=True)
    except Exception:
        pass

def _log_json(tag: str, obj: Any, limit: int = 2000) -> None:
    try:
        s = json.dumps(obj, ensure_ascii=False)
        if len(s) > limit:
            s = s[:limit] + "...(trunc)"
        _log(tag, s)
    except Exception as e:
        _log(tag, "json_err:", repr(e))

# -------------------- Imports with safety --------------------
boot_error: Optional[str] = None
brand_warn: Optional[str] = None

try:
    from data import (
        get_players, get_players_index, refresh_players,
        find_player_by_name,
    )
except Exception as e:
    boot_error = f"data import error: {repr(e)}\n{traceback.format_exc()}"
    # Provide safe fallbacks
    def get_players(*a, **k): return []
    def get_players_index(*a, **k): return {}
    def refresh_players(*a, **k): return 0, {"ok": False, "error": "no_data_module"}
    def find_player_by_name(q: str): return []

# Optional helpers from data (not guaranteed to exist in all versions)
def _try_import_from_data(name: str):
    try:
        from data import __dict__ as _d
        return _d.get(name)
    except Exception:
        return None

ensure_headshot_png = _try_import_from_data("ensure_headshot_png")
ensure_team_logo_png = _try_import_from_data("ensure_team_logo_png")
display_name_for     = _try_import_from_data("display_name_for")
set_display_name_override = _try_import_from_data("set_display_name_override")
set_team_override    = _try_import_from_data("set_team_override")
get_team_override    = _try_import_from_data("get_team_override")

try:
    from team_brand import team_colors_for, color_name_for
except Exception as e:
    if not boot_error:
        boot_error = f"team_brand import error: {repr(e)}\n{traceback.format_exc()}"
    # Safe fallbacks
    def team_colors_for(team_id: str) -> Tuple[str,str,str]:
        # primary, dark, light
        return ("#007ACC", "#005EA8", "#66B2FF")
    def color_name_for(hex_color: str) -> str:
        return "синий"

try:
    import graphics
except Exception as e:
    if not boot_error:
        boot_error = f"graphics import error: {repr(e)}\n{traceback.format_exc()}"

# -------------------- HTTP client helpers --------------------
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _tg_post(method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        url = f"{TELEGRAM_API}/{method}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "ignore")
        if DEBUG:
            _log("[TG->]", method, "payload=", json.dumps(payload, ensure_ascii=False)[:1200])
            _log("[TG<-]", raw[:1200])
        return json.loads(raw)
    except Exception:
        _log("[TG] send error:", traceback.format_exc())
        return None

def _tg_send_message(chat_id: int, text: str, parse_mode: Optional[str]=None, reply_to_id: Optional[int]=None, reply_markup: Optional[Dict]=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_to_id: payload["reply_to_message_id"] = reply_to_id
    if reply_markup: payload["reply_markup"] = reply_markup
    return _tg_post("sendMessage", payload)

def _tg_send_chat_action(chat_id: int, action: str = "typing"):
    return _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})

# multipart/form-data for sending PNG as a document (preserves transparency)
def _tg_send_png_document(chat_id: int, filename: str, png_bytes: bytes, caption: Optional[str] = None):
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    parts: List[bytes] = []

    def add_field(name: str, value: str):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append((f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode())
        parts.append((value + "\r\n").encode())

    def add_file(name: str, filename: str, content_type: str, data: bytes):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append((f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n').encode())
        parts.append((f"Content-Type: {content_type}\r\n\r\n").encode())
        parts.append(data)
        parts.append(b"\r\n")

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)

    add_file("document", filename, "image/png", png_bytes)
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)

    try:
        url = f"{TELEGRAM_API}/sendDocument"
        req = Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
        if DEBUG:
            _log("[TG->] sendDocument bytes=", len(png_bytes))
            _log("[TG<-]", raw[:800])
        return json.loads(raw)
    except Exception:
        _log("[TG] sendDocument error:", traceback.format_exc())
        return None

# -------------------- FastAPI app & middleware --------------------
app = FastAPI()

@app.middleware("http")
async def _req_logger(request: Request, call_next):
    rid = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    try:
        if DEBUG:
            body = await request.body()
            _log(f"[RID={rid}] {request.method} {request.url}")
            if body:
                b = body.decode("utf-8", errors="ignore")
                _log(f"[RID={rid}] body:", (b[:2000] + ("...(trunc)" if len(b) > 2000 else "")))
        resp = await call_next(request)
        if DEBUG:
            _log(f"[RID={rid}] {resp.status_code}")
        return resp
    except Exception:
        _log(f"[RID={rid}] unhandled EXC:\n{traceback.format_exc()}")
        # Никогда не отдаём 500 Telegram'у/монитору
        return PlainTextResponse("OK", status_code=200)

# -------------------- Utils: parsing & images --------------------
HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /help — это сообщение\n"
    "• /find <имя/фамилия> — найти игрока (например: /find Doncic)\n"
    "• /card <имя> | <метрики через запятую> — собрать плашку (PNG, прозрачный фон)\n"
    "  пример: /card Devin Booker | 10 очков, 12 передач\n"
)

def _parse_stats_blob(blob: str) -> List[Tuple[str, str]]:
    # "10 очков, 12 передач, 7 подборов" -> [("10","ОЧКОВ"), ("12","ПЕРЕДАЧ"), ...]
    stats: List[Tuple[str, str]] = []
    for chunk in [c.strip() for c in blob.split(",") if c.strip()]:
        m = re.match(r"^\s*([+\-]?\d+(?:[.,]\d+)?)\s*(.*)$", chunk)
        if m:
            num = m.group(1).replace(",", ".")
            label = (m.group(2) or "").strip()
            stats.append((num, label))
        else:
            # fallback: всё в label
            stats.append((chunk, ""))
    return stats

def _open_image_from_any(source: Any) -> Optional[Image.Image]:
    try:
        if source is None:
            return None
        if isinstance(source, (bytes, bytearray)):
            return Image.open(io.BytesIO(source)).convert("RGBA")
        if isinstance(source, str):
            if os.path.exists(source):
                return Image.open(source).convert("RGBA")
            # could be URL, but we avoid external fetch here
            return None
        return None
    except Exception:
        _log("[img] open error:", traceback.format_exc())
        return None

def _ensure_headshot_image(person_id: str) -> Optional[Image.Image]:
    # Prefer helper from data.py
    if callable(ensure_headshot_png):
        try:
            path_or_bytes = ensure_headshot_png(str(person_id))  # do not pass unknown kwargs
            return _open_image_from_any(path_or_bytes)
        except Exception:
            _log("[tg] headshot ensure err", person_id, traceback.format_exc())
    # Fallback: no image
    return None

def _ensure_team_logo_image(team_id: str) -> Optional[Image.Image]:
    # Prefer helper from data.py
    if callable(ensure_team_logo_png):
        try:
            path_or_bytes = ensure_team_logo_png(str(team_id))
            img = _open_image_from_any(path_or_bytes)
            if img:
                return img
        except Exception:
            _log("[tg] logo ensure err", team_id, traceback.format_exc())
    # Fallback to local cache path
    local = os.path.join("assets", "cache", f"logo_{team_id}.png")
    if os.path.exists(local):
        try:
            return Image.open(local).convert("RGBA")
        except Exception:
            pass
    return None

def _current_display_name(p: Dict[str, Any]) -> str:
    # prefer data.display_name_for if exists
    try:
        if callable(display_name_for):
            name = display_name_for(p)
            if name: return name
    except Exception:
        pass
    fn = (p.get("firstName") or "").strip()
    ln = (p.get("lastName") or "").strip()
    disp = (p.get("displayName") or "").strip()
    if disp: return disp
    nm = (fn + " " + ln).strip()
    return nm if nm else (p.get("personId") or "PLAYER").strip()

# -------------------- Command handlers --------------------
def _force_reply(text: str) -> Dict[str, Any]:
    # ForceReply так, чтобы в моб. клиенте курсор сразу был в ответе
    return {"force_reply": True, "input_field_placeholder": text}

def _request_russian_name(chat_id: int, msg_id: int, player: Dict[str, Any]) -> None:
    pid = str(player.get("personId") or "")
    base = f"Как подписать игрока {_current_display_name(player)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]"
    _tg_send_message(chat_id, base, reply_to_id=msg_id, reply_markup=_force_reply("Напишите имя на русском"))

def _save_russian_name_from_reply(update: Dict[str, Any]) -> Optional[str]:
    """
    Если пользователь ответил на наше ForceReply сообщение вида:
    "... [setname:<pid>]", вытащим pid и вернём русский текст-имя.
    """
    message = update.get("message") or {}
    reply_to = message.get("reply_to_message") or {}
    rt_text = (reply_to.get("text") or "") + " " + " ".join(reply_to.get("entities", []) or [])
    m = re.search(r"\[setname:(\d+)\]", rt_text)
    if not m:
        return None
    pid = m.group(1)
    ru_name = (message.get("text") or "").strip()
    if not ru_name:
        return None
    # persist override if function exists
    if callable(set_display_name_override):
        try:
            ok = set_display_name_override(pid, ru_name)
            if DEBUG: _log("[override] set_display_name", pid, ru_name, "ok=", ok)
        except Exception:
            _log("[override] set_display_name error:", traceback.format_exc())
    return pid

def _find_best_player_by_name(q: str) -> Optional[Dict[str, Any]]:
    # Use data.find_player_by_name if present; else local scan
    try:
        if callable(find_player_by_name):
            items = find_player_by_name(q) or []
            if items:
                return items[0]
    except Exception:
        pass
    # Local fallback
    ql = q.strip().lower()
    for p in get_players():
        disp = _current_display_name(p).lower()
        if ql in disp:
            return p
    return None

def _parse_card_command(text: str) -> Tuple[str, List[Tuple[str,str]]]:
    # "/card Devin Booker | 10 очков, 12 передач"
    body = text.split(" ", 1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) >= 2:
        name = parts[0]
        stats = _parse_stats_blob(parts[1])
        return name, stats
    # Fallback: name only
    return (body.strip() or ""), []

# -------------------- Routes --------------------
@app.get("/api/telegram")
async def telegram_get(request: Request):
    if request.query_params.get("secret") != WEBHOOK_SECRET:
        return JSONResponse({"ok": False, "detail": "bad secret"}, status_code=400)

    action = request.query_params.get("action") or ""
    if action == "diag":
        mods = {"graphics": "ok" if 'graphics' in globals() else "error",
                "team_brand":"ok"}
        return JSONResponse({
            "ok": True,
            "py": "3.12",
            "platform": "Linux",
            "modules": mods,
            "has_bot_token": bool(BOT_TOKEN),
            "boot_error": boot_error,
            "brand_warn": brand_warn
        })

    if action == "refresh":
        try:
            n, meta = refresh_players()
            return JSONResponse({"ok": bool(meta.get("ok")), "refreshed": True, "players_indexed": n, **meta})
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": True, "players_indexed": 0, "error": repr(e)})

    if action == "ping":
        return JSONResponse({"ok": True, "pong": int(time.time())})

    return JSONResponse({"ok": True, "route": "telegram-get"})

@app.post("/api/telegram")
async def telegram_webhook(request: Request):
    # Secret
    if request.query_params.get("secret") != WEBHOOK_SECRET:
        _log("bad secret on webhook")
        return PlainTextResponse("OK", status_code=200)

    # Parse update
    try:
        upd = await request.json()
    except Exception:
        body = await request.body()
        _log("bad json body:", body[:2000])
        return PlainTextResponse("OK", status_code=200)

    if DEBUG: _log_json("update", upd)

    try:
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id:
            _log("no chat_id in update")
            return PlainTextResponse("OK", status_code=200)

        # Handle ForceReply save name
        saved_pid = _save_russian_name_from_reply(upd)
        if saved_pid:
            _tg_send_message(chat_id, f"Сохранил имя для {saved_pid}", parse_mode=None)
            # не выходим — пользователь мог отправить имя как ответ и отдельно команду

        # Simple commands
        if text == "/start":
            _tg_send_message(chat_id, "Привет! Я онлайн 🤖")
            return PlainTextResponse("OK", status_code=200)
        if text == "/help":
            _tg_send_message(chat_id, HELP_TEXT)
            return PlainTextResponse("OK", status_code=200)

        if text.startswith("/find"):
            q = text.split(" ", 1)[1].strip() if " " in text else ""
            if not q:
                _tg_send_message(chat_id, "Укажи имя, например: /find Doncic")
                return PlainTextResponse("OK", status_code=200)
            p = _find_best_player_by_name(q)
            if not p:
                _tg_send_message(chat_id, f"Не нашёл игрока: {q}")
                return PlainTextResponse("OK", status_code=200)
            _tg_send_message(chat_id, f"{_current_display_name(p)} (id={p.get('personId')}, teamId={p.get('teamId')})")
            return PlainTextResponse("OK", status_code=200)

        if text.startswith("/card"):
            # Typing…
            _tg_send_chat_action(chat_id, "typing")
            # Parse
            name_q, stats = _parse_card_command(text)
            if not name_q:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики через запятую>")
                return PlainTextResponse("OK", status_code=200)

            p = _find_best_player_by_name(name_q)
            if not p:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK", status_code=200)

            # Ask for Russian display name if not set (very simple heuristic)
            disp = _current_display_name(p)
            # Если явно латиница — попросим русское имя
            if re.search(r"[A-Za-z]", disp) and callable(set_display_name_override):
                _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
                _request_russian_name(chat_id, msg.get("message_id"), p)
                return PlainTextResponse("OK", status_code=200)

            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")

            pid = str(p.get("personId") or "")
            team_id = str(p.get("teamId") or "0")

            # Images
            head_img = _ensure_headshot_image(pid)
            if not head_img:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK", status_code=200)

            logo_img = _ensure_team_logo_image(team_id)
            colors = team_colors_for(team_id)  # tuple of hex

            # Render PNG via graphics
            try:
                png_bytes = graphics.render_card(
                    template="single",
                    player_name=_current_display_name(p),
                    team_name="",
                    team_logo_img=logo_img,
                    team_colors=colors,
                    head_img=head_img,
                    stats=stats,
                    note=None,
                )
            except Exception:
                _log("[render] error:\n" + traceback.format_exc())
                _tg_send_message(chat_id, "Ошибка при рендере плашки.")
                return PlainTextResponse("OK", status_code=200)

            # Send as document to keep alpha channel
            _tg_send_png_document(chat_id, "card.png", png_bytes, caption=None)
            return PlainTextResponse("OK", status_code=200)

        # Unknown command -> help
        _tg_send_message(chat_id, HELP_TEXT)
        return PlainTextResponse("OK", status_code=200)

    except Exception:
        _log("handler EXC:\n" + traceback.format_exc())
        # Возвращаем 200, чтобы Telegram не копил pending_update_count
        return PlainTextResponse("OK", status_code=200)
