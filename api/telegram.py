# api/telegram.py
# Webhook FastAPI для Телеграма. Поддержка: /start /help /stop /find /card /cards /card2 /cardbad(/bad)
# Главные фишки:
# 1) Единый status-message с обновлением текста по шагам (поиск / ожидание имени / рендер / готово или ошибка)
# 2) Персистентные русские имена через GitHub Gist (GIST_TOKEN + GIST_ID)
# 3) Любая карточка упаковывается в 1920x1080 RGBA (прозрачный фон), чтобы не было «плавающих» размеров

from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid, base64
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

DEBUG = os.getenv("DEBUG", "1") in ("1","true","yes")
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
API_ORIGIN = os.getenv("API_ORIGIN") or None

# ---- Gist-персист для RU-имен ----
GIST_TOKEN = (os.getenv("GIST_TOKEN") or "").strip()
GIST_ID    = (os.getenv("GIST_ID") or "").strip()
GIST_FILE  = (os.getenv("GIST_FILE") or "ru_names.json").strip()

# -------------------- logging --------------------
def _log(*a: Any) -> None:
    try: print(*a, flush=True)
    except: pass

# -------------------- safe imports --------------------
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
    "render_card", "render_card2", "render_card_bad", "render_card_special"
])
if _graphics_err and DEBUG: _log("[boot] graphics import error:", _graphics_err)
(render_card, render_card2, render_card_bad, render_card_special) = ([_ for _ in _graphics_objs] + [None]*4)[:4]

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
    req = UrlRequest(url, data=body, headers={"Content-Type":"application/json"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "raw": raw.decode("utf-8","ignore")}

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

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int]=None, parse_mode: Optional[str]=None):
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _tg_post("sendMessage", payload)

def _tg_edit_message(chat_id: int, message_id: int, text: str, parse_mode: Optional[str]=None):
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    return _tg_post("editMessageText", payload)

def _multipart_boundary() -> str:
    return "----WebKitFormBoundary" + uuid.uuid4().hex

def _encode_multipart(fields: Dict[str,str], files: Dict[str,Tuple[str,bytes,str]]) -> Tuple[bytes,str]:
    boundary = _multipart_boundary()
    lines: List[bytes] = []
    for k,v in fields.items():
        lines.append(b"--"+boundary.encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b""); lines.append(v.encode("utf-8"))
    for field,(filename,content,ctype) in files.items():
        lines.append(b"--"+boundary.encode())
        lines.append(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {ctype}".encode())
        lines.append(b""); lines.append(content)
    lines.append(b"--"+boundary.encode()+b"--")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"

def _tg_send_png_as_document(chat_id: int, png_bytes: bytes, filename: str="card.png", caption: Optional[str]=None):
    url = _tg_url("sendDocument")
    fields = {"chat_id": str(chat_id)}
    if caption: fields["caption"] = caption
    body, ctype = _encode_multipart(fields, {"document": (filename, png_bytes, "image/png")})
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    try:
        with http_urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8","ignore")
            try: return json.loads(raw)
            except: return {"ok": False, "raw": raw}
    except Exception as e:
        if DEBUG: _log("[tg] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# -------------------- helpers: image wrap 1920x1080 --------------------
from PIL import Image

CANVAS_W, CANVAS_H = 1920, 1080
LEFT_MARGIN = 80  # куда клеим карточку на канвас
def _as_image(obj: Any) -> Image.Image:
    if isinstance(obj, Image.Image): return obj
    if isinstance(obj, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(obj))).convert("RGBA")
    # Пытаемся .save() => значит это тоже Image-подобное
    bio = io.BytesIO()
    obj.save(bio, "PNG")
    bio.seek(0)
    return Image.open(bio).convert("RGBA")

def _wrap_to_1080p(obj: Any) -> Image.Image:
    img = _as_image(obj)
    if img.size == (CANVAS_W, CANVAS_H):
        return img
    base = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0,0,0,0))
    x = LEFT_MARGIN
    y = max(0, (CANVAS_H - img.height)//2)
    base.alpha_composite(img, (x, y))
    return base

def _img_to_png_bytes(obj: Any) -> bytes:
    im = _wrap_to_1080p(obj)
    bio = io.BytesIO()
    im.save(bio, "PNG")
    return bio.getvalue()

# -------------------- players / normalize --------------------
PLAYERS_READY = False

def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ё","е")
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщьыъэюя -'/"  # допустимые
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

def ensure_players_loaded(force: bool=False) -> List[Dict[str,Any]]:
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
        _log("[players] ensure failed:", repr(e)); PLAYERS_READY = False; return []

def _player_by_id(pid: str) -> Optional[Dict[str,Any]]:
    for p in ensure_players_loaded(False):
        if str(p.get("personId") or p.get("id") or "") == str(pid):
            return p
    return None

def search_players_loose(q: str) -> List[Dict[str,Any]]:
    qn = _normalize(q)
    ps = ensure_players_loaded(False)
    if find_player_by_name:
        try:
            hits = find_player_by_name(q)
            if hits: return hits
        except Exception: pass
    out = []
    for p in ps:
        dn = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if dn and qn in _normalize(dn):
            out.append(p)
            if len(out) >= 10: break
    return out

# -------------------- stats parsing --------------------
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
    "%": "%",
    "мин": "МИНУТЫ",
    "плюс": "ПЛЮС/МИНУС",
    "фол": "ФОЛЫ",
    "потер": "ПОТЕРИ",
}
STAT_VALUE_RE = re.compile(r"^\s*([0-9]+(?:\s*[-/]\s*[0-9]+)?(?:\s*из\s*[0-9]+)?)\s*([^\d,]*)$", re.IGNORECASE)

