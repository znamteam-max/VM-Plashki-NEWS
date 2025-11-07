# api/telegram.py
from __future__ import annotations
import os, io, json, time, base64, re, traceback, uuid, unicodedata
from typing import Any, Dict, List, Tuple, Optional

# --- NETWORK (avoid name clash with FastAPI Request) ---
from urllib.request import Request as HttpRequest, urlopen as http_urlopen
from urllib.error import URLError, HTTPError

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import JSONResponse, PlainTextResponse

# --- DEBUG ---
DEBUG = os.getenv("DEBUG", "0") == "1"

def _dbg(*args: Any) -> None:
    if DEBUG:
        try: print("[tg]", *args, flush=True)
        except Exception: pass

def _log(*args: Any) -> None:
    try: print("[tg]", *args, flush=True)
    except Exception: pass

# --- SAFE IMPORTS ---
def _safe_import(mod_name: str, names: List[str]):
    try:
        m = __import__(mod_name, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, None, out
    except Exception as e:
        return None, f"{type(e).__name__}: {e}\n{traceback.format_exc()}", []

# data / team_brand / graphics
_data, data_err, _ = _safe_import(
    "data",
    ["get_players_index","find_player_by_name","refresh_players","drop_players_cache",
     "ensure_headshot_png","ensure_team_logo_png"]
)
if _data:
    get_players_index, find_player_by_name, refresh_players, drop_players_cache, \
    ensure_headshot_png, ensure_team_logo_png = _
else:
    get_players_index = find_player_by_name = refresh_players = drop_players_cache = None
    ensure_headshot_png = ensure_team_logo_png = None

_team_brand, brand_err, _ = _safe_import("team_brand", ["team_colors_for", "color_name_for"])
if _team_brand:
    team_colors_for, color_name_for = _
else:
    team_colors_for = color_name_for = None

_graphics, graphics_err, _ = _safe_import(
    "graphics",
    ["render_card","render_card2","render_card_bad","render_card_special","render_card_drN"]
)
if _graphics:
    render_card, render_card2, render_card_bad, render_card_special, render_card_drN = _
else:
    render_card = render_card2 = render_card_bad = render_card_special = render_card_drN = None

# --- ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip() or "hook-123"
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS","").replace(" ","").split(",") if x)

# overrides in GitHub (optional)
OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","assets/players_overrides.json").strip()

# --- TG API helpers ---
def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_json(url: str, payload: Dict[str,Any], timeout: int = 25) -> Dict[str,Any]:
    data = json.dumps(payload).encode("utf-8")
    req = HttpRequest(url, data=data, headers={"Content-Type":"application/json"})
    try:
        with http_urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except HTTPError as e:
        try: err_body = e.read().decode("utf-8","ignore")
        except Exception: err_body = ""
        _log("[TG] HTTPError", e.code, err_body)
        try: return json.loads(err_body)
        except Exception: return {"ok": False, "status": e.code, "body": err_body}
    except Exception as e:
        _log("[TG] urlopen error:", repr(e))
        return {"ok": False, "error": repr(e)}
    try: return json.loads(raw.decode("utf-8"))
    except Exception: return json.loads(raw)

def _tg_post(method: str, payload: Dict[str,Any]) -> Dict[str,Any]:
    try: return _http_json(_tg_url(method), payload)
    except Exception:
        _log("[TG] send error:", traceback.format_exc()); return {"ok": False, "error": "send-failed"}

def _escape_html(s: str) -> str:
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int]=None, parse_mode: Optional[str]="HTML"):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
        payload["disable_web_page_preview"] = True
    return _tg_post("sendMessage", payload)

def _build_multipart(fields: Dict[str,Tuple[str,bytes,str]], data_fields: Dict[str,str]) -> Tuple[bytes,str]:
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    lines: List[bytes] = []
    for k,v in data_fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b"")
        lines.append(v.encode("utf-8"))
    for k,(filename, content, ctype) in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {ctype}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(f"--{boundary}--".encode()); lines.append(b"")
    return b"\r\n".join(lines), boundary

