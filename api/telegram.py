# api/telegram.py
from __future__ import annotations
import json, os, re, time, uuid
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError
from PIL import Image

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
    render_card, render_card2, render_card_bad, render_card_dr, render_card_special
)

app = FastAPI()

BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ADMIN_IDS      = [s.strip() for s in os.getenv("ADMIN_IDS", "").split(",") if s.strip()]
ASK_COLOR_ALWAYS = os.getenv("ASK_COLOR_ALWAYS", "1") == "1"
DR_TEMPLATE_PATH = os.getenv("DR_TEMPLATE_PATH", "assets/templates/card_dr_base.png")

STATE_PATH = "/tmp/tg_state.json"

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

def _tg_send_chat_action(chat_id: int, action: str = "typing"):
    return _tg_post_json("sendChatAction", {"chat_id": chat_id, "action": action})

def _tg_send_message_safe(chat_id: int, text: str, reply_to_message_id: Optional[int]=None, reply_markup: Optional[Dict[str,Any]]=None, parse_mode: Optional[str]="HTML") -> Dict[str, Any]:
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

def _tg_send_document_png_safe(chat_id: int, png_bytes: bytes, filename: str = "card.png", caption: Optional[str]=None) -> Dict[str, Any]:
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    parts: List[bytes] = []
    def add_field(name: str, value: str):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode()); parts.append(b"\r\n")
    def add_file(name: str, filename: str, mime: str, data: bytes):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        parts.append(f"Content-Type: {mime}\n\n".encode())
        parts.append(data); parts.append(b"\r\n")
    add_field("chat_id", str(chat_id))
    if caption: add_field("caption", caption)
    add_file("document", filename, "image/png", png_bytes)
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = f"{_tg_base()}/sendDocument"
    return _safe_http_json(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}, "POST", timeout=30)

HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /find <имя/фамилия> — найти игрока (англ/рус/алиас)\n"
    "• /card <игрок> | <метрики> — обычная плашка\n"
    "• /card2 <игрок1> | <метрики1> | <игрок2> | <метрики2> — парная плашка\n"
    "• /cardBAD <игрок> | <метрики> — плохо сыграл (коричневый, 💩)\n"
    "• /cardDR <игрок> | <метрики> — Делает Разницу (по шаблону)\n"
    "• /cardS <игрок> | <метрики> | <информация> — особая с боковой вставкой\n"
    "• /name <имя> — задать русское имя игроку\n"
    "• /team <имя> — задать/переопределить teamId\n"
    "• /alias <pid> <алиас> — добавить алиас\n"
    "• /color <teamId|имя> — сохранить дефолтный цвет команды (#HEX или AUTO)\n"
)

def _parse_pipe_args(text: str, cmd: str, parts: int) -> Optional[List[str]]:
    # парсер: /cmd a | b | c ...
    body = text[len(cmd):].strip()
    if "|" not in body: return None
    chunks = [c.strip() for c in body.split("|")]
    if len(chunks) < parts: return None
    return chunks[:parts]

def _parse_stats(s: str) -> List[Tuple[str,str]]:
    out: List[Tuple[str,str]] = []
    for chunk in [c.strip() for c in s.split(",") if c.strip()]:
        m = re.match(r"^(\d+)\s*(.+)$", chunk)
        if m: out.append((m.group(1), m.group(2).strip()))
        else:  out.append((chunk, ""))
    return out

def _best_candidates(query: str, limit: int = 4) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q: return []
    found = find_player_by_name(q)
    if found: return found[:limit]
    allp = get_players()
    scored: List[Tuple[int, Dict[str,Any]]] = []
    toks = [t for t in re.split(r"\s+", q) if t]
    for p in allp:
        dn = (p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}").strip().lower()
        sc = 0
        if dn:
            if any(dn.startswith(t) for t in toks): sc += 3
            if any(t in dn for t in toks): sc += 2
        scored.append((sc, p))
    scored.sort(key=lambda x: (-x[0], x[1].get("lastName",""), x[1].get("firstName","")))
    return [p for sc,p in scored if sc>0][:limit]

def _force_reply() -> Dict[str,Any]:
    return {"force_reply": True, "selective": True}

