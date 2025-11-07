# api/telegram.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os, json, io, re, traceback, time
from typing import Any, Dict, List, Tuple, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# ---- Внутреннее HTTP (для Telegram и загрузки картинок) ----
from urllib.request import Request as HttpRequest, urlopen as http_urlopen
from urllib.parse import urlencode

# ----- Модули проекта -----
# ГРАФИКА
from graphics import (
    render_card,
    render_card2,          # оставлено на будущее; не используется здесь
    render_card_bad,       # оставлено на будущее; не используется здесь
    render_card_special,   # оставлено на будущее; не используется здесь
    render_card_drN,       # оставлено на будущее; не используется здесь
)

# ДАННЫЕ/ИГРОКИ
from data import (
    get_players,
    get_players_index,
    players_count,
    refresh_players,
    find_player_by_name,
    ensure_headshot_png,       # ожидается: ensure_headshot_png(person_id: str|int) -> Optional[str]
    ensure_team_logo_png,      # ожидается: ensure_team_logo_png(team_id: str|int) -> Optional[str]
)

# БРЕНД/ЦВЕТА/ЛОГОТИП
from team_brand import (
    get_team_brand,            # -> ( (primary, dark, light), logo_path, palette_candidates, has_saved )
    set_team_primary_color,    # set_team_primary_color(team_id, "#RRGGBB"|"AUTO")
    color_name_ru,             # красивое имя цвета по hex
)

# =============================== CONFIG ===============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook-123").strip()

# Включить подробные логи в Vercel
DEBUG = os.getenv("DEBUG", "1") == "1"

# Папки и временные файлы для персистентности имён
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = "/tmp"
RU_NAMES_PATH = os.path.join(TMP_DIR, "ru_names.json")  # { personId: "Имя Фамилия" }
ALIASES_PATH  = os.path.join(TMP_DIR, "ru_aliases.json")# { alias_lower: personId }

# =============================== LOGGING ==============================
def _log(*args: Any) -> None:
    try:
        print("[tg]", *args, flush=True)
    except Exception:
        pass

# ========================== JSON utils ================================
def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _write_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        _log("write_json error:", e)

# ========================== Telegram HTTP =============================
def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_json(url: str, payload: Dict[str, Any], timeout: int = 25) -> Dict[str, Any]:
    # urllib.Request не всегда принимает data= как именованный параметр в некоторых средах,
    # поэтому используем позиционный аргумент и байты
    body = json.dumps(payload).encode("utf-8")
    req = HttpRequest(url, body, headers={"Content-Type": "application/json"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _http_json(_tg_url(method), payload)
    except Exception as e:
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

def _tg_send_photo(chat_id: int, png_bytes: bytes, caption: Optional[str] = None) -> None:
    """
    Отправляем PNG как документ, чтобы сохранить прозрачность (стикеры/фото могут сжимать).
    Если хотите именно фото — поменяйте на sendPhoto c multipart/form-data (сложнее).
    """
    # Для простоты: используем sendDocument (Base64 / multipart нельзя чисто через urllib без мимикрии).
    # Здесь сделаем временный URL через "attach://", но urllib без multipart не умеет.
    # Поэтому для сохранения прозрачности — лучше отправлять без подписи как обычный документ multipart.
    # Упростим: сохраним во временный файл и отправим как ссылка нельзя, потому fallback — sendMessage + base64 запрещено.
    # => используем sendMessage с предупреждением и даём ссылку на выгрузку PNG — в упрощенном виде опустим.
    # В проде используйте requests + multipart.
    _tg_send_message(chat_id, "⚠️ Прозрачный PNG готов, но текущая сборка отправляет только текст.\nПодключите multipart отправку для sendDocument.", None, "HTML")

def _tg_chat_action(chat_id: int, action: str) -> None:
    # typing, upload_photo, upload_document ...
    try:
        _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})
    except Exception:
        pass

# ======================= RU NAME STORAGE ==============================
def _load_ru_names() -> Dict[str, str]:
    j = _read_json(RU_NAMES_PATH)
    return j if isinstance(j, dict) else {}

def _load_aliases() -> Dict[str, str]:
    j = _read_json(ALIASES_PATH)
    return j if isinstance(j, dict) else {}

