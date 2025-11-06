# api/telegram.py
# FastAPI webhook для Telegram. Безопасные импорты, локальные лого, интерактивные уточнения.

from __future__ import annotations
import os, io, json, re, traceback, time
from typing import Any, Dict, List, Tuple, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.request import Request as UrlReq, urlopen
from urllib.parse import urlencode

BOOT_ERR: Optional[str] = None

def _exc_str(e: Exception) -> str:
    return f"{e.__class__.__name__}: {e}\n" + traceback.format_exc()

# -------- безопасные импорты --------
try:
    # data: только то, что точно есть
    from data import (
        get_players_index, players_index,
        find_player_by_name, refresh_players,
        ensure_headshot_png,
    )
    # бренд-цвета
    from team_brand import team_colors_for, color_name_for
    # графика (с мягкими алиасами)
    try:
        from graphics import (
            render_card, render_card2, render_card_bad,
            render_card_special, render_card_drN
        )
    except Exception as e:
        # графика недоступна — заглушки
        def _gfx_fail(*a, **k):
            raise RuntimeError(f"graphics import failed: {e}\n{traceback.format_exc()}")
        render_card = render_card2 = render_card_bad = render_card_special = render_card_drN = _gfx_fail
    else:
        # совместимость для старого имени
        try:
            from graphics import render_card_dr  # может отсутствовать
        except Exception:
            def render_card_dr(n, player_name, head_img, logo_img, stats):
                return render_card_drN(n, player_name, head_img, logo_img, stats)
except Exception as e:
    BOOT_ERR = _exc_str(e)

# -------- FastAPI app --------
app = FastAPI()

# -------- конфиг/окружение --------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook-123").strip()
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
CACHE_DIR = os.path.join(ASSETS_DIR, "cache")
TMP_DIR = "/tmp"

# Хранилище переименований и overrides (в /tmp на жизнь процесса)
RUS_NAMES_PATH = os.path.join(TMP_DIR, "rus_names.json")
TEAM_OVR_PATH  = os.path.join(TMP_DIR, "team_overrides.json")
COLOR_OVR_PATH = os.path.join(TMP_DIR, "color_overrides.json")  # на игрока

# Память для незавершённых сценариев (по chat_id)
PENDING: Dict[str, Dict[str, Any]] = {}

# -------- утилиты --------
def _load_json(path: str) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

RUS_NAMES: Dict[str, str] = _load_json(RUS_NAMES_PATH) or {}
TEAM_OVR : Dict[str, str] = _load_json(TEAM_OVR_PATH) or {}
COLOR_OVR: Dict[str, str] = _load_json(COLOR_OVR_PATH) or {}

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "no_bot_token"}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if isinstance(payload.get("reply_markup"), dict):
        payload["reply_markup"] = json.dumps(payload["reply_markup"], ensure_ascii=False)
    body = urlencode(payload).encode("utf-8")
    req = UrlReq(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=25) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "raw": raw[:200]}

def _send_msg(chat_id: int, text: str, reply_to: Optional[int] = None, html: bool = True):
    return _tg_post("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML" if html else "Markdown",
        "reply_to_message_id": reply_to or "",
        "disable_web_page_preview": 1,
    })

def _edit_msg(chat_id: int, message_id: int, text: str, html: bool = True, reply_markup: Optional[dict] = None):
    return _tg_post("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML" if html else "Markdown",
        "disable_web_page_preview": 1,
        "reply_markup": reply_markup or "",
    })