def parse_stats_list(raw: str) -> List[Tuple[str,str]]:
    if not raw: return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Tuple[str,str]] = []
    for p in parts:
        m = STAT_VALUE_RE.match(p)
        if not m: continue
        val = re.sub(r"\s{2,}", " ", m.group(1))
        tail = (m.group(2) or "").strip().lower()
        label = "СТАТ"
        for k,v in STAT_TOKEN_MAP.items():
            if k in tail:
                label = v; break
        out.append((val, label))
    return out

# -------------------- RU names: overrides + Gist fallback --------------------
_RU_CACHE: Dict[str,str] = {}
_RU_CACHE_LOADED = False

def _gist_headers() -> Dict[str,str]:
    return {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "vm-plashki-news-bot"
    }

def _gist_read_cache() -> None:
    global _RU_CACHE, _RU_CACHE_LOADED
    if _RU_CACHE_LOADED: return
    if not (GIST_TOKEN and GIST_ID):
        _RU_CACHE_LOADED = True; return
    try:
        req = UrlRequest(f"https://api.github.com/gists/{GIST_ID}", headers=_gist_headers())
        with http_urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8","ignore"))
        files = data.get("files") or {}
        file = files.get(GIST_FILE)
        if file and "content" in file:
            _RU_CACHE = json.loads(file["content"]) if file["content"] else {}
        _RU_CACHE_LOADED = True
        _log("[ru-cache] loaded from gist:", len(_RU_CACHE))
    except Exception as e:
        _log("[ru-cache] gist load error:", repr(e))
        _RU_CACHE_LOADED = True  # чтобы не дергать бесконечно

def _gist_write_cache() -> bool:
    if not (GIST_TOKEN and GIST_ID): return False
    try:
        payload = {
            "files": {
                GIST_FILE: {
                    "content": json.dumps(_RU_CACHE, ensure_ascii=False, indent=2)
                }
            }
        }
        req = UrlRequest(f"https://api.github.com/gists/{GIST_ID}",
                         data=json.dumps(payload).encode("utf-8"),
                         headers=_gist_headers(), method="PATCH")
        with http_urlopen(req, timeout=15) as r:
            _ = r.read()
        return True
    except Exception as e:
        _log("[ru-cache] gist save error:", repr(e))
        return False

def _get_ru_name(pid: str) -> Optional[str]:
    # 1) внешний overrides (если настроен)
    try:
        if overrides_get_name_ru:
            val = overrides_get_name_ru(pid)
            if val: return val
    except Exception:
        pass
    # 2) наш Gist-кеш
    _gist_read_cache()
    return _RU_CACHE.get(str(pid))

def _save_ru_name(pid: str, name_ru: str) -> bool:
    ok = False
    try:
        if overrides_save_name_ru:
            overrides_save_name_ru(pid, name_ru)
            ok = True
    except Exception as e:
        _log("[ru-save] overrides error:", repr(e))
    # Обязательно пишем в Gist, чтобы жить переживало редеплои
    _gist_read_cache()
    _RU_CACHE[str(pid)] = name_ru
    ok_gist = _gist_write_cache()
    return ok or ok_gist

