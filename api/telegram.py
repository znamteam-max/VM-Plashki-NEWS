# api/telegram.py
# Crash-proof webhook + diag for Vercel
from __future__ import annotations

import os, sys, json, time, traceback, importlib
from typing import Any, Dict, Optional, List, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.request import Request as HttpRequest, urlopen as http_urlopen

# ------------------- Config -------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook-123").strip()
DEBUG = os.getenv("DEBUG", "1") == "1"

app = FastAPI()

# ------------------- Logging ------------------
def _log(*a: Any) -> None:
    try:
        print("[tg]", *a, flush=True)
    except:
        pass

# ------------------- Tiny utils ----------------
def _http_json(url: str, payload: Dict[str, Any], timeout: int = 25) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = HttpRequest(url, body, headers={"Content-Type": "application/json"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _http_json(_tg_url(method), payload)
    except Exception:
        _log("[TG] send error:", traceback.format_exc())
        raise

def _tg_send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None, parse_mode: Optional[str] = "HTML") -> None:
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True
    _tg_post("sendMessage", payload)

def _tg_chat_action(chat_id: int, action: str) -> None:
    try:
        _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})
    except Exception:
        pass

# ------------------- Lazy imports ----------------
def _try_import(mod_name: str, attrs: Optional[List[str]] = None) -> Tuple[Optional[Any], Optional[str], Dict[str, Any]]:
    """
    Пытается импортировать модуль и (опционально) его атрибуты.
    Возвращает: (module_or_None, error_text_or_None, exported_attr_dict)
    """
    try:
        m = importlib.import_module(mod_name)
    except Exception as e:
        return None, "".join(traceback.format_exception_only(type(e), e)), {}
    out: Dict[str, Any] = {}
    if attrs:
        for a in attrs:
            try:
                out[a] = getattr(m, a)
            except Exception as e:
                return m, f"Attribute '{a}' missing: {e}", {}
    return m, None, out