def _send_photo(chat_id: int, png_bytes: bytes, caption: str = ""):
    # Чтобы телега не превращала PNG в JPG — отправляем как документ.
    # Но если хочешь как «фото» (без прозрачности) — можно sendPhoto.
    # Здесь — документ, чтобы сохранить прозрачность гарантированно.
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    boundary = "----VMFormBoundary" + str(int(time.time()*1000))
    def part(field, content, filename=None, ctype="application/octet-stream"):
        out = []
        out.append(f"--{boundary}".encode())
        if filename:
            out.append(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode())
            out.append(f"Content-Type: {ctype}".encode())
            out.append(b"")
            out.append(content)
        else:
            out.append(f'Content-Disposition: form-data; name="{field}"'.encode())
            out.append(b"")
            out.append(content.encode())
        return b"\r\n".join(out)

    body = b""
    body += part("chat_id", str(chat_id))
    body += b"\r\n"
    if caption:
        body += part("caption", caption)
        body += b"\r\n"
    body += part("document", png_bytes, filename="card.png", ctype="image/png")
    body += b"\r\n--" + boundary.encode() + b"--\r\n"

    req = UrlReq(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urlopen(req, timeout=30) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "raw": raw[:200]}

def _normalize_stats_block(text: str) -> List[Tuple[str, str]]:
    # "10 очков, 12 передач, 7 подборов" -> [("10","ОЧКОВ"), ...]
    parts = [p.strip() for p in text.split(",") if p.strip()]
    out: List[Tuple[str, str]] = []
    for p in parts:
        m = re.match(r"^\s*([0-9]+)\s+(.+?)\s*$", p)
        if m:
            out.append((m.group(1), m.group(2)))
        else:
            # без разделения — кладём как значение
            out.append((p, ""))
    return out

def _pick_display_name(p: Dict[str, Any]) -> str:
    pid = str(p.get("personId") or "")
    if pid in RUS_NAMES and RUS_NAMES[pid].strip():
        return RUS_NAMES[pid].strip()
    # fallback — английское
    d = p.get("displayName") or ""
    if d: return d
    fn, ln = p.get("firstName","").strip(), p.get("lastName","").strip()
    return (fn + " " + ln).strip()

def _load_team_logo_img(team_id: str) -> Optional["Image.Image"]:
    try:
        from PIL import Image
        if not team_id or team_id == "0":
            return None
        path_png = os.path.join(CACHE_DIR, f"logo_{team_id}.png")
        if os.path.exists(path_png):
            return Image.open(path_png).convert("RGBA")
        # нет локального — пропускаем (SVG не конвертируем на сервере)
        return None
    except Exception:
        return None

def _team_colors_for(team_id: str) -> Tuple[str, str, str]:
    try:
        c = team_colors_for(team_id)
        if isinstance(c, (list, tuple)) and len(c) >= 3:
            return str(c[0]), str(c[1]), str(c[2])
    except Exception:
        pass
    # дефолт
    return ("#007ACC", "#005A99", "#66B6FF")

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /help — это сообщение\n"
    "• /find <имя> — найти игрока (например: /find Doncic)\n"
    "• /name <имя> — интерактивно задать русское имя (ответь на сообщение с подсказкой)\n"
    "• /team <имя> — задать/переопределить команду (ответь числом teamId)\n"
    "• /card <имя> | <статистика> — плашка (Авто/Свой цвет)\n"
    "• /cardBAD <имя> | <статистика> — «плохо сыграл» (коричневый, 💩)\n"
    "• /cardS <имя> | <статистика> | <инфо> — плашка с доп. блоком\n"
    "• /card2 <имя1> | <статистика1> | <имя2> | <статистика2> — дуо\n"
    "• /cardDR3 (или 4/5) — шаблон «делает разницу» (подхват позиций из макета)\n"
)

# -------- GET (ping/diag/refresh) --------
@app.get("/api/telegram")
async def telegram_get(secret: str = "", action: Optional[str] = None):
    if secret != WEBHOOK_SECRET:
        return JSONResponse({"detail": "bad secret"}, status_code=403)

    if action == "diag":
        return JSONResponse({
            "ok": BOOT_ERR is None,
            "py": os.getenv("PYTHON_VERSION", "3.12"),
            "platform": "Linux",
            "modules": {
                "graphics": "ok" if "render_card" in globals() else "error",
            },
            "has_bot_token": bool(BOT_TOKEN),
            "boot_error": BOOT_ERR,
        })
    if action == "refresh":
        try:
            n, meta = refresh_players(drop_cache=False)
            return JSONResponse({"ok": bool(n), "refreshed": True, "players_indexed": n, **(meta or {})})
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": True, "players_indexed": 0, "error": repr(e)})
    return JSONResponse({"ok": True, "route": "telegram-get", "boot_error": BOOT_ERR})

