# api/telegram.py — центрирование name+stats уже в graphics; тут: поток выбора цвета
# для card2 (сначала команда 1, затем команда 2), после обоих — рендер;
# парсер статов поддерживает 3/5, (3 из 5), "3 из 5"; ForceReply без мусора, имя жирным.

from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid, html
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
    "get_players", "refresh_players", "find_player_by_name",
    "display_name_for", "overrides_save_name_ru", "overrides_get_name_ru",
    "ensure_headshot_png", "ensure_team_logo_png"
])
(get_players, refresh_players, find_player_by_name,
 display_name_for, overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png) = ([_ for _ in _data_objs] + [None]*8)[:8]

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

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int]=None,
                     parse_mode: Optional[str]=None, reply_markup: Optional[Dict]=None):
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
    s = s.replace("ё", "е")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеэжзийклмнопрстуфхцчшщьыъэюя -'"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

# трёшки — синонимы
STAT_TOKEN_MAP = {
    "очк":"ОЧКИ","передач":"ПЕРЕДАЧИ","подбор":"ПОДБОРЫ","блок":"БЛОКИ",
    "стил":"ПЕРЕХВАТЫ","мин":"МИНУТЫ","фол":"ФОЛЫ","потер":"ПОТЕРИ",
    "дабл":"ДАБЛ-ДАБЛ","трипл":"ТРИПЛ-ДАБЛ",
    "трех":"3-ОЧКОВЫЕ","трёх":"3-ОЧКОВЫЕ","треш":"3-ОЧКОВЫЕ","трешк":"3-ОЧКОВЫЕ","трешки":"3-ОЧКОВЫЕ",
    "трёш":"3-ОЧКОВЫЕ","трёшк":"3-ОЧКОВЫЕ","трёшки":"3-ОЧКОВЫЕ",
    "трехочков":"3-ОЧКОВЫЕ","трёхочков":"3-ОЧКОВЫЕ","3-очков":"3-ОЧКОВЫЕ","3 очк":"3-ОЧКОВЫЕ",
}

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1].strip()
    if (s.startswith("«") and s.endswith("»")) or (s.startswith("“") and s.endswith("”")):
        return s[1:-1].strip()
    return s

def parse_stats_list(raw: str) -> List[Tuple[str,str]]:
    if not raw: return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Tuple[str,str]] = []
    for p in parts:
        p = _strip_quotes(p)
        m = re.match(r"^\s*\(?(?P<val>(?:\d+\s*из\s*\d+)|(?:\d+/\d+)|(?:\d+))\)?\s*(?P<label>[^\d,]+)?", p, flags=re.IGNORECASE)
        if not m: continue
        val = m.group("val")
        lbl_raw = (m.group("label") or "").strip().lower().replace("ё","е")
        lbl = "СТАТ"
        for k,v in STAT_TOKEN_MAP.items():
            if k in lbl_raw:
                lbl = v
                break
        out.append((val, lbl))
    return out

def ensure_players_loaded(force: bool=False) -> List[Dict[str,Any]]:
    global PLAYERS_READY
    ps = []
    try:
        ps = get_players(force_refresh=bool(force)) if get_players else []
        if not ps or len(ps) < 50:
            if refresh_players:
                refresh_players()
                ps = get_players(force_refresh=False) if get_players else []
        PLAYERS_READY = bool(ps and len(ps) >= 50)
    except Exception as e:
        if DEBUG: _log("[players] ensure failed:", repr(e))
        PLAYERS_READY = False
    return ps or []

def _search_players_loose(q: str) -> List[Dict[str,Any]]:
    qn = _normalize(q)
    ps = ensure_players_loaded(False)
    if not ps: return []
    try:
        if find_player_by_name:
            hits = find_player_by_name(q)
            if hits: return hits
    except Exception:
        pass
    out = []
    for p in ps:
        dn = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if dn and qn in _normalize(dn):
            out.append(p)
            if len(out) >= 10: break
    return out

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

# ------------ Контексты ------------
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