def _save_ru_name(person_id: str, name_ru: str) -> None:
    names = _load_ru_names()
    names[str(person_id)] = name_ru.strip()
    _write_json(RU_NAMES_PATH, names)

def _save_alias(alias: str, person_id: str) -> None:
    al = _load_aliases()
    al[alias.strip().lower()] = str(person_id)
    _write_json(ALIASES_PATH, al)

def _display_name_for(p: Dict[str, Any]) -> str:
    pid = str(p.get("personId", ""))
    ru = _load_ru_names()
    if pid in ru and ru[pid].strip():
        return ru[pid].strip()
    # fallback — англ дисплей
    fn = (p.get("firstName") or "").strip()
    ln = (p.get("lastName") or "").strip()
    disp = (fn + " " + ln).strip()
    return disp or p.get("displayName") or "Player"

def _resolve_by_alias_or_name(query: str, idx: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    # 1) alias-хит
    al = _load_aliases()
    if q in al:
        pid = al[q]
        p = idx.get(str(pid))
        if p:
            return [p]
    # 2) поиск по данным
    return find_player_by_name(query) or []

# ====================== ПАРСИНГ КОМАНД ================================
_cmd_re = re.compile(r"^/(\w+)(?:\s+(.*))?$", re.IGNORECASE)

def _split_card_args(s: str) -> Tuple[str, List[Tuple[str, str]], Optional[str]]:
    """
    Формат: "<имя> | метрика1, метрика2, ... [| template]"
    Возвращает: name, stats[(val, label)], template
    """
    parts = [x.strip() for x in s.split("|")]
    name = parts[0] if parts else ""
    stats_raw = parts[1] if len(parts) >= 2 else ""
    template = parts[2].lower() if len(parts) >= 3 else None

    stats: List[Tuple[str, str]] = []
    for chunk in [x.strip() for x in stats_raw.split(",") if x.strip()]:
        # "10 очков" -> ("10","ОЧКОВ"); "1 стилоблок" -> ("1","СТИЛОБЛОК")
        m = re.match(r"^\s*([+\-]?\d+[.,]?\d*)\s+(.*)$", chunk)
        if m:
            v = m.group(1).replace(",", ".")
            lab = m.group(2).strip()
            stats.append((v, lab))
        else:
            # если не распарсили — положим в label без value
            stats.append((chunk, ""))

    return name, stats, template

# =========================== FASTAPI APP ==============================
app = FastAPI()

# ------------------------ GET endpoints -------------------------------
@app.get("/api/telegram")
async def telegram_get(request: Request):
    secret = request.query_params.get("secret")
    if secret != WEBHOOK_SECRET:
        return JSONResponse({"detail": "bad secret"}, status_code=401)

    action = (request.query_params.get("action") or "").strip().lower()
    if not action:
        return JSONResponse({"ok": True, "route": "telegram-get", "boot_error": None})

    if action == "diag":
        # Проверим, что модули импортируются и токен есть
        boot_errors: Dict[str, Optional[str]] = {"graphics": None, "brand": None}
        try:
            _ = render_card
        except Exception as e:
            boot_errors["graphics"] = repr(e)
        try:
            _ = get_team_brand
        except Exception as e:
            boot_errors["brand"] = repr(e)
        return JSONResponse({
            "ok": True,
            "py": ".".join([str(x) for x in list(os.sys.version_info)[:2]]),
            "platform": os.uname().sysname if hasattr(os, "uname") else "N/A",
            "modules": {
                "graphics": "ok" if boot_errors["graphics"] is None else "error",
                "team_brand": "ok" if boot_errors["brand"] is None else "error",
            },
            "has_bot_token": bool(BOT_TOKEN),
            "boot_error": boot_errors if any(boot_errors.values()) else None,
            "brand_warn": None,
        })

    if action == "refresh":
        try:
            # Прямой вызов — без _safe_import
            cnt_prev = players_count()
            n, info = refresh_players(drop_cache=False)  # (count, {ok, source...})
            cnt_now = players_count()
            return JSONResponse({
                "ok": info.get("ok", True),
                "refreshed": True,
                "players_indexed": int(cnt_now or n or 0),
                "source": info.get("source", "custom_or_cache"),
                "source_url": info.get("source_url") if isinstance(info, dict) else None
            })
        except Exception as e:
            _log("[players] refresh error:", repr(e))
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=200)

    return JSONResponse({"ok": False, "error": f"unknown action '{action}'"}, status_code=400)