def _tg_send_document(chat_id: int, filename: str, content: bytes, caption: Optional[str]=None):
    url = _tg_url("sendDocument")
    data_fields = {"chat_id": str(chat_id)}
    if caption:
        data_fields["caption"] = caption
        data_fields["parse_mode"] = "HTML"
    fields = {"document": (filename, content, "image/png")}
    body, boundary = _build_multipart(fields, data_fields)
    req = HttpRequest(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with http_urlopen(req, timeout=60) as r:
            raw = r.read()
        try: return json.loads(raw.decode("utf-8"))
        except Exception: return json.loads(raw)
    except HTTPError as e:
        try: err_body = e.read().decode("utf-8","ignore")
        except Exception: err_body = ""
        _log("[TG] sendDocument HTTPError", e.code, err_body)
        try: return json.loads(err_body)
        except Exception: return {"ok": False, "status": e.code, "body": err_body}
    except Exception as e:
        _log("[TG] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# --- OVERRIDES (runtime + optional GitHub) ---
_OVR: Dict[str, Dict[str, Any]] = {}

def _split_ru_name(full: str) -> Tuple[str,str]:
    t = " ".join(full.strip().split())
    if not t: return "", ""
    parts = t.split(" ", 1)
    return (parts[0], parts[1] if len(parts)>1 else "")

def _apply_runtime_override(pid: str, patch: Dict[str,Any]) -> None:
    base = _OVR.get(pid, {})
    base.update(patch)
    _OVR[pid] = base
    os.environ["PLAYERS_OVERRIDES_JSON"] = json.dumps(_OVR, ensure_ascii=False)
    try:
        if drop_players_cache: drop_players_cache()
        if refresh_players: refresh_players()
    except Exception:
        pass

def _save_ru_name_override(pid: str, ru_fullname: str) -> None:
    fn, ln = _split_ru_name(ru_fullname)
    _apply_runtime_override(pid, {"firstName": fn, "lastName": ln})
    # (опционально) синк в GitHub — опущен для краткости

def _save_team_override(pid: str, team_id: str) -> None:
    _apply_runtime_override(pid, {"teamId": str(team_id)})

# --- HELP TEXT ---
HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /help — это сообщение\n"
    "• /refresh — обновить список игроков\n"
    "• /find <имя/фамилия> — поиск игрока (например: /find Lebron)\n"
    "• /name <имя> — задать русское имя (ответьте на сообщение)\n"
    "• /team <имя> — задать teamId (ответьте числом)\n"
    "• /card <имя> | <метрики> — плашка игрока (PNG)\n"
)

# --- PLAYERS CACHE HELPERS ---
def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def _players_idx() -> Dict[str,Dict[str,Any]]:
    try: return get_players_index() or {}
    except Exception: return {}

def _players_count() -> int:
    try: return len(_players_idx())
    except Exception: return 0

def _ensure_players_ready(min_expected: int = 100, tries: int = 2) -> int:
    n = _players_count()
    if n >= min_expected: return n
    for _ in range(tries):
        try:
            if refresh_players:
                refresh_players()
        except Exception as e:
            _log("[players] refresh error:", repr(e))
        n = _players_count()
        if n >= min_expected: break
    _dbg("players ready:", n)
    return n

def _best_match(q: str) -> Optional[Dict[str,Any]]:
    if not q: return None
    _ensure_players_ready()
    qn = _norm(q)
    # 1) data.find_player_by_name (если есть реализованный поиск)
    try:
        res = find_player_by_name(q)
        if res: return res[0]
    except Exception:
        pass
    # 2) подстрока по display/first/last (нормализовано)
    for p in _players_idx().values():
        disp = (p.get("displayName") or (p.get("firstName","")+" "+p.get("lastName",""))).strip()
        if qn and _norm(disp).find(qn) != -1:
            return p
    # 3) подстрока по фамилии отдельно
    for p in _players_idx().values():
        if qn and _norm(p.get("lastName","")).find(qn) != -1:
            return p
    return None

def _suggest_players(q: str, limit: int = 5) -> List[Dict[str,Any]]:
    _ensure_players_ready()
    qn = _norm(q)
    cand: List[Tuple[int,Dict[str,Any]]] = []
    for p in _players_idx().values():
        disp = (p.get("displayName") or (p.get("firstName","")+" "+p.get("lastName",""))).strip()
        fulln = _norm(disp)
        score = 0
        if fulln == qn: score = 100
        elif fulln.startswith(qn): score = 90
        elif qn in fulln: score = 70
        elif _norm(p.get("lastName","")).startswith(qn): score = 60
        if score>0:
            cand.append((score,p))
    cand.sort(key=lambda x: -x[0])
    return [p for _,p in cand[:limit]]

# --- HEAD/LOGO/COLORS ---
def _as_image(obj: Any):
    try:
        from PIL import Image
    except Exception:
        return None
    if obj is None: return None
    if hasattr(obj, "size") and hasattr(obj, "mode"): return obj
    try: return Image.open(io.BytesIO(obj)).convert("RGBA")
    except Exception: return None

def _head_img_for(pid: str):
    try:
        b = ensure_headshot_png(pid)
        return _as_image(b)
    except Exception as e:
        _log("[tg] headshot ensure err", pid, repr(e)); return None

def _logo_img_for(team_id: str):
    if not team_id or str(team_id) == "0": return None
    try:
        b = ensure_team_logo_png(team_id)
        return _as_image(b)
    except Exception as e:
        _log("[tg] team logo ensure err", team_id, repr(e)); return None

def _colors_for_team(team_id: str, custom_hex: Optional[str]) -> Tuple[str,str,str,str]:
    primary = "#007ACC"; dark = "#005A99"; light="#8CC7F2"; name="синий"
    if team_colors_for:
        try:
            t = team_colors_for(str(team_id))
            if isinstance(t, (list,tuple)) and len(t)>=3:
                primary, dark, light = t[0], t[1], t[2]
        except Exception: pass
    if custom_hex:
        primary = custom_hex
        try:
            from graphics import _hex_to_rgb, _shade
            rgb = _hex_to_rgb(primary)
            d = _shade(rgb, 0.65)
            dark = "#%02X%02X%02X" % d
            light = primary
        except Exception:
            dark = primary; light = primary
    if color_name_for:
        try: name = color_name_for(primary) or name
        except Exception: pass
    return primary, dark, light, name

def _render_card_png(player: Dict[str,Any], stats: List[Tuple[str,str]], custom_hex: Optional[str]) -> bytes:
    pid = str(player.get("personId") or "")
    team_id = str(player.get("teamId") or "0")
    head = _head_img_for(pid)
    if head is None:
        raise RuntimeError("Не удалось получить фото игрока")
    logo = _logo_img_for(team_id)
    colors = _colors_for_team(team_id, custom_hex)
    display = (player.get("displayName") or (player.get("firstName","")+" "+player.get("lastName",""))).strip()
    return render_card("single", display, "", logo, colors[:3], head, stats)

# --- FASTAPI app ---
app = FastAPI()

def _ok(data: Dict[str,Any]) -> JSONResponse:
    return JSONResponse(data)

def _bad_secret() -> JSONResponse:
    return JSONResponse({"detail": "bad secret"}, status_code=403)

# --- GET ---
@app.get("/api/telegram")
async def telegram_get(request: FastAPIRequest):
    q = dict(request.query_params)
    if q.get("secret") != WEBHOOK_SECRET:
        return _bad_secret()
    action = (q.get("action") or "").strip()
    if action == "diag":
        return _ok({
            "ok": True,
            "py": "3.12",
            "platform": "Linux",
            "modules": {
                "graphics": ("ok" if _graphics and not graphics_err else "error"),
                "team_brand": ("ok" if _team_brand and not brand_err else "error"),
            },
            "has_bot_token": bool(BOT_TOKEN),
            "boot_error": None if not (graphics_err or brand_err) else {"graphics":graphics_err, "brand":brand_err},
            "brand_warn": None
        })
    if action == "refresh":
        try:
            n, meta = refresh_players()
            src = (meta or {}).get("source","?")
            url = (meta or {}).get("url") or os.getenv("PLAYERS_URL") or ""
            return _ok({"ok": True, "refreshed": True, "players_indexed": n, "source": src, "source_url": url})
        except Exception as e:
            return _ok({"ok": False, "refreshed": False, "error": repr(e)})
    if action == "players":
        n = _ensure_players_ready()
        # вернём пару примеров
        sample = []
        try:
            for i,(pid,p) in enumerate(_players_idx().items()):
                if i>=3: break
                nm = (p.get("displayName") or (p.get("firstName","")+" "+p.get("lastName",""))).strip()
                sample.append({"personId": pid, "name": nm})
        except Exception:
            pass
        return _ok({"ok": True, "count": n, "sample": sample})
    return _ok({"ok": True, "route": "telegram-get"})

# --- POST (webhook) ---
@app.post("/api/telegram")
async def webhook_query(request: FastAPIRequest):
    if request.query_params.get("secret") != WEBHOOK_SECRET:
        return PlainTextResponse("OK")
    rid = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    try: raw = await request.body()
    except Exception: raw = b""
    try: body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception: body = {}
    _log(f"[RID={rid}] POST {str(request.url)}")
    if DEBUG: _log(f"[RID={rid}] body:", json.dumps(body, ensure_ascii=False))

    msg = body.get("message") or body.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    reply_to = msg.get("reply_to_message") or {}

    # access control
    if ADMIN_IDS and chat_id not in ADMIN_IDS and text not in ("/start","/help"):
        _tg_send_message(chat_id, "Привет! Попроси доступ у администратора 😊", parse_mode=None)
        return PlainTextResponse("OK")

    # reply flows
    if reply_to and text:
        rtxt = (reply_to.get("text") or "")
        m = re.search(r"\[setname:([0-9]+)\]", rtxt)
        if m:
            pid = m.group(1)
            ru = text.strip()
            _save_ru_name_override(pid, ru)
            _tg_send_message(chat_id, f"Сохранил имя для {pid}: {_escape_html(ru)}", parse_mode="HTML")
            _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
            return PlainTextResponse("OK")
        m = re.search(r"\[setteam:([0-9]+)\]", rtxt)
        if m:
            pid = m.group(1)
            team = re.sub(r"[^\d]","", text)
            if not team:
                _tg_send_message(chat_id, "Пришлите число teamId (например 1610612747).", reply_to=msg.get("message_id"), parse_mode=None)
                return PlainTextResponse("OK")
            _save_team_override(pid, team)
            _tg_send_message(chat_id, f"Ок, teamId для {pid} = {team}", parse_mode=None)
            return PlainTextResponse("OK")
        m = re.search(r"\[choosecolor:([0-9]+):([0-9]+)\]", rtxt)
        if m:
            choice = text.strip()
            if choice == "1" or choice.lower() == "авто":
                _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
                return PlainTextResponse("OK")
            if choice == "2":
                _tg_send_message(chat_id, "Пришлите цвет в формате #RRGGBB\n[setcolor:%s:%s]" % (m.group(1), m.group(2)), parse_mode="HTML")
                return PlainTextResponse("OK")
            if re.search(r"#([0-9A-Fa-f]{6})", choice):
                _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
                return PlainTextResponse("OK")
            return PlainTextResponse("OK")
        m = re.search(r"\[setcolor:([0-9]+):([0-9]+)\]", rtxt)
        if m:
            cm = re.search(r"#([0-9A-Fa-f]{6})", text.strip())
            if not cm:
                _tg_send_message(chat_id, "Нужен цвет формата #RRGGBB. Пришлите ещё раз.", parse_mode=None)
                return PlainTextResponse("OK")
            _tg_send_message(chat_id, f"Цвет принят: #{cm.group(1)}\n<i>Готовлю плашку…</i>", parse_mode="HTML")
            return PlainTextResponse("OK")

    # commands
    if text == "/start":
        _ensure_players_ready()
        _tg_send_message(chat_id, "Я здесь! ✌️\n\n"+HELP_TEXT, parse_mode=None)
        return PlainTextResponse("OK")

    if text == "/help":
        _tg_send_message(chat_id, HELP_TEXT, parse_mode=None)
        return PlainTextResponse("OK")

    if text == "/refresh":
        n = _ensure_players_ready(tries=3)
        _tg_send_message(chat_id, f"Готово: в кеше {n} игроков.", parse_mode=None)
        return PlainTextResponse("OK")

    if text.startswith("/find"):
        q = text[len("/find"):].strip()
        if not q:
            _tg_send_message(chat_id, "Укажи часть имени: /find Lebron", parse_mode=None)
            return PlainTextResponse("OK")
        _ensure_players_ready()
        res = _suggest_players(q, limit=8)
        if not res:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷", parse_mode=None)
            return PlainTextResponse("OK")
        lines = []
        for p in res:
            nm = (p.get("displayName") or (p.get("firstName","")+" "+p.get("lastName",""))).strip()
            lines.append(f"{_escape_html(nm)} (id={p.get('personId')}, teamId={p.get('teamId')})")
        _tg_send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        return PlainTextResponse("OK")

    if text.startswith("/name"):
        q = text[len("/name"):].strip()
        if not q:
            _tg_send_message(chat_id, "Укажи имя: /name Kevin Durant", parse_mode=None)
            return PlainTextResponse("OK")
        _ensure_players_ready()
        p = _best_match(q)
        if not p:
            # покажем кандидатов
            cands = _suggest_players(q)
            if not cands:
                _tg_send_message(chat_id, f"Не нашёл игрока: {_escape_html(q)}", parse_mode="HTML")
                return PlainTextResponse("OK")
            # первый кандидат
            p = cands[0]
        en = (p.get("firstName","")+" "+p.get("lastName","")).strip() or (p.get("displayName") or "")
        _tg_send_message(
            chat_id,
            "<i>Уточнения…</i>\n\n"
            f"Как подписать игрока {_escape_html(en)} на плашке?\n"
            "Ответьте на это сообщение русским именем.\n"
            f"[setname:{p.get('personId')}]",
            reply_to=msg.get("message_id"),
            parse_mode="HTML"
        )
        return PlainTextResponse("OK")

    if text.startswith("/team"):
        q = text[len("/team"):].strip()
        if not q:
            _tg_send_message(chat_id, "Укажи имя: /team Kevin Durant", parse_mode=None)
            return PlainTextResponse("OK")
        _ensure_players_ready()
        p = _best_match(q) or ( _suggest_players(q,1)[0] if _suggest_players(q,1) else None )
        if not p:
            _tg_send_message(chat_id, f"Не нашёл игрока: {_escape_html(q)}", parse_mode="HTML")
            return PlainTextResponse("OK")
        en = (p.get("firstName","")+" "+p.get("lastName","")).strip() or (p.get("displayName") or "")
        _tg_send_message(
            chat_id,
            "<i>Уточнения…</i>\n\n"
            f"Какой teamId у {_escape_html(en)}? Пришлите число (например, 1610612756)\n"
            f"(сейчас: {p.get('teamId') or '0'})\n"
            f"[setteam:{p.get('personId')}]",
            reply_to=msg.get("message_id"),
            parse_mode="HTML"
        )
        return PlainTextResponse("OK")

    if text.startswith("/card"):
        try:
            player_raw, stats, color_raw = _parse_card_text(text)
            if not player_raw:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики>", parse_mode=None)
                return PlainTextResponse("OK")

            _ensure_players_ready()
            p = _best_match(player_raw)
            if not p:
                cands = _suggest_players(player_raw)
                if cands:
                    lines = ["Не нашёл точного совпадения. Похожие:"]
                    for c in cands: 
                        nm = (c.get("displayName") or (c.get("firstName","")+" "+c.get("lastName",""))).strip()
                        lines.append(f"• {_escape_html(nm)} (id={c.get('personId')})")
                    _tg_send_message(chat_id, "\n".join(lines), parse_mode="HTML")
                else:
                    _tg_send_message(chat_id, f"Не нашёл игрока: {_escape_html(player_raw)}", parse_mode="HTML")
                return PlainTextResponse("OK")

            disp = (p.get("displayName") or "").strip()
            en = (p.get("firstName","")+" "+p.get("lastName","")).strip() or disp
            # спросим русское имя, если похоже, что латиница
            if re.search(r"[A-Za-z]", en):
                _tg_send_message(
                    chat_id,
                    "<i>Уточнения…</i>\n\n"
                    f"Как подписать игрока {_escape_html(en)} на плашке?\n"
                    "Ответьте на это сообщение русским именем.\n"
                    f"[setname:{p.get('personId')}]",
                    reply_to=msg.get("message_id"),
                    parse_mode="HTML"
                )

            # выбор цвета (авто/свой)
            if not color_raw:
                _tg_send_message(
                    chat_id,
                    "<i>Уточнения…</i>\n\n"
                    "Выберите цвет плашки:\n"
                    "1 — Авто (цвета команды)\n"
                    "2 — Свой цвет (#RRGGBB)\n\n"
                    f"[choosecolor:{p.get('personId')}:{p.get('teamId') or '0'}]",
                    reply_to=msg.get("message_id"),
                    parse_mode="HTML"
                )

            _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
            png = _render_card_png(p, stats, color_raw)
            _tg_send_document(chat_id, f"card_{p.get('personId')}.png", png)
            return PlainTextResponse("OK")

        except Exception as e:
            _log("[tg] card err", traceback.format_exc())
            _tg_send_message(chat_id, f"Ошибка: {_escape_html(str(e))}", parse_mode="HTML")
            return PlainTextResponse("OK")

    # fallback
    _tg_send_message(chat_id, HELP_TEXT, parse_mode=None)
    return PlainTextResponse("OK")

# --- PARSERS (placed at end for clarity) ---
def _parse_card_text(txt: str) -> Tuple[str, List[Tuple[str,str]], Optional[str]]:
    t = txt.strip()
    if t.startswith("/card"): t = t[len("/card"):].strip()
    parts = [p.strip() for p in t.split("|")]
    player_raw = parts[0] if parts else ""
    stats_raw = parts[1] if len(parts)>1 else ""
    color_raw = None
    if len(parts)>2:
        m = re.search(r"(#?[0-9A-Fa-f]{6})", parts[2])
        if m: color_raw = m.group(1)
    stats: List[Tuple[str,str]] = []
    if stats_raw:
        for chunk in stats_raw.split(","):
            c = chunk.strip()
            if not c: continue
            m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s+(.*)$", c)
            if m: stats.append((m.group(1), m.group(2)))
            else: stats.append((c, ""))
    return player_raw, stats, color_raw
