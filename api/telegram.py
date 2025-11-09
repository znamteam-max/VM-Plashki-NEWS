# api/telegram.py
# Webhook FastAPI для Телеграма. Поддержка: /start /help /stop /find /card /cards /card2 /cardbad(/bad)
# Поток:
# 1) /card* -> поиск -> если нет RU-имени, спрашиваем (reply) -> рендер -> PNG как документ.
# 2) Любая ошибка рендера — отправляем ❌ и текст ошибки.
# 3) Если имя(имена) добавляются в ответ на вопрос — автоматически дорисовываем то, что «ждёт» (PENDING).

from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

DEBUG = os.getenv("DEBUG", "1") in ("1", "true", "yes")
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
API_ORIGIN = os.getenv("API_ORIGIN") or None

# -------------------- logging --------------------
def _log(*a: Any) -> None:
    try:
        print(*a, flush=True)
    except Exception:
        pass

def _err(*a: Any) -> None:
    try:
        print(*a, flush=True)
    except Exception:
        pass

# -------------------- safe imports --------------------
def _safe_import(modname: str, names: List[str]) -> Tuple[Optional[Any], List[Any], Optional[str]]:
    try:
        m = __import__(modname, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, out, None
    except Exception as e:
        return None, [], f"{e.__class__.__name__}: {e}"

# data
_data_mod, _data_objs, _data_err = _safe_import(
    "data",
    [
        "get_players",
        "refresh_players",
        "find_player_by_name",
        "display_name_for",
        "overrides_save_name_ru",
        "overrides_get_name_ru",
        "ensure_headshot_png",
        "ensure_team_logo_png",
    ],
)
if _data_err and DEBUG:
    _log("[boot] data import error:", _data_err)

(
    get_players,
    refresh_players,
    find_player_by_name,
    display_name_for,
    overrides_save_name_ru,
    overrides_get_name_ru,
    ensure_headshot_png,
    ensure_team_logo_png,
) = ([_ for _ in _data_objs] + [None] * 8)[:8]

# team_brand
_brand_mod, _brand_objs, _brand_err = _safe_import(
    "team_brand",
    [
        "get_team_brand",
        "color_name_ru",
        "set_team_primary_color",
    ],
)
if _brand_err and DEBUG:
    _log("[boot] team_brand import error:", _brand_err)

(get_team_brand, color_name_ru, set_team_primary_color) = ([_ for _ in _brand_objs] + [None] * 3)[:3]

# graphics
_graphics_mod, _graphics_objs, _graphics_err = _safe_import(
    "graphics",
    [
        "render_card",
        "render_card2",
        "render_card_bad",
        "render_card_special",
    ],
)
if _graphics_err and DEBUG:
    _log("[boot] graphics import error:", _graphics_err)

(render_card, render_card2, render_card_bad, render_card_special) = ([_ for _ in _graphics_objs] + [None] * 4)[:4]

# Подстрахуемся: если графика не загрузилась/не callable — создаём явные заглушки с внятной ошибкой.
def _graphics_guard_factory(err_msg: str):
    def _nope(*a: Any, **kw: Any):
        raise RuntimeError(f"graphics not loaded: {err_msg}")
    return _nope

if (render_card is None) or (not callable(render_card)):
    render_card = _graphics_guard_factory(_graphics_err or "render_card not callable")
if (render_card2 is None) or (not callable(render_card2)):
    render_card2 = _graphics_guard_factory(_graphics_err or "render_card2 not callable")
if (render_card_bad is None) or (not callable(render_card_bad)):
    render_card_bad = _graphics_guard_factory(_graphics_err or "render_card_bad not callable")
if (render_card_special is None) or (not callable(render_card_special)):
    render_card_special = _graphics_guard_factory(_graphics_err or "render_card_special not callable")

app = FastAPI()

# -------------------- Telegram HTTP --------------------
def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_json(url: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
        if DEBUG:
            _log("[tg] HTTPError", method, e)
        return {"ok": False, "error": f"HTTPError {e.code}"}
    except URLError as e:
        if DEBUG:
            _log("[tg] URLError", method, e)
        return {"ok": False, "error": "URLError"}
    except Exception as e:
        if DEBUG:
            _log("[tg] send error:", repr(e))
        return {"ok": False, "error": repr(e)}

def _tg_send_message(
    chat_id: int,
    text: str,
    reply_to: Optional[int] = None,
    parse_mode: Optional[str] = None,
):
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _tg_post("sendMessage", payload)

def _tg_edit_message(chat_id: int, message_id: int, text: str, parse_mode: Optional[str] = None):
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _tg_post("editMessageText", payload)

def _multipart_boundary() -> str:
    return "----WebKitFormBoundary" + uuid.uuid4().hex

def _encode_multipart(fields: Dict[str, str], files: Dict[str, Tuple[str, bytes, str]]) -> Tuple[bytes, str]:
    boundary = _multipart_boundary()
    lines: List[bytes] = []
    for k, v in fields.items():
        lines.append(b"--" + boundary.encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b"")
        lines.append(v.encode("utf-8"))
    for field, (filename, content, ctype) in files.items():
        lines.append(b"--" + boundary.encode())
        lines.append(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {ctype}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(b"--" + boundary.encode() + b"--")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"

def _tg_send_png_as_document(
    chat_id: int,
    png_bytes: bytes,
    filename: str = "card.png",
    caption: Optional[str] = None,
):
    url = _tg_url("sendDocument")
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    body, ctype = _encode_multipart(fields, {"document": (filename, png_bytes, "image/png")})
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    try:
        with http_urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": False, "raw": raw}
    except Exception as e:
        if DEBUG:
            _log("[tg] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# -------------------- helpers: players / normalize --------------------
PLAYERS_READY = False

def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ё", "е")
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'/"  # допустимые
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

def _img_to_png_bytes(obj: Any) -> bytes:
    from PIL import Image
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if isinstance(obj, Image.Image):
        bio = io.BytesIO()
        obj.save(bio, "PNG")
        return bio.getvalue()
    # вдруг уже bytes-подобное
    try:
        bio = io.BytesIO()
        obj.save(bio, "PNG")
        return bio.getvalue()
    except Exception:
        return bytes(obj)

def ensure_players_loaded(force: bool = False) -> List[Dict[str, Any]]:
    global PLAYERS_READY
    try:
        ps = get_players(force_refresh=bool(force)) if get_players else []
        if not ps or len(ps) < 100:
            if refresh_players:
                cnt, _src = refresh_players()
                _log("[players] refresh:", cnt, _src)
                ps = get_players(force_refresh=False) if get_players else []
        PLAYERS_READY = bool(ps and len(ps) >= 100)
        _log(f"[players] ready={PLAYERS_READY} count={len(ps) if ps else 0}")
        return ps or []
    except Exception as e:
        _log("[players] ensure failed:", repr(e))
        PLAYERS_READY = False
        return []

def _players_by_id_map() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for p in ensure_players_loaded(False):
        pid = str(p.get("personId") or p.get("id") or "")
        if pid:
            out[pid] = p
    return out

def _player_by_id(pid: str) -> Optional[Dict[str, Any]]:
    if not pid:
        return None
    for p in ensure_players_loaded(False):
        if str(p.get("personId") or p.get("id") or "") == str(pid):
            return p
    return None

def search_players_loose(q: str) -> List[Dict[str, Any]]:
    qn = _normalize(q)
    ps = ensure_players_loaded(False)
    if find_player_by_name:
        try:
            hits = find_player_by_name(q)
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

# -------------------- parsing stats --------------------
STAT_TOKEN_MAP = {
    "очк": "ОЧКИ",
    "подбор": "ПОДБОРЫ",
    "передач": "ПЕРЕДАЧИ",
    "перехват": "ПЕРЕХВАТЫ",
    "блок": "БЛОКИ",
    "стило": "СТИЛОБЛОКИ",
    "трёш": "3-ОЧКОВЫЕ",
    "трех": "3-ОЧКОВЫЕ",
    "трёх": "3-ОЧКОВЫЕ",
    "броск": "БРОСКИ С ИГРЫ",
    "%": "%",  # % трёшек / % бросков
    "мин": "МИНУТЫ",
    "плюс": "ПЛЮС/МИНУС",
    "фол": "ФОЛЫ",
    "потер": "ПОТЕРИ",
}

STAT_VALUE_RE = re.compile(
    r"^\s*([0-9]+(?:\s*[-/]\s*[0-9]+)?(?:\s*из\s*[0-9]+)?)\s*([^\d,]*)$",
    flags=re.IGNORECASE,
)

def parse_stats_list(raw: str) -> List[Tuple[str, str]]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Tuple[str, str]] = []
    for p in parts:
        m = STAT_VALUE_RE.match(p)
        if not m:
            continue
        val = re.sub(r"\s{2,}", " ", m.group(1))
        tail = (m.group(2) or "").strip().lower()
        label = "СТАТ"
        for k, v in STAT_TOKEN_MAP.items():
            if k in tail:
                label = v
                break
        out.append((val, label))
    return out

# -------------------- display name / images --------------------
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
    return p.get("displayName") or f"{p.get('firstName','').strip()} {p.get('lastName','').strip()}".strip()

def _ensure_headshot_image(p: Dict[str, Any]):
    from PIL import Image
    try:
        hs = ensure_headshot_png(p) if ensure_headshot_png else None
        if hs is None:
            return None
        if isinstance(hs, (bytes, bytearray)):
            return Image.open(io.BytesIO(hs)).convert("RGBA")
        if isinstance(hs, str):
            return Image.open(hs).convert("RGBA")
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
        brand = get_team_brand(team_id) if get_team_brand else None
        if brand:
            _, logo_path, _, _ = brand
            if logo_path and os.path.exists(logo_path):
                return Image.open(logo_path).convert("RGBA")
        return None
    except Exception as e:
        _log("[tg] team logo ensure err", team_id, repr(e))
        return None

def _team_brand_tuple(team_id: str):
    try:
        colors, logo_path, palette, saved = (
            get_team_brand(team_id) if get_team_brand else (("#0A2A4A", "#081E36", "#0A2A4A"), None, [], False)
        )
        logo_img = None
        if logo_path and os.path.exists(logo_path):
            from PIL import Image
            logo_img = Image.open(logo_path).convert("RGBA")
        return colors, logo_img, palette, saved
    except Exception as e:
        _log("[tg] team_brand err", team_id, repr(e))
        return (("#0A2A4A", "#081E36", "#0A2A4A"), None, [], False)

# -------------------- state: pending per chat --------------------
PENDING: Dict[int, Dict[str, Any]] = {}  # chat_id -> {type, ...}

def _pending_set(chat_id: int, payload: Dict[str, Any]) -> None:
    PENDING[chat_id] = payload
    if DEBUG:
        _log("[pending] set", chat_id, payload.get("type"))

def _pending_clear(chat_id: int) -> None:
    if chat_id in PENDING:
        del PENDING[chat_id]
        if DEBUG:
            _log("[pending] clear", chat_id)

# Удобняшка: достраиваем карточку, если все имена уже заданы
def _resume_if_ready(chat_id: int) -> None:
    ctx = PENDING.get(chat_id)
    if not ctx:
        return
    t = ctx.get("type")

    try:
        if t == "card":
            pid = str(ctx["pid"])
            p = _player_by_id(pid) or {}
            ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            if not ru:
                return
            team_id = str(p.get("teamId") or "0")
            colors, logo_img, _, _ = _team_brand_tuple(team_id)
            head = _ensure_headshot_image(p)
            if head is None:
                _tg_send_message(chat_id, "❌ Не удалось получить фото игрока")
                _pending_clear(chat_id)
                return
            img = render_card(ru, "", logo_img, colors, head, ctx["stats"])
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png")
            _pending_clear(chat_id)
            return

        if t == "cards":
            pid = str(ctx["pid"])
            p = _player_by_id(pid) or {}
            ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            if not ru:
                return
            team_id = str(p.get("teamId") or "0")
            colors, logo_img, _, _ = _team_brand_tuple(team_id)
            head = _ensure_headshot_image(p)
            if head is None:
                _tg_send_message(chat_id, "❌ Не удалось получить фото игрока")
                _pending_clear(chat_id)
                return
            img = render_card_special(ru, "", logo_img, colors, head, ctx["stats"], ctx["right"])
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"cards_{pid}.png")
            _pending_clear(chat_id)
            return

        if t == "card2":
            idA = str(ctx["idA"])
            idB = str(ctx["idB"])
            ruA = overrides_get_name_ru(idA) if overrides_get_name_ru else None
            ruB = overrides_get_name_ru(idB) if overrides_get_name_ru else None
            if not (ruA and ruB):
                return
            pA = _player_by_id(idA) or {}
            pB = _player_by_id(idB) or {}
            colorsA, logoA, _, _ = _team_brand_tuple(str(pA.get("teamId") or "0"))
            colorsB, logoB, _, _ = _team_brand_tuple(str(pB.get("teamId") or "0"))
            headA = _ensure_headshot_image(pA)
            headB = _ensure_headshot_image(pB)
            if headA is None or headB is None:
                _tg_send_message(chat_id, "❌ Не удалось получить фото одного из игроков")
                _pending_clear(chat_id)
                return
            img = render_card2(
                ruA, "", logoA, colorsA, headA, ctx["statsA"],
                ruB, "", logoB, colorsB, headB, ctx["statsB"],
            )
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{idA}_{idB}.png")
            _pending_clear(chat_id)
            return
    except Exception as e:
        _tg_send_message(chat_id, f"❌ Ошибка рендера: {e!r}")
        _pending_clear(chat_id)

# -------------------- tags / secret --------------------
# Было: r"\[setname:(\d+)\]"
# Стало: матчим и "[setname:123]", и "setname:123", и с пробелами.
SETNAME_TAG_RE = re.compile(r"(?:\[\s*)?setname:(\d+)(?:\s*\])?", re.IGNORECASE)

def _check_secret(req: Request):
    s = (req.query_params.get("secret") or "").strip()
    if not WEBHOOK_SECRET or s != WEBHOOK_SECRET:
        return PlainTextResponse("bad secret", status_code=401)
    return None

# -------------------- GET --------------------
@app.get("/api/telegram")
async def telegram_get(request: Request):
    bad = _check_secret(request)
    if bad:
        return bad

    action = (request.query_params.get("action") or "").strip().lower()

    if action == "diag":
        return JSONResponse(
            {
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
                    "graphics": ("graphics.render_* not callable" if _graphics_err else None),
                    "team_brand": _brand_err,
                },
                "api_origin": API_ORIGIN,
            }
        )

    if action == "refresh":
        try:
            cnt, src = refresh_players()
            try:
                cur = get_players(False)
                cnt = len(cur) if isinstance(cur, list) else int(cnt)
            except Exception:
                pass
            return JSONResponse(
                {
                    "ok": True,
                    "refreshed": True,
                    "players_indexed": int(cnt),
                    "source": ("custom" if src else "none"),
                    "source_url": (src or None),
                }
            )
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)

    if action == "test_find":
        q = request.query_params.get("q") or ""
        hits = search_players_loose(q)
        return JSONResponse(
            {
                "ok": True,
                "q": q,
                "players_ready": PLAYERS_READY,
                "hits": [
                    {"id": h.get("personId"), "name": h.get("displayName"), "teamId": h.get("teamId")}
                    for h in hits[:10]
                ],
            }
        )

    return PlainTextResponse("OK")