# -------------------- display images --------------------
def _ensure_headshot_image(p: Dict[str,Any]):
    try:
        hs = ensure_headshot_png(p) if ensure_headshot_png else None
        if hs is None: return None
        if isinstance(hs, (bytes, bytearray)):
            return Image.open(io.BytesIO(hs)).convert("RGBA")
        if isinstance(hs, str):
            return Image.open(hs).convert("RGBA")
        return hs.convert("RGBA")
    except Exception as e:
        _log("[tg] headshot ensure err", p.get("personId"), repr(e)); return None

def _ensure_team_logo_image(team_id: str):
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
        _log("[tg] team logo ensure err", team_id, repr(e)); return None

def _team_brand_tuple(team_id: str):
    try:
        colors, logo_path, palette, saved = get_team_brand(team_id) if get_team_brand else (("#0A2A4A","#081E36","#0A2A4A"), None, [], False)
        logo_img = None
        if logo_path and os.path.exists(logo_path):
            logo_img = Image.open(logo_path).convert("RGBA")
        return colors, logo_img, palette, saved
    except Exception as e:
        _log("[tg] team_brand err", team_id, repr(e))
        return (("#0A2A4A","#081E36","#0A2A4A"), None, [], False)

# -------------------- state & statuses --------------------
# В PENDING храним: type, ... + status_msg_id (сообщение, которое правим)
PENDING: Dict[int, Dict[str, Any]] = {}

def _status_send(chat_id: int, text: str) -> Optional[int]:
    resp = _tg_send_message(chat_id, text)
    try:
        return (resp.get("result") or {}).get("message_id")
    except Exception:
        return None

def _status_update(chat_id: int, msg_id: Optional[int], text: str):
    if not msg_id: return
    _tg_edit_message(chat_id, msg_id, text)

def _pending_set(chat_id: int, payload: Dict[str, Any]) -> None:
    PENDING[chat_id] = payload
    if DEBUG: _log("[pending] set", chat_id, payload.get("type"))

def _pending_clear(chat_id: int) -> None:
    if chat_id in PENDING:
        del PENDING[chat_id]
        if DEBUG: _log("[pending] clear", chat_id)

# Достраиваем карточку после сохранения имени
def _resume_if_ready(chat_id: int) -> None:
    ctx = PENDING.get(chat_id)
    if not ctx: return
    t = ctx.get("type")
    sid = ctx.get("status_msg_id")  # статусное сообщение

    try:
        if t == "card":
            pid = str(ctx["pid"]); p = _player_by_id(pid) or {}
            ru = _get_ru_name(pid)
            if not ru:
                _status_update(chat_id, sid, "🧩 Жду подтверждение имени игрока…")
                return
            _status_update(chat_id, sid, "🖼 Рендерю плашку…")
            team_id = str(p.get("teamId") or "0")
            colors, logo_img, _, _ = _team_brand_tuple(team_id)
            head = _ensure_headshot_image(p)
            if head is None:
                _status_update(chat_id, sid, "❌ Не удалось получить фото игрока")
                _pending_clear(chat_id); return
            img = render_card(ru, "", logo_img, colors, head, ctx["stats"])
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png")
            _status_update(chat_id, sid, "✅ Готово")
            _pending_clear(chat_id); return

        if t == "cards":
            pid = str(ctx["pid"]); p = _player_by_id(pid) or {}
            ru = _get_ru_name(pid)
            if not ru:
                _status_update(chat_id, sid, "🧩 Жду подтверждение имени игрока…")
                return
            _status_update(chat_id, sid, "🖼 Рендерю плашку…")
            team_id = str(p.get("teamId") or "0")
            colors, logo_img, _, _ = _team_brand_tuple(team_id)
            head = _ensure_headshot_image(p)
            if head is None:
                _status_update(chat_id, sid, "❌ Не удалось получить фото игрока")
                _pending_clear(chat_id); return
            right_txt = (ctx["right"] or "").rstrip() + "\n "  # пустая строка внизу, чтобы не резалось
            img = render_card_special(ru, "", logo_img, colors, head, ctx["stats"], right_txt)
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"cards_{pid}.png")
            _status_update(chat_id, sid, "✅ Готово")
            _pending_clear(chat_id); return

        if t == "card2":
            idA, idB = str(ctx["idA"]), str(ctx["idB"])
            ruA, ruB = _get_ru_name(idA), _get_ru_name(idB)
            if not (ruA and ruB):
                missing = "A" if not ruA else "B"
                _status_update(chat_id, sid, f"🧩 Жду подтверждение имени игрока {missing}…")
                return
            _status_update(chat_id, sid, "🖼 Рендерю двойную плашку…")
            pA, pB = _player_by_id(idA) or {}, _player_by_id(idB) or {}
            colorsA, logoA, _, _ = _team_brand_tuple(str(pA.get("teamId") or "0"))
            colorsB, logoB, _, _ = _team_brand_tuple(str(pB.get("teamId") or "0"))
            headA, headB = _ensure_headshot_image(pA), _ensure_headshot_image(pB)
            if headA is None or headB is None:
                _status_update(chat_id, sid, "❌ Не удалось получить фото одного из игроков")
                _pending_clear(chat_id); return
            img = render_card2(
                ruA, "", logoA, colorsA, headA, ctx["statsA"],
                ruB, "", logoB, colorsB, headB, ctx["statsB"],
            )
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{idA}_{idB}.png")
            _status_update(chat_id, sid, "✅ Готово")
            _pending_clear(chat_id); return

    except Exception as e:
        _status_update(chat_id, sid, f"❌ Ошибка рендера: {e!r}")
        _pending_clear(chat_id)