# ---- Цвет: только 2 варианта (Авто/Свой HEX) ----
def _ask_color_minimal(chat_id: int, team_id: str, ask_text: str):
    kb = [
        [{"text":"Авто (из лого/пресета)", "callback_data": f"pickcol:{team_id}:AUTO"}],
        [{"text":"Ввести свой #HEX",       "callback_data": f"askhex:{team_id}:ASK"}],
    ]
    _tg_send_message_safe(chat_id, ask_text, reply_markup={"inline_keyboard": kb})

def _render_single_flow(chat_id:int, player:Dict[str,Any], ru_name:str,
                        stats:List[Tuple[str,str]], primary_override: Optional[str]=None):
    # лого
    team_id = str(player.get("teamId") or "0")
    (primary, dark, light), logo_path, _palette, _has_saved = get_team_brand(team_id)
    if primary_override and primary_override != "AUTO":
        from team_brand import _hex_to_rgb, _shade, _hex as _tohex
        primary = primary_override
        rgb = _hex_to_rgb(primary)
        dark = _tohex(_shade(rgb, 0.65)); light = primary

    logo_img = None
    if logo_path and os.path.exists(logo_path):
        try: logo_img = Image.open(logo_path).convert("RGBA")
        except Exception: logo_img = None

    head_url = ensure_headshot_png(player)
    head_img = open_headshot_variants(head_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))

    png = render_card(
        template="single",
        player_name=ru_name or display_name_for(player),
        team_name=team_id,
        team_logo_img=logo_img,
        team_colors=(primary, dark, light),
        head_img=head_img,
        stats=stats,
        note=None,
    )
    _tg_send_document_png_safe(chat_id, png, filename="card.png")
    from team_brand import color_name_ru
    _tg_send_message_safe(chat_id, f"Цвет: {color_name_ru(primary)} ({primary})")

def _ensure_ru_then_color(chat_id: int, message_id: int, p: Dict[str,Any],
                          stats: List[Tuple[str,str]], mode: str, extras: Dict[str,Any] | None = None):
    pid = str(p.get("personId"))
    ov = get_overrides().get(pid) or {}
    saved_ru = str(ov.get("ruName") or "").strip()

    _tg_send_message_safe(chat_id, "<i>Уточнения…</i>")
    if not saved_ru:
        en = display_name_for(p)
        msg = _tg_send_message_safe(
            chat_id,
            f"Как подписать игрока <b>{en}</b> на плашке?\nОтветьте на это сообщение русским именем.",
            reply_to_message_id=message_id, reply_markup=_force_reply()
        )
        if msg.get("ok"):
            _set_dialog(chat_id, {"mode":"set_ru_then_color", "pid": pid, "stats": stats, "flow_mode": mode, "extras": extras or {}})
            return
        else:
            saved_ru = en

    # только 2 опции: авто и свой HEX
    team_id = str(p.get("teamId") or "0")
    _ask_color_minimal(chat_id, team_id, "Выберите основной цвет плашки:")
    _set_dialog(chat_id, {"mode":"wait_color_for_flow", "pid": pid, "stats": stats, "ru": saved_ru, "flow_mode": mode, "extras": extras or {}})

@app.get("/api/telegram")
async def telegram_get(request: Request):
    secret = request.query_params.get("secret", "")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")
    action = request.query_params.get("action")
    if action == "refresh":
        n, meta = refresh_players()
        return JSONResponse({"ok": True, "refreshed": True, "players_indexed": n, **meta})
    return JSONResponse({"ok": True, "route": "telegram-get"})

