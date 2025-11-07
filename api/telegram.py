# api/telegram.py — Telegram webhook с чистым промптом, ForceReply, выбором цвета и поддержкой /card /cards /bad /card2
from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid, base64, html
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

DEBUG = os.getenv("DEBUG", "1") in ("1","true","yes")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
API_ORIGIN = os.getenv("API_ORIGIN")

def _log(*a): 
    try: print(*a, flush=True)
    except: pass

def _safe_import(modname: str, names: List[str]):
    try:
        m = __import__(modname, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, out, None
    except Exception as e:
        return None, [], f"{e.__class__.__name__}: {e}"

# data
_data_mod, _data_objs, _data_err = _safe_import("data", [
    "get_players", "refresh_players", "find_player_by_name", "search_players_loose",
    "display_name_for", "overrides_save_name_ru", "overrides_get_name_ru",
    "ensure_headshot_png", "ensure_team_logo_png"
])
(get_players, refresh_players, find_player_by_name, search_players_loose,
 display_name_for, overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png) = ([_ for _ in _data_objs] + [None]*9)[:9]

# team_brand
_brand_mod, _brand_objs, _brand_err = _safe_import("team_brand", [
    "get_team_brand", "color_name_ru", "set_team_primary_color"
])
(get_team_brand, color_name_ru, set_team_primary_color) = ([_ for _ in _brand_objs] + [None]*3)[:3]

# graphics
_graphics_mod, _graphics_objs, _graphics_err = _safe_import("graphics", [
    "render_card", "render_card2", "render_card_bad", "render_card_special", "render_card_drN"
])
(render_card, render_card2, render_card_bad, render_card_special, render_card_drN) = ([_ for _ in _graphics_objs] + [None]*5)[:5]

app = FastAPI()

# ------------ Telegram HTTP ------------
def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_json(url: str, payload: Dict[str, Any], timeout: int = 25) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = UrlRequest(url, data=body, headers={"Content-Type": "application/json"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "raw": raw.decode("utf-8","ignore")}

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _http_json(_tg_url(method), payload)
    except Exception as e:
        if DEBUG: _log("[tg] send error:", repr(e))
        return {"ok": False, "error": repr(e)}

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int]=None, parse_mode: Optional[str]=None, reply_markup: Optional[Dict]=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = reply_markup
    return _tg_post("sendMessage", payload)

def _tg_send_chat_action(chat_id: int, action: str="typing"):
    return _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})

def _tg_answer_cbq(cb_id: str, text: str = "", show_alert: bool = False):
    return _tg_post("answerCallbackQuery", {"callback_query_id": cb_id, "text": text, "show_alert": show_alert})

def _multipart_boundary() -> str:
    return "----WebKitFormBoundary" + uuid.uuid4().hex

def _encode_multipart(fields: Dict[str,str], files: Dict[str, Tuple[str, bytes, str]]) -> Tuple[bytes, str]:
    boundary = _multipart_boundary()
    lines: List[bytes] = []
    for name, value in fields.items():
        lines += [
            b"--" + boundary.encode(),
            f'Content-Disposition: form-data; name="{name}"'.encode(),
            b"",
            value.encode("utf-8"),
        ]
    for field_name, (filename, content, content_type) in files.items():
        lines += [
            b"--" + boundary.encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode(),
            f"Content-Type: {content_type}".encode(),
            b"",
            content,
        ]
    lines.append(b"--" + boundary.encode() + b"--")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"

def _tg_send_png_as_document(chat_id: int, png_bytes: bytes, filename: str="card.png", caption: Optional[str]=None):
    url = _tg_url("sendDocument")
    fields = {"chat_id": str(chat_id)}
    if caption: fields["caption"] = caption
    files = {"document": (filename, png_bytes, "image/png")}
    body, ctype = _encode_multipart(fields, files)
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    try:
        with http_urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8","ignore")
            try: return json.loads(raw)
            except Exception: return {"ok": False, "raw": raw}
    except Exception as e:
        if DEBUG: _log("[tg] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# ------------ Утилиты домена ------------
PLAYERS_READY = False

def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

STAT_TOKEN_MAP = {
    "очк":"ОЧКИ","передач":"ПЕРЕДАЧИ","подбор":"ПОДБОРЫ","блок":"БЛОКИ",
    "стил":"ПЕРЕХВАТЫ","мин":"МИНУТЫ","трёх":"3-ОЧКОВЫЕ","трех":"3-ОЧКОВЫЕ",
    "фол":"ФОЛЫ","потер":"ПОТЕРИ","дабл":"ДАБЛ-ДАБЛ","трипл":"ТРИПЛ-ДАБЛ",
}

def parse_stats_list(raw: str) -> List[Tuple[str,str]]:
    if not raw: return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Tuple[str,str]] = []
    for p in parts:
        m = re.match(r"^\s*([0-9]+)\s*([^\d,]+)?", p, flags=re.IGNORECASE)
        if not m: continue
        val = m.group(1)
        lbl_raw = (m.group(2) or "").strip().lower()
        lbl = "СТАТ"
        for k,v in STAT_TOKEN_MAP.items():
            if k in lbl_raw: lbl=v; break
        out.append((val, lbl))
    return out

