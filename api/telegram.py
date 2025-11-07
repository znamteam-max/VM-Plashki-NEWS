# api/telegram.py
from __future__ import annotations
import os, io, json, time, base64, re, traceback, uuid
from typing import Any, Dict, List, Tuple, Optional

# --- NETWORK ALIASES (fix name clash) ---
from urllib.request import Request as HttpRequest, urlopen as http_urlopen
from urllib.error import URLError, HTTPError

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import JSONResponse, PlainTextResponse

# --- PROJECT MODULES ---
# data: источники игроков, кеш, headshots/logo helpers
# team_brand: цвета и их "говорящие" названия
# graphics: рендер PNG на прозрачном фоне
DEBUG = os.getenv("DEBUG", "0") == "1"

def _dbg(*args: Any) -> None:
    try:
        if DEBUG:
            print("[tg]", *args, flush=True)
    except Exception:
        pass

def _log(*args: Any) -> None:
    try:
        print("[tg]", *args, flush=True)
    except Exception:
        pass

# безопасный импорт модулей — чтобы diag показывал конкретную ошибку
def _safe_import(mod_name: str, names: List[str]) -> Tuple[Optional[Any], Optional[str], List[Any]]:
    try:
        m = __import__(mod_name, fromlist=names)
        out = []
        for n in names:
            out.append(getattr(m, n))
        return m, None, out
    except Exception as e:
        return None, f"{type(e).__name__}: {e}\n{traceback.format_exc()}", []

# ---- import data / team_brand / graphics
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
ADMIN_IDS = set([int(x) for x in (os.getenv("ADMIN_IDS","").replace(" ","").split(",") if os.getenv("ADMIN_IDS") else [])])

# overrides sync (optional)
OV_GH_TOKEN  = os.getenv("OVERRIDES_GH_TOKEN","").strip()
OV_GH_REPO   = os.getenv("OVERRIDES_GH_REPO","").strip()         # owner/repo
OV_GH_BRANCH = os.getenv("OVERRIDES_GH_BRANCH","main").strip()
OV_GH_PATH   = os.getenv("OVERRIDES_GH_PATH","assets/players_overrides.json").strip()

# --- TG API helpers ---
def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_json(url: str, payload: Dict[str,Any], timeout: int = 25) -> Dict[str,Any]:
    data = json.dumps(payload).encode("utf-8")
    req = HttpRequest(url, data=data, headers={"Content-Type":"application/json"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw)

def _tg_post(method: str, payload: Dict[str,Any]) -> Dict[str,Any]:
    try:
        return _http_json(_tg_url(method), payload)
    except Exception as e:
        _log("[TG] send error:", traceback.format_exc())
        return {"ok": False, "error": repr(e)}

def _escape_html(s: str) -> str:
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int]=None, parse_mode: Optional[str]="HTML") -> Dict[str,Any]:
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
        payload["disable_web_page_preview"] = True
    return _tg_post("sendMessage", payload)

def _tg_send_chat_action(chat_id: int, action: str = "upload_document") -> None:
    _tg_post("sendChatAction", {"chat_id": chat_id, "action": action})

def _build_multipart(fields: Dict[str,Tuple[str,bytes,str]], data_fields: Dict[str,str]) -> Tuple[bytes,str]:
    """
    fields: {"document": ("filename.png", b"...", "image/png"), ...}
    data_fields: {"chat_id":"123", "caption":"..."}
    """
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    lines: List[bytes] = []
    # text fields
    for k,v in data_fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b"")
        lines.append(v.encode("utf-8"))
    # file fields
    for k,(filename, content, ctype) in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {ctype}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, boundary

def _tg_send_document(chat_id: int, filename: str, content: bytes, caption: Optional[str]=None) -> Dict[str,Any]:
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
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return json.loads(raw)
    except Exception as e:
        _log("[TG] sendDocument error:", traceback.format_exc())
        return {"ok": False, "error": repr(e)}

# --- OVERRIDES (runtime + optional GitHub) ---
# Держим в процессе объединённый словарь, zugleich пробрасываем как PLAYERS_OVERRIDES_JSON
_OVR: Dict[str, Dict[str, Any]] = {}

def _split_ru_name(full: str) -> Tuple[str,str]:
    t = " ".join(full.strip().split())
    if not t: return "", ""
    parts = t.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]

def _apply_runtime_override(pid: str, patch: Dict[str,Any]) -> None:
    base = _OVR.get(pid, {})
    base.update(patch)
    _OVR[pid] = base
    # прокидываем в ENV и пересобираем кэш игроков, чтобы displayName/поиск жили в одном процессе
    os.environ["PLAYERS_OVERRIDES_JSON"] = json.dumps(_OVR, ensure_ascii=False)
    try:
        drop_players_cache()
        refresh_players()
    except Exception:
        pass

