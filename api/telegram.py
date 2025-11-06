# api/telegram.py — стабильный сервер: безопасные импорты, диагностика, без 500 на старте
from __future__ import annotations
import json, os, sys, re, time, uuid, traceback, platform
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError

app = FastAPI()

# ---------------- ENV ----------------
BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

# ---------------- SAFE IMPORTS ----------------
STARTUP: Dict[str, Any] = {
    "ok": True,
    "errors": {},
    "py": sys.version.split()[0],
    "platform": platform.platform(),
    "modules": {}
}

def _safe_import(modname: str, names: Optional[List[str]] = None):
    try:
        m = __import__(modname, fromlist=names or [])
        STARTUP["modules"][modname] = "ok"
        if names:
            out = []
            for n in names:
                out.append(getattr(m, n))
            return out if len(out) > 1 else out[0]
        return m
    except Exception:
        STARTUP["ok"] = False
        STARTUP["errors"][modname] = traceback.format_exc(limit=5)
        return None

# data (важно для refresh/find/card и т.п.)
_data = _safe_import("data", [
    "refresh_players", "get_players", "get_players_index", "find_player_by_name",
    "ensure_headshot_png", "open_headshot_variants", "display_name_for",
    "set_player_ru_name", "set_player_team", "set_player_alias", "get_overrides"
])

if _data:
    (refresh_players, get_players, get_players_index, find_player_by_name,
     ensure_headshot_png, open_headshot_variants, display_name_for,
     set_player_ru_name, set_player_team, set_player_alias, get_overrides) = _data
else:
    # Заглушки, чтобы эндпоинты не падали
    def refresh_players(*a, **kw): return 0, {"ok": False, "error": "data_import_failed"}
    def get_players(*a, **kw): return []
    def get_players_index(*a, **kw): return {}
    def find_player_by_name(*a, **kw): return []
    def ensure_headshot_png(*a, **kw): raise RuntimeError("data module not loaded")
    def open_headshot_variants(*a, **kw): return []
    def display_name_for(p): return (p.get("displayName") if isinstance(p, dict) else "") or ""
    def set_player_ru_name(*a, **kw): return False
    def set_player_team(*a, **kw): return False
    def set_player_alias(*a, **kw): return False
    def get_overrides(*a, **kw): return {}

# team_brand (цвета/логотипы — могут понадобиться позже)
_tb = _safe_import("team_brand", ["get_team_brand", "set_team_primary_color", "get_team_logo_path", "color_name_ru"])
if _tb:
    (get_team_brand, set_team_primary_color, get_team_logo_path, color_name_ru) = _tb
else:
    def get_team_brand(team_id: str):
        return {"primary": "#007ACC", "dark": "#005A99", "light": "#78C3FF"}
    def set_team_primary_color(team_id: str, hx: str): return True
    def get_team_logo_path(team_id: str): return None
    def color_name_ru(hx: str): return "синий"

# graphics (тяжёлый import Pillow) — импортируем тоже безопасно
_gfx = _safe_import("graphics", [
    "render_card", "render_card2", "render_card_bad", "render_card_dr",
    "render_card_special", "render_card_drN"
])
if _gfx:
    (render_card, render_card2, render_card_bad, render_card_dr,
     render_card_special, render_card_drN) = _gfx
else:
    # Заглушки, чтобы не падало при других действиях
    def render_card(*a, **kw): raise RuntimeError("graphics module not loaded")
    def render_card2(*a, **kw): raise RuntimeError("graphics module not loaded")
    def render_card_bad(*a, **kw): raise RuntimeError("graphics module not loaded")
    def render_card_dr(*a, **kw): raise RuntimeError("graphics module not loaded")
    def render_card_special(*a, **kw): raise RuntimeError("graphics module not loaded")
    def render_card_drN(*a, **kw): raise RuntimeError("graphics module not loaded")

# Пытаемся узнать версию Pillow (если есть)
try:
    import PIL
    STARTUP["modules"]["Pillow"] = getattr(PIL, "__version__", "ok")
except Exception:
    if "graphics" in STARTUP["errors"]:
        STARTUP["modules"]["Pillow"] = "missing (graphics import failed)"
    else:
        STARTUP["modules"]["Pillow"] = "missing"

# ---------------- TG HELPERS ----------------
def _tg_base() -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else "https://api.telegram.org/bot"

