# api/telegram.py
# Telegram webhook for VM-Plashki-NEWS
# - Crash-guard: не падает при импорт-ошибках, показывает причину через action=diag
# - Команды: /start, /help, /find, /card, /card2, /cardBAD, /cardS, /name, /team, /refresh
# - Интерактив: русское имя ([setname:PID]), выбор цвета ([setcolor:TEAMID])
# - PNG отсылается как sendDocument (сохраняет прозрачный фон)

from __future__ import annotations

import os, io, json, re, base64, traceback
from typing import Any, Dict, List, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# -------------------- BOOT GUARD --------------------
BOOT_ERR: Optional[str] = None

def _exc_to_str(e: Exception) -> str:
    return f"{e.__class__.__name__}: {e}\n{traceback.format_exc()}"

try:
    # data: players + helpers
    from data import (
        get_players, get_players_index, refresh_players, drop_players_cache,
        find_player_by_name, players_count, players, players_index,
        ensure_headshot_png, ensure_team_logo_png,
        display_name_for, save_display_name_override, save_team_override,
    )
    # team brand colors
    from team_brand import team_colors_for, color_name_for
    # graphics renderers
    try:
        from graphics import (
            render_card, render_card2, render_card_bad,
            render_card_special, render_card_drN
        )
        try:
            # Back-compat: старые пути могли звать render_card_dr(...)
            from graphics import render_card_dr   # type: ignore
        except Exception:
            def render_card_dr(n, player_name, head_img, logo_img, stats):  # type: ignore
                return render_card_drN(n, player_name, head_img, logo_img, stats)
    except Exception as ge:
        # графика не должна валить запуск
        BOOT_ERR = _exc_to_str(ge)
        def _gfx_fail(*a, **k):  # type: ignore
            raise RuntimeError(f"graphics import failed: {BOOT_ERR}")
        render_card = render_card2 = render_card_bad = render_card_special = render_card_drN = render_card_dr = _gfx_fail  # type: ignore
except Exception as e:
    BOOT_ERR = _exc_to_str(e)

app = FastAPI()

# -------------------- CONFIG --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook-123").strip()
ALLOW_DEBUG_NO_SECRET = os.getenv("ALLOW_DEBUG_NO_SECRET", "0") == "1"

# -------------------- TG HTTP --------------------
def _tg_api_url(method: str) -> str:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_json(url: str, payload: Dict[str, Any], timeout: int = 25) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={
        "Content-Type": "application/json",
        "User-Agent": "vm-plashki-news/telegram",
        "Connection": "close",
    })
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8"))

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _http_json(_tg_api_url(method), payload, timeout=25)

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None, parse_mode: Optional[str] = "HTML") -> None:
    p = {"chat_id": chat_id, "text": text}
    if parse_mode: p["parse_mode"] = parse_mode
    if reply_to: p["reply_to_message_id"] = reply_to
    try:
        _tg_post("sendMessage", p)
    except Exception as e:
        print("[tg] sendMessage err", e)

def _tg_chat_action(chat_id: int, action: str = "typing"):
    try:
        _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})
    except Exception:
        pass

def _tg_send_png_as_document(chat_id: int, png_bytes: bytes, filename: str = "card.png", caption: Optional[str] = None, reply_to: Optional[int] = None):
    # multipart/form-data вручную
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    lines: List[bytes] = []
    def add_field(name: str, value: str):
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(value.encode())

    def add_file(name: str, filename: str, content_type: str, data: bytes):
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")
        lines.append(data)

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)
        add_field("parse_mode", "HTML")
    if reply_to:
        add_field("reply_to_message_id", str(reply_to))
    add_file("document", filename, "image/png", png_bytes)
    lines.append(f"--{boundary}--".encode())
    body = b"\r\n".join(lines)

    req = Request(_tg_api_url("sendDocument"), data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "vm-plashki-news/telegram",
        "Connection": "close",
    })
    try:
        with urlopen(req, timeout=45) as r:
            r.read()
    except Exception as e:
        print("[tg] sendDocument err", e)

# -------------------- UTILS --------------------
_HEX_RE = re.compile(r"#?[0-9A-Fa-f]{6}")