# -------- хелперы сценариев --------
def _ask_rus_name(chat_id: int, pid: str, eng_name: str) -> None:
    _send_msg(chat_id,
              f"Как подписать игрока <b>{eng_name}</b> на плашке?\n"
              f"Ответьте <u>на это сообщение</u> русским именем.\n"
              f"[setname:{pid}]")

def _ask_team_override(chat_id: int, pid: str, cur_tid: str) -> None:
    _send_msg(chat_id,
              f"Укажите <b>teamId</b> для игрока (текущее: <code>{cur_tid}</code>). "
              f"Ответьте <u>на это сообщение</u> числом.\n"
              f"[setteam:{pid}]")

def _ask_color(chat_id: int, ctx_id: str) -> Dict[str, Any]:
    kb = {
        "inline_keyboard": [[
            {"text": "Авто", "callback_data": f"pickcolor:auto:{ctx_id}"},
            {"text": "Свой цвет", "callback_data": f"pickcolor:custom:{ctx_id}"},
        ]]
    }
    return _tg_post("sendMessage", {
        "chat_id": chat_id,
        "text": "<i>Уточнения…</i>\nВыбери цвет плашки:",
        "parse_mode": "HTML",
        "reply_markup": json.dumps(kb, ensure_ascii=False),
    })

def _ensure_pid_by_query(q: str) -> Optional[Dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return None
    # быстрый поиск
    try:
        res = find_player_by_name(q)
        if isinstance(res, list) and len(res) >= 1:
            # если точное совпадение по displayName, берём первое
            return res[0]
    except Exception:
        pass
    # запасной путь — весь индекс и фильтр
    try:
        idx = players_index()
        for p in idx.values():
            name = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}".strip()
            if name and q.lower() in name.lower():
                return p
    except Exception:
        pass
    return None

def _apply_overrides(p: Dict[str, Any]) -> Dict[str, Any]:
    # возвращает копию с подменённым teamId при наличии override
    out = dict(p)
    pid = str(p.get("personId") or "")
    if pid in TEAM_OVR and TEAM_OVR[pid]:
        out["teamId"] = str(TEAM_OVR[pid])
    return out

