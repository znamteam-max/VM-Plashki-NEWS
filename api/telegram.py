# api/telegram.py
# FastAPI webhook для Telegram + вспомогательные GET-действия.
# Поддержка: /start /help /find /card "<имя>" | "<статы, через запятую>"
# Интерактив: запрос русского имени (реплаем на сообщение с [setname:<id>][ctx:<base64>])
# После ответа с именем плашка автоматически достраивается.

from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid, base64
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

# --- Логи и конфиг ---
DEBUG = os.getenv("DEBUG", "1") in ("1", "true", "yes")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ORIGIN = os.getenv("API_ORIGIN")  # опционально подсветим в /diag

CTX_DIR = "/tmp"  # контексты для дорендера после setname
def _ctx_path(chat_id: Any, pid: Any) -> str:
    return os.path.join(CTX_DIR, f"ctx_{chat_id}_{pid}.json")

def _log(*a):
    try:
        print(*a, flush=True)
    except:
        pass

# --- Безопасный импорт зависимостей ---
def _safe_import(modname: str, names: List[str]):
    try:
        m = __import__(modname, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, out, None
    except Exception as e:
        return None, [], f"{e.__class__.__name__}: {e}"

# data
_data_mod, _data_objs, _data_err = _safe_import("data", [
    "get_players", "refresh_players", "find_player_by_name",
    "display_name_for", "overrides_save_name_ru", "overrides_get_name_ru",
    "ensure_headshot_png", "ensure_team_logo_png", "search_players_loose"
])
if _data_err and DEBUG: _log("[boot] data import error:", _data_err)
(get_players, refresh_players, find_player_by_name,
 display_name_for, overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png, search_players_loose) = ([_ for _ in _data_objs] + [None]*9)[:9]

# team_brand
_brand_mod, _brand_objs, _brand_err = _safe_import("team_brand", [
    "get_team_brand", "color_name_ru", "set_team_primary_color"
])
if _brand_err and DEBUG: _log("[boot] team_brand import error:", _brand_err)
(get_team_brand, color_name_ru, set_team_primary_color) = ([_ for _ in _brand_objs] + [None]*3)[:3]

# graphics
_graphics_mod, _graphics_objs, _graphics_err = _safe_import("graphics", [
    "render_card", "render_card2", "render_card_bad",
    "render_card_special", "render_card_drN"
])
if _graphics_err and DEBUG: _log("[boot] graphics import error:", _graphics_err)
(render_card, render_card2, render_card_bad,
 render_card_special, render_card_drN) = ([_ for _ in _graphics_objs] + [None]*5)[:5]

app = FastAPI()

# --- Telegram HTTP ---
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
        return {"ok": False, "raw": raw.decode("utf-8", "ignore")}

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _http_json(_tg_url(method), payload)
    except HTTPError as e:
        if DEBUG: _log("[tg] HTTPError", method, e)
        return {"ok": False, "error": f"HTTPError {e.code}"}
    except URLError as e:
        if DEBUG: _log("[tg] URLError", method, e)
        return {"ok": False, "error": "URLError"}
    except Exception as e:
        if DEBUG: _log("[tg] send error:", repr(e))
        return {"ok": False, "error": repr(e)}

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None, parse_mode: Optional[str] = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _tg_post("sendMessage", payload)

def _tg_send_chat_action(chat_id: int, action: str = "typing"):
    return _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})

def _multipart_boundary() -> str:
    return "----WebKitFormBoundary" + uuid.uuid4().hex

def _encode_multipart(fields: Dict[str, str], files: Dict[str, Tuple[str, bytes, str]]) -> Tuple[bytes, str]:
    boundary = _multipart_boundary()
    CRLF = b"\r\n"
    lines: List[bytes] = []
    for name, value in fields.items():
        lines.append(b"--" + boundary.encode())
        header = f'Content-Disposition: form-data; name="{name}"'.encode()
        lines.append(header)
        lines.append(b"")
        lines.append(value.encode("utf-8"))
    for field_name, (filename, content, content_type) in files.items():
        lines.append(b"--" + boundary.encode())
        header = f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode()
        lines.append(header)
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(b"--" + boundary.encode() + b"--")
    body = b"\r\n".join(lines)
    ctype = f"multipart/form-data; boundary={boundary}"
    return body, ctype