# ------------------- GET: /api/telegram ----------------
@app.get("/api/telegram")
async def telegram_get(request: Request):
    secret = request.query_params.get("secret")
    if secret != WEBHOOK_SECRET:
        return JSONResponse({"detail": "bad secret"}, status_code=401)

    action = (request.query_params.get("action") or "").strip().lower()

    if not action:
        return JSONResponse({"ok": True, "route": "telegram-get", "boot_error": None})

    if action == "diag":
        # НИЧЕГО не импортируем на уровне модуля — проверяем здесь.
        py = ".".join(map(str, sys.version_info[:3]))
        platform = sys.platform
        mod_reports: Dict[str, Any] = {}

        for name, attrs in [
            ("data", ["refresh_players", "players_count", "get_players_index", "find_player_by_name", "ensure_headshot_png"]),
            ("graphics", ["render_card"]),
            ("team_brand", ["get_team_brand", "set_team_primary_color", "color_name_ru"]),
        ]:
            m, err, exported = _try_import(name, attrs)
            mod_reports[name] = {"ok": err is None, "error": err}

        return JSONResponse({
            "ok": True,
            "py": py,
            "platform": platform,
            "has_bot_token": bool(BOT_TOKEN),
            "modules": {k: ("ok" if v["ok"] else "error") for k, v in mod_reports.items()},
            "errors": {k: v["error"] for k, v in mod_reports.items() if v["error"]},
        })

    if action == "refresh":
        # безопасная попытка индексации игроков
        data_m, err, exp = _try_import("data", ["refresh_players", "players_count"])
        if err:
            return JSONResponse({"ok": False, "refreshed": False, "error": f"import data failed: {err}"}, status_code=200)
        try:
            cnt_before = exp["players_count"]()
        except Exception as e:
            cnt_before = None
        try:
            n, info = exp["refresh_players"](drop_cache=False)
            cnt_after = exp["players_count"]()
            return JSONResponse({
                "ok": (info.get("ok", True) if isinstance(info, dict) else True),
                "refreshed": True,
                "players_indexed": int(cnt_after or n or 0),
                "source": (info.get("source") if isinstance(info, dict) else None),
                "source_url": (info.get("source_url") if isinstance(info, dict) else None),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=200)

    if action == "ping":
        return JSONResponse({"ok": True, "pong": int(time.time())})

    return JSONResponse({"ok": False, "error": f"unknown action '{action}'"}, status_code=400)

# ------------------- POST: webhook ----------------
@app.post("/api/telegram")
async def webhook_query(request: Request):
    secret = request.query_params.get("secret")
    if secret != WEBHOOK_SECRET:
        return PlainTextResponse("NO", status_code=403)

    rid = f"{int(time.time()*1000)}-{os.urandom(3).hex()}"
    try:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        if DEBUG:
            _log(f"[RID={rid}] POST {str(request.url)}")
            _log(f"[RID={rid}] body: {raw}")
        update = json.loads(raw or "{}")
    except Exception:
        return PlainTextResponse("BAD", status_code=200)

    msg = (update.get("message") or update.get("edited_message") or
           update.get("channel_post") or update.get("edited_channel_post"))
    if not msg:
        return PlainTextResponse("OK", status_code=200)

    chat_id = int(msg.get("chat", {}).get("id", 0) or 0)
    text = msg.get("text") or ""
    reply_to = msg.get("reply_to_message")
    message_id = int(msg.get("message_id", 0))

    # Импортируем всё лениво, и если не вышло — отвечаем, но не падаем
    data_m, data_err, data = _try_import("data", ["refresh_players", "players_count", "get_players_index", "find_player_by_name", "ensure_headshot_png"])
    graphics_m, graphics_err, graphics = _try_import("graphics", ["render_card"])
    brand_m, brand_err, brand = _try_import("team_brand", ["get_team_brand", "color_name_ru"])

    if text.strip().startswith("/start") or text.strip().startswith("/help"):
        HELP = (
            "Привет! Я онлайн 🤖\n\n"
            "Команды:\n"
            "• /start — проверка связи\n"
            "• /help — это сообщение\n"
            "• /find <имя> — поиск игрока\n"
            "• /card <имя> | 10 очков, 12 передач — сделать плашку\n"
        )
        try:
            _tg_send_message(chat_id, HELP)
        except Exception:
            pass
        return PlainTextResponse("OK", status_code=200)

    # /find
    if text.strip().lower().startswith("/find"):
        q = text.strip()[5:].strip()
        if not q:
            try: _tg_send_message(chat_id, "Укажите имя: <code>/find Lebron</code>", parse_mode="HTML")
            except: pass
            return PlainTextResponse("OK", status_code=200)
        if data_err:
            try: _tg_send_message(chat_id, f"Сервис недоступен (data import error): {data_err[:140]}")
            except: pass
            return PlainTextResponse("OK", status_code=200)
        try:
            if data["players_count"]() <= 0:
                data["refresh_players"](drop_cache=False)
            idx = data["get_players_index"]()
            cand = data["find_player_by_name"](q) or []
            if not cand:
                _tg_send_message(chat_id, "Ничего не нашёл 🤷")
            else:
                out = []
                for p in cand[:6]:
                    pid = p.get("personId")
                    fn = (p.get("firstName") or "").strip()
                    ln = (p.get("lastName") or "").strip()
                    out.append(f"{fn} {ln} (id={pid}, teamId={p.get('teamId','0')})")
                _tg_send_message(chat_id, "\n".join(out))
        except Exception as e:
            _log("find error:", traceback.format_exc())
            try: _tg_send_message(chat_id, "Ошибка поиска.")
            except: pass
        return PlainTextResponse("OK", status_code=200)

    # /card <name> | stats
    if text.strip().lower().startswith("/card"):
        if data_err or graphics_err or brand_err:
            msg_err = []
            if data_err: msg_err.append("data")
            if graphics_err: msg_err.append("graphics")
            if brand_err: msg_err.append("team_brand")
            try: _tg_send_message(chat_id, f"Сервис недоступен (импорт: {', '.join(msg_err)}). Откройте /api/telegram?action=diag&secret=... для диагностики.")
            except: pass
            return PlainTextResponse("OK", status_code=200)

        # разбор аргументов
        s = text.strip()[5:].strip()
        parts = [x.strip() for x in s.split("|")]
        name_part = parts[0] if parts else ""
        stats_raw = parts[1] if len(parts) >= 2 else ""
        stats: List[Tuple[str, str]] = []
        for chunk in [x.strip() for x in stats_raw.split(",") if x.strip()]:
            import re as _re
            m = _re.match(r"^\s*([+\-]?\d+[.,]?\d*)\s+(.*)$", chunk)
            if m:
                v = m.group(1).replace(",", ".")
                lab = m.group(2).strip()
                stats.append((v, lab))
            else:
                stats.append((chunk, ""))

        try:
            if data["players_count"]() <= 0:
                data["refresh_players"](drop_cache=False)
            idx = data["get_players_index"]()
            cand = data["find_player_by_name"](name_part) or []
            if not cand:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_part}")
                return PlainTextResponse("OK", status_code=200)

            p = cand[0]
            pid = str(p.get("personId", ""))
            team_id = str(p.get("teamId", "0") or "0")

            # имя для отображения (англ, для простоты; русское хранилище можно подключить отдельно)
            fn = (p.get("firstName") or "").strip()
            ln = (p.get("lastName") or "").strip()
            display_name = (fn + " " + ln).strip() or "Player"

            # голова
            head_path = data["ensure_headshot_png"](pid)
            if not head_path or not os.path.exists(head_path):
                _tg_send_message(chat_id, "Не удалось получить фото игрока 😕")
                return PlainTextResponse("OK", status_code=200)

            # бренд
            colors, logo_path, palette, has_saved = brand["get_team_brand"](team_id)

            from PIL import Image
            head_img = Image.open(head_path).convert("RGBA")
            team_logo_img = None
            if logo_path and os.path.exists(logo_path):
                try:
                    team_logo_img = Image.open(logo_path).convert("RGBA")
                except Exception:
                    team_logo_img = None

            _tg_chat_action(chat_id, "typing")
            _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")

            png_bytes = graphics["render_card"]("single", display_name, "", team_logo_img, colors, head_img, stats)
            # Упрощённо: без multipart — просто сообщим, что PNG готов.
            _tg_send_message(chat_id, "✅ Плашка готова (PNG). Подключите multipart, чтобы отправлять файл как документ.")
        except Exception as e:
            _log("card error:", traceback.format_exc())
            try: _tg_send_message(chat_id, "Ошибка при создании плашки.")
            except: pass
        return PlainTextResponse("OK", status_code=200)

    # по умолчанию — молча ок
    return PlainTextResponse("OK", status_code=200)
