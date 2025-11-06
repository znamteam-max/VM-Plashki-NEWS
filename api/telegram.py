# api/telegram.py — интерактивные уточнения (RU-имя, цвет), статусы и все типы плашек
from __future__ import annotations
import json, os, re, time, uuid, traceback
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError
from PIL import Image

# --- внешние модули проекта ---
from data import (
    refresh_players, get_players, get_players_index, find_player_by_name,
    ensure_headshot_png, open_headshot_variants, display_name_for,
    set_player_ru_name, set_player_team, set_player_alias, get_overrides,
)
from team_brand import (
    get_team_brand, set_team_primary_color, get_team_logo_path,
    color_name_ru
)
from graphics import (
    render_card, render_card2, render_card_bad, render_card_dr, render_card_special,
    render_card_drN
)

# ------------------- конфиг -------------------
app = FastAPI()

BOT_TOKEN         = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET    = os.getenv("WEBHOOK_SECRET", "").strip()
TEAM_LOGO_DIR     = os.getenv("TEAM_LOGO_DIR", "assets/cache")
PLACEHOLDER_HEAD  = os.getenv("PLACEHOLDER_HEAD", "assets/placeholders/head.png")
DR_TEMPLATES_DIR  = os.getenv("DR_TEMPLATES_DIR", "assets/templates")

STATE_PATH = "/tmp/tg_state.json"

# ------------------- утилиты -------------------
def _log(*args: Any) -> None:
    try: print("[tg]", *args, flush=True)
    except: pass

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH): return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except: pass

def _set_dialog(chat_id: int, obj: Dict[str, Any]):
    st = _load_state(); st[str(chat_id)] = obj; _save_state(st)

def _get_dialog(chat_id: int) -> Optional[Dict[str, Any]]:
    return _load_state().get(str(chat_id))

def _clear_dialog(chat_id: int):
    st = _load_state()
    if str(chat_id) in st:
        del st[str(chat_id)]
        _save_state(st)

def _tg_base() -> str:
    if not BOT_TOKEN:
        return "https://api.telegram.org/bot"
    return f"https://api.telegram.org/bot{BOT_TOKEN}"

def _safe_http_json(url: str, body: Optional[bytes], headers: Dict[str, str], method: str, timeout: int = 25) -> Dict[str, Any]:
    try:
        req = UrlRequest(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        desc = ""
        try:
            raw = e.read(); desc = raw.decode("utf-8", errors="ignore")
        except Exception: pass
        _log("HTTPError", e.code, url, desc[:300])
        try:
            js = json.loads(desc)
            return js if isinstance(js, dict) else {"ok": False, "status": e.code, "description": desc}
        except Exception:
            return {"ok": False, "status": e.code, "description": desc or str(e)}
    except URLError as e:
        _log("URLError", repr(e), url)
        return {"ok": False, "error": repr(e)}
    except Exception as e:
        _log("EXC", repr(e), url)
        return {"ok": False, "error": repr(e)}

def _tg_post_json(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{_tg_base()}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _safe_http_json(url, body, {"Content-Type":"application/json"}, "POST")

def _tg_send_action(chat_id: int, action: str) -> None:
    try:
        _tg_post_json("sendChatAction", {"chat_id": chat_id, "action": action})
    except Exception as e:
        _log("sendChatAction err", e)

def _tg_send_message_safe(chat_id: int, text: str, reply_to_message_id: Optional[int]=None,
                          reply_markup: Optional[Dict[str,Any]]=None, parse_mode: Optional[str]="HTML") -> Dict[str, Any]:
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id: payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup: payload["reply_markup"] = reply_markup
    if parse_mode: payload["parse_mode"] = parse_mode
    res = _tg_post_json("sendMessage", payload)
    if not res.get("ok"):
        desc = (res.get("description") or "").lower()
        if "parse" in desc or "entity" in desc:
            payload.pop("parse_mode", None)
            res2 = _tg_post_json("sendMessage", payload)
            if res2.get("ok"): return res2
        if "too many requests" in desc or res.get("status") == 429:
            time.sleep(1.2)
            res3 = _tg_post_json("sendMessage", payload)
            return res3
    return res

def _tg_edit_message_text_safe(chat_id: int, message_id: int, text: str, parse_mode: Optional[str]="HTML"):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    res = _tg_post_json("editMessageText", payload)
    if not res.get("ok"):
        _tg_send_message_safe(chat_id, text, parse_mode=parse_mode)
    return res

def _build_multipart(boundary: str, fields: Dict[str, str], files: List[Tuple[str, str, str, bytes]]) -> bytes:
    parts: List[bytes] = []
    def add_field(name: str, value: str):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode()); parts.append(b"\r\n")
    def add_file(name: str, filename: str, mime: str, data: bytes):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
        parts.append(data); parts.append(b"\r\n")
    for k,v in fields.items(): add_field(k,v)
    for (name, filename, mime, data) in files: add_file(name, filename, mime, data)
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)