def _parse_stats(block: str) -> List[Tuple[str, str]]:
    # "10 очков, 12 передач, 15 подборов, 1 стилоблок"
    out: List[Tuple[str,str]] = []
    for part in re.split(r"[;,|/]\s*|,\s*", block.strip()):
        if not part: continue
        m = re.match(r"\s*([+\-]?\d+(?:\.\d+)?)\s*(.*)", part)
        if m:
            val = m.group(1)
            lab = (m.group(2) or "").strip()
            out.append((val, lab))
        else:
            out.append((part.strip(), ""))  # fallback
    return out

def _ensure_headshot_pil(p: Dict[str, Any]) -> Optional["Image.Image"]:
    # Обёртка: разные сигнатуры в data.ensure_headshot_png
    try:
        path = ensure_headshot_png(p)  # type: ignore
    except TypeError:
        try:
            path = ensure_headshot_png(p.get("personId"))  # type: ignore
        except Exception as e:
            print("[tg] headshot ensure err", p.get("personId"), e)
            return None
    except Exception as e:
        print("[tg] headshot ensure err", p.get("personId"), e)
        return None
    try:
        from PIL import Image
        return Image.open(path).convert("RGBA")
    except Exception as e:
        print("[tg] headshot open err", path, e)
        return None

def _ensure_logo_pil(team_id: str) -> Optional["Image.Image"]:
    # Сначала assets/cache/logo_{teamId}.png
    try:
        from PIL import Image
        cache_path = os.path.join("assets", "cache", f"logo_{team_id}.png")
        if os.path.exists(cache_path):
            return Image.open(cache_path).convert("RGBA")
    except Exception:
        pass
    # Затем data.ensure_team_logo_png
    try:
        path = ensure_team_logo_png(team_id)  # type: ignore
        if path and os.path.exists(path):
            from PIL import Image
            return Image.open(path).convert("RGBA")
    except Exception as e:
        print("[tg] logo ensure err", team_id, e)
    return None

def _player_display_name_ru(p: Dict[str,Any]) -> str:
    try:
        dn = display_name_for(p)  # уже учитывает overrides (RU)
        if dn: return dn
    except Exception:
        pass
    # fallback EN
    fn = (p.get("firstName") or "").strip()
    ln = (p.get("lastName") or "").strip()
    return (fn + " " + ln).strip()

def _send_italic(chat_id: int, text: str, reply_to: Optional[int] = None):
    _tg_send_message(chat_id, f"<i>{text}</i>", reply_to=reply_to, parse_mode="HTML")

# -------------------- ROUTES: HEALTH/DIAG --------------------
@app.get("/api/telegram")
async def telegram_get(secret: str = "", action: Optional[str] = None):
    if secret != WEBHOOK_SECRET and not ALLOW_DEBUG_NO_SECRET:
        return JSONResponse({"detail": "bad secret"}, status_code=403)

    if action == "diag":
        return JSONResponse({
            "ok": BOOT_ERR is None,
            "py": os.getenv("PYTHON_VERSION", "3.12"),
            "platform": os.uname().sysname if hasattr(os, "uname") else "n/a",
            "modules": {
                "graphics": "ok" if BOOT_ERR is None else "error",
            },
            "has_bot_token": bool(BOT_TOKEN),
            "boot_error": BOOT_ERR,
        })
    return JSONResponse({"ok": True, "route": "telegram-get", "boot_error": BOOT_ERR})