# -------- основной хэндлер Telegram --------
@app.post("/api/telegram")
async def telegram_webhook(request: Request, secret: str = ""):
    if secret != WEBHOOK_SECRET:
        return PlainTextResponse("forbidden", status_code=403)

    if BOOT_ERR:
        return PlainTextResponse("BOOT_ERR\n" + BOOT_ERR, status_code=200)

    try:
        update = await request.json()
    except Exception:
        return PlainTextResponse("no json", status_code=200)

    message = update.get("message") or update.get("edited_message")
    callback = update.get("callback_query")

    # --- inline callback (выбор цвета) ---
    if callback:
        data = callback.get("data") or ""
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        msg_id  = callback.get("message", {}).get("message_id")
        if data.startswith("pickcolor:"):
            _, mode, ctx_id = data.split(":", 2)
            ctx = PENDING.get(str(chat_id)) or {}
            # ctx хранит: kind: card|cardS|card2, payload(...)
            if not ctx or ctx.get("ctx_id") != ctx_id:
                _edit_msg(chat_id, msg_id, "Контекст не найден. Попробуйте заново.")
                return PlainTextResponse("OK", status_code=200)

            # цвет
            custom_hex = None
            if mode == "custom":
                # попросим прислать код цвета
                _edit_msg(chat_id, msg_id, "<i>Уточнения…</i>\nПришли код цвета, например <code>#552583</code>.", reply_markup=None)
                ctx["await_color_hex"] = True
                PENDING[str(chat_id)] = ctx
                return PlainTextResponse("OK", status_code=200)

            # AUTO: строим плашку
            _edit_msg(chat_id, msg_id, "<i>Готовлю плашку…</i>")
            await _build_and_send_from_ctx(chat_id, ctx, custom_hex=None)
            PENDING.pop(str(chat_id), None)
            return PlainTextResponse("OK", status_code=200)

        return PlainTextResponse("OK", status_code=200)

    # --- обычные сообщения ---
    if not message:
        return PlainTextResponse("OK", status_code=200)

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    reply_to = message.get("reply_to_message") or {}

    # обработка ответов на подсказки: [setname:pid], [setteam:pid], ожидание цвета
    if reply_to:
        rtxt = reply_to.get("text") or ""
        m1 = re.search(r"\[setname:(\d+)\]", rtxt)
        m2 = re.search(r"\[setteam:(\d+)\]", rtxt)
        ctx = PENDING.get(str(chat_id)) or {}

        if m1:
            pid = m1.group(1)
            RUS_NAMES[pid] = text.strip()
            _save_json(RUS_NAMES_PATH, RUS_NAMES)
            _send_msg(chat_id, f"Сохранил имя для <code>{pid}</code>: <b>{RUS_NAMES[pid]}</b>")
            # если ждём цвет/рендер — предложим выбор цвета
            if ctx:
                ctx_id = ctx.get("ctx_id") or str(int(time.time()*1000))
                ctx["ctx_id"] = ctx_id
                PENDING[str(chat_id)] = ctx
                _ask_color(chat_id, ctx_id)
            return PlainTextResponse("OK", status_code=200)

        if m2:
            pid = m2.group(1)
            tid = re.sub(r"\D+", "", text)
            if not tid:
                _send_msg(chat_id, "Это не похоже на teamId. Пришлите число, например <code>1610612747</code>.")
                return PlainTextResponse("OK", status_code=200)
            TEAM_OVR[pid] = tid
            _save_json(TEAM_OVR_PATH, TEAM_OVR)
            _send_msg(chat_id, f"Сохранил teamId для <code>{pid}</code>: <b>{tid}</b>")
            return PlainTextResponse("OK", status_code=200)

        # Ждём кастомный цвет?
        if ctx and ctx.get("await_color_hex"):
            hex_code = text.strip()
            if not re.search(r"^#?[0-9A-Fa-f]{6}$", hex_code):
                _send_msg(chat_id, "Пришлите цвет в формате <code>#RRGGBB</code>, например <code>#552583</code>.")
                return PlainTextResponse("OK", status_code=200)
            if not hex_code.startswith("#"):
                hex_code = "#" + hex_code
            _send_msg(chat_id, "<i>Готовлю плашку…</i>")
            await _build_and_send_from_ctx(chat_id, ctx, custom_hex=hex_code)
            PENDING.pop(str(chat_id), None)
            return PlainTextResponse("OK", status_code=200)

    # команды
    if text.lower().startswith("/start"):
        _send_msg(chat_id, "Я тут! 👋\n\n" + HELP_TEXT)
        return PlainTextResponse("OK", status_code=200)

    if text.lower().startswith("/help"):
        _send_msg(chat_id, HELP_TEXT)
        return PlainTextResponse("OK", status_code=200)

    if text.lower().startswith("/find"):
        q = text.split(" ", 1)[-1].strip()
        p = _ensure_pid_by_query(q)
        if not p:
            _send_msg(chat_id, f"Игрок <b>{q}</b> не найден.")
            return PlainTextResponse("OK", status_code=200)
        _send_msg(chat_id, f"{p.get('displayName')} (id={p.get('personId')}, teamId={p.get('teamId')})")
        return PlainTextResponse("OK", status_code=200)

    if text.lower().startswith("/name"):
        q = text.split(" ", 1)[-1].strip()
        p = _ensure_pid_by_query(q)
        if not p:
            _send_msg(chat_id, f"Игрок <b>{q}</b> не найден.")
            return PlainTextResponse("OK", status_code=200)
        pid = str(p.get("personId"))
        _ask_rus_name(chat_id, pid, p.get("displayName") or "")
        # запомним контекст, чтобы после имени сразу спросить цвет
        PENDING[str(chat_id)] = {"kind": "rename_only", "pid": pid, "ctx_id": str(int(time.time()*1000))}
        return PlainTextResponse("OK", status_code=200)

    if text.lower().startswith("/team"):
        q = text.split(" ", 1)[-1].strip()
        p = _ensure_pid_by_query(q)
        if not p:
            _send_msg(chat_id, f"Игрок <b>{q}</b> не найден.")
            return PlainTextResponse("OK", status_code=200)
        pid = str(p.get("personId"))
        cur_tid = str(p.get("teamId") or "0")
        _ask_team_override(chat_id, pid, cur_tid)
        return PlainTextResponse("OK", status_code=200)

    # --- парсинг генераторов карточек ---
    # /card <имя> | <статы>
    if text.lower().startswith("/card "):
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 2:
            _send_msg(chat_id, "Формат: /card Имя | статистика")
            return PlainTextResponse("OK", status_code=200)
        name_q, stats_s = parts[0], parts[1]
        p = _ensure_pid_by_query(name_q)
        if not p:
            _send_msg(chat_id, f"Не нашёл игрока: <b>{name_q}</b>")
            return PlainTextResponse("OK", status_code=200)
        pid = str(p.get("personId"))
        # если нет русского — попросим
        if pid not in RUS_NAMES:
            _send_msg(chat_id, "<i>Уточнения…</i>")
            _ask_rus_name(chat_id, pid, p.get("displayName") or "")
            # сохраним контекст для автопродолжения
            PENDING[str(chat_id)] = {
                "kind": "card",
                "pid": pid,
                "stats": _normalize_stats_block(stats_s),
                "ctx_id": str(int(time.time()*1000)),
            }
            return PlainTextResponse("OK", status_code=200)

        # есть имя — спросим цвет
        ctx_id = str(int(time.time()*1000))
        PENDING[str(chat_id)] = {
            "kind": "card",
            "pid": pid,
            "stats": _normalize_stats_block(stats_s),
            "ctx_id": ctx_id,
        }
        _ask_color(chat_id, ctx_id)
        return PlainTextResponse("OK", status_code=200)

    # /cardBAD <имя> | <статы>
    if text.lower().startswith("/cardbad "):
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 2:
            _send_msg(chat_id, "Формат: /cardBAD Имя | статистика")
            return PlainTextResponse("OK", status_code=200)
        name_q, stats_s = parts[0], parts[1]
        p = _ensure_pid_by_query(name_q)
        if not p:
            _send_msg(chat_id, f"Не нашёл игрока: <b>{name_q}</b>")
            return PlainTextResponse("OK", status_code=200)
        pid = str(p.get("personId"))
        # имя на русском при необходимости
        if pid not in RUS_NAMES:
            _send_msg(chat_id, "<i>Уточнения…</i>")
            _ask_rus_name(chat_id, pid, p.get("displayName") or "")
            PENDING[str(chat_id)] = {
                "kind": "cardbad",
                "pid": pid,
                "stats": _normalize_stats_block(stats_s),
                "ctx_id": str(int(time.time()*1000)),
            }
            return PlainTextResponse("OK", status_code=200)

        # BAD — без выбора цвета (фиксированный)
        _send_msg(chat_id, "<i>Готовлю плашку…</i>")
        await _build_and_send_from_ctx(chat_id, {"kind":"cardbad","pid":pid,"stats":_normalize_stats_block(stats_s)}, None)
        return PlainTextResponse("OK", status_code=200)

    # /cardS <имя> | <статы> | <инфо>
    if text.lower().startswith("/cards "):
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 3:
            _send_msg(chat_id, "Формат: /cardS Имя | статистика | информация")
            return PlainTextResponse("OK", status_code=200)
        name_q, stats_s, info_s = parts[0], parts[1], parts[2]
        p = _ensure_pid_by_query(name_q)
        if not p:
            _send_msg(chat_id, f"Не нашёл игрока: <b>{name_q}</b>")
            return PlainTextResponse("OK", status_code=200)
        pid = str(p.get("personId"))
        if pid not in RUS_NAMES:
            _send_msg(chat_id, "<i>Уточнения…</i>")
            _ask_rus_name(chat_id, pid, p.get("displayName") or "")
            PENDING[str(chat_id)] = {
                "kind": "cards",
                "pid": pid,
                "stats": _normalize_stats_block(stats_s),
                "info": info_s,
                "ctx_id": str(int(time.time()*1000)),
            }
            return PlainTextResponse("OK", status_code=200)

        ctx_id = str(int(time.time()*1000))
        PENDING[str(chat_id)] = {
            "kind": "cards",
            "pid": pid,
            "stats": _normalize_stats_block(stats_s),
            "info": info_s,
            "ctx_id": ctx_id,
        }
        _ask_color(chat_id, ctx_id)
        return PlainTextResponse("OK", status_code=200)

    # /card2 <имя1> | <статы1> | <имя2> | <статы2>
    if text.lower().startswith("/card2 "):
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 4:
            _send_msg(chat_id, "Формат: /card2 Имя1 | статистика1 | Имя2 | статистика2")
            return PlainTextResponse("OK", status_code=200)
        name1, stats1_s, name2, stats2_s = parts[0], parts[1], parts[2], parts[3]
        p1 = _ensure_pid_by_query(name1)
        p2 = _ensure_pid_by_query(name2)
        if not p1 or not p2:
            _send_msg(chat_id, "Кого-то из игроков не нашёл.")
            return PlainTextResponse("OK", status_code=200)
        # для простоты: цвета авто (по командам)
        _send_msg(chat_id, "<i>Готовлю плашку…</i>")
        await _build_and_send_card2(chat_id, p1, _normalize_stats_block(stats1_s), p2, _normalize_stats_block(stats2_s))
        return PlainTextResponse("OK", status_code=200)

    # /cardDR3|4|5 <имя> | <статы…>
    if re.match(r"^/carddr[345]\b", text.lower()):
        m = re.match(r"^/carddr([345])\s+(.+)$", text, flags=re.IGNORECASE)
        if not m:
            _send_msg(chat_id, "Формат: /cardDR3 Имя | статы…")
            return PlainTextResponse("OK", status_code=200)
        n = int(m.group(1))
        body = m.group(2)
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 2:
            _send_msg(chat_id, f"Формат: /cardDR{n} Имя | статы…")
            return PlainTextResponse("OK", status_code=200)
        name_q, stats_s = parts[0], parts[1]
        p = _ensure_pid_by_query(name_q)
        if not p:
            _send_msg(chat_id, f"Не нашёл игрока: <b>{name_q}</b>")
            return PlainTextResponse("OK", status_code=200)
        pid = str(p.get("personId"))
        if pid not in RUS_NAMES:
            _send_msg(chat_id, "<i>Уточнения…</i>")
            _ask_rus_name(chat_id, pid, p.get("displayName") or "")
            PENDING[str(chat_id)] = {
                "kind": f"carddr{n}",
                "pid": pid,
                "stats": _normalize_stats_block(stats_s),
                "ctx_id": str(int(time.time()*1000)),
            }
            return PlainTextResponse("OK", status_code=200)
        _send_msg(chat_id, "<i>Готовлю плашку…</i>")
        await _build_and_send_dr(chat_id, n, p, _normalize_stats_block(stats_s))
        return PlainTextResponse("OK", status_code=200)

    # неизвестно — help
    _send_msg(chat_id, HELP_TEXT)
    return PlainTextResponse("OK", status_code=200)