def _bold_html(s: str) -> str:
    return f"<b>{html.escape(s)}</b>"

def _ask_russian_name(chat_id: int, reply_to_msg_id: Optional[int], p: Dict[str,Any],
                      stats: List[Tuple[str,str]], template: str, extra: Dict[str,Any]=None):
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

def _ask_color_choice(chat_id: int, reply_to: Optional[int], ctx: Dict[str,Any], team_ids: List[str], stage: int = 0):
    # сохраняем прогресс подбора цвета
    ctx_local = dict(ctx)
    ctx_local["team_ids"] = team_ids
    ctx_local["color_stage"] = stage
    ctx_local["chosen_colors"] = ctx_local.get("chosen_colors") or {}
    _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx_local)

    # показываем кнопки для текущей команды
    if stage < len(team_ids):
        tid = team_ids[stage]
        rows = [[
            {"text": f"Цвет команды {stage+1}: авто", "callback_data": f"color:auto:{tid}"},
            {"text": f"Цвет команды {stage+1}: свой HEX", "callback_data": f"color:ask:{tid}"},
        ]]
        _tg_send_message(chat_id, "Выберите цвет плашки:", reply_to=reply_to, reply_markup={"inline_keyboard": rows})
    else:
        # всё выбрано — рендер
        _finish_render_from_ctx(chat_id)

def _finish_render_from_ctx(chat_id: int):
    ctx = _load_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json")) or {}
    template = (ctx.get("template") or "single").lower()

    players = ensure_players_loaded(False)
    if not players:
        _tg_send_message(chat_id, "Игроки не подгружены.")
        return

    def _player_by_pid(pid: str) -> Optional[Dict[str,Any]]:
        return next((pp for pp in players if str(pp.get("personId"))==str(pid)), None)

    try:
        if template in ("single","card","cards","cardspecial","cards_","bad","cardbad"):
            pid = str(ctx.get("pid") or "")
            p = _player_by_pid(pid)
            if not p:
                _tg_send_message(chat_id, "Игрок не найден (pid)."); return
            ru_name = None
            if overrides_get_name_ru: ru_name = overrides_get_name_ru(pid)
            if not ru_name:
                ru_name = display_name_for(p) if display_name_for else (p.get("displayName") or "")
            head = _ensure_headshot_image(p)
            if not head:
                _tg_send_message(chat_id, "Не удалось получить фото игрока."); return
            team_id = str(ctx.get("teamId") or p.get("teamId") or "0")
            colors, logo_img = _team_brand_tuple(team_id)
            stats = ctx.get("stats") or []

            _tg_send_chat_action(chat_id, "upload_document")
            if template in ("bad","cardbad"):
                png = render_card_bad(ru_name, head, stats, team_logo_img=logo_img)
                _tg_send_png_as_document(chat_id, png, filename=f"card_bad_{pid}.png"); return
            if template in ("cards","cardspecial","cards_"):
                info = ctx.get("info") or ""
                png = render_card_special(ru_name, logo_img, colors, head, stats, info)
                _tg_send_png_as_document(chat_id, png, filename=f"cardS_{pid}.png"); return
            png = render_card("single", ru_name, "", logo_img, colors, head, stats)
            _tg_send_png_as_document(chat_id, png, filename=f"card_{pid}.png"); return

        if template in ("card2","dual"):
            pid1 = str(ctx.get("pid") or "")
            pid2 = str(ctx.get("pid2") or "")
            p1 = _player_by_pid(pid1); p2 = _player_by_pid(pid2) if pid2 else None
            if not p1 or not p2:
                _tg_send_message(chat_id, "Один из игроков не найден."); return

            ru1 = overrides_get_name_ru(pid1) if overrides_get_name_ru else None
            if not ru1: ru1 = display_name_for(p1) if display_name_for else (p1.get("displayName") or "")
            ru2 = overrides_get_name_ru(pid2) if overrides_get_name_ru else None
            if not ru2: ru2 = display_name_for(p2) if display_name_for else (p2.get("displayName") or "")

            head1 = _ensure_headshot_image(p1); head2 = _ensure_headshot_image(p2)
            if not head1 or not head2:
                _tg_send_message(chat_id, "Нет фото одного из игроков."); return

            colors1, logo1 = _team_brand_tuple(str(p1.get("teamId") or "0"))
            colors2, logo2 = _team_brand_tuple(str(p2.get("teamId") or "0"))
            stats1 = ctx.get("stats") or []
            stats2 = ctx.get("stats2") or []

            _tg_send_chat_action(chat_id, "upload_document")
            png = render_card2(ru1, logo1, colors1, head1, stats1, ru2, logo2, colors2, head2, stats2)
            _tg_send_png_as_document(chat_id, png, filename=f"card2_{pid1}_{pid2}.png")
            return

        _tg_send_message(chat_id, "Неизвестный шаблон.")
    except Exception as e:
        _tg_send_message(chat_id, f"Ошибка рендера: {repr(e)}")

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
    "• /card <имя> | <метрики>\n"
    "• /cards <имя> | <метрики> | <текст справа>\n"
    "• /bad <имя> | <метрики>  (или /cardBAD)\n"
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
            "errors": {
                "data": _data_err, "graphics": _graphics_err, "team_brand": _brand_err,
            },
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
                "ok": True, "refreshed": True, "players_indexed": int(cnt_now),
                "source": (str(src) if src else "none"), "source_url": (str(src) if src else "none"),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)
    if action == "test_find":
        q = (request.query_params.get("q") or "").strip()
        hits = _search_players_loose(q)
        return JSONResponse({"ok": True, "q": q, "players_ready": PLAYERS_READY, "hits": hits[:5]})
    return PlainTextResponse("OK")