def _tg_send_png_as_document(chat_id: int, png_bytes: bytes, filename: str = "card.png", caption: Optional[str] = None):
    url = _tg_url("sendDocument")
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    files = {"document": (filename, png_bytes, "image/png")}
    body, ctype = _encode_multipart(fields, files)
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    try:
        with http_urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": False, "raw": raw}
    except Exception as e:
        if DEBUG: _log("[tg] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# --- Доменные утилиты ---
PLAYERS_READY = False

def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

STAT_TOKEN_MAP = {
    "очк": "ОЧКИ",
    "передач": "ПЕРЕДАЧИ",
    "подбор": "ПОДБОРЫ",
    "блок": "БЛОКИ",
    "стил": "ПЕРЕХВАТЫ",
    "мин": "МИНУТЫ",
    "трёх": "3-ОЧКОВЫЕ",
    "трех": "3-ОЧКОВЫЕ",
    "фол": "ФОЛЫ",
    "потер": "ПОТЕРИ",
    "дабл": "ДАБЛ-ДАБЛ",
    "трипл": "ТРИПЛ-ДАБЛ",
}

def parse_stats_list(raw: str) -> List[Tuple[str, str]]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Tuple[str, str]] = []
    for p in parts:
        m = re.match(r"^\s*([0-9]+)\s*([^\d,]+)?", p, flags=re.IGNORECASE)
        if not m:
            continue
        val = m.group(1)
        lbl_raw = (m.group(2) or "").strip().lower()
        lbl = "СТАТ"
        for k, v in STAT_TOKEN_MAP.items():
            if k in lbl_raw:
                lbl = v
                break
        out.append((val, lbl))
    return out

def ensure_players_loaded(force: bool = False) -> List[Dict[str, Any]]:
    global PLAYERS_READY
    ps = []
    try:
        ps = get_players(force_refresh=bool(force)) if get_players else []
        if not ps or len(ps) < 50:
            if DEBUG: _log("[players] empty -> refresh()")
            if refresh_players:
                cnt, info = refresh_players()
                if DEBUG: _log("[players] refresh:", {"count": cnt, "src": info})
                ps = get_players(force_refresh=False) if get_players else []
        PLAYERS_READY = bool(ps and len(ps) >= 50)
        if DEBUG: _log(f"[players] ready={PLAYERS_READY} count={len(ps) if ps else 0}")
    except Exception as e:
        if DEBUG: _log("[players] ensure failed:", repr(e))
        PLAYERS_READY = False
    return ps or []

def _team_brand_tuple(team_id: str) -> Tuple[Tuple[str, str, str], Optional[Any]]:
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

def _ensure_headshot_image(p: Dict[str, Any]):
    from PIL import Image
    try:
        hs = ensure_headshot_png(p) if ensure_headshot_png else None
        if hs is None:
            return None
        if isinstance(hs, bytes):
            return Image.open(io.BytesIO(hs)).convert("RGBA")
        if isinstance(hs, str):
            return Image.open(hs).convert("RGBA")
        return hs.convert("RGBA")
    except Exception as e:
        if DEBUG: _log("[tg] headshot ensure err", p.get("personId"), repr(e))
        return None