def _tg_send_document_png_safe(chat_id: int, png_bytes: bytes, filename: str = "card.png", caption: Optional[str]=None) -> Dict[str, Any]:
    url = f"{_tg_base()}/sendDocument"
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    fields = {"chat_id": str(chat_id)}
    if caption: fields["caption"] = caption
    body = _build_multipart(boundary, fields, [("document", filename, "image/png", png_bytes)])
    try:
        req = UrlRequest(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        with urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            try: return json.loads(raw)
            except: return {"ok": True}
    except Exception as e:
        _log("sendDocument err", repr(e))
        return {"ok": False, "error": repr(e)}

# --------------- вспомогательное ---------------
def _parse_stats_chunk(chunk: str) -> List[Tuple[str,str]]:
    items = [s.strip() for s in chunk.split(",") if s.strip()]
    out: List[Tuple[str,str]] = []
    for it in items:
        m = re.match(r"^\s*([+\-]?\d+(?:[.,]\d+)?)\s+(.+?)\s*$", it)
        if m:
            val = m.group(1).replace(",", ".")
            lab = m.group(2)
            out.append((val, lab))
        else:
            out.append((it, ""))
    return out

def _load_logo_img(team_id: str) -> Optional[Image.Image]:
    p = get_team_logo_path(team_id)
    if p and os.path.exists(p):
        try: return Image.open(p).convert("RGBA")
        except: pass
    guess = os.path.join(TEAM_LOGO_DIR, f"{team_id}.png")
    if os.path.exists(guess):
        try: return Image.open(guess).convert("RGBA")
        except: pass
    return None

def _load_head_img(person_id: str) -> Image.Image:
    try:
        pth = ensure_headshot_png(person_id)
        if pth and os.path.exists(pth):
            return Image.open(pth).convert("RGBA")
    except Exception as e:
        _log("headshot ensure err", person_id, e)
    try:
        pth = ensure_headshot_png({"personId": person_id})
        if pth and os.path.exists(pth):
            return Image.open(pth).convert("RGBA")
    except Exception as e:
        _log("headshot ensure (dict) err", person_id, e)
    try:
        variants = open_headshot_variants(person_id) or []
        for p in variants:
            if p and os.path.exists(p):
                return Image.open(p).convert("RGBA")
    except Exception as e:
        _log("headshot variants err", person_id, e)
    try:
        import io
        url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{person_id}.png"
        req = UrlRequest(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urlopen(req, timeout=8) as r:
            data = r.read()
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        _log("headshot cdn err", person_id, e)
    try:
        return Image.open(PLACEHOLDER_HEAD).convert("RGBA")
    except Exception:
        return Image.new("RGBA", (512, 512), (0, 0, 0, 0))

def _status(chat_id: int, text_html: str, reply_to: Optional[int]=None) -> Optional[int]:
    _tg_send_action(chat_id, "typing")
    r = _tg_send_message_safe(chat_id, f"<i>{text_html}</i>", reply_to_message_id=reply_to, parse_mode="HTML")
    try:
        return r["result"]["message_id"] if r.get("ok") else None
    except: return None

def _status_edit(chat_id: int, msg_id: Optional[int], text_html: str):
    if not msg_id:
        _tg_send_message_safe(chat_id, f"<i>{text_html}</i>", parse_mode="HTML")
    else:
        _tg_edit_message_text_safe(chat_id, msg_id, f"<i>{text_html}</i>", parse_mode="HTML")

def _display_has_cyrillic(name: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", name or ""))

def _need_ru_name(p: Dict[str,Any]) -> bool:
    disp = display_name_for(p)
    return not _display_has_cyrillic(disp)

def _parse_color_answer(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t: return None
    low = t.lower()
    if low in ("авто", "auto", "по умолчанию"):
        return None
    m = re.search(r"#([0-9a-fA-F]{6})", t)
    if m:
        return "#" + m.group(1)
    return None

def _color_tuple(custom_hex: Optional[str], team_id: str) -> Tuple[str,str,str]:
    if custom_hex:
        return (custom_hex, custom_hex, custom_hex)
    brand = get_team_brand(team_id)
    # универсальный парсер (dict/tuple/list/str) уже есть в graphics, но здесь дадим простой вариант:
    if isinstance(brand, dict):
        return (brand.get("primary") or "#007ACC", brand.get("dark") or "#007ACC", brand.get("light") or "#007ACC")
    if isinstance(brand, (tuple, list)) and len(brand) >= 1:
        p = str(brand[0]); d = str(brand[1] if len(brand)>1 else p); l = str(brand[2] if len(brand)>2 else p)
        return (p,d,l)
    if isinstance(brand, str):
        hx = re.findall(r'#[0-9A-Fa-f]{6}', brand)
        if hx:
            p = hx[0]; d = hx[1] if len(hx)>1 else p; l = hx[2] if len(hx)>2 else p
            return (p,d,l)
    return ("#007ACC", "#005A99", "#78C3FF")

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /find <имя/фамилия> — найти игрока (например: /find Doncic)\n"
    "• /card <имя> | <метрики> — обычная плашка (перед рендером уточню RU-имя и цвет)\n"
    "• /card2 <имя1> | <метрики1> | <имя2> | <метрики2> — парная (уточню RU-имена и цвет)\n"
    "• /cardBAD <имя> | <метрики> — сыграл плохо 💩 (фикс. цвет, без лого команды)\n"
    "• /cardS <имя> | <метрики> | <инфо> — спец-плашка с доп. блоком\n"
    "• /cardDR3|/cardDR4|/cardDR5 <имя> | <метрики...> — шаблоны DR\n"
    "• /name <имя> — задать русское имя интерактивно (ответьте на сообщение)\n"
    "• /team <имя> — задать/переопределить teamId (ответьте числом)\n"
)

# ------------------- маршруты -------------------
@app.get("/api/telegram")
async def webhook_get(secret: str, action: Optional[str] = None):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="bad secret")
    if action == "refresh":
        cnt, info = refresh_players()
        ok = info.get("ok", False)
        return JSONResponse({"ok": ok, "refreshed": True, "players_indexed": cnt, **info})
    return JSONResponse({"ok": True, "route": "telegram-get"})

@app.post("/api/telegram")
async def webhook_query(request: Request, secret: str):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="bad secret")

    update = await request.json()
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg: return PlainTextResponse("OK")
    chat_id = int(msg["chat"]["id"])
    msg_id  = int(msg["message_id"])
    text    = msg.get("text") or ""

    try:
        # базовые
        if not text or text.strip() == "/start":
            _tg_send_message_safe(chat_id, "Я тут. Отправьте /help для списка команд.")
            return PlainTextResponse("OK")
        if text.strip().startswith("/help"):
            _tg_send_message_safe(chat_id, HELP_TEXT)
            return PlainTextResponse("OK")
        if text.strip().startswith("/find"):
            q = text.split(" ",1)[1].strip() if " " in text else ""
            if not q:
                _tg_send_message_safe(chat_id, "Укажите имя: /find Doncic")
                return PlainTextResponse("OK")
            p = _find_best_player(q)
            if not p:
                _tg_send_message_safe(chat_id, f"Не нашёл игрока: {q}")
                return PlainTextResponse("OK")
            disp = display_name_for(p)
            _tg_send_message_safe(chat_id, f"{disp} (id={p['personId']}, teamId={p.get('teamId','0')})")
            return PlainTextResponse("OK")

        # ---------- интерактивные ответы (reply) ----------
        if msg.get("reply_to_message") and text:
            reply_text = msg["reply_to_message"].get("text") or ""
            # RU-имя
            if "[setname:" in reply_text:
                m = re.search(r"\[setname:(\d+)\]", reply_text)
                if m:
                    pid = m.group(1)
                    ru = text.strip()
                    set_player_ru_name(pid, ru)
                    # если в состоянии есть оп — продолжаем к шагу выбора цвета
                    st = _get_dialog(chat_id) or {}
                    st["ru_ok_for"] = st.get("ru_ok_for", []) + [pid]
                    _set_dialog(chat_id, st)
                    _tg_send_message_safe(chat_id, f"Имя сохранено: {ru}")
                    # Есть ли ещё кто-то без RU (для card2)?
                    if st.get("op") == "card2":
                        pending = [x for x in st.get("players", []) if x not in st.get("ru_ok_for", [])]
                        if pending:
                            pid2 = pending[0]
                            _tg_send_message_safe(chat_id, f"Как подписать игрока {st.get('ask_display_'+pid2)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid2}]")
                            return PlainTextResponse("OK")
                    # Переходим к цвету
                    _tg_send_message_safe(chat_id, "Цвет плашки: пришлите #RRGGBB или напишите «Авто».\n[setcolor]")
                    st["await"] = "color"
                    _set_dialog(chat_id, st)
                    return PlainTextResponse("OK")
            # Цвет
            if "[setcolor" in reply_text:
                st = _get_dialog(chat_id) or {}
                custom = _parse_color_answer(text)
                st["custom_hex"] = custom
                _set_dialog(chat_id, st)
                # рендер в зависимости от op
                op = st.get("op")
                try:
                    if op == "card":
                        p = st["p"]
                        head = _load_head_img(p["personId"])
                        colors = _color_tuple(custom, p.get("teamId", "0"))
                        logo  = _load_logo_img(p.get("teamId","0"))
                        stats = _parse_stats_chunk(st["stats_raw"])
                        disp  = display_name_for(p)
                        _tg_send_action(chat_id, "upload_document")
                        png = render_card("single", disp, "", logo, colors, head, stats)
                        _tg_send_document_png_safe(chat_id, png, "card.png")
                        _clear_dialog(chat_id)
                        return PlainTextResponse("OK")
                    if op == "card2":
                        p1, p2 = st["p1"], st["p2"]
                        head1 = _load_head_img(p1["personId"]); head2 = _load_head_img(p2["personId"])
                        # если custom — применим к обоим
                        colors1 = _color_tuple(custom, p1.get("teamId","0"))
                        colors2 = _color_tuple(custom, p2.get("teamId","0"))
                        logo1 = _load_logo_img(p1.get("teamId","0"))
                        logo2 = _load_logo_img(p2.get("teamId","0"))
                        stats1 = _parse_stats_chunk(st["stats1"])
                        stats2 = _parse_stats_chunk(st["stats2"])
                        disp1 = display_name_for(p1); disp2 = display_name_for(p2)
                        _tg_send_action(chat_id, "upload_document")
                        png = render_card2(disp1, logo1, colors1, head1, stats1,
                                           disp2, logo2, colors2, head2, stats2)
                        _tg_send_document_png_safe(chat_id, png, "card2.png")
                        _clear_dialog(chat_id)
                        return PlainTextResponse("OK")
                    if op == "cardS":
                        p = st["p"]
                        head = _load_head_img(p["personId"])
                        colors = _color_tuple(custom, p.get("teamId", "0"))
                        logo  = _load_logo_img(p.get("teamId","0"))
                        stats = _parse_stats_chunk(st["stats_raw"])
                        info  = st["info"]
                        disp  = display_name_for(p)
                        _tg_send_action(chat_id, "upload_document")
                        png = render_card_special(disp, logo, colors, head, stats, info)
                        _tg_send_document_png_safe(chat_id, png, "card_special.png")
                        _clear_dialog(chat_id)
                        return PlainTextResponse("OK")
                except Exception as e:
                    _log("render after color err", traceback.format_exc())
                    _tg_send_message_safe(chat_id, f"<i>Ошибка:</i> {e}")
                    _clear_dialog(chat_id)
                    return PlainTextResponse("OK")

        # ---------- card2 ----------
        if text.startswith("/card2"):
            st_id = _status(chat_id, "Уточнения…", reply_to=msg_id)
            try:
                parts = [s.strip() for s in text[len("/card2"):].split("|")]
                if len(parts) < 4:
                    _status_edit(chat_id, st_id, "Ошибка: формат. Пример: <code>/card2 Игрок1 | 10 очков | Игрок2 | 8 передач</code>")
                    return PlainTextResponse("OK")
                n1, s1, n2, s2 = parts[0], parts[1], parts[2], parts[3]
                p1 = _find_best_player(n1); p2 = _find_best_player(n2)
                if not p1 or not p2:
                    _status_edit(chat_id, st_id, "Ошибка: один из игроков не найден.")
                    return PlainTextResponse("OK")
                st = {"op":"card2", "players":[p1["personId"], p2["personId"]], "p1":p1, "p2":p2, "stats1":s1, "stats2":s2, "ru_ok_for":[]}
                st["ask_display_"+p1["personId"]] = display_name_for(p1)
                st["ask_display_"+p2["personId"]] = display_name_for(p2)
                _set_dialog(chat_id, st)
                # RU-имена по очереди
                need = []
                if _need_ru_name(p1): need.append(p1["personId"])
                if _need_ru_name(p2): need.append(p2["personId"])
                if need:
                    pid = need[0]
                    _status_edit(chat_id, st_id, "Уточнения…")
                    _tg_send_message_safe(chat_id, f"Как подписать игрока {display_name_for(p1) if pid==p1['personId'] else display_name_for(p2)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]")
                    return PlainTextResponse("OK")
                # сразу к цвету
                _status_edit(chat_id, st_id, "Уточнения…")
                _tg_send_message_safe(chat_id, "Цвет плашки: пришлите #RRGGBB или напишите «Авто».\n[setcolor]")
                st["await"] = "color"
                _set_dialog(chat_id, st)
            except Exception as e:
                _log("card2 err", traceback.format_exc())
                _status_edit(chat_id, st_id, f"Ошибка: {e}")
            return PlainTextResponse("OK")

        # ---------- cardBAD ----------
        if text.startswith("/cardBAD"):
            st_id = _status(chat_id, "Готовлю плашку…", reply_to=msg_id)
            try:
                parts = [s.strip() for s in text[len("/cardBAD"):].split("|")]
                if len(parts) < 2:
                    _status_edit(chat_id, st_id, "Ошибка: формат. Пример: <code>/cardBAD Игрок | 2 очка, 6 потерь</code>")
                    return PlainTextResponse("OK")
                name_q, stats_raw = parts[0], parts[1]
                p = _find_best_player(name_q)
                if not p:
                    _status_edit(chat_id, st_id, f"Ошибка: не нашёл {name_q}")
                    return PlainTextResponse("OK")
                # RU-имя (если нет — спросим и продолжим после ответа)
                if _need_ru_name(p):
                    st = {"op":"cardBAD", "p":p, "stats_raw":stats_raw}
                    _set_dialog(chat_id, st)
                    _status_edit(chat_id, st_id, "Уточнения…")
                    _tg_send_message_safe(chat_id, f"Как подписать игрока {display_name_for(p)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p['personId']}]")
                    return PlainTextResponse("OK")
                head = _load_head_img(p["personId"])
                stats = _parse_stats_chunk(stats_raw)
                png = render_card_bad(display_name_for(p), head, stats)
                _tg_send_action(chat_id, "upload_document")
                _tg_send_document_png_safe(chat_id, png, "card_bad.png")
                _status_edit(chat_id, st_id, "Готово ✅")
            except Exception as e:
                _log("cardBAD err", traceback.format_exc())
                _status_edit(chat_id, st_id, f"Ошибка: {e}")
            return PlainTextResponse("OK")

        # ---------- cardS ----------
        if text.startswith("/cardS"):
            st_id = _status(chat_id, "Уточнения…", reply_to=msg_id)
            try:
                parts = [s.strip() for s in text[len("/cardS"):].split("|")]
                if len(parts) < 3:
                    _status_edit(chat_id, st_id, "Ошибка: формат. Пример: <code>/cardS Игрок | 28 очков | сломал серию</code>")
                    return PlainTextResponse("OK")
                name_q, stats_raw, info = parts[0], parts[1], parts[2]
                p = _find_best_player(name_q)
                if not p:
                    _status_edit(chat_id, st_id, f"Ошибка: не нашёл {name_q}")
                    return PlainTextResponse("OK")
                st = {"op":"cardS", "p":p, "stats_raw":stats_raw, "info":info}
                _set_dialog(chat_id, st)
                # если нет RU — спросим
                if _need_ru_name(p):
                    _tg_send_message_safe(chat_id, f"Как подписать игрока {display_name_for(p)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p['personId']}]")
                    return PlainTextResponse("OK")
                # сразу к цвету
                _tg_send_message_safe(chat_id, "Цвет плашки: пришлите #RRGGBB или напишите «Авто».\n[setcolor]")
                st["await"] = "color"
                _set_dialog(chat_id, st)
            except Exception as e:
                _log("cardS err", traceback.format_exc())
                _status_edit(chat_id, st_id, f"Ошибка: {e}")
            return PlainTextResponse("OK")

        # ---------- card (single) ----------
        if text.startswith("/card"):
            st_id = _status(chat_id, "Уточнения…", reply_to=msg_id)
            try:
                body = text[len("/card"):].strip()
                parts = [s.strip() for s in body.split("|")]
                if len(parts) < 2:
                    _status_edit(chat_id, st_id, "Ошибка: формат. Пример: <code>/card Игрок | 10 очков, 12 передач</code>")
                    return PlainTextResponse("OK")
                name_q, stats_raw = parts[0], parts[1]
                p = _find_best_player(name_q)
                if not p:
                    _status_edit(chat_id, st_id, f"Ошибка: не нашёл {name_q}")
                    return PlainTextResponse("OK")
                st = {"op":"card", "p":p, "stats_raw":stats_raw}
                _set_dialog(chat_id, st)
                # если нет RU — спросим
                if _need_ru_name(p):
                    _tg_send_message_safe(chat_id, f"Как подписать игрока {display_name_for(p)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p['personId']}]")
                    return PlainTextResponse("OK")
                # сразу к цвету
                _tg_send_message_safe(chat_id, "Цвет плашки: пришлите #RRGGBB или напишите «Авто».\n[setcolor]")
                st["await"] = "color"
                _set_dialog(chat_id, st)
            except Exception as e:
                _log("card err", traceback.format_exc())
                _status_edit(chat_id, st_id, f"Ошибка: {e}")
            return PlainTextResponse("OK")

        # ---------- name/team ручные ----------
        if text.startswith("/name"):
            q = text.split(" ",1)[1].strip() if " " in text else ""
            if not q:
                _tg_send_message_safe(chat_id, "Укажите игрока: /name Kevin Durant")
                return PlainTextResponse("OK")
            p = _find_best_player(q)
            if not p:
                _tg_send_message_safe(chat_id, f"Не нашёл игрока: {q}")
                return PlainTextResponse("OK")
            _tg_send_message_safe(chat_id, f"Как подписать игрока {display_name_for(p)} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{p['personId']}]")
            return PlainTextResponse("OK")

        if text.startswith("/team"):
            q = text.split(" ",1)[1].strip() if " " in text else ""
            if not q:
                _tg_send_message_safe(chat_id, "Укажите игрока: /team Kevin Durant")
                return PlainTextResponse("OK")
            p = _find_best_player(q)
            if not p:
                _tg_send_message_safe(chat_id, f"Не нашёл игрока: {q}")
                return PlainTextResponse("OK")
            _tg_send_message_safe(chat_id, f"Какой teamId назначить для {display_name_for(p)}?\nОтветьте числом.\n[setteam:{p['personId']}]")
            return PlainTextResponse("OK")

        # обработка reply на setteam
        if msg.get("reply_to_message") and text:
            reply_text = msg["reply_to_message"].get("text") or ""
            if "[setteam:" in reply_text:
                m = re.search(r"\[setteam:(\d+)\]", reply_text)
                if m:
                    pid = m.group(1)
                    team_id = text.strip()
                    set_player_team(pid, team_id)
                    _tg_send_message_safe(chat_id, f"Команда сохранена: {team_id}")
                    return PlainTextResponse("OK")

        # неизвестная команда
        _tg_send_message_safe(chat_id, HELP_TEXT)
        return PlainTextResponse("OK")

    except Exception as e:
        _log("top-level err", traceback.format_exc())
        try:
            _tg_send_message_safe(chat_id, f"<i>Ошибка:</i> {e}", parse_mode="HTML")
        except:
            pass
        return PlainTextResponse("OK")

# ---------- поиск игрока ----------
def _find_best_player(query: str) -> Optional[Dict[str, Any]]:
    cands = find_player_by_name(query)
    if not cands:
        q = query.lower().replace("ё","е").replace("’","'").strip()
        cands = [p for p in get_players() if q in (p.get("displayName","") or f"{p.get('firstName','')} {p.get('lastName','')}").lower()]
    if not cands: return None
    cands.sort(key=lambda p: (not p.get("isActive", True), p.get("lastName",""), p.get("firstName","")))
    return cands[0]