# -------- сборщики карточек по контексту --------
async def _build_and_send_from_ctx(chat_id: int, ctx: Dict[str, Any], custom_hex: Optional[str]) -> None:
    kind = ctx.get("kind")
    pid  = ctx.get("pid")
    stats= ctx.get("stats") or []
    info = ctx.get("info") or ""

    # получаем игрока по pid
    idx = players_index()
    p = None
    if pid and str(pid) in idx:
        p = _apply_overrides(idx[str(pid)])
    if not p:
        _send_msg(chat_id, "Контекст потерян — не нашёл игрока.")
        return

    # head
    try:
        head_path = ensure_headshot_png(str(p.get("personId")))
        from PIL import Image
        head_img = Image.open(head_path).convert("RGBA")
    except Exception as e:
        _send_msg(chat_id, f"Не удалось получить фото игрока: {e}")
        return

    # имя
    name_ru = _pick_display_name(p)

    # цвета/лого
    team_id = str(p.get("teamId") or "0")
    logo_img = _load_team_logo_img(team_id) if kind != "cardbad" else None
    if custom_hex:
        primary = custom_hex
        colors = (primary, primary, primary)
    else:
        colors = _team_colors_for(team_id)

    # ветки
    if kind == "card":
        png = render_card("single", name_ru, "", logo_img, colors, head_img, stats)
        _send_photo(chat_id, png)
        return
    if kind == "cardbad":
        png = render_card_bad(name_ru, head_img, stats)
        _send_photo(chat_id, png)
        return
    if kind == "cards":
        png = render_card_special(name_ru, logo_img, colors, head_img, stats, info)
        _send_photo(chat_id, png)
        return
    # carddrN
    if kind and kind.startswith("carddr"):
        try:
            n = int(kind.replace("carddr",""))
        except Exception:
            n = 3
        png = render_card_drN(n, name_ru, head_img, logo_img, stats)
        _send_photo(chat_id, png)
        return

    _send_msg(chat_id, "Неизвестный контекст.")