def ensure_players_loaded(force: bool=False) -> List[Dict[str,Any]]:
    global PLAYERS_READY
    ps = []
    try:
        ps = get_players(force_refresh=bool(force)) if get_players else []
        if not ps or len(ps) < 50:
            if refresh_players:
                cnt, info = refresh_players()
                ps = get_players(force_refresh=False) if get_players else []
        PLAYERS_READY = bool(ps and len(ps) >= 50)
    except Exception as e:
        if DEBUG: _log("[players] ensure failed:", repr(e))
        PLAYERS_READY = False
    return ps or []

def _team_brand_tuple(team_id: str) -> Tuple[Tuple[str,str,str], Optional[Any]]:
    try:
        colors, logo_path, _, _ = get_team_brand(team_id) if get_team_brand else (("#007ACC","#005C99","#007ACC"), None, [], False)
        logo_img = None
        if logo_path and os.path.exists(logo_path):
            from PIL import Image
            logo_img = Image.open(logo_path).convert("RGBA")
        return colors, logo_img
    except Exception as e:
        if DEBUG: _log("[tg] team_brand err", team_id, repr(e))
        return (("#007ACC","#005C99","#007ACC"), None)

def _ensure_headshot_image(p: Dict[str,Any]):
    from PIL import Image
    try:
        hs = ensure_headshot_png(p) if ensure_headshot_png else None
        if hs is None: return None
        if isinstance(hs, bytes): return Image.open(io.BytesIO(hs)).convert("RGBA")
        if isinstance(hs, str):   return Image.open(hs).convert("RGBA")
        return hs.convert("RGBA")
    except Exception as e:
        if DEBUG: _log("[tg] headshot ensure err", p.get("personId"), repr(e))
        return None

# ------------ Контекст и промпты ------------
CTX_DIR = "/tmp"

def _save_json(path: str, obj: Any):
    try:
        with open(path,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False)
    except Exception as e:
        if DEBUG: _log("[ctx] save error", path, e)

def _load_json(path: str) -> Optional[Any]:
    try:
        if os.path.exists(path):
            with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        if DEBUG: _log("[ctx] load error", path, e)
    return None

def _ask_name_ctx_path(chat_id: int, msg_id: int) -> str:
    return os.path.join(CTX_DIR, f"askname_{chat_id}_{msg_id}.json")

def _color_ctx_path(chat_id: int, msg_id: int) -> str:
    return os.path.join(CTX_DIR, f"askcolor_{chat_id}_{msg_id}.json")

def _bold_html(s: str) -> str:
    return f"<b>{html.escape(s)}</b>"

def _ask_russian_name(chat_id: int, reply_to_msg_id: Optional[int], p: Dict[str,Any], stats: List[Tuple[str,str]], template: str, extra: Dict[str,Any]=None):
    pid = str(p.get("personId") or "")
    name_en = display_name_for(p) if display_name_for else (p.get("displayName") or "")
    name_html = _bold_html(name_en)

    markup = {"force_reply": True, "input_field_placeholder": "Введите русское имя"}
    resp = _tg_send_message(
        chat_id,
        f"Как подписать игрока {name_html} на плашке?\nОтветьте на это сообщение русским именем.",
        reply_to=reply_to_msg_id,
        parse_mode="HTML",
        reply_markup=markup
    )
    msg_id = None
    try:
        if resp.get("ok"): msg_id = resp["result"]["message_id"]
    except Exception:
        pass
    if msg_id:
        ctx = {"template": template, "stats": stats, "pid": pid, "teamId": str(p.get("teamId") or "0")}
        if extra: ctx.update(extra)
        _save_json(_ask_name_ctx_path(chat_id, msg_id), ctx)