HELP = (
    "Команды:\n"
    "• /find <имя> — поиск игрока\n"
    "• /card <имя> | <стата>\n"
    "• /cards <имя> | <стата> | <надпись справа>\n"
    "• /card2 <A> | <статаA> || <B> | <статаB>\n"
    "• /cardbad (или /bad) <имя> | <стата>\n"
    "• /stop — сбросить контекст\n"
)

# -------------------- POST (webhook) --------------------
@app.post("/api/telegram")
async def telegram_post(request: Request):
    bad = _check_secret(request)
    if bad:
        return bad

    rid = f"[RID={int(time.time()*1000)}-{uuid.uuid4().hex[:6]}]"
    raw = (await request.body()).decode("utf-8", "ignore")
    if DEBUG:
        _log("[tg] ", rid, "POST", request.url, "\nbody:", raw)

    try:
        upd = json.loads(raw)
    except Exception:
        return PlainTextResponse("OK")

    # /stop
    msg = upd.get("message") or upd.get("edited_message")
    cb = upd.get("callback_query")

    if msg and isinstance(msg.get("text"), str) and msg["text"].strip().lower().startswith("/stop"):
        _pending_clear(msg["chat"]["id"])
        _tg_send_message(msg["chat"]["id"], "Готово. Контекст сброшен ✅")
        return PlainTextResponse("OK")

    # обработка ответа с русским именем (reply)
    if msg and msg.get("reply_to_message"):
        chat_id = msg["chat"]["id"]
        rtxt = (msg["reply_to_message"].get("text") or "") + " " + (msg["reply_to_message"].get("caption") or "")
        m = SETNAME_TAG_RE.search(rtxt)
        if not m:
            # фоллбек на всякий
            m = re.search(r"setname\s*:\s*(\d+)", rtxt, re.IGNORECASE)
        if m and overrides_save_name_ru:
            pid = m.group(1)
            try:
                nm = msg["text"].strip()
                overrides_save_name_ru(pid, nm)
                _tg_send_message(chat_id, f"Сохранил имя для {pid}: {nm}")
            except Exception as e:
                _tg_send_message(chat_id, f"Не удалось сохранить имя: {e!r}")
                return PlainTextResponse("OK")
            # пробуем дорисовать, если что-то ждёт
            _resume_if_ready(chat_id)
            return PlainTextResponse("OK")

    # callback-кнопки (если где-то останутся)
    if cb:
        data = cb.get("data") or ""
        if data.startswith("color:"):
            _tg_post("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Цвет применён"})
            return PlainTextResponse("OK")

    if not msg:
        return PlainTextResponse("OK")

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    low = text.lower()

    # --- команды ---
    if low.startswith("/start"):
        _tg_send_message(chat_id, "Я здесь. Готов работать 💼\n\n" + HELP)
        return PlainTextResponse("OK")

    if low.startswith("/help"):
        _tg_send_message(chat_id, HELP)
        return PlainTextResponse("OK")

    if low.startswith("/find"):
        q = text[5:].strip()
        hits = search_players_loose(q)
        if not hits:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷")
        else:
            lines = [
                f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})" for h in hits[:8]
            ]
            _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK")

    # ---- /cardbad | /bad ----
    if low.startswith("/cardbad") or low.startswith("/bad"):
        args = text.split(" ", 1)[1] if " " in text else ""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, "Формат: /cardbad <имя> | <стата>")
            return PlainTextResponse("OK")
        qname, raw_stats = parts[0], parts[1]
        stats = parse_stats_list(raw_stats)

        _tg_send_message(chat_id, "_Ищу игрока…_", parse_mode="Markdown")
        hits = search_players_loose(qname)
        if not hits:
            _tg_send_message(chat_id, f"Не нашёл игрока: {qname}")
            return PlainTextResponse("OK")
        p = hits[0]
        pid = str(p.get("personId") or "")
        ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
        if not ru:
            _pending_set(chat_id, {"type": "card", "pid": pid, "stats": stats})  # переиспользуем «card» логику
            _tg_send_message(
                chat_id,
                f"Как подписать игрока *{p.get('displayName')}* на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg["message_id"],
                parse_mode="Markdown",
            )
            return PlainTextResponse("OK")

        team_id = str(p.get("teamId") or "0")
        colors, _, _, _ = _team_brand_tuple(team_id)  # cardbad всё равно коричневая внутри graphics
        head = _ensure_headshot_image(p)
        if head is None:
            _tg_send_message(chat_id, "❌ Не удалось получить фото игрока")
            return PlainTextResponse("OK")

        try:
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            img = render_card_bad(ru, "", None, colors, head, stats)
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"cardBAD_{pid}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"❌ Ошибка рендера: {e!r}")
        return PlainTextResponse("OK")

    # ---- /cards ----
    if low.startswith("/cards"):
        args = text.split(" ", 1)[1] if " " in text else ""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 3:
            _tg_send_message(chat_id, "Формат: /cards <имя> | <стата> | <надпись справа>")
            return PlainTextResponse("OK")

        qname, raw_stats, right_text = parts[0], parts[1], parts[2]
        stats = parse_stats_list(raw_stats)

        _tg_send_message(chat_id, "_Ищу игрока…_", parse_mode="Markdown")
        hits = search_players_loose(qname)
        if not hits:
            _tg_send_message(chat_id, f"Не нашёл игрока: {qname}")
            return PlainTextResponse("OK")
        p = hits[0]
        pid = str(p.get("personId") or "")
        ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
        if not ru:
            _pending_set(chat_id, {"type": "cards", "pid": pid, "stats": stats, "right": right_text})
            _tg_send_message(
                chat_id,
                f"Как подписать игрока *{p.get('displayName')}* на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg["message_id"],
                parse_mode="Markdown",
            )
            return PlainTextResponse("OK")

        team_id = str(p.get("teamId") or "0")
        colors, logo_img, _, _ = _team_brand_tuple(team_id)
        head = _ensure_headshot_image(p)
        if head is None:
            _tg_send_message(chat_id, "❌ Не удалось получить фото игрока")
            return PlainTextResponse("OK")

        try:
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            img = render_card_special(ru, "", logo_img, colors, head, stats, right_text)
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"cards_{pid}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"❌ Ошибка рендера: {e!r}")
        return PlainTextResponse("OK")

    # ---- /card2 ----
    if low.startswith("/card2"):
        args = text.split(" ", 1)[1] if " " in text else ""
        sides = [s.strip() for s in args.split("||")]
        if len(sides) != 2:
            _tg_send_message(chat_id, "Формат: /card2 A | <статаA> || B | <статаB>")
            return PlainTextResponse("OK")

        def _parse_side(s: str) -> Tuple[str, List[Tuple[str, str]]]:
            parts = [p.strip() for p in s.split("|")]
            if len(parts) < 2:
                return s.strip(), []
            return parts[0], parse_stats_list(parts[1])

        nameA, statsA = _parse_side(sides[0])
        nameB, statsB = _parse_side(sides[1])

        _tg_send_message(chat_id, "_Ищу игроков…_", parse_mode="Markdown")
        hitsA = search_players_loose(nameA)
        hitsB = search_players_loose(nameB)
        if not hitsA:
            _tg_send_message(chat_id, f"Не нашёл игрока: {nameA}")
            return PlainTextResponse("OK")
        if not hitsB:
            _tg_send_message(chat_id, f"Не нашёл игрока: {nameB}")
            return PlainTextResponse("OK")

        pA, pB = hitsA[0], hitsB[0]
        idA, idB = str(pA.get("personId") or ""), str(pB.get("personId") or "")

        need_ruA = overrides_get_name_ru(idA) if overrides_get_name_ru else None
        need_ruB = overrides_get_name_ru(idB) if overrides_get_name_ru else None
        if not (need_ruA and need_ruB):
            _pending_set(
                chat_id,
                {
                    "type": "card2",
                    "idA": idA,
                    "idB": idB,
                    "statsA": statsA,
                    "statsB": statsB,
                },
            )
            if not need_ruA:
                _tg_send_message(
                    chat_id,
                    f"Как подписать игрока *{pA.get('displayName')}* на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{idA}]",
                    reply_to=msg["message_id"],
                    parse_mode="Markdown",
                )
                return PlainTextResponse("OK")
            if not need_ruB:
                _tg_send_message(
                    chat_id,
                    f"Как подписать игрока *{pB.get('displayName')}* на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{idB}]",
                    reply_to=msg["message_id"],
                    parse_mode="Markdown",
                )
                return PlainTextResponse("OK")

        ruA = overrides_get_name_ru(idA) if overrides_get_name_ru else None
        ruB = overrides_get_name_ru(idB) if overrides_get_name_ru else None
        colorsA, logoA, _, _ = _team_brand_tuple(str(pA.get("teamId") or "0"))
        colorsB, logoB, _, _ = _team_brand_tuple(str(pB.get("teamId") or "0"))
        headA = _ensure_headshot_image(pA)
        headB = _ensure_headshot_image(pB)
        if headA is None or headB is None:
            _tg_send_message(chat_id, "❌ Не удалось получить фото одного из игроков")
            return PlainTextResponse("OK")

        try:
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            img = render_card2(
                ruA, "", logoA, colorsA, headA, statsA,
                ruB, "", logoB, colorsB, headB, statsB,
            )
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{idA}_{idB}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"❌ Ошибка рендера: {e!r}")
        finally:
            _pending_clear(chat_id)
        return PlainTextResponse("OK")

    # ---- /card ----
    if low.startswith("/card"):
        args = text.split(" ", 1)[1] if " " in text else ""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, "Формат: /card <имя> | <стата>")
            return PlainTextResponse("OK")

        qname, raw_stats = parts[0], parts[1]
        stats = parse_stats_list(raw_stats)

        _tg_send_message(chat_id, "_Ищу игрока…_", parse_mode="Markdown")
        hits = search_players_loose(qname)
        if not hits:
            _tg_send_message(chat_id, f"Не нашёл игрока: {qname}")
            return PlainTextResponse("OK")
        p = hits[0]
        pid = str(p.get("personId") or "")
        ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
        if not ru:
            _pending_set(chat_id, {"type": "card", "pid": pid, "stats": stats})
            _tg_send_message(
                chat_id,
                f"Как подписать игрока *{p.get('displayName')}* на плашке?\n"
                f"Ответьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg["message_id"],
                parse_mode="Markdown",
            )
            return PlainTextResponse("OK")

        team_id = str(p.get("teamId") or "0")
        colors, logo_img, _, _ = _team_brand_tuple(team_id)
        head = _ensure_headshot_image(p)
        if head is None:
            _tg_send_message(chat_id, "❌ Не удалось получить фото игрока")
            return PlainTextResponse("OK")

        try:
            _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
            img = render_card(ru, "", logo_img, colors, head, stats)
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png")
        except Exception as e:
            _tg_send_message(chat_id, f"❌ Ошибка рендера: {e!r}")
        finally:
            _pending_clear(chat_id)
        return PlainTextResponse("OK")

    # fallback
    _tg_send_message(chat_id, HELP)
    return PlainTextResponse("OK")