async def _build_and_send_card2(chat_id: int,
                                p1: Dict[str,Any], stats1: List[Tuple[str,str]],
                                p2: Dict[str,Any], stats2: List[Tuple[str,str]]) -> None:
    idx = players_index()
    # overrides
    p1 = _apply_overrides(p1)
    p2 = _apply_overrides(p2)

    from PIL import Image
    # heads
    head1 = Image.open(ensure_headshot_png(str(p1.get("personId")))).convert("RGBA")
    head2 = Image.open(ensure_headshot_png(str(p2.get("personId")))).convert("RGBA")
    # names
    name1 = _pick_display_name(p1); name2 = _pick_display_name(p2)
    # logos
    logo1 = _load_team_logo_img(str(p1.get("teamId") or "0"))
    logo2 = _load_team_logo_img(str(p2.get("teamId") or "0"))
    # colors auto
    colors1 = _team_colors_for(str(p1.get("teamId") or "0"))
    colors2 = _team_colors_for(str(p2.get("teamId") or "0"))

    png = render_card2(name1, logo1, colors1, head1, stats1,
                       name2, logo2, colors2, head2, stats2)
    _send_photo(chat_id, png)

async def _build_and_send_dr(chat_id: int, n: int, p: Dict[str,Any], stats: List[Tuple[str,str]]) -> None:
    from PIL import Image
    p = _apply_overrides(p)
    head = Image.open(ensure_headshot_png(str(p.get("personId")))).convert("RGBA")
    logo = _load_team_logo_img(str(p.get("teamId") or "0"))
    name = _pick_display_name(p)
    png = render_card_drN(n, name, head, logo, stats)
    _send_photo(chat_id, png)