def _ask_color_choice(chat_id: int, reply_to: Optional[int], ctx: Dict[str,Any], team_ids: List[str]):
    # inline-кнопки: на каждую команду две кнопки
    rows = []
    for idx, tid in enumerate(team_ids):
        rows.append([
            {"text": f"Цвет команды {idx+1}: авто", "callback_data": f"color:auto:{tid}"},
            {"text": f"Цвет команды {idx+1}: свой HEX", "callback_data": f"color:ask:{tid}"},
        ])
    _tg_send_message(
        chat_id,
        "Выберите цвет плашки:",
        reply_to=reply_to,
        reply_markup={"inline_keyboard": rows}
    )
    # запомним общий контекст «последнего рендера» на всякий
    _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx)

# ------------ Маршруты ------------
def _check_secret(request: Request) -> Optional[PlainTextResponse]:
    secret = (request.query_params.get("secret") or "").strip()
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return PlainTextResponse("bad secret", status_code=401)
    return None

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /help — это сообщение\n"
    "• /find <имя/фамилия> — найти игрока\n"
    "• /card <имя> | <метрики через запятую>\n"
    "• /cards <имя> | <метрики> | <текст справа>\n"
    "• /bad <имя> | <метрики>\n"
    "• /card2 <имя1> | <метрики1> | <имя2> | <метрики2>\n"
)

@app.get("/api/telegram")
async def telegram_get(request: Request):
    bad = _check_secret(request)
    if bad: return bad
    action = (request.query_params.get("action") or "").strip()

    if action == "diag":
        return JSONResponse({
            "ok": True,
            "py": ".".join(map(str, __import__("sys").version_info[:3])),
            "platform": __import__("platform").system().lower(),
            "has_bot_token": bool(BOT_TOKEN),
            "modules": {
                "data": "ok" if _data_err is None else "error",
                "graphics": "ok" if _graphics_err is None else "error",
                "team_brand": "ok" if _brand_err is None else "error",
            },
            "errors": {"data": _data_err, "graphics": _graphics_err, "team_brand": _brand_err},
            "api_origin": API_ORIGIN or None,
        })

    if action == "refresh":
        try:
            cnt, src = refresh_players()
            try:
                players_now = get_players(False)
                cnt_now = len(players_now) if isinstance(players_now, list) else int(cnt)
            except Exception:
                cnt_now = int(cnt)
            return JSONResponse({"ok": True, "refreshed": True, "players_indexed": int(cnt_now),
                                 "source": (src if isinstance(src,str) else str(src)),
                                 "source_url": ("norm" if "norm" in str(src) else ("pt" if "pt" in str(src) else "none"))})
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)

    if action == "test_find":
        q = (request.query_params.get("q") or "").strip()
        ready = bool(ensure_players_loaded(False))
        hits = search_players_loose(q) if search_players_loose else []
        return JSONResponse({"ok": True, "q": q, "players_ready": ready, "hits": hits})

    if action == "ping":
        return JSONResponse({"ok": True})

    return PlainTextResponse("OK")