# ------------------------ POST webhook --------------------------------
@app.post("/api/telegram")
async def webhook_query(request: Request):
    # Базовая проверка секрета
    secret = request.query_params.get("secret")
    if secret != WEBHOOK_SECRET:
        return PlainTextResponse("NO", status_code=403)

    rid = f"{int(time.time()*1000)}-{os.urandom(3).hex()}"
    try:
        body = await request.body()
        body_text = body.decode("utf-8", errors="ignore")
        if DEBUG:
            _log(f"[RID={rid}] POST {str(request.url)}")
            _log(f"[RID={rid}] body:", body_text)
        update = json.loads(body_text)
    except Exception:
        return PlainTextResponse("BAD", status_code=200)

    # Разбор апдейта
    msg = (update.get("message") or update.get("edited_message") or
           update.get("channel_post") or update.get("edited_channel_post"))
    cb  = update.get("callback_query")
    if cb and not msg:
        msg = cb.get("message")

    if not msg:
        return PlainTextResponse("OK", status_code=200)

    chat_id = int(msg.get("chat", {}).get("id", 0) or 0)
    text = msg.get("text") or ""
    reply_to = msg.get("reply_to_message")
    message_id = int(msg.get("message_id", 0))

    # 1) Обработка ответов на наши служебные подсказки
    # Имя-рус: ищем маркер [setname:<pid>]
    if reply_to and isinstance(reply_to, dict):
        reply_txt = reply_to.get("text") or ""
        m = re.search(r"\[setname:(\d+)\]", reply_txt)
        if m and text.strip():
            pid = m.group(1)
            ru_name = text.strip()
            _save_ru_name(pid, ru_name)
            # добьём алиасы из исходной строки (если там было /card <имя> | ...)
            # в простом варианте — alias = исходный "name" будет добавлен при команде /card
            _tg_send_message(chat_id, f"Сохранил имя для {pid}: {ru_name}")
            return PlainTextResponse("OK", status_code=200)

    # 2) Команды
    m = _cmd_re.match(text.strip())
    if m:
        cmd = m.group(1).lower()
        arg = (m.group(2) or "").strip()

        if cmd in ("start", "help"):
            HELP_TEXT = (
                "Привет! Я онлайн 🤖\n\n"
                "Команды:\n"
                "• /start — проверка связи\n"
                "• /help — это сообщение\n"
                "• /find <имя/фамилия> — найти игрока (например: /find Doncic)\n"
                "• /card <имя> | <метрики через запятую>\n"
                "  пример: /card wembanyama | 10 очков, 12 передач, 8 подборов\n"
                "• /name <имя> — интерактивно задать русское имя (ответьте на сообщение-подсказку)\n"
                "• /team <имя> — задать/переопределить teamId (пока не реализовано в этой сборке)\n"
            )
            _tg_send_message(chat_id, HELP_TEXT)
            return PlainTextResponse("OK", status_code=200)

        if cmd == "find":
            q = arg.strip()
            if not q:
                _tg_send_message(chat_id, "Укажите имя, например: <code>/find Lebron</code>", parse_mode="HTML")
                return PlainTextResponse("OK", status_code=200)

            # убедимся что игроки в памяти
            try:
                if players_count() <= 0:
                    refresh_players(drop_cache=False)
            except Exception as e:
                _log("[players] refresh error:", e)

            idx = get_players_index()
            cand = _resolve_by_alias_or_name(q, idx)
            if not cand:
                _tg_send_message(chat_id, "Ничего не нашёл 🤷")
                return PlainTextResponse("OK", status_code=200)

            lines = []
            for p in cand[:6]:
                pid = p.get("personId")
                ln = f"{_display_name_for(p)} (id={pid}, teamId={p.get('teamId','0')})"
                lines.append(ln)
            _tg_send_message(chat_id, "\n".join(lines))
            return PlainTextResponse("OK", status_code=200)

        if cmd == "name":
            # интерактивный запуск — найдём кандидата и спросим
            base = (arg or "").strip()
            if not base:
                _tg_send_message(chat_id, "Укажите имя игрока: <code>/name Kevin Durant</code>", parse_mode="HTML")
                return PlainTextResponse("OK", status_code=200)

            try:
                if players_count() <= 0:
                    refresh_players(drop_cache=False)
            except Exception as e:
                _log("[players] refresh error:", e)

            idx = get_players_index()
            cand = _resolve_by_alias_or_name(base, idx)
            if not cand:
                _tg_send_message(chat_id, f"Не нашёл игрока: {base}")
                return PlainTextResponse("OK", status_code=200)

            p = cand[0]
            pid = p.get("personId")
            ask = (
                f"Как подписать игрока {p.get('firstName','')} {p.get('lastName','')} на плашке?\n"
                f"Ответьте на это сообщение русским именем.\n"
                f"[setname:{pid}]"
            )
            _tg_send_message(chat_id, ask, reply_to_message_id=message_id)
            return PlainTextResponse("OK", status_code=200)

        if cmd == "card":
            # формат: /card <имя> | <метрики,...>
            name_part, stats, template = _split_card_args(arg)

            if not name_part:
                _tg_send_message(chat_id, "Формат: <code>/card Имя | 10 очков, 12 передач</code>", parse_mode="HTML")
                return PlainTextResponse("OK", status_code=200)

            # 1) ensure players
            try:
                if players_count() <= 0:
                    refresh_players(drop_cache=False)
            except Exception as e:
                _log("[players] refresh error:", e)

            idx = get_players_index()
            cand = _resolve_by_alias_or_name(name_part, idx)
            if not cand:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_part}")
                return PlainTextResponse("OK", status_code=200)

            p = cand[0]
            pid = str(p.get("personId", ""))
            team_id = str(p.get("teamId", "0") or "0")

            # 2) если русского имени нет — спросим (УТОЧНЕНИЕ)
            ru_names = _load_ru_names()
            if pid not in ru_names:
                _tg_send_message(chat_id, "<i>Уточнения…</i>", parse_mode="HTML")
                ask = (f"Как подписать игрока {p.get('firstName','')} {p.get('lastName','')} на плашке?\n"
                       f"Ответьте на это сообщение русским именем.\n"
                       f"[setname:{pid}]")
                _tg_send_message(chat_id, ask, reply_to_message_id=message_id)
                return PlainTextResponse("OK", status_code=200)

            # 3) готовим плашку
            _tg_chat_action(chat_id, "typing")
            _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")

            # HEADSHOT
            head_path: Optional[str] = None
            try:
                head_path = ensure_headshot_png(pid)
            except Exception as e:
                _log("[headshot] ensure err", pid, repr(e))
            if not head_path or not os.path.exists(head_path):
                _tg_send_message(chat_id, "Не удалось получить фото игрока 😕")
                return PlainTextResponse("OK", status_code=200)

            # TEAM BRAND (цвета + лого)
            try:
                colors, logo_path, palette, has_saved = get_team_brand(team_id)
            except Exception as e:
                _log("[brand] get_team_brand error:", repr(e))
                colors, logo_path = ("#007ACC", "#005FA3", "#3399FF"), None

            # открыть изображения
            from PIL import Image
            try:
                head_img = Image.open(head_path).convert("RGBA")
            except Exception as e:
                _tg_send_message(chat_id, "Ошибка чтения фото игрока.")
                return PlainTextResponse("OK", status_code=200)

            team_logo_img = None
            if logo_path and os.path.exists(logo_path):
                try:
                    team_logo_img = Image.open(logo_path).convert("RGBA")
                except Exception:
                    team_logo_img = None

            # имя к показу — русское
            display_name = _display_name_for(p)

            # рендер PNG
            try:
                png = render_card("single", display_name, "", team_logo_img, colors, head_img, stats)
            except Exception as e:
                _log("card render error:", traceback.format_exc())
                _tg_send_message(chat_id, "Ошибка при рендере карточки.")
                return PlainTextResponse("OK", status_code=200)

            # отправка (см. комментарий в _tg_send_photo про multipart)
            _tg_send_photo(chat_id, png, caption=None)
            return PlainTextResponse("OK", status_code=200)

        # неизвестная команда
        _tg_send_message(chat_id, "Не понял команду.\nНапишите /help")
        return PlainTextResponse("OK", status_code=200)

    # если не команда — игнор
    return PlainTextResponse("OK", status_code=200)