@app.post("/api/telegram")
async def webhook_query(request: Request):
    bad = _check_secret(request)
    if bad: return bad

    rid = f"[RID={int(time.time()*1000)}-{uuid.uuid4().hex[:6]}]"
    try:
        body = await request.body()
        raw = body.decode("utf-8","ignore")
        if DEBUG: _log("[tg]", rid, "POST", request.url, "\nbody:", raw)
        update = json.loads(raw)
    except Exception as e:
        if DEBUG: _log("[tg]", rid, "json error:", repr(e))
        return PlainTextResponse("OK")

    ensure_players_loaded(False)

    # callback: выбор цвета (поддержка цепочки для card2)
    cb = update.get("callback_query")
    if cb and cb.get("data","").startswith("color:"):
        data = cb["data"].split(":")
        cb_id = cb.get("id")
        chat_id = cb.get("message",{}).get("chat",{}).get("id")
        if not chat_id:
            _tg_answer_cbq(cb_id, "no chat"); return PlainTextResponse("OK")
        if len(data) == 3:
            _, kind, team_id = data
            ctx = _load_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json")) or {}
            stage = int(ctx.get("color_stage") or 0)
            team_ids = ctx.get("team_ids") or [team_id]
            chosen = ctx.get("chosen_colors") or {}

            if kind == "auto":
                if set_team_primary_color: set_team_primary_color(team_id, "AUTO")
                chosen[team_id] = "AUTO"
                _tg_answer_cbq(cb_id, "Цвет: авто")
            elif kind == "ask":
                _tg_answer_cbq(cb_id, "Отправьте HEX")
                _tg_send_message(chat_id, "Пришлите HEX цвет, например #1D428A — ответом на это сообщение.")
                _save_json(os.path.join(CTX_DIR, f"await_hex_{chat_id}.json"), {"teamId": team_id})
                # не двигаем stage до получения HEX
                return PlainTextResponse("OK")

            # сохраним прогресс и двинемся к следующему
            stage += 1
            ctx["chosen_colors"] = chosen
            ctx["color_stage"] = stage
            _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx)
            if stage < len(team_ids):
                _ask_color_choice(chat_id, cb.get("message",{}).get("message_id"), ctx, team_ids, stage=stage)
            else:
                _finish_render_from_ctx(chat_id)
        return PlainTextResponse("OK")

    msg = update.get("message") or update.get("edited_message")
    if not msg: return PlainTextResponse("OK")
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    # HEX после запроса
    if text.startswith("#"):
        ctx_hex = _load_json(os.path.join(CTX_DIR, f"await_hex_{chat_id}.json"))
        if ctx_hex and set_team_primary_color:
            team_id = ctx_hex.get("teamId")
            ok = set_team_primary_color(team_id, text.strip())
            _tg_send_message(chat_id, "Цвет сохранён." if ok else "HEX не принят.")
            try: os.remove(os.path.join(CTX_DIR, f"await_hex_{chat_id}.json"))
            except Exception: pass
            # после HEX двигаем стадию дальше
            ctx = _load_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json")) or {}
            stage = int(ctx.get("color_stage") or 0)
            team_ids = ctx.get("team_ids") or []
            if stage < len(team_ids):
                _ask_color_choice(chat_id, msg.get("message_id"), ctx, team_ids, stage=stage+1)
            else:
                _finish_render_from_ctx(chat_id)
            return PlainTextResponse("OK")

    # ForceReply: русское имя
    rpl = msg.get("reply_to_message")
    if rpl and text:
        rtxt = (rpl.get("text") or "") + " " + (rpl.get("caption") or "")
        if "Как подписать игрока" in rtxt:
            ctx_path = _ask_name_ctx_path(chat_id, rpl.get("message_id"))
            ctx = _load_json(ctx_path) or {}
            pid = str(ctx.get("pid") or "")
            template = (ctx.get("template") or "single").lower()
            stats = ctx.get("stats") or []
            team_id = str(ctx.get("teamId") or "0")

            if overrides_save_name_ru:
                try:
                    overrides_save_name_ru(pid, text.strip())
                    _tg_send_message(chat_id, f"Сохранил имя для {pid}: {text.strip()}")
                except Exception as e:
                    _tg_send_message(chat_id, f"Не удалось сохранить имя: {repr(e)}")
                    return PlainTextResponse("OK")

            # Подготовим контекст цветовых шагов (для card2 — двуступенчато)
            if template in ("card2","dual"):
                # уже на этапе команды (/card2 …) мы попробуем вычислить второго игрока и его teamId
                team_ids = []
                team_ids.append(team_id)
                if ctx.get("pid2"):
                    team_ids.append(str(ctx.get("teamId2") or "0"))
                _ask_color_choice(chat_id, rpl.get("message_id"), ctx, team_ids, stage=0)
            elif template in ("bad","cardbad"):
                # для bad — сразу рендер без выбора цвета
                players = ensure_players_loaded(False)
                p = next((pp for pp in players if str(pp.get("personId"))==pid), None)
                if not p:
                    _tg_send_message(chat_id, "Игрок не найден (pid)."); return PlainTextResponse("OK")
                head = _ensure_headshot_image(p)
                if not head:
                    _tg_send_message(chat_id, "Не удалось получить фото игрока."); return PlainTextResponse("OK")
                colors, logo_img = _team_brand_tuple(str(p.get("teamId") or "0"))
                _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
                png = render_card_bad(text.strip(), head, stats, team_logo_img=logo_img)
                _tg_send_png_as_document(chat_id, png, filename=f"card_bad_{pid}.png")
            else:
                _ask_color_choice(chat_id, rpl.get("message_id"), ctx, [team_id], stage=0)
            return PlainTextResponse("OK")

    # команды
    if text.startswith("/start"):
        _tg_send_message(chat_id, "Я здесь. Готов работать 💼")
        return PlainTextResponse("OK")

    if text.startswith("/help"):
        _tg_send_message(chat_id, HELP_TEXT)
        return PlainTextResponse("OK")

    if text.startswith("/find"):
        q = text[len("/find"):].strip()
        hits = _search_players_loose(q)
        if not hits:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷")
            return PlainTextResponse("OK")
        lines = []
        for h in hits[:5]:
            lines.append(f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})")
        _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK")

    def _handle_card_common(name_q: str, stats_raw: str, template: str, extra: Dict[str,Any]=None):
        stats = parse_stats_list(stats_raw)
        _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
        _tg_send_chat_action(chat_id, "typing")
        hits = _search_players_loose(name_q)
        if not hits:
            _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
            return
        if len(hits) > 1:
            menu = "\n".join([f"{i+1}. {h.get('displayName')} (id={h.get('personId')})" for i,h in enumerate(hits[:4])])
            _tg_send_message(chat_id, "Нашёл несколько вариантов:\n" + menu + "\nУточните запрос.")
            return
        p = hits[0]
        pid = str(p.get("personId") or "")

        # card2: заранее вычислим второго игрока, чтобы знать teamId2 для цветового мастера
        ctx_extra = extra.copy() if extra else {}
        if template in ("card2","dual"):
            name2 = ctx_extra.get("name2") or ""
            hits2 = _search_players_loose(name2) if name2 else []
            if hits2:
                p2 = hits2[0]
                ctx_extra["pid2"] = str(p2.get("personId") or "")
                ctx_extra["teamId2"] = str(p2.get("teamId") or "0")

        ru_name = None
        try:
            if overrides_get_name_ru: ru_name = overrides_get_name_ru(pid)
        except Exception: pass
        if not ru_name:
            ctx_pack = {"template": template, "stats": stats, "pid": pid, "teamId": str(p.get("teamId") or "0")}
            if ctx_extra: ctx_pack.update(ctx_extra)
            _ask_russian_name(chat_id, msg.get("message_id"), p, stats, template, ctx_pack)
            return

        # RU имя уже есть -> запускаем мастер выбора цветов
        ctx = {"template": template, "stats": stats, "pid": pid, "teamId": str(p.get("teamId") or "0")}
        if ctx_extra: ctx.update(ctx_extra)
        _save_json(os.path.join(CTX_DIR, f"last_ctx_{chat_id}.json"), ctx)

        if template in ("card2","dual"):
            team_ids = [str(p.get("teamId") or "0")]
            if ctx.get("teamId2"): team_ids.append(str(ctx.get("teamId2")))
            _ask_color_choice(chat_id, msg.get("message_id"), ctx, team_ids, stage=0)
        else:
            _ask_color_choice(chat_id, msg.get("message_id"), ctx, [str(p.get("teamId") or "0")], stage=0)

    if text.startswith("/bad") or text.startswith("/cardBAD"):
        args = text.split(" ",1)[-1].strip() if " " in text else ""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, "Формат: /bad <имя> | <метрики>")
            return PlainTextResponse("OK")
        _handle_card_common(parts[0], parts[1], "bad")
        return PlainTextResponse("OK")

    if text.startswith("/card2"):
        args = text.split(" ",1)[-1].strip() if " " in text else ""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 4:
            _tg_send_message(chat_id, "Формат: /card2 <имя1> | <метрики1> | <имя2> | <метрики2>")
            return PlainTextResponse("OK")
        _handle_card_common(parts[0], parts[1], "card2", {"name2": parts[2], "stats2": parse_stats_list(parts[3])})
        return PlainTextResponse("OK")

    if text.startswith("/cards") or text.startswith("/cardS"):
        args = text.split(" ",1)[-1].strip() if " " in text else ""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 3:
            _tg_send_message(chat_id, "Формат: /cards <имя> | <метрики> | <текст справа>")
            return PlainTextResponse("OK")
        _handle_card_common(parts[0], parts[1], "cards", {"info": parts[2]})
        return PlainTextResponse("OK")

    if text.startswith("/card"):
        args = text[len("/card"):].strip()
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2:
            _tg_send_message(chat_id, "Формат: /card <имя> | <метрики>")
            return PlainTextResponse("OK")
        _handle_card_common(parts[0], parts[1], "single")
        return PlainTextResponse("OK")

    _tg_send_message(chat_id, HELP_TEXT)
    return PlainTextResponse("OK")
