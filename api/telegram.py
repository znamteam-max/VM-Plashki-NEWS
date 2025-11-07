# api/telegram.py
# FastAPI webhook для Telegram + вспомогательные GET-действия.
# Поддержка: /start /help /find /card "<имя>" | "<статы, через запятую>"
# Интерактив: запрос русского имени (реплаем на сообщение с [setname:<id>])
# PNG отсылается как документ (прозрачность сохраняется).

from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid, mimetypes
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

def _log(*a):
    try:
        print(*a, flush=True)
    except:
        pass

# --- Безопасный импорт зависимостей (диагностика не падала на бою) ---
def _safe_import(modname: str, names: List[str]) -> Tuple[Optional[Any], List[Any], Optional[str]]:
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
    "ensure_headshot_png", "ensure_team_logo_png"
])
if _data_err and DEBUG: _log("[boot] data import error:", _data_err)
(get_players, refresh_players, find_player_by_name,
 display_name_for, overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png) = ([_ for _ in _data_objs] + [None]*8)[:8]

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

# --- Утилиты Telegram API ---
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

# --- Вспомогательные утилиты домена ---
PLAYERS_READY = False

def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

def ensure_players_loaded(force: bool = False) -> List[Dict[str, Any]]:
    """
    Гарантированная подгрузка игроков в память (через data.get_players / refresh_players).
    """
    global PLAYERS_READY
    ps = []
    try:
        ps = get_players(force_refresh=bool(force)) if get_players else []
        if not ps or len(ps) < 100:
            _log("[players] empty -> refresh()")
            if refresh_players:
                cnt, info = refresh_players()
                _log("[players] refresh:", {"count": cnt, **(info or {})})
                ps = get_players(force_refresh=False) if get_players else []
        PLAYERS_READY = bool(ps and len(ps) >= 100)
        _log(f"[players] ready={PLAYERS_READY} count={len(ps) if ps else 0}")
    except Exception as e:
        _log("[players] ensure failed:", repr(e))
        PLAYERS_READY = False
    return ps or []

def search_players_loose(q: str) -> List[Dict[str, Any]]:
    """
    Мягкий поиск: сперва data.find_player_by_name (если есть),
    затем ручной подстрочный поиск по displayName/first+last (без диакритики).
    """
    qn = _normalize(q)
    ps = ensure_players_loaded(False)
    if not ps:
        return []
    try:
        if find_player_by_name:
            hits = find_player_by_name(q) or []
            if hits:
                return hits
    except Exception:
        pass
    out = []
    for p in ps:
        dn = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if dn and qn in _normalize(dn):
            out.append(p)
            if len(out) >= 10:
                break
    return out

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
    """
    "10 очков, 12 передач, 8 подборов" -> [("10","ОЧКИ"),("12","ПЕРЕДАЧИ"),("8","ПОДБОРЫ")]
    """
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

def _display_name_for_player(p: Dict[str, Any]) -> str:
    pid = str(p.get("personId") or p.get("id") or "")
    try:
        if overrides_get_name_ru:
            ru = overrides_get_name_ru(pid)
            if ru:
                return ru
    except Exception:
        pass
    if display_name_for:
        try:
            return display_name_for(p)
        except Exception:
            pass
    # fallback
    return p.get("displayName") or f"{p.get('firstName','').strip()} {p.get('lastName','').strip()}".strip()

def _ensure_headshot_image(p: Dict[str, Any]):
    """
    ensure_headshot_png(p) может вернуть bytes или PIL.Image.Image или путь.
    Вернём PIL.Image.Image.
    """
    from PIL import Image
    try:
        hs = ensure_headshot_png(p) if ensure_headshot_png else None
        if hs is None:
            return None
        if isinstance(hs, bytes):
            return Image.open(io.BytesIO(hs)).convert("RGBA")
        if isinstance(hs, str):
            return Image.open(hs).convert("RGBA")
        # возможно уже Image
        return hs.convert("RGBA")
    except Exception as e:
        _log("[tg] headshot ensure err", p.get("personId"), repr(e))
        return None

def _ensure_team_logo_image(team_id: str):
    from PIL import Image
    try:
        path = ensure_team_logo_png(team_id) if ensure_team_logo_png else None
        if path and os.path.exists(path):
            return Image.open(path).convert("RGBA")
        # team_brand.get_team_brand тоже вернёт путь
        brand = get_team_brand(team_id) if get_team_brand else None
        if brand:
            _, logo_path, _, _ = brand
            if logo_path and os.path.exists(logo_path):
                return Image.open(logo_path).convert("RGBA")
        return None
    except Exception as e:
        _log("[tg] team logo ensure err", team_id, repr(e))
        return None

def _team_brand_tuple(team_id: str) -> Tuple[Tuple[str, str, str], Optional[Any], List[str], bool]:
    """
    Обёртка вокруг team_brand.get_team_brand: вернёт цвета, PIL-логотип, список предложенных цветов, признак сохранённого цвета.
    """
    try:
        colors, logo_path, palette, saved = get_team_brand(team_id) if get_team_brand else (("#007ACC", "#005C99", "#007ACC"), None, [], False)
        logo_img = None
        if logo_path and os.path.exists(logo_path):
            from PIL import Image
            logo_img = Image.open(logo_path).convert("RGBA")
        return colors, logo_img, palette, saved
    except Exception as e:
        _log("[tg] team_brand err", team_id, repr(e))
        return (("#007ACC", "#005C99", "#007ACC"), None, [], False)