@app.post("/api/telegram")
async def webhook_query(request: Request):
    bad = _check_secret(request)
    if bad: return bad

    try:
        raw = (await request.body()).decode("utf-8","ignore")
        update = json.loads(raw)
        if DEBUG: _log("[tg] update", raw)
    except Exception as e:
        if DEBUG: _log("[tg] json error", repr(e))
        return PlainTextResponse("OK")

    ensure_players_loaded(False)

    # --- callback_query (inline кнопки цвета) ---
    if update.get("callback_query"):
        cb = update["callback_query"]
        cb_id = cb.get("id")
        from_user = (cb.get("from") or {})
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = cb.get("data") or ""
        _tg_answer_cbq(cb_id, "")
        if data.startswith("color:"):
            try:
                _, kind, team_id = data.split(":", 2)
            except ValueError:
                return PlainTextResponse("OK")

            # восстановим последний контекст чата
            ctx = _load_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json")) or {}
            template = ctx.get("template") or "single"

            if kind == "auto":
                if set_team_primary_color: set_team_primary_color(str(team_id), "AUTO")
                _tg_send_message(chat_id, f"Цвет команды сохранён: авто")
                # рендер после авто
                return _render_from_ctx(chat_id, ctx)

            if kind == "ask":
                # спросим HEX
                markup = {"force_reply": True, "input_field_placeholder": "#RRGGBB"}
                resp = _tg_send_message(chat_id, f"Укажи HEX для команды (пример: <b>#1D428A</b>)", parse_mode="HTML", reply_markup=markup)
                mid = None
                if resp.get("ok"):
                    mid = resp["result"]["message_id"]
                if mid:
                    cctx = {"teamId": str(team_id), "ctx": ctx}
                    _save_json(_color_ctx_path(chat_id, mid), cctx)
                return PlainTextResponse("OK")

        return PlainTextResponse("OK")

    # --- обычные сообщения ---
    msg = update.get("message") or update.get("edited_message")
    if not msg: return PlainTextResponse("OK")
    chat = msg.get("chat") or {}; chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    # A) Ответ на ForceReply «русское имя»
    rpl = msg.get("reply_to_message")
    if rpl and text:
        # 1) Проверка: это ответ на запрос HEX?
        color_pth = _color_ctx_path(chat_id, rpl.get("message_id"))
        if os.path.exists(color_pth):
            cctx = _load_json(color_pth) or {}
            team_id = str((cctx.get("teamId") or "0"))
            hexv = text.strip().upper()
            if hexv == "АВТО" or hexv == "AUTO":
                if set_team_primary_color: set_team_primary_color(team_id, "AUTO")
                _tg_send_message(chat_id, "Цвет команды: авто")
            else:
                if not re.fullmatch(r"#?[0-9A-F]{6}", hexv):
                    _tg_send_message(chat_id, "Некорректный HEX. Пример: #1D428A")
                    return PlainTextResponse("OK")
                if not hexv.startswith("#"): hexv = "#" + hexv
                if set_team_primary_color: set_team_primary_color(team_id, hexv)
                _tg_send_message(chat_id, f"Цвет команды сохранён: {hexv}")

            # дорендер по контексту
            ctx = cctx.get("ctx") or {}
            return _render_from_ctx(chat_id, ctx)

        # 2) Ответ на запрос русского имени
        ask_pth = _ask_name_ctx_path(chat_id, rpl.get("message_id"))
        if os.path.exists(ask_pth) and overrides_save_name_ru:
            ctx = _load_json(ask_pth) or {}
            pid = str(ctx.get("pid") or "")
            try:
                ok = overrides_save_name_ru(pid, text.strip())
                if not ok:
                    _tg_send_message(chat_id, "Не удалось сохранить имя (override=false)")
                    return PlainTextResponse("OK")
            except Exception as e:
                _tg_send_message(chat_id, f"Не удалось сохранить имя: {repr(e)}")
                return PlainTextResponse("OK")

            _tg_send_message(chat_id, f"Сохранил имя для {pid}: {text.strip()}")

            # предложим выбор цвета (single/special: одна команда; card2: две)
            templ = ctx.get("template") or "single"
            if templ == "card2":
                team_ids = [str(ctx.get("team1Id") or "0"), str(ctx.get("team2Id") or "0")]
            else:
                team_ids = [str(ctx.get("teamId") or "0")]
            _ask_color_choice(chat_id, msg.get("message_id"), ctx, team_ids)
            return PlainTextResponse("OK")

    # Команды
    if text.startswith("/start"):
        _tg_send_message(chat_id, "Я здесь. Готов работать 💼")
        return PlainTextResponse("OK")

    if text.startswith("/help"):
        _tg_send_message(chat_id, HELP_TEXT)
        return PlainTextResponse("OK")

    if text.startswith("/find"):
        q = text[len("/find"):].strip()
        hits = search_players_loose(q) if search_players_loose else []
        if not hits:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷")
            return PlainTextResponse("OK")
        lines = [f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})" for h in hits[:5]]
        _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK")

    # /bad
    if text.startswith("/bad"):
        try:
            args = text[len("/bad"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _tg_send_message(chat_id, "Формат: /bad <имя> | <метрики>")
                return PlainTextResponse("OK")
            name_q, stats_raw = parts[0], parts[1]
            stats = parse_stats_list(stats_raw)
            hits = search_players_loose(name_q) if search_players_loose else []
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")
            ru_name = (overrides_get_name_ru(pid) if overrides_get_name_ru else None)
            if not ru_name:
                _ask_russian_name(chat_id, msg.get("message_id"), p, stats, template="bad")
                return PlainTextResponse("OK")
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            head_img = _ensure_headshot_image(p)
            if not head_img:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")
            png = render_card_bad(ru_name, head_img, stats)
            _tg_send_png_as_document(chat_id, png, filename=f"bad_{pid}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /cards — SPECIAL
    if text.startswith("/cards"):
        try:
            args = text[len("/cards"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 3:
                _tg_send_message(chat_id, "Формат: /cards <имя> | <метрики> | <текст справа>")
                return PlainTextResponse("OK")
            name_q, stats_raw, info_text = parts[0], parts[1], parts[2]
            stats = parse_stats_list(stats_raw)
            hits = search_players_loose(name_q) if search_players_loose else []
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")
            ru_name = (overrides_get_name_ru(pid) if overrides_get_name_ru else None)
            if not ru_name:
                _ask_russian_name(chat_id, msg.get("message_id"), p, stats, template="special", extra={"info": info_text})
                return PlainTextResponse("OK")
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            team_id = str(p.get("teamId") or "0")
            colors, logo_img = _team_brand_tuple(team_id)
            head_img = _ensure_headshot_image(p)
            if not head_img:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")
            png = render_card_special(ru_name, logo_img, colors, head_img, stats, info_text)
            _tg_send_png_as_document(chat_id, png, filename=f"cards_{pid}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /card2 — DUO
    if text.startswith("/card2"):
        try:
            args = text[len("/card2"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 4:
                _tg_send_message(chat_id, "Формат: /card2 <имя1> | <метрики1> | <имя2> | <метрики2>")
                return PlainTextResponse("OK")
            name_q1, stats_raw1, name_q2, stats_raw2 = parts[0], parts[1], parts[2], parts[3]
            stats1, stats2 = parse_stats_list(stats_raw1), parse_stats_list(stats_raw2)
            hits1 = search_players_loose(name_q1) if search_players_loose else []
            hits2 = search_players_loose(name_q2) if search_players_loose else []
            if not hits1 or not hits2:
                _tg_send_message(chat_id, "Не нашёл одного из игроков.")
                return PlainTextResponse("OK")
            p1, p2 = hits1[0], hits2[0]
            pid1, pid2 = str(p1.get("personId","")), str(p2.get("personId",""))
            ru1 = overrides_get_name_ru(pid1) if overrides_get_name_ru else None
            ru2 = overrides_get_name_ru(pid2) if overrides_get_name_ru else None

            ctx = {
                "template":"card2",
                "pid1":pid1,"team1Id":str(p1.get("teamId") or "0"),"stats1":stats1,
                "pid2":pid2,"team2Id":str(p2.get("teamId") or "0"),"stats2":stats2
            }

            if not ru1:
                _ask_russian_name(chat_id, msg.get("message_id"), p1, stats1, template="card2", extra=ctx)
                return PlainTextResponse("OK")
            if not ru2:
                _ask_russian_name(chat_id, msg.get("message_id"), p2, stats2, template="card2", extra=ctx)
                return PlainTextResponse("OK")

            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            colors1, logo1 = _team_brand_tuple(ctx["team1Id"])
            colors2, logo2 = _team_brand_tuple(ctx["team2Id"])
            head1 = _ensure_headshot_image(p1)
            head2 = _ensure_headshot_image(p2)
            if not head1 or not head2:
                _tg_send_message(chat_id, "Не удалось получить фото одного из игроков.")
                return PlainTextResponse("OK")
            png = render_card2(
                ru1, logo1, colors1, head1, stats1,
                ru2, logo2, colors2, head2, stats2
            )
            _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx)
            # сразу предложим цвета для обеих команд
            _ask_color_choice(chat_id, msg.get("message_id"), ctx, [ctx["team1Id"], ctx["team2Id"]])
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{pid1}_{pid2}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /card — SINGLE
    if text.startswith("/card"):
        try:
            args = text[len("/card"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            name_q, stats_raw = parts[0], parts[1]
            stats = parse_stats_list(stats_raw)

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            hits = search_players_loose(name_q) if search_players_loose else []
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            if len(hits) > 1:
                menu = "\n".join([f"{i+1}. {h.get('displayName')} (id={h.get('personId')})" for i,h in enumerate(hits[:5])])
                _tg_send_message(chat_id, "Нашёл несколько вариантов:\n" + menu + "\nУточните запрос.")
                return PlainTextResponse("OK")

            p = hits[0]
            pid = str(p.get("personId") or "")
            ru_name = (overrides_get_name_ru(pid) if overrides_get_name_ru else None)
            ctx = {"template":"single", "pid":pid, "teamId":str(p.get("teamId") or "0"), "stats":stats}
            if not ru_name:
                _ask_russian_name(chat_id, msg.get("message_id"), p, stats, template="single")
                _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx)
                return PlainTextResponse("OK")

            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            colors, logo_img = _team_brand_tuple(ctx["teamId"])
            head_img = _ensure_headshot_image(p)
            if not head_img:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")
            png = render_card("single", ru_name, "", logo_img, colors, head_img, stats)
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png")
            _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx)
            _ask_color_choice(chat_id, msg.get("message_id"), ctx, [ctx["teamId"]])
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    _tg_send_message(chat_id, HELP_TEXT)
    return PlainTextResponse("OK")

# ------------ Рендер по сохранённому контексту (после выбора цвета/HEX) ------------
def _render_from_ctx(chat_id: int, ctx: Dict[str,Any]) -> PlainTextResponse:
    try:
        templ = ctx.get("template") or "single"
        if templ == "single":
            pid = str(ctx.get("pid") or "")
            ps = get_players(False) or []
            p = next((x for x in ps if str(x.get("personId")) == pid), None)
            if not p: 
                _tg_send_message(chat_id, "Не нашёл игрока для рендера. Пришлите /card ещё раз.")
                return PlainTextResponse("OK")
            ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            if not ru:
                _tg_send_message(chat_id, "Имя не сохранено — отправьте /card ещё раз.")
                return PlainTextResponse("OK")
            colors, logo_img = _team_brand_tuple(str(p.get("teamId") or "0"))
            head_img = _ensure_headshot_image(p)
            if not head_img:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")
            png = render_card("single", ru, "", logo_img, colors, head_img, ctx.get("stats") or [])
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png")
            return PlainTextResponse("OK")

        if templ == "special":
            pid = str(ctx.get("pid") or "")
            ps = get_players(False) or []
            p = next((x for x in ps if str(x.get("personId")) == pid), None)
            if not p:
                _tg_send_message(chat_id, "Не нашёл игрока для special.")
                return PlainTextResponse("OK")
            ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            if not ru:
                _tg_send_message(chat_id, "Имя не сохранено — отправьте /cards ещё раз.")
                return PlainTextResponse("OK")
            colors, logo_img = _team_brand_tuple(str(p.get("teamId") or "0"))
            head_img = _ensure_headshot_image(p)
            if not head_img:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")
            info = ctx.get("info") or ""
            png = render_card_special(ru, logo_img, colors, head_img, ctx.get("stats") or [], info)
            _tg_send_png_as_document(chat_id, png, filename=f"cards_{pid}.png")
            return PlainTextResponse("OK")

        if templ == "card2":
            pid1, pid2 = str(ctx.get("pid1") or ""), str(ctx.get("pid2") or "")
            ps = get_players(False) or []
            p1 = next((x for x in ps if str(x.get("personId")) == pid1), None)
            p2 = next((x for x in ps if str(x.get("personId")) == pid2), None)
            if not p1 or not p2:
                _tg_send_message(chat_id, "Не нашёл игроков для card2.")
                return PlainTextResponse("OK")
            ru1 = overrides_get_name_ru(pid1) if overrides_get_name_ru else None
            ru2 = overrides_get_name_ru(pid2) if overrides_get_name_ru else None
            colors1, logo1 = _team_brand_tuple(str(p1.get("teamId") or "0"))
            colors2, logo2 = _team_brand_tuple(str(p2.get("teamId") or "0"))
            head1 = _ensure_headshot_image(p1); head2 = _ensure_headshot_image(p2)
            if not head1 or not head2:
                _tg_send_message(chat_id, "Не удалось получить фото одного из игроков.")
                return PlainTextResponse("OK")
            png = render_card2(
                ru1 or (p1.get("displayName") or ""),
                logo1, colors1, head1, ctx.get("stats1") or [],
                ru2 or (p2.get("displayName") or ""),
                logo2, colors2, head2, ctx.get("stats2") or [],
            )
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{pid1}_{pid2}.png")
            return PlainTextResponse("OK")
    except Exception as e:
        _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
    return PlainTextResponse("OK")