# --- Контекст при запросе имени ---
def _encode_ctx(ctx: Dict[str, Any]) -> str:
    raw = json.dumps(ctx, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")

def _decode_ctx(s: str) -> Optional[Dict[str, Any]]:
    try:
        raw = base64.urlsafe_b64decode(s.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None

def _persist_ctx(chat_id: int, pid: str, ctx: Dict[str, Any]) -> None:
    try:
        with open(_ctx_path(chat_id, pid), "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False)
    except Exception as e:
        if DEBUG: _log("[ctx] save error", e)

def _load_ctx(chat_id: int, pid: str) -> Optional[Dict[str, Any]]:
    try:
        pth = _ctx_path(chat_id, pid)
        if os.path.exists(pth):
            with open(pth, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        if DEBUG: _log("[ctx] load error", e)
    return None

def _ask_russian_name(chat_id: int, reply_to_msg_id: Optional[int], p: Dict[str, Any], stats: List[Tuple[str,str]], template: str = "single"):
    pid = str(p.get("personId") or "")
    team_id = str(p.get("teamId") or "0")
    name_en = display_name_for(p) if display_name_for else (p.get("displayName") or "")
    ctx = {
        "pid": pid,
        "teamId": team_id,
        "stats": stats,
        "template": template,
        "ts": int(time.time()),
    }
    ctx_token = _encode_ctx(ctx)
    _persist_ctx(chat_id, pid, ctx)

    txt = (
        f"Как подписать игрока {name_en} на плашке?\n"
        f"Ответьте на это сообщение русским именем.\n"
        f"[setname:{pid}][ctx:{ctx_token}]"
    )
    _tg_send_message(chat_id, txt, reply_to=reply_to_msg_id)

# --- Секрет ---
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
    "• /find <имя/фамилия> — найти игрока (например: /find Doncic)\n"
    "• /card <имя> | <метрики через запятую>\n"
    "  пример: /card wembanyama | 10 очков, 12 передач, 8 подборов\n"
    "—\n"
    "Если попрошу русское имя — ответь на сообщение текстом с нужной записью.\n"
)

# --- GET роуты ---
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
                players_now = get_players(force_refresh=False)
                cnt_now = len(players_now) if isinstance(players_now, list) else int(cnt)
            except Exception:
                cnt_now = int(cnt)
            return JSONResponse({
                "ok": True, "refreshed": True,
                "players_indexed": int(cnt_now),
                "source": (src if isinstance(src, str) else str(src)),
                "source_url": ("norm" if "norm" in str(src) else ("pt" if "pt" in str(src) else "none"))
            })
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)

    if action == "test_find":
        q = (request.query_params.get("q") or "").strip()
        ready = bool(ensure_players_loaded(False))
        hits = search_players_loose(q) if search_players_loose else []
        return JSONResponse({
            "ok": True, "q": q, "players_ready": ready,
            "hits": hits
        })

    if action == "ping":
        return JSONResponse({"ok": True})

    return PlainTextResponse("OK")

# --- POST: Telegram webhook ---
@app.post("/api/telegram")
async def webhook_query(request: Request):
    bad = _check_secret(request)
    if bad: return bad

    rid = f"[RID={int(time.time()*1000)}-{uuid.uuid4().hex[:6]}]"
    try:
        body = await request.body()
        raw = body.decode("utf-8", "ignore")
        if DEBUG: _log("[tg]", rid, "POST", request.url, "\nbody:", raw)
        update = json.loads(raw)
    except Exception as e:
        if DEBUG: _log("[tg]", rid, "json error:", repr(e))
        return PlainTextResponse("OK")

    ensure_players_loaded(False)

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return PlainTextResponse("OK")

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    # 1) Реплай на запрос русского имени
    rpl = msg.get("reply_to_message")
    if rpl and text:
        rtxt = (rpl.get("text") or "") + " " + (rpl.get("caption") or "")
        m = re.search(r"\[setname:(\d+)\]", rtxt)
        if m and overrides_save_name_ru:
            pid = m.group(1)
            # восстанавливаем контекст
            ctx_token = None
            m2 = re.search(r"\[ctx:([A-Za-z0-9_\-+=/]+)\]", rtxt)
            if m2:
                ctx_token = m2.group(1)

            # 1) Сохраняем имя RU
            err = None
            try:
                ok = overrides_save_name_ru(pid, text.strip())
                if not ok:
                    err = "не удалось сохранить имя (override=false)"
            except Exception as e:
                err = repr(e)

            if err:
                _tg_send_message(chat_id, f"Не удалось сохранить имя: {err}")
                return PlainTextResponse("OK")
            else:
                _tg_send_message(chat_id, f"Сохранил имя для {pid}: {text.strip()}")

            # 2) Достаём контекст
            ctx = None
            if ctx_token:
                ctx = _decode_ctx(ctx_token)
            if not ctx:
                ctx = _load_ctx(chat_id, pid)

            if not ctx:
                # контекста нет — просим прислать /card снова
                _tg_send_message(chat_id, "Теперь отправьте свою команду /card ещё раз — я подготовлю плашку.")
                return PlainTextResponse("OK")

            # 3) Готовим плашку сразу
            try:
                _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
                _tg_send_chat_action(chat_id, "upload_document")

                # По pid найдём игрока
                pid_s = str(ctx.get("pid") or pid)
                players = get_players(False) or []
                target = None
                for p in players:
                    if str(p.get("personId")) == pid_s:
                        target = p; break
                if not target:
                    # на крайний случай — мягкий поиск по имени в кеше
                    cand = search_players_loose(text) if search_players_loose else []
                    if cand:
                        target = cand[0]
                if not target:
                    _tg_send_message(chat_id, "Не нашёл игрока после сохранения имени. Отправьте /card ещё раз.")
                    return PlainTextResponse("OK")

                ru_name = text.strip()
                team_id = str(target.get("teamId") or "0")
                stats = ctx.get("stats") or []
                template = ctx.get("template") or "single"

                colors, logo_img = _team_brand_tuple(team_id)
                head_img = _ensure_headshot_image(target)
                if head_img is None:
                    _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                    return PlainTextResponse("OK")

                png = render_card(template, ru_name, "", logo_img, colors, head_img, stats)
                fname = f"card_{pid_s}.png"
                rsp = _tg_send_png_as_document(chat_id, png, filename=fname)
                if not rsp.get("ok"):
                    _tg_send_message(chat_id, f"Ошибка отправки PNG: {rsp}")
            except Exception as e:
                _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
            return PlainTextResponse("OK")

    # 2) Команды
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
        lines = []
        for h in hits[:5]:
            lines.append(f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})")
        _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK")

    if text.startswith("/card"):
        try:
            args = text[len("/card"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            name_q = parts[0]
            stats_raw = parts[1]
            stats = parse_stats_list(stats_raw)

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            _tg_send_chat_action(chat_id, "typing")

            hits = search_players_loose(name_q) if search_players_loose else []
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            if len(hits) > 1:
                menu = "\n".join([f"{i+1}. {h.get('displayName')} (id={h.get('personId')})" for i, h in enumerate(hits[:5])])
                _tg_send_message(chat_id, "Нашёл несколько вариантов:\n" + menu + "\nУточните запрос.")
                return PlainTextResponse("OK")

            p = hits[0]
            pid = str(p.get("personId") or "")
            ru_name = None
            try:
                if overrides_get_name_ru:
                    ru_name = overrides_get_name_ru(pid)
            except Exception:
                ru_name = None

            if not ru_name:
                # спросим имя и сохраним контекст
                _ask_russian_name(chat_id, msg.get("message_id"), p, stats, template="single")
                return PlainTextResponse("OK")

            # рендер
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            _tg_send_chat_action(chat_id, "upload_document")

            team_id = str(p.get("teamId") or "0")
            colors, logo_img = _team_brand_tuple(team_id)
            head_img = _ensure_headshot_image(p)
            if head_img is None:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")

            png = render_card("single", ru_name, "", logo_img, colors, head_img, stats)
            fname = f"card_{pid}.png"
            rsp = _tg_send_png_as_document(chat_id, png, filename=fname)
            if not rsp.get("ok"):
                _tg_send_message(chat_id, f"Ошибка отправки PNG: {rsp}")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # если до сюда дошли — покажем хелп
    _tg_send_message(chat_id, HELP_TEXT)
    return PlainTextResponse("OK")