# -------------------- tags / secret --------------------
# Принимаем и "[setname:123]" и "setname:123"
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
    if bad: return bad
    action = (request.query_params.get("action") or "").strip().lower()

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
            "api_origin": API_ORIGIN,
        })

    if action == "refresh":
        try:
            cnt, src = refresh_players()
            try:
                cur = get_players(False); cnt = len(cur) if isinstance(cur, list) else int(cnt)
            except Exception: pass
            return JSONResponse({"ok": True, "refreshed": True, "players_indexed": int(cnt),
                                 "source": ("custom" if src else "none"), "source_url": (src or None)})
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)

    if action == "test_find":
        q = request.query_params.get("q") or ""
        hits = search_players_loose(q)
        return JSONResponse({"ok": True, "q": q, "players_ready": PLAYERS_READY,
                             "hits": [{"id": h.get("personId"), "name": h.get("displayName"), "teamId": h.get("teamId")} for h in hits[:10]]})

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
    if bad: return bad

    rid = f"[RID={int(time.time()*1000)}-{uuid.uuid4().hex[:6]}]"
    raw = (await request.body()).decode("utf-8","ignore")
    if DEBUG: _log("[tg] ", rid, "POST", request.url, "\nbody:", raw)
    try:
        upd = json.loads(raw)
    except Exception:
        return PlainTextResponse("OK")

    # /stop
    msg = upd.get("message") or upd.get("edited_message")
    cb  = upd.get("callback_query")
    if msg and isinstance(msg.get("text"), str) and msg["text"].strip().lower().startswith("/stop"):
        _pending_clear(msg["chat"]["id"])
        _tg_send_message(msg["chat"]["id"], "Готово. Контекст сброшен ✅")
        return PlainTextResponse("OK")

    # ответ на вопрос с именем (reply)
    if msg and msg.get("reply_to_message"):
        chat_id = msg["chat"]["id"]
        rtxt = (msg["reply_to_message"].get("text") or "") + " " + (msg["reply_to_message"].get("caption") or "")
        m = SETNAME_TAG_RE.search(rtxt) or re.search(r"setname\s*:\s*(\d+)", rtxt, re.IGNORECASE)
        if m:
            pid = m.group(1)
            nm = (msg["text"] or "").strip()
            if not nm:
                _tg_send_message(chat_id, "Имя пустое, введите заново.")
                return PlainTextResponse("OK")
            ok = _save_ru_name(pid, nm)
            if ok:
                _tg_send_message(chat_id, f"💾 Сохранил имя навсегда для {pid}: {nm}")
            else:
                _tg_send_message(chat_id, f"⚠️ Не удалось сохранить имя глобально, но попробую продолжить: {nm}")
            _resume_if_ready(chat_id)
            return PlainTextResponse("OK")

    # callback цвета (если останутся)
    if cb:
        data = cb.get("data") or ""
        if data.startswith("color:"):
            _tg_post("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Цвет применён"})
            return PlainTextResponse("OK")

    if not msg: return PlainTextResponse("OK")

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    low  = text.lower()

    # --- команды ---
    if low.startswith("/start"):
        _tg_send_message(chat_id, "Я здесь. Готов работать 💼\n\n"+HELP); return PlainTextResponse("OK")
    if low.startswith("/help"):
        _tg_send_message(chat_id, HELP); return PlainTextResponse("OK")
    if low.startswith("/find"):
        q = text[5:].strip()
        hits = search_players_loose(q)
        if not hits: _tg_send_message(chat_id, "Ничего не нашёл 🤷")
        else:
            lines = [f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})" for h in hits[:8]]
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

        sid = _status_send(chat_id, "🔎 Ищу игрока…")
        hits = search_players_loose(qname)
        if not hits:
            _status_update(chat_id, sid, f"❌ Не нашёл игрока: {qname}")
            return PlainTextResponse("OK")
        p = hits[0]; pid = str(p.get("personId") or "")
        ru = _get_ru_name(pid)
        if not ru:
            _pending_set(chat_id, {"type":"card","pid":pid,"stats":stats,"status_msg_id":sid})
            _tg_send_message(chat_id,
                f"Как подписать игрока *{p.get('displayName')}* на плашке?\n"
                f"Ответьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg["message_id"], parse_mode="Markdown")
            _status_update(chat_id, sid, "🧩 Жду русское имя игрока…")
            return PlainTextResponse("OK")

        team_id = str(p.get("teamId") or "0")
        colors, _, _, _ = _team_brand_tuple(team_id)
        head = _ensure_headshot_image(p)
        if head is None:
            _status_update(chat_id, sid, "❌ Не удалось получить фото игрока"); return PlainTextResponse("OK")
        try:
            _status_update(chat_id, sid, "🖼 Рендерю плашку…")
            img = render_card_bad(ru, "", None, colors, head, stats)  # внутри — коричневая + 💩
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"cardBAD_{pid}.png")
            _status_update(chat_id, sid, "✅ Готово")
        except Exception as e:
            _status_update(chat_id, sid, f"❌ Ошибка рендера: {e!r}")
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

        sid = _status_send(chat_id, "🔎 Ищу игрока…")
        hits = search_players_loose(qname)
        if not hits:
            _status_update(chat_id, sid, f"❌ Не нашёл игрока: {qname}")
            return PlainTextResponse("OK")
        p = hits[0]; pid = str(p.get("personId") or "")
        ru = _get_ru_name(pid)
        if not ru:
            _pending_set(chat_id, {"type":"cards","pid":pid,"stats":stats,"right":right_text,"status_msg_id":sid})
            _tg_send_message(chat_id,
                f"Как подписать игрока *{p.get('displayName')}* на плашке?\n"
                f"Ответьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg["message_id"], parse_mode="Markdown")
            _status_update(chat_id, sid, "🧩 Жду русское имя игрока…")
            return PlainTextResponse("OK")

        team_id = str(p.get("teamId") or "0")
        colors, logo_img, _, _ = _team_brand_tuple(team_id)
        head = _ensure_headshot_image(p)
        if head is None:
            _status_update(chat_id, sid, "❌ Не удалось получить фото игрока"); return PlainTextResponse("OK")
        try:
            _status_update(chat_id, sid, "🖼 Рендерю плашку…")
            right_text = right_text.rstrip() + "\n "  # защита от среза последней строки
            img = render_card_special(ru, "", logo_img, colors, head, stats, right_text)
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"cards_{pid}.png")
            _status_update(chat_id, sid, "✅ Готово")
        except Exception as e:
            _status_update(chat_id, sid, f"❌ Ошибка рендера: {e!r}")
        return PlainTextResponse("OK")

    # ---- /card2 ----
    if low.startswith("/card2"):
        args = text.split(" ", 1)[1] if " " in text else ""
        sides = [s.strip() for s in args.split("||")]
        if len(sides) != 2:
            _tg_send_message(chat_id, "Формат: /card2 A | <статаA> || B | <статаB>")
            return PlainTextResponse("OK")

        def _parse_side(s: str) -> Tuple[str, List[Tuple[str,str]]]:
            parts = [p.strip() for p in s.split("|")]
            if len(parts) < 2: return s.strip(), []
            return parts[0], parse_stats_list(parts[1])

        nameA, statsA = _parse_side(sides[0])
        nameB, statsB = _parse_side(sides[1])

        sid = _status_send(chat_id, "🔎 Ищу игроков…")
        hitsA = search_players_loose(nameA)
        hitsB = search_players_loose(nameB)
        if not hitsA:
            _status_update(chat_id, sid, f"❌ Не нашёл игрока: {nameA}"); return PlainTextResponse("OK")
        if not hitsB:
            _status_update(chat_id, sid, f"❌ Не нашёл игрока: {nameB}"); return PlainTextResponse("OK")

        pA, pB = hitsA[0], hitsB[0]
        idA, idB = str(pA.get("personId") or ""), str(pB.get("personId") or "")

        ruA, ruB = _get_ru_name(idA), _get_ru_name(idB)
        if not (ruA and ruB):
            _pending_set(chat_id, {"type":"card2","idA":idA,"idB":idB,"statsA":statsA,"statsB":statsB,"status_msg_id":sid})
            if not ruA:
                _tg_send_message(chat_id,
                    f"Как подписать игрока *{pA.get('displayName')}* на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{idA}]",
                    reply_to=msg["message_id"], parse_mode="Markdown")
                _status_update(chat_id, sid, "🧩 Жду русское имя игрока A…")
                return PlainTextResponse("OK")
            if not ruB:
                _tg_send_message(chat_id,
                    f"Как подписать игрока *{pB.get('displayName')}* на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{idB}]",
                    reply_to=msg["message_id"], parse_mode="Markdown")
                _status_update(chat_id, sid, "🧩 Жду русское имя игрока B…")
                return PlainTextResponse("OK")

        colorsA, logoA, _, _ = _team_brand_tuple(str(pA.get("teamId") or "0"))
        colorsB, logoB, _, _ = _team_brand_tuple(str(pB.get("teamId") or "0"))
        headA, headB = _ensure_headshot_image(pA), _ensure_headshot_image(pB)
        if headA is None or headB is None:
            _status_update(chat_id, sid, "❌ Не удалось получить фото одного из игроков"); return PlainTextResponse("OK")
        try:
            _status_update(chat_id, sid, "🖼 Рендерю двойную плашку…")
            img = render_card2(
                ruA, "", logoA, colorsA, headA, statsA,
                ruB, "", logoB, colorsB, headB, statsB
            )
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{idA}_{idB}.png")
            _status_update(chat_id, sid, "✅ Готово")
        except Exception as e:
            _status_update(chat_id, sid, f"❌ Ошибка рендера: {e!r}")
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

        sid = _status_send(chat_id, "🔎 Ищу игрока…")
        hits = search_players_loose(qname)
        if not hits:
            _status_update(chat_id, sid, f"❌ Не нашёл игрока: {qname}")
            return PlainTextResponse("OK")
        p = hits[0]; pid = str(p.get("personId") or "")
        ru = _get_ru_name(pid)
        if not ru:
            _pending_set(chat_id, {"type":"card","pid":pid,"stats":stats,"status_msg_id":sid})
            _tg_send_message(chat_id,
                f"Как подписать игрока *{p.get('displayName')}* на плашке?\n"
                f"Ответьте на это сообщение русским именем.\n[setname:{pid}]",
                reply_to=msg["message_id"], parse_mode="Markdown")
            _status_update(chat_id, sid, "🧩 Жду русское имя игрока…")
            return PlainTextResponse("OK")

        team_id = str(p.get("teamId") or "0")
        colors, logo_img, _, _ = _team_brand_tuple(team_id)
        head = _ensure_headshot_image(p)
        if head is None:
            _status_update(chat_id, sid, "❌ Не удалось получить фото игрока")
            return PlainTextResponse("OK")
        try:
            _status_update(chat_id, sid, "🖼 Рендерю плашку…")
            img = render_card(ru, "", logo_img, colors, head, stats)
            png = _img_to_png_bytes(img)
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png")
            _status_update(chat_id, sid, "✅ Готово")
        except Exception as e:
            _status_update(chat_id, sid, f"❌ Ошибка рендера: {e!r}")
        finally:
            _pending_clear(chat_id)
        return PlainTextResponse("OK")

    # fallback
    _tg_send_message(chat_id, HELP)
    return PlainTextResponse("OK")