def _safe_http_json(url: str, body: Optional[bytes], headers: Dict[str,str], method: str, timeout: int = 25) -> Dict[str, Any]:
    try:
        req = UrlRequest(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        msg = ""
        try: msg = e.read().decode("utf-8", "ignore")
        except: pass
        return {"ok": False, "status": e.code, "description": msg or str(e)}
    except URLError as e:
        return {"ok": False, "error": repr(e)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}

def _tg_post_json(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_http_json(f"{_tg_base()}/{method}", json.dumps(payload, ensure_ascii=False).encode(), {"Content-Type":"application/json"}, "POST")

def _tg_send_message(chat_id: int, text: str, parse_mode: Optional[str]="HTML") -> None:
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    _tg_post_json("sendMessage", payload)

# ---------------- ROUTES ----------------
@app.get("/api/telegram")
async def telegram_get(secret: str, action: Optional[str] = None):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="bad secret")

    # Диагностика (без health-эндпойнта)
    if action == "diag":
        # Не возвращаем секреты, только статусы
        resp = {
            "ok": STARTUP["ok"],
            "py": STARTUP["py"],
            "platform": STARTUP["platform"],
            "modules": STARTUP["modules"],
            "errors": STARTUP["errors"],
            "has_bot_token": bool(BOT_TOKEN),
        }
        return JSONResponse(resp)

    if action == "refresh":
        # Если модуль data не загрузился — вернём понятный ответ, но не упадём.
        if "data" in STARTUP["errors"]:
            return JSONResponse({"ok": False, "refreshed": False, "players_indexed": 0, "error": "data_import_failed", "detail": STARTUP["errors"]["data"]}, status_code=200)
        cnt, info = refresh_players()
        return JSONResponse({"ok": bool(info.get("ok")), "refreshed": True, "players_indexed": cnt, **info})

    return JSONResponse({"ok": True, "route": "telegram-get"})

@app.post("/api/telegram")
async def telegram_post(request: Request, secret: str):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="bad secret")

    # Если на старте упал импорт graphics/data/team_brand — не валим весь хэндлер.
    startup_errors = STARTUP.get("errors") or {}
    if startup_errors:
        # Не бойся — дальше можно мягко работать, но если прям сейчас нельзя — вернём пояснение
        pass

    try:
        update = await request.json()
    except Exception:
        # Даже если пришло не JSON — не падаем 500.
        return PlainTextResponse("OK")

    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return PlainTextResponse("OK")

    chat_id = int(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()

    # Если нет BOT_TOKEN — не уронить функцию, а вернуть подсказку.
    if not BOT_TOKEN:
        return JSONResponse({"ok": False, "error": "BOT_TOKEN is empty"})

    # Если graphics не загрузился — сообщим в чат и выйдем (иначе дальше всё равно упадёт при рендере).
    if "graphics" in startup_errors:
        _tg_send_message(chat_id, "<i>Ошибка старта:</i> модуль <b>graphics</b> не загрузился.\nПопробуй задеплоить с корректным <code>requirements.txt</code> (нужен Pillow) и проверь синтаксис.\n\nПодробности:\n<code>{}</code>".format(startup_errors["graphics"]))
        return PlainTextResponse("OK")

    # Если data не загрузился — тоже мягко объясняем.
    if "data" in startup_errors:
        _tg_send_message(chat_id, "<i>Ошибка старта:</i> модуль <b>data</b> не загрузился.\nПодробности:\n<code>{}</code>".format(startup_errors["data"]))
        return PlainTextResponse("OK")

    # Дальше — твоя обычная логика. Чтобы ответ соответствовал твоим прежним файлам,
    # просто выдадим help (минимально). Основная логика /card и др. остаётся в твоих версиях,
    # которые ты уже загружал — здесь мы не дублируем её, только страхуем от 500.
    if not text or text == "/start":
        _tg_send_message(chat_id, "Я тут. Отправьте /help.")
        return PlainTextResponse("OK")

    if text.startswith("/help"):
        _tg_send_message(chat_id,
            "Привет! Я онлайн 🤖\n\n"
            "• /find <имя> — найти игрока\n"
            "• /card, /card2, /cardBAD, /cardS, /cardDR3/4/5 — плашки\n"
            "• /name <имя> — задать русское имя (ответьте на запрос)\n"
            "• /team <имя> — задать teamId (ответьте числом)\n"
        )
        return PlainTextResponse("OK")

    # Если нужно — здесь можно подключить твой текущий роутинг (мы не переписываем его заново).
    # Чтобы не ломать, вернём «команда не распознана», но функция не упадёт.
    _tg_send_message(chat_id, "Команда принята, но логика не загружена в этом лёгком «анти-500» варианте.\n"
                              "Перепроверь импорт модулей (см. /api/telegram?action=diag).")
    return PlainTextResponse("OK")