# -------------------- WEBHOOK (POST) --------------------
@app.post("/api/telegram")
async def webhook_query(request: Request, secret: str = ""):
    if secret != WEBHOOK_SECRET and not ALLOW_DEBUG_NO_SECRET:
        return PlainTextResponse("forbidden", status_code=403)
    if BOOT_ERR:
        return PlainTextResponse("BOOT_ERR\n" + BOOT_ERR, status_code=200)

    try:
        update = await request.json()
    except Exception:
        return PlainTextResponse("no json", status_code=200)

    # Поддерживаем только message/supergroup
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return PlainTextResponse("OK", status_code=200)

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")

    if not chat_id or not text:
        return PlainTextResponse("OK", status_code=200)

    # Ответ-на-бота для интерактива (setname / setcolor / pick)
    reply_to = msg.get("reply_to_message") or {}
    reply_text = (reply_to.get("text") or "")
    if reply_text:
        # 1) Ответ на "Как подписать игрока ... [setname:PID]"
        m = re.search(r"\[setname:(\d+)\](?:\|(.+))?", reply_text)
        if m:
            pid = m.group(1)
            # RU имя — весь ввод без слеша
            ru = text.strip()
            if ru.startswith("/"):  # если случайно команда — игнор
                _tg_send_message(chat_id, "Пришлите имя на русском (не командой).", reply_to=msg_id)
                return PlainTextResponse("OK", status_code=200)
            try:
                save_display_name_override(pid, ru)  # persist
            except Exception as e:
                _tg_send_message(chat_id, f"Не удалось сохранить имя: {e}", reply_to=msg_id)
                return PlainTextResponse("OK", status_code=200)
            _tg_send_message(chat_id, f"Сохранил имя: <b>{ru}</b>", reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        # 2) Ответ на выбор цвета "[setcolor:TEAMID]"
        m = re.search(r"\[setcolor:(\d+)\]", reply_text)
        if m:
            color = text.strip()
            if color.lower() in ("авто", "auto"):
                _tg_send_message(chat_id, "Ок, цвет: авто.", reply_to=msg_id)
                return PlainTextResponse("OK", status_code=200)
            if not _HEX_RE.fullmatch(color if color.startswith("#") else "#" + color):
                _tg_send_message(chat_id, "Пришлите HEX вида <code>#552583</code> или слово <b>авто</b>.", reply_to=msg_id)
                return PlainTextResponse("OK", status_code=200)
            # это просто подтверждение — сам цвет подставляется на этапе рендера, когда он реально нужен
            _tg_send_message(chat_id, f"Принял ваш цвет: <code>{color if color.startswith('#') else '#'+color}</code> (применяйте при создании плашки).", reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        # 3) Ответ на список кандидатов "[pick:card:pid1,pid2,...]"
        m = re.search(r"\[pick:(card|card2|cardS|cardBAD):([0-9,]+)\]", reply_text)
        if m:
            # Мы не тащим сохранённый контекст — это лёгкий выбор игрока для последующей команды
            # Пользователю подскажем: вызовите команду ещё раз с выбранным именем/ID
            _tg_send_message(chat_id, "Выберите игрока и повторите команду с его именем/ID.", reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

    # -------------------- Команды --------------------
    low = text.lower()

    if low == "/start" or low.startswith("/start "):
        _tg_send_message(chat_id, "Привет! Я онлайн 🤖\n\nКоманды:\n"
            "• /find <имя> — найти игрока\n"
            "• /card <имя> | <статы>\n"
            "• /card2 <имя1> | <статы1> | <имя2> | <статы2>\n"
            "• /cardBAD <имя> | <статы>\n"
            "• /cardS <имя> | <статы> | <инфо>\n"
            "• /name <имя> — задать русское имя\n"
            "• /team <имя> — задать teamId\n"
            "• /refresh — обновить базу игроков")
        return PlainTextResponse("OK", status_code=200)

    if low.startswith("/help"):
        _tg_send_message(chat_id, "Помощь:\n"
            "Форматы:\n"
            "• /card Booker | 10 очков, 8 передач, 6 подборов\n"
            "• /card2 Booker | 20 очков | Durant | 18 очков\n"
            "• /cardBAD Player | 5 потерь, 2 фола\n"
            "• /cardS Player | 18 очков, 10 подборов | Вернулся после травмы\n\n"
            "Русское имя задаётся интерактивно при первом вызове или командой /name.")
        return PlainTextResponse("OK", status_code=200)

    if low.startswith("/refresh"):
        _tg_send_message(chat_id, "<i>Обновляю базу игроков…</i>")
        try:
            n, info = refresh_players(drop_cache=False)
            _tg_send_message(chat_id, f"Ок: игроков {n}, источник: {info.get('source')}")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка refresh: {e}")
        return PlainTextResponse("OK", status_code=200)

    if low.startswith("/find "):
        q = text.split(" ", 1)[1].strip()
        cand = find_player_by_name(q) or []
        if not cand:
            _tg_send_message(chat_id, f"Не нашёл: {q}")
        else:
            lines = []
            for p in cand[:10]:
                lines.append(f"{_player_display_name_ru(p)} (id={p.get('personId')}, teamId={p.get('teamId')})")
            _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK", status_code=200)

    if low.startswith("/name "):
        q = text.split(" ", 1)[1].strip()
        cand = find_player_by_name(q) or []
        if not cand:
            _tg_send_message(chat_id, f"Не нашёл: {q}")
            return PlainTextResponse("OK", status_code=200)
        # если несколько — берём первого
        p = cand[0]
        en = (p.get("firstName","") + " " + p.get("lastName","")).strip()
        _tg_send_message(chat_id, f"Как подписать игрока {en} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p.get('personId')}]", reply_to=msg_id)
        return PlainTextResponse("OK", status_code=200)

    if low.startswith("/team "):
        q = text.split(" ", 1)[1].strip()
        cand = find_player_by_name(q) or []
        if not cand:
            _tg_send_message(chat_id, f"Не нашёл: {q}")
            return PlainTextResponse("OK", status_code=200)
        p = cand[0]
        _tg_send_message(chat_id, f"Пришлите teamId (число) для игрока id={p.get('personId')}.\n"
                                  f"Текущий: {p.get('teamId')}")
        return PlainTextResponse("OK", status_code=200)

    # -------- /card --------
    if low.startswith("/card "):
        # /card <name> | <stats>
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, "Формат: /card <имя> | <статы>")
            return PlainTextResponse("OK", status_code=200)

        name_q = parts[0]
        stats_s = parts[1]
        stats = _parse_stats(stats_s)

        cand = find_player_by_name(name_q) or []
        if not cand:
            # кандидаты из похожих — предложим 4 варианта + добавление
            _tg_send_message(chat_id,
                "Не нашёл игрока. Возможные варианты:\n"
                "1) Уточните имя (фамилию)\n"
                "2) Используйте /find <имя>\n"
                "3) Проверьте раскладку/опечатки\n\n[pick:card:]",
                reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        p = cand[0]
        pid = p.get("personId")
        team_id = str(p.get("teamId") or "0")

        # Русское имя?
        ru = display_name_for(p) if callable(display_name_for) else None  # type: ignore
        if not ru:
            en = (p.get("firstName","") + " " + p.get("lastName","")).strip()
            _tg_send_message(chat_id,
                f"Как подписать игрока {en} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        # Цвет: предлагаем «авто» или свой hex — ответить на это сообщение
        _send_italic(chat_id, "Уточнения…", reply_to=msg_id)
        _tg_send_message(chat_id,
            f"Цвет плашки: <b>авто</b> или свой HEX (например, <code>#552583</code>)?\n"
            f"Ответьте на это сообщение словом <b>авто</b> или HEX.\n[setcolor:{team_id}]")

        # Тут мы не ждём ответ синхронно — используем авто
        _send_italic(chat_id, "Готовлю плашку…")
        # Собираем head, logo, colors
        head = _ensure_headshot_pil(p)
        if head is None:
            _tg_send_message(chat_id, "Не удалось получить фото игрока.", reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        colors = team_colors_for(team_id) if callable(team_colors_for) else ("#007ACC", "#003E6B", "#66B2FF")  # type: ignore
        logo_img = _ensure_logo_pil(team_id)

        # Рендер
        try:
            png = render_card("single", _player_display_name_ru(p), "", logo_img, colors, head, stats)  # type: ignore
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка рендера: {e}", reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        _tg_send_png_as_document(chat_id, png, filename="card.png")
        return PlainTextResponse("OK", status_code=200)

    # -------- /card2 --------
    if low.startswith("/card2 "):
        # /card2 name1 | stats1 | name2 | stats2
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 4:
            _tg_send_message(chat_id, "Формат: /card2 <имя1> | <статы1> | <имя2> | <статы2>")
            return PlainTextResponse("OK", status_code=200)

        name1, stats1_s, name2, stats2_s = parts[0], parts[1], parts[2], parts[3]
        stats1, stats2 = _parse_stats(stats1_s), _parse_stats(stats2_s)

        cand1 = find_player_by_name(name1) or []
        cand2 = find_player_by_name(name2) or []
        if not cand1 or not cand2:
            _tg_send_message(chat_id, "Не нашёл одного из игроков. Проверьте имена.")
            return PlainTextResponse("OK", status_code=200)

        p1, p2 = cand1[0], cand2[0]
        pid1, pid2 = p1.get("personId"), p2.get("personId")
        team1, team2 = str(p1.get("teamId") or "0"), str(p2.get("teamId") or "0")

        # RU имена — если нет, спросим отдельно
        if not display_name_for(p1) or not display_name_for(p2):  # type: ignore
            missing = []
            if not display_name_for(p1): missing.append(pid1)  # type: ignore
            if not display_name_for(p2): missing.append(pid2)  # type: ignore
            _tg_send_message(chat_id,
                "Нужно задать русские имена:\n" +
                "\n".join([f"[setname:{m}]" for m in missing]),
                reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        _send_italic(chat_id, "Уточнения…", reply_to=msg_id)
        _tg_send_message(chat_id,
            f"Цвета плашек: <b>авто</b> или свой HEX (каждому) — ответьте на это сообщение для каждого.\n"
            f"[setcolor:{team1}]  [setcolor:{team2}]")

        _send_italic(chat_id, "Готовлю плашку…")
        head1, head2 = _ensure_headshot_pil(p1), _ensure_headshot_pil(p2)
        if head1 is None or head2 is None:
            _tg_send_message(chat_id, "Не удалось получить фото одного из игроков.")
            return PlainTextResponse("OK", status_code=200)

        colors1 = team_colors_for(team1) if callable(team_colors_for) else ("#007ACC","#003E6B","#66B2FF")  # type: ignore
        colors2 = team_colors_for(team2) if callable(team_colors_for) else ("#007ACC","#003E6B","#66B2FF")  # type: ignore
        logo1, logo2 = _ensure_logo_pil(team1), _ensure_logo_pil(team2)

        try:
            png = render_card2(
                _player_display_name_ru(p1), logo1, colors1, head1, stats1,
                _player_display_name_ru(p2), logo2, colors2, head2, stats2
            )  # type: ignore
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка рендера: {e}")
            return PlainTextResponse("OK", status_code=200)

        _tg_send_png_as_document(chat_id, png, filename="card2.png")
        return PlainTextResponse("OK", status_code=200)

    # -------- /cardBAD --------
    if low.startswith("/cardbad ") or low.startswith("/cardbad@"):
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, "Формат: /cardBAD <имя> | <статы>")
            return PlainTextResponse("OK", status_code=200)

        name_q, stats_s = parts[0], parts[1]
        stats = _parse_stats(stats_s)
        cand = find_player_by_name(name_q) or []
        if not cand:
            _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
            return PlainTextResponse("OK", status_code=200)

        p = cand[0]
        if not display_name_for(p):  # type: ignore
            en = (p.get("firstName","") + " " + p.get("lastName","")).strip()
            _tg_send_message(chat_id,
                f"Как подписать игрока {en} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p.get('personId')}]",
                reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        _send_italic(chat_id, "Готовлю плашку…")
        head = _ensure_headshot_pil(p)
        if head is None:
            _tg_send_message(chat_id, "Не удалось получить фото игрока.")
            return PlainTextResponse("OK", status_code=200)

        try:
            png = render_card_bad(_player_display_name_ru(p), head, stats)  # type: ignore
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка рендера: {e}")
            return PlainTextResponse("OK", status_code=200)

        _tg_send_png_as_document(chat_id, png, filename="card_bad.png")
        return PlainTextResponse("OK", status_code=200)

    # -------- /cardS --------
    if low.startswith("/cards "):
        # /cardS <name> | <stats> | <info>
        body = text.split(" ", 1)[1]
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 3:
            _tg_send_message(chat_id, "Формат: /cardS <имя> | <статы> | <инфо>")
            return PlainTextResponse("OK", status_code=200)

        name_q, stats_s, info = parts[0], parts[1], parts[2]
        stats = _parse_stats(stats_s)

        cand = find_player_by_name(name_q) or []
        if not cand:
            _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
            return PlainTextResponse("OK", status_code=200)

        p = cand[0]
        team_id = str(p.get("teamId") or "0")
        if not display_name_for(p):  # type: ignore
            en = (p.get("firstName","") + " " + p.get("lastName","")).strip()
            _tg_send_message(chat_id,
                f"Как подписать игрока {en} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p.get('personId')}]",
                reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        _send_italic(chat_id, "Уточнения…", reply_to=msg_id)
        _tg_send_message(chat_id,
            f"Цвет плашки: авто или свой HEX?\nОтветьте на это сообщение словом <b>авто</b> или HEX.\n[setcolor:{team_id}]")

        _send_italic(chat_id, "Готовлю плашку…")
        head = _ensure_headshot_pil(p)
        if head is None:
            _tg_send_message(chat_id, "Не удалось получить фото игрока.")
            return PlainTextResponse("OK", status_code=200)

        colors = team_colors_for(team_id) if callable(team_colors_for) else ("#007ACC","#003E6B","#66B2FF")  # type: ignore
        logo_img = _ensure_logo_pil(team_id)

        try:
            png = render_card_special(_player_display_name_ru(p), logo_img, colors, head, stats, info)  # type: ignore
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка рендера: {e}")
            return PlainTextResponse("OK", status_code=200)

        _tg_send_png_as_document(chat_id, png, filename="card_s.png")
        return PlainTextResponse("OK", status_code=200)

    # -------- DR variations (по макетам) --------
    if low.startswith("/carddr"):
        # /cardDR3 <name> | <stats...> (макет определяет количество ячеек)
        m = re.match(r"^/carddr(\d+)\s+(.*)$", text, re.IGNORECASE)
        if not m:
            _tg_send_message(chat_id, "Формат: /cardDR3 <имя> | <статы…>")
            return PlainTextResponse("OK", status_code=200)
        n = int(m.group(1))
        body = m.group(2)
        parts = [s.strip() for s in body.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, f"Формат: /cardDR{n} <имя> | <статы…>")
            return PlainTextResponse("OK", status_code=200)

        name_q, stats_s = parts[0], "|".join(parts[1:])
        stats = _parse_stats(stats_s)

        cand = find_player_by_name(name_q) or []
        if not cand:
            _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
            return PlainTextResponse("OK", status_code=200)

        p = cand[0]
        team_id = str(p.get("teamId") or "0")

        if not display_name_for(p):  # type: ignore
            en = (p.get("firstName","") + " " + p.get("lastName","")).strip()
            _tg_send_message(chat_id,
                f"Как подписать игрока {en} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p.get('personId')}]",
                reply_to=msg_id)
            return PlainTextResponse("OK", status_code=200)

        _send_italic(chat_id, "Готовлю плашку…")
        head = _ensure_headshot_pil(p)
        if head is None:
            _tg_send_message(chat_id, "Не удалось получить фото игрока.")
            return PlainTextResponse("OK", status_code=200)
        logo_img = _ensure_logo_pil(team_id)

        try:
            png = render_card_drN(n, _player_display_name_ru(p), head, logo_img, stats)  # type: ignore
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка рендера: {e}")
            return PlainTextResponse("OK", status_code=200)

        _tg_send_png_as_document(chat_id, png, filename=f"cardDR{n}.png")
        return PlainTextResponse("OK", status_code=200)

    # Если ничего не совпало
    _tg_send_message(chat_id,
        "Не понял команду.\n\nКоманды:\n"
        "• /find <имя>\n"
        "• /card <имя> | <статы>\n"
        "• /card2 <имя1> | <статы1> | <имя2> | <статы2>\n"
        "• /cardBAD <имя> | <статы>\n"
        "• /cardS <имя> | <статы> | <инфо>\n"
        "• /refresh")
    return PlainTextResponse("OK", status_code=200)