@app.post("/api/telegram")
async def webhook_query(request: Request):
    secret = request.query_params.get("secret", "")
    if secret != WEBHOOK_SECRET:
        return PlainTextResponse("forbidden", status_code=403)
    if not BOT_TOKEN:
        return PlainTextResponse("no bot token", status_code=500)

    try:
        upd = await request.json()
    except Exception as e:
        _log("json parse error", repr(e))
        return PlainTextResponse("OK")

    try:
        # callback_query
        if "callback_query" in upd:
            cb = upd["callback_query"]; data = cb.get("data") or ""
            chat_id = cb["message"]["chat"]["id"]
            msg_id = cb["message"]["message_id"]

            # выбор игрока из кандидатов
            if data.startswith("pick:"):
                pid = data.split(":",1)[1]
                p = get_players_index().get(pid)
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                if not p:
                    _tg_send_message_safe(chat_id, "Игрок не найден в базе. Обновите список и попробуйте снова.")
                    return PlainTextResponse("OK")
                _ensure_ru_then_color(chat_id, msg_id, p, stats=[], mode="single")
                return PlainTextResponse("OK")

            # color: авто
            if data.startswith("pickcol:"):
                _, team_id, hexv = data.split(":")
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                dlg = _get_dialog(chat_id) or {}
                flow_mode = dlg.get("flow_mode") or "single"
                pid = (dlg.get("pid") or "")
                p = get_players_index().get(str(pid))
                if not p:
                    _clear_dialog(chat_id); _tg_send_message_safe(chat_id, "Игрок не найден."); return PlainTextResponse("OK")
                ru = dlg.get("ru") or display_name_for(p)
                stats = dlg.get("stats") or []
                extras = dlg.get("extras") or {}
                _tg_send_message_safe(chat_id, "<i>Готовлю плашку…</i>")
                _clear_dialog(chat_id)
                # маршрутизация по flow
                if flow_mode == "single":
                    _render_single_flow(chat_id, p, ru, stats, primary_override=None if hexv=="AUTO" else hexv)
                elif flow_mode == "special":
                    # /cardS — без выбора здесь, но на случай, если попросили — применим разовый цвет
                    (primary, dark, light), logo_path, _pc, _hs = get_team_brand(str(p.get("teamId") or "0"))
                    if hexv != "AUTO":
                        from team_brand import _hex_to_rgb, _shade, _hex as _tohex
                        primary = hexv
                        rgb = _hex_to_rgb(primary)
                        dark = _tohex(_shade(rgb, 0.65)); light = primary
                    logo_img = None
                    if logo_path and os.path.exists(logo_path):
                        try: logo_img = Image.open(logo_path).convert("RGBA")
                        except: pass
                    head_url = ensure_headshot_png(p)
                    head_img = open_headshot_variants(head_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))
                    png = render_card_special(
                        player_name=ru, team_logo_img=logo_img, team_colors=(primary,dark,light),
                        head_img=head_img, stats=stats, info_text=str(extras.get("info") or "")
                    )
                    _tg_send_document_png_safe(chat_id, png, filename="card.png")
                    from team_brand import color_name_ru
                    _tg_send_message_safe(chat_id, f"Цвет: {color_name_ru(primary)} ({primary})")
                else:
                    # прочие флоу не используют выбор цвета (BAD/DR/DUO) — игнор
                    pass
                return PlainTextResponse("OK")

            # color: запрос на ввод HEX
            if data.startswith("askhex:"):
                _, team_id, _ = data.split(":")
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                _tg_send_message_safe(chat_id, "Пришлите код цвета в формате <b>#RRGGBB</b> (только для этой плашки).", reply_to_message_id=msg_id, reply_markup=_force_reply())
                dlg = _get_dialog(chat_id) or {}
                if not dlg: dlg = {}
                dlg["pending_team_id"] = team_id
                dlg["mode"] = "wait_hex_then_render"
                _set_dialog(chat_id, dlg)
                return PlainTextResponse("OK")

            _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
            return PlainTextResponse("OK")

        # обычные сообщения
        if "message" in upd:
            m = upd["message"]
            chat_id = m["chat"]["id"]
            text = (m.get("text") or "").strip()

            dlg = _get_dialog(chat_id)
            if dlg and m.get("reply_to_message"):
                mode = dlg.get("mode")

                if mode == "set_ru_then_color":
                    pid = dlg["pid"]; ru = text.strip()
                    set_player_ru_name(pid, ru)
                    p = get_players_index().get(pid)
                    if not p:
                        _clear_dialog(chat_id); _tg_send_message_safe(chat_id, "Игрок не найден."); return PlainTextResponse("OK")
                    team_id = str(p.get("teamId") or "0")
                    _ask_color_minimal(chat_id, team_id, "Выберите основной цвет плашки:")
                    _set_dialog(chat_id, {"mode":"wait_color_for_flow", "pid": pid, "stats": dlg.get("stats") or [], "ru": ru, "flow_mode": dlg.get("flow_mode") or "single", "extras": dlg.get("extras") or {}})
                    return PlainTextResponse("OK")

                if mode == "wait_hex_then_render":
                    hexv = text.strip().upper()
                    if not re.fullmatch(r"#([0-9A-F]{6})", hexv):
                        _tg_send_message_safe(chat_id, "Формат цвета: <b>#RRGGBB</b>. Пришлите заново.", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                        return PlainTextResponse("OK")
                    flow_mode = dlg.get("flow_mode") or "single"
                    pid = dlg.get("pid") or ""
                    p = get_players_index().get(str(pid))
                    if not p:
                        _clear_dialog(chat_id); _tg_send_message_safe(chat_id, "Игрок не найден."); return PlainTextResponse("OK")
                    ru = dlg.get("ru") or display_name_for(p)
                    stats = dlg.get("stats") or []
                    extras = dlg.get("extras") or {}
                    _tg_send_message_safe(chat_id, "<i>Готовлю плашку…</i>")
                    _clear_dialog(chat_id)
                    if flow_mode == "single":
                        _render_single_flow(chat_id, p, ru, stats, primary_override=hexv)
                    elif flow_mode == "special":
                        (primary, dark, light), logo_path, _pc, _hs = get_team_brand(str(p.get("teamId") or "0"))
                        from team_brand import _hex_to_rgb, _shade, _hex as _tohex
                        primary = hexv
                        rgb = _hex_to_rgb(primary)
                        dark = _tohex(_shade(rgb, 0.65)); light = primary
                        logo_img = None
                        if logo_path and os.path.exists(logo_path):
                            try: logo_img = Image.open(logo_path).convert("RGBA")
                            except: pass
                        head_url = ensure_headshot_png(p)
                        head_img = open_headshot_variants(head_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))
                        png = render_card_special(
                            player_name=ru, team_logo_img=logo_img, team_colors=(primary,dark,light),
                            head_img=head_img, stats=stats, info_text=str(extras.get("info") or "")
                        )
                        _tg_send_document_png_safe(chat_id, png, filename="card.png")
                        from team_brand import color_name_ru
                        _tg_send_message_safe(chat_id, f"Цвет: {color_name_ru(primary)} ({primary})")
                    else:
                        pass
                    return PlainTextResponse("OK")

                if mode == "set_ru_interactive":
                    pid = dlg["pid"]; ru = text
                    set_player_ru_name(pid, ru)
                    _clear_dialog(chat_id)
                    _tg_send_message_safe(chat_id, f"Ок! Имя сохранено: <b>{ru}</b> (pid={pid})")
                    return PlainTextResponse("OK")

                if mode == "set_team_interactive":
                    pid = dlg["pid"]
                    team_text = text.strip()
                    if not re.fullmatch(r"\d{9,}", team_text):
                        _tg_send_message_safe(chat_id, "Ожидаю числовой teamId (например: 1610612756). Попробуйте ещё раз.", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                        return PlainTextResponse("OK")
                    set_player_team(pid, team_text)
                    _clear_dialog(chat_id)
                    _tg_send_message_safe(chat_id, f"Команда обновлена: <b>{team_text}</b> для pid={pid}")
                    return PlainTextResponse("OK")

            # команды
            if text.startswith("/start") or text.startswith("/help"):
                _tg_send_message_safe(chat_id, HELP_TEXT); return PlainTextResponse("OK")

            if text.startswith("/find"):
                q = text[len("/find"):].strip()
                if not q:
                    _tg_send_message_safe(chat_id, "Укажите часть имени: /find Doncic"); return PlainTextResponse("OK")
                res = find_player_by_name(q)
                if not res:
                    cand = _best_candidates(q)
                    if not cand:
                        _tg_send_message_safe(chat_id, "Ничего не найдено.")
                        return PlainTextResponse("OK")
                    kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get("personId"))}] for p in cand]
                    _tg_send_message_safe(chat_id, "Выберите игрока:", reply_markup={"inline_keyboard": kb})
                    return PlainTextResponse("OK")
                lines = [f"• {p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')}, teamId={p.get('teamId')})" for p in res[:10]]
                _tg_send_message_safe(chat_id, "\n".join(lines) if lines else "Пусто")
                return PlainTextResponse("OK")

            if text.startswith("/name"):
                q = text[len("/name"):].strip()
                if not q:
                    _tg_send_message_safe(chat_id, "Формат: /name <имя на англ/рус>"); return PlainTextResponse("OK")
                res = find_player_by_name(q)
                if not res:
                    cand = _best_candidates(q)
                    if not cand:
                        _tg_send_message_safe(chat_id, "Игрок не найден."); return PlainTextResponse("OK")
                    kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get("personId"))}] for p in cand]
                    _tg_send_message_safe(chat_id, "Выберите игрока:", reply_markup={"inline_keyboard": kb})
                    return PlainTextResponse("OK")
                p = res[0]
                en = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
                msg = _tg_send_message_safe(chat_id, f"Как подписать игрока <b>{en}</b> на русском?\nОтветьте на это сообщение именем.", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                if msg.get("ok"):
                    _set_dialog(chat_id, {"mode":"set_ru_interactive", "pid": str(p.get("personId")), "ask_msg_id": msg["result"]["message_id"]})
                return PlainTextResponse("OK")

            if text.startswith("/team"):
                q = text[len("/team"):].strip()
                if not q:
                    _tg_send_message_safe(chat_id, "Формат: /team <имя>"); return PlainTextResponse("OK")
                res = find_player_by_name(q)
                if not res:
                    _tg_send_message_safe(chat_id, "Игрок не найден."); return PlainTextResponse("OK")
                p = res[0]
                en = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
                msg = _tg_send_message_safe(chat_id, f"Введите числовой teamId для <b>{en}</b> (например 1610612756).", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                if msg.get("ok"):
                    _set_dialog(chat_id, {"mode":"set_team_interactive", "pid": str(p.get("personId")), "ask_msg_id": msg["result"]["message_id"]})
                return PlainTextResponse("OK")

            if text.startswith("/alias"):
                rest = text[len("/alias"):].strip()
                m2 = re.match(r"^(\d+)\s+(.+)$", rest)
                if not m2:
                    _tg_send_message_safe(chat_id, "Формат: /alias <pid> <алиас>"); return PlainTextResponse("OK")
                pid, alias = m2.group(1), m2.group(2)
                set_player_alias(pid, alias)
                _tg_send_message_safe(chat_id, f"Алиас добавлен: <b>{alias}</b> для pid={pid}")
                return PlainTextResponse("OK")

            if text.startswith("/color"):
                q = text[len("/color"):].strip()
                team_id = None
                if re.fullmatch(r"\d{9,}", q):
                    team_id = q
                else:
                    res = find_player_by_name(q)
                    if res:
                        team_id = str(res[0].get("teamId") or "0")
                if not team_id:
                    _tg_send_message_safe(chat_id, "Формат: /color <teamId> или /color <имя игрока>")
                    return PlainTextResponse("OK")
                _tg_send_message_safe(chat_id, "Пришлите <b>#RRGGBB</b> — сохраню как дефолт для этой команды. Или <b>AUTO</b> чтобы сбросить.", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                _set_dialog(chat_id, {"mode":"save_team_color", "team_id": team_id})
                return PlainTextResponse("OK")

            if dlg and dlg.get("mode") == "save_team_color":
                team_id = dlg.get("team_id")
                val = text.strip().upper()
                if val == "AUTO":
                    ok = set_team_primary_color(team_id, "AUTO")
                else:
                    if not re.fullmatch(r"#([0-9A-F]{6})", val):
                        _tg_send_message_safe(chat_id, "Формат: #RRGGBB или AUTO. Ещё раз.", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                        return PlainTextResponse("OK")
                    ok = set_team_primary_color(team_id, val)
                _clear_dialog(chat_id)
                _tg_send_message_safe(chat_id, "Ок!") if ok else _tg_send_message_safe(chat_id, "Не удалось сохранить.")
                return PlainTextResponse("OK")

            # ---- Генерация карточек ----
            if text.startswith("/card2"):
                args = _parse_pipe_args(text, "/card2", 4)
                if not args:
                    _tg_send_message_safe(chat_id, "Формат: /card2 игрок1 | метрики1 | игрок2 | метрики2")
                    return PlainTextResponse("OK")
                n1, s1, n2, s2 = args
                stats1, stats2 = _parse_stats(s1), _parse_stats(s2)
                res1, res2 = find_player_by_name(n1), find_player_by_name(n2)
                if not res1 or not res2:
                    _tg_send_message_safe(chat_id, "Не нашёл одного из игроков.")
                    return PlainTextResponse("OK")
                p1, p2 = res1[0], res2[0]

                # p1
                (c1, logo1p, _pc1, _hs1) = (*get_team_brand(str(p1.get("teamId") or "0"))[:4],)
                (primary1, dark1, light1) = c1
                logo1 = None
                if logo1p and os.path.exists(logo1p):
                    try: logo1 = Image.open(logo1p).convert("RGBA")
                    except: pass
                head1_url = ensure_headshot_png(p1)
                head1 = open_headshot_variants(head1_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))
                # p2
                (c2, logo2p, _pc2, _hs2) = (*get_team_brand(str(p2.get("teamId") or "0"))[:4],)
                (primary2, dark2, light2) = c2
                logo2 = None
                if logo2p and os.path.exists(logo2p):
                    try: logo2 = Image.open(logo2p).convert("RGBA")
                    except: pass
                head2_url = ensure_headshot_png(p2)
                head2 = open_headshot_variants(head2_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))

                png = render_card2(
                    display_name_for(p1), logo1, (primary1,dark1,light1), head1, stats1,
                    display_name_for(p2), logo2, (primary2,dark2,light2), head2, stats2
                )
                _tg_send_document_png_safe(chat_id, png, filename="card.png")
                return PlainTextResponse("OK")

            if text.startswith("/cardBAD"):
                args = _parse_pipe_args(text, "/cardBAD", 2)
                if not args:
                    _tg_send_message_safe(chat_id, "Формат: /cardBAD игрок | метрики")
                    return PlainTextResponse("OK")
                name, s = args
                stats = _parse_stats(s)
                res = find_player_by_name(name)
                if not res:
                    _tg_send_message_safe(chat_id, "Игрок не найден.")
                    return PlainTextResponse("OK")
                p = res[0]
                ru = (get_overrides().get(str(p.get("personId")), {}) or {}).get("ruName") or display_name_for(p)
                head_url = ensure_headshot_png(p)
                head_img = open_headshot_variants(head_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))
                png = render_card_bad(ru, head_img, stats)
                _tg_send_document_png_safe(chat_id, png, filename="card.png")
                return PlainTextResponse("OK")

            if text.startswith("/cardDR"):
                args = _parse_pipe_args(text, "/cardDR", 2)
                if not args:
                    _tg_send_message_safe(chat_id, "Формат: /cardDR игрок | метрики")
                    return PlainTextResponse("OK")
                name, s = args
                stats = _parse_stats(s)
                res = find_player_by_name(name)
                if not res:
                    _tg_send_message_safe(chat_id, "Игрок не найден.")
                    return PlainTextResponse("OK")
                p = res[0]
                ru = (get_overrides().get(str(p.get("personId")), {}) or {}).get("ruName") or display_name_for(p)
                head_url = ensure_headshot_png(p)
                head_img = open_headshot_variants(head_url) or Image.new("RGBA", (1040,1040), (0,0,0,0))
                png = render_card_dr(ru, head_img, stats, template_path=DR_TEMPLATE_PATH)
                _tg_send_document_png_safe(chat_id, png, filename="card.png")
                return PlainTextResponse("OK")

            if text.startswith("/cardS"):
                args = _parse_pipe_args(text, "/cardS", 3)
                if not args:
                    _tg_send_message_safe(chat_id, "Формат: /cardS игрок | метрики | информация")
                    return PlainTextResponse("OK")
                name, s, info = args
                stats = _parse_stats(s)
                res = find_player_by_name(name)
                if not res:
                    _tg_send_message_safe(chat_id, "Игрок не найден.")
                    return PlainTextResponse("OK")
                p = res[0]
                # Уточнения + цвет (минимальный выбор)
                extras = {"info": info}
                _ensure_ru_then_color(chat_id, m.get("message_id"), p, stats, mode="special", extras=extras)
                return PlainTextResponse("OK")

            if text.startswith("/card"):
                # обычная плашка (без impact/single в конце)
                args_raw = text[len("/card"):].strip()
                if "|" not in args_raw:
                    _tg_send_message_safe(chat_id, "Формат: /card игрок | метрики")
                    return PlainTextResponse("OK")
                name, s = [c.strip() for c in args_raw.split("|", 1)]
                stats = _parse_stats(s)
                res = find_player_by_name(name)
                if not res:
                    cand = _best_candidates(name)
                    kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get('personId'))}] for p in cand]
                    _tg_send_message_safe(chat_id, "Не нашёл точного игрока. Выберите:", reply_markup={"inline_keyboard": kb})
                    return PlainTextResponse("OK")
                p = res[0]
                _ensure_ru_then_color(chat_id, m.get("message_id"), p, stats, mode="single")
                return PlainTextResponse("OK")

            _tg_send_message_safe(chat_id, HELP_TEXT)
            return PlainTextResponse("OK")

    except Exception as e:
        _log("webhook error", repr(e))
    return PlainTextResponse("OK")