# --- Секрет/защита ---
def _check_secret(request: Request) -> Optional[PlainTextResponse]:
    secret = (request.query_params.get("secret") or "").strip()
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return PlainTextResponse("bad secret", status_code=401)
    return None

# --- РОУТЫ GET ---
@app.get("/api/telegram")
async def telegram_get(request: Request):
    bad = _check_secret(request)
    if bad:
        return bad
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
            "errors": {
                "data": _data_err,
                "graphics": _graphics_err,
                "team_brand": _brand_err,
            },
            "api_origin": API_ORIGIN or None,
        })
    elif action == "refresh":
        try:
            cnt, info = refresh_players() if refresh_players else (0, {"error": "no refresh_players"})
            return JSONResponse({"ok": True if cnt else False, "refreshed": True, "players_indexed": cnt, "source": (info or {}).get("source"), "source_url": (info or {}).get("url")})
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)})
    elif action == "players_count":
        ps = ensure_players_loaded(False)
        return JSONResponse({"ok": True, "count": len(ps)})
    elif action == "test_find":
        q = (request.query_params.get("q") or "").strip()
        ps = ensure_players_loaded(False)
        hits = search_players_loose(q) if q else []
        return JSONResponse({
            "ok": True,
            "q": q,
            "players_ready": bool(ps),
            "hits": [{"id": h.get("personId"), "name": h.get("displayName"), "teamId": h.get("teamId")} for h in hits[:5]]
        })
    else:
        return JSONResponse({"ok": True, "route": "telegram-get", "boot_error": None if not any([_data_err, _brand_err, _graphics_err]) else {"data": _data_err, "brand": _brand_err, "graphics": _graphics_err}})

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

# --- РОУТ POST: Telegram webhook ---
@app.post("/api/telegram")
async def webhook_query(request: Request):
    bad = _check_secret(request)
    if bad:
        return bad

    rid = f"[RID={int(time.time()*1000)}-{uuid.uuid4().hex[:6]}]"
    try:
        body = await request.body()
        raw = body.decode("utf-8", "ignore")
        if DEBUG: _log("[tg]", rid, "POST", request.url, "\nbody:", raw)
        update = json.loads(raw)
    except Exception as e:
        if DEBUG: _log("[tg]", rid, "json error:", repr(e))
        return PlainTextResponse("OK")

    # прогреем игроков
    ensure_players_loaded(False)

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return PlainTextResponse("OK")

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    # 1) Обработка реплая на запрос русского имени
    rpl = msg.get("reply_to_message")
    if rpl and text:
        rtxt = (rpl.get("text") or "") + " " + (rpl.get("caption") or "")
        m = re.search(r"\[setname:(\d+)\]", rtxt)
        if m and overrides_save_name_ru:
            pid = m.group(1)
            try:
                overrides_save_name_ru(pid, text.strip())
                _tg_send_message(chat_id, f"Сохранил имя для {pid}: {text.strip()}")
            except Exception as e:
                _tg_send_message(chat_id, f"Не удалось сохранить имя: {repr(e)}")
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
        hits = search_players_loose(q)
        if not hits:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷")
            return PlainTextResponse("OK")
        lines = []
        for h in hits[:5]:
            lines.append(f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})")
        _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK")

    if text.startswith("/card"):
        # формат: /card <имя> | <статы>
        try:
            args = text[len("/card"):].strip()
            # сплит по | : имя | статы
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            name_q = parts[0]
            stats_raw = parts[1]
            stats = parse_stats_list(stats_raw)

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            _tg_send_chat_action(chat_id, "typing")

            hits = search_players_loose(name_q)
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            if len(hits) > 1:
                # предложим топ-4
                menu = "\n".join([f"{i+1}. {h.get('displayName')} (id={h.get('personId')})" for i, h in enumerate(hits[:4])])
                _tg_send_message(chat_id, "Нашёл несколько вариантов:\n" + menu + "\nУточните запрос.")
                return PlainTextResponse("OK")

            p = hits[0]
            pid = str(p.get("personId") or "")
            # имя на русском?
            ru_name = None
            try:
                if overrides_get_name_ru:
                    ru_name = overrides_get_name_ru(pid)
            except Exception:
                pass

            if not ru_name:
                # спрашиваем имя
                sent = _tg_send_message(
                    chat_id,
                    f"Как подписать игрока {p.get('displayName')} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]"
                )
                return PlainTextResponse("OK")

            # готовим плашку
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            _tg_send_chat_action(chat_id, "upload_document")

            # бренд (цвета + логотип)
            team_id = str(p.get("teamId") or "0")
            colors, logo_img, _, _ = _team_brand_tuple(team_id)

            # headshot
            head_img = _ensure_headshot_image(p)
            if head_img is None:
                _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                return PlainTextResponse("OK")

            # рендер PNG
            png = render_card("single", ru_name, "", logo_img, colors, head_img, stats)
            # отправка как документ (с прозрачностью)
            fname = f"card_{pid}.png"
            _tg_send_png_as_document(chat_id, png, filename=fname)

        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # если до сюда дошли — покажем хелп
    _tg_send_message(chat_id, HELP_TEXT)
    return PlainTextResponse("OK")