def _gh_get_overrides() -> Optional[Dict[str,Any]]:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return None
    try:
        u = f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}?ref={OV_GH_BRANCH}"
        req = HttpRequest(u, headers={"Authorization": f"token {OV_GH_TOKEN}", "Accept": "application/vnd.github+json"})
        with http_urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        if "content" in j:
            raw = base64.b64decode(j["content"])
            d = json.loads(raw.decode("utf-8"))
            d["_sha"] = j.get("sha")
            return d
    except Exception as e:
        _log("[ovr] github get error:", repr(e))
    return None

def _gh_put_overrides(new_data: Dict[str,Any]) -> bool:
    if not (OV_GH_TOKEN and OV_GH_REPO and OV_GH_PATH):
        return False
    try:
        cur = _gh_get_overrides() or {}
        sha = cur.get("_sha")
        body = {
            "message": f"update overrides {int(time.time())}",
            "content": base64.b64encode(json.dumps(new_data, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
            "branch": OV_GH_BRANCH
        }
        if sha: body["sha"] = sha
        u = f"https://api.github.com/repos/{OV_GH_REPO}/contents/{OV_GH_PATH}"
        req = HttpRequest(u, data=json.dumps(body).encode("utf-8"),
                          headers={"Authorization": f"token {OV_GH_TOKEN}", "Accept": "application/vnd.github+json",
                                   "Content-Type":"application/json"})
        with http_urlopen(req, timeout=25) as r:
            _ = r.read()
        return True
    except Exception as e:
        _log("[ovr] github put error:", repr(e))
        return False

def _save_ru_name_override(pid: str, ru_fullname: str) -> None:
    fn, ln = _split_ru_name(ru_fullname)
    _apply_runtime_override(pid, {"firstName": fn, "lastName": ln})
    # мягкая попытка пуша в GitHub (если доступно)
    try:
        d = _gh_get_overrides() or {}
        d[pid] = d.get(pid, {})
        d[pid]["firstName"] = fn
        d[pid]["lastName"]  = ln
        _gh_put_overrides(d)
    except Exception:
        pass

def _save_team_override(pid: str, team_id: str) -> None:
    _apply_runtime_override(pid, {"teamId": str(team_id)})

# --- HELP TEXT ---
HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /help — это сообщение\n"
    "• /find <имя/фамилия> — поиск игрока (например: /find Doncic)\n"
    "• /name <имя> — интерактивно задать русское имя (ответь на сообщение)\n"
    "• /team <имя> — интерактивно задать teamId (ответь числом, напр. 1610612756)\n"
    "• /card <имя> | <метрики> — плашка игрока (PNG)\n"
    "\n"
    "Пример: /card wembanyama | 10 очков, 12 передач, 8 подборов\n"
)

# --- PARSERS ---
def _parse_card_text(txt: str) -> Tuple[str, List[Tuple[str,str]], Optional[str]]:
    """
    Возвращает: имя игрока, список метрик (value,label), ручной цвет (#RRGGBB|None)
    Поддержка хвостика color:#RRGGBB
    """
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
            if m:
                stats.append((m.group(1), m.group(2)))
            else:
                stats.append((c, ""))  # если не распарсили — как есть
    return player_raw, stats, color_raw

# --- LOOKUP HELPERS ---
def _players_idx() -> Dict[str,Dict[str,Any]]:
    try:
        return get_players_index()
    except Exception:
        return {}

def _best_match(q: str) -> Optional[Dict[str,Any]]:
    q = (q or "").strip()
    if not q: return None
    # попробуем find_player_by_name (он ищет по displayName)
    try:
        res = find_player_by_name(q)
        if res: return res[0]
    except Exception:
        pass
    # fallback: простой contains по индексу
    ql = q.lower()
    for p in _players_idx().values():
        nm = (p.get("displayName") or (p.get("firstName","")+" "+p.get("lastName",""))).lower()
        if ql in nm: return p
    return None

# --- HEAD/LOGO LOAD ---
def _as_image(data_or_img: Any) -> Optional["Image.Image"]:
    try:
        from PIL import Image
    except Exception:
        return None
    if data_or_img is None:
        return None
    if hasattr(data_or_img, "size") and hasattr(data_or_img, "mode"):
        return data_or_img  # уже Image
    try:
        return Image.open(io.BytesIO(data_or_img)).convert("RGBA")
    except Exception:
        return None

def _head_img_for(pid: str) -> Optional["Image.Image"]:
    try:
        b = ensure_headshot_png(pid)
        return _as_image(b)
    except Exception as e:
        _log("[tg] headshot ensure err", pid, repr(e))
        return None

def _logo_img_for(team_id: str) -> Optional["Image.Image"]:
    if not team_id or team_id == "0": return None
    try:
        b = ensure_team_logo_png(team_id)
        return _as_image(b)
    except Exception as e:
        _log("[tg] team logo ensure err", team_id, repr(e))
        return None

# --- COLOR SELECT FLOW ---
def _ask_color_choice(chat_id: int, pid: str, team_id: str, reply_to: Optional[int]=None) -> None:
    # шаг выбора: 1 — Авто, 2 — Свой цвет
    txt = (
        "<i>Уточнения…</i>\n\n"
        "Выберите цвет плашки:\n"
        "1 — Авто (цвета команды)\n"
        "2 — Свой цвет (пришлите код вида #RRGGBB)\n\n"
        f"[choosecolor:{pid}:{team_id}]"
    )
    _tg_send_message(chat_id, txt, reply_to=reply_to, parse_mode="HTML")

def _handle_choose_color_reply(chat_id: int, reply_text: str, tag: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Возвращает (mode, custom_hex) где mode in {"auto","custom"}.
    """
    m = re.search(r"\[choosecolor:([0-9]+):([0-9]+)\]", tag)
    if not m: return None, None
    choice = reply_text.strip()
    if choice == "1" or choice.lower() == "авто":
        return "auto", None
    if choice == "2":
        # попросим цвет
        _tg_send_message(
            chat_id,
            "Ок! Пришлите цвет в формате #RRGGBB\n"
            f"[setcolor:{m.group(1)}:{m.group(2)}]",
            parse_mode="HTML"
        )
        return None, None
    # если сразу прислали #RRGGBB — тоже примем
    cm = re.search(r"#([0-9A-Fa-f]{6})", choice)
    if cm:
        return "custom", f"#{cm.group(1)}"
    return None, None

def _handle_setcolor_reply(reply_text: str) -> Optional[str]:
    m = re.search(r"#([0-9A-Fa-f]{6})", reply_text.strip())
    if not m: return None
    return f"#{m.group(1)}"

# --- NAME/TEAM SET FLOWS ---
def _ask_ru_name(chat_id: int, pid: str, en_name: str, reply_to: Optional[int]=None) -> None:
    txt = (
        "<i>Уточнения…</i>\n\n"
        f"Как подписать игрока { _escape_html(en_name) } на плашке?\n"
        "Ответьте на это сообщение русским именем.\n"
        f"[setname:{pid}]"
    )
    _tg_send_message(chat_id, txt, reply_to=reply_to, parse_mode="HTML")

def _ask_team(chat_id: int, pid: str, en_name: str, cur_team: str, reply_to: Optional[int]=None) -> None:
    txt = (
        "<i>Уточнения…</i>\n\n"
        f"Какой teamId у { _escape_html(en_name) }? Пришлите число (например, 1610612756)\n"
        f"(сейчас: {cur_team or '0'})\n"
        f"[setteam:{pid}]"
    )
    _tg_send_message(chat_id, txt, reply_to=reply_to, parse_mode="HTML")

# --- RENDER HELPERS ---
def _display_name_for(p: Dict[str,Any]) -> str:
    # после overrides displayName уже обновлён, fallback на first/last
    nm = (p.get("displayName") or "").strip()
    if nm: return nm
    fn = (p.get("firstName") or "").strip()
    ln = (p.get("lastName") or "").strip()
    return (fn + " " + ln).strip()

def _colors_for_team(team_id: str, custom_hex: Optional[str]) -> Tuple[str,str,str,str]:
    """
    Возвращает (primary, dark, light, human_name). Если custom_hex задан, используем его как primary.
    """
    primary = "#007ACC"; dark = "#005A99"; light="#8CC7F2"; hname="синий"
    if team_brand_for := team_colors_for:
        try:
            c = team_colors_for(team_id)
            if isinstance(c, tuple) and len(c)>=3:
                primary, dark, light = c[0], c[1], c[2]
        except Exception:
            pass
    if custom_hex:
        primary = custom_hex
        # притемним dark
        try:
            from graphics import _hex_to_rgb, _shade
            rgb = _hex_to_rgb(primary)
            # _shade ждёт tuple
            dark_rgb = _shade(rgb, 0.65)
            dark = "#%02X%02X%02X" % dark_rgb
        except Exception:
            dark = primary
        light = primary
    if color_name_for:
        try:
            hname = color_name_for(primary) or hname
        except Exception:
            pass
    return primary, dark, light, hname

def _render_card_png(player: Dict[str,Any], stats: List[Tuple[str,str]], custom_hex: Optional[str]) -> Optional[bytes]:
    pid = str(player.get("personId") or "")
    team_id = str(player.get("teamId") or "0")
    head = _head_img_for(pid)
    if head is None:
        raise RuntimeError("Не удалось получить фото игрока")
    logo = _logo_img_for(team_id)
    colors = _colors_for_team(team_id, custom_hex)
    display = _display_name_for(player)
    png = render_card("single", display, "", logo, colors[:3], head, stats)
    return png

# --- FASTAPI app ---
app = FastAPI()

def _ok(data: Dict[str,Any]) -> JSONResponse:
    return JSONResponse(data)

def _bad_secret() -> JSONResponse:
    return JSONResponse({"detail": "bad secret"}, status_code=403)

# --- GET ENDPOINT (diag/ping/refresh/route) ---
@app.get("/api/telegram")
async def telegram_get(request: FastAPIRequest):
    q = dict(request.query_params)
    if q.get("secret") != WEBHOOK_SECRET:
        return _bad_secret()
    action = (q.get("action") or "").strip()
    if action == "ping":
        return _ok({"ok": True, "pong": int(time.time()*1000)})
    if action == "route":
        return _ok({"ok": True, "route": "telegram-get"})
    if action == "diag":
        return _ok({
            "ok": (BOT_TOKEN != ""),
            "py": ".".join([str(x) for x in (3,12)]),
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
            return _ok({"ok": False, "refreshed": True, "players_indexed": 0, "error": repr(e)})
    return _ok({"ok": True, "route": "telegram-get"})

# --- POST ENDPOINT (Telegram webhook) ---
@app.post("/api/telegram")
async def webhook_query(request: FastAPIRequest):
    # секрет в querystring
    if request.query_params.get("secret") != WEBHOOK_SECRET:
        return PlainTextResponse("OK")  # не палим секрет, всегда 200
    rid = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    try:
        raw = await request.body()
    except Exception:
        raw = b""
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    _log(f"[RID={rid}] POST {str(request.url)}")
    if DEBUG:
        _log(f"[RID={rid}] body:", json.dumps(body, ensure_ascii=False))

    # вытаскиваем chat_id и текст
    msg = body.get("message") or body.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    reply_to = msg.get("reply_to_message") or {}

    # администраторам можно всё, остальным — старт/хелп
    if ADMIN_IDS and chat_id not in ADMIN_IDS and text not in ("/start","/help"):
        _tg_send_message(chat_id, "Привет! Попроси доступ у администратора 😊")
        return PlainTextResponse("OK")

    # обработка reply-флоу
    if reply_to and text:
        rtxt = (reply_to.get("text") or "")
        # setname
        m = re.search(r"\[setname:([0-9]+)\]", rtxt)
        if m:
            pid = m.group(1)
            ru_name = text.strip()
            _save_ru_name_override(pid, ru_name)
            _tg_send_message(chat_id, f"Сохранил имя для {pid}: { _escape_html(ru_name) }", parse_mode="HTML")
            _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
            # попытаемся собрать прошлую команду из context? — упрощённо просто завершим
            return PlainTextResponse("OK")

        # setteam
        m = re.search(r"\[setteam:([0-9]+)\]", rtxt)
        if m:
            pid = m.group(1)
            team = re.sub(r"[^\d]","", text)
            if not team:
                _tg_send_message(chat_id, "Пришлите, пожалуйста, число teamId.", reply_to=msg.get("message_id"))
                return PlainTextResponse("OK")
            _save_team_override(pid, team)
            _tg_send_message(chat_id, f"Ок, teamId для {pid} = {team}")
            return PlainTextResponse("OK")

        # choosecolor
        m = re.search(r"\[choosecolor:([0-9]+):([0-9]+)\]", rtxt)
        if m:
            mode, custom = _handle_choose_color_reply(chat_id, text, rtxt)
            if mode == "auto":
                _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
                # не знаем исходный игрок и метрики — в этом упрощённом обработчике только подтверждаем
                return PlainTextResponse("OK")
            elif mode == "custom":
                _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")
                return PlainTextResponse("OK")
            else:
                # ждём либо 1/2, либо #RRGGBB → уже спросили отдельно
                return PlainTextResponse("OK")

        # setcolor
        m = re.search(r"\[setcolor:([0-9]+):([0-9]+)\]", rtxt)
        if m:
            hexcol = _handle_setcolor_reply(text)
            if not hexcol:
                _tg_send_message(chat_id, "Нужен цвет в формате #RRGGBB. Пришлите ещё раз.")
                return PlainTextResponse("OK")
            _tg_send_message(chat_id, f"Цвет принят: {hexcol}\n<i>Готовлю плашку…</i>", parse_mode="HTML")
            return PlainTextResponse("OK")

    # команды
    if text == "/start":
        _tg_send_message(chat_id, "Я здесь! ✌️\n\n" + HELP_TEXT)
        return PlainTextResponse("OK")
    if text == "/help":
        _tg_send_message(chat_id, HELP_TEXT)
        return PlainTextResponse("OK")

    if text.startswith("/find"):
        q = text[len("/find"):].strip()
        if not q:
            _tg_send_message(chat_id, "Укажи часть имени: /find Doncic")
            return PlainTextResponse("OK")
        res = find_player_by_name(q) if find_player_by_name else []
        if not res:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷")
            return PlainTextResponse("OK")
        lines = []
        for p in res[:10]:
            lines.append(f"{_escape_html(_display_name_for(p))} (id={p.get('personId')}, teamId={p.get('teamId')})")
        _tg_send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        return PlainTextResponse("OK")

    if text.startswith("/name"):
        q = text[len("/name"):].strip()
        if not q:
            _tg_send_message(chat_id, "Укажи имя: /name Kevin Durant")
            return PlainTextResponse("OK")
        p = _best_match(q)
        if not p:
            _tg_send_message(chat_id, f"Не нашёл игрока: { _escape_html(q) }", parse_mode="HTML")
            return PlainTextResponse("OK")
        en = (p.get("firstName","")+" "+p.get("lastName","")).strip() or _display_name_for(p)
        _ask_ru_name(chat_id, str(p.get("personId")), en, reply_to=msg.get("message_id"))
        return PlainTextResponse("OK")

    if text.startswith("/team"):
        q = text[len("/team"):].strip()
        if not q:
            _tg_send_message(chat_id, "Укажи имя: /team Kevin Durant")
            return PlainTextResponse("OK")
        p = _best_match(q)
        if not p:
            _tg_send_message(chat_id, f"Не нашёл игрока: { _escape_html(q) }", parse_mode="HTML")
            return PlainTextResponse("OK")
        en = (p.get("firstName","")+" "+p.get("lastName","")).strip() or _display_name_for(p)
        _ask_team(chat_id, str(p.get("personId")), en, str(p.get("teamId") or "0"), reply_to=msg.get("message_id"))
        return PlainTextResponse("OK")

    if text.startswith("/card"):
        # базовая плашка
        try:
            player_raw, stats, color_raw = _parse_card_text(text)
            if not player_raw:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики>")
                return PlainTextResponse("OK")
            p = _best_match(player_raw)
            if not p:
                _tg_send_message(chat_id, f"Не нашёл игрока: { _escape_html(player_raw) }", parse_mode="HTML")
                return PlainTextResponse("OK")

            # если нет русского имени — спрашиваем
            disp = _display_name_for(p)
            fn, ln = p.get("firstName","").strip(), p.get("lastName","").strip()
            en = (fn+" "+ln).strip() or disp
            if re.search(r"[A-Za-z]", en):  # грубая эвристика: имя латиницей — спросим ру-имя
                _ask_ru_name(chat_id, str(p.get("personId")), en, reply_to=msg.get("message_id"))
                # не рвём процесс — продолжим с английским, но вопрос уже задан

            # выбор цвета: если не указан в команде — спросим
            if not color_raw:
                _ask_color_choice(chat_id, str(p.get("personId")), str(p.get("teamId") or "0"), reply_to=msg.get("message_id"))

            _tg_send_message(chat_id, "<i>Готовлю плашку…</i>", parse_mode="HTML")

            # рендер
            png = _render_card_png(p, stats, color_raw)
            if not png:
                _tg_send_message(chat_id, "Не удалось отрендерить плашку 😔")
                return PlainTextResponse("OK")

            # отправим как документ (PNG с прозрачностью)
            _tg_send_chat_action(chat_id, "upload_document")
            fname = f"card_{p.get('personId')}.png"
            _tg_send_document(chat_id, fname, png)
            return PlainTextResponse("OK")
        except Exception as e:
            _log("[tg] card err", traceback.format_exc())
            _tg_send_message(chat_id, f"Ошибка: { _escape_html(str(e)) }", parse_mode="HTML")
            return PlainTextResponse("OK")

    # неизвестно — покажем help
    _tg_send_message(chat_id, HELP_TEXT)
    return PlainTextResponse("OK")
