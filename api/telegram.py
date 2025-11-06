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
from graphics import render_card

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ADMIN_IDS = [s.strip() for s in os.getenv("ADMIN_IDS", "").split(",") if s.strip()]
ASK_COLOR_ALWAYS = os.getenv("ASK_COLOR_ALWAYS", "1") == "1"  # по ТЗ — спрашиваем всегда

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
        parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
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
    "• /card <имя> | <метрики через запятую> | [impact|single]\n"
    "  пример: /card wembanyama | 10 очков, 12 передач | impact\n"
    "• /name <имя> — интерактивно задать русское имя для игрока\n"
    "• /team <имя> — интерактивно задать/переопределить teamId\n"
    "• /alias <pid> <алиас> — добавить алиас к игроку\n"
    "• /color <имя|teamId> — выбрать/сохранить основной цвет команды\n"
    "• /help — это сообщение\n"
)

def _parse_card_cmd(text: str) -> Optional[Tuple[str, List[Tuple[str,str]], str]]:
    m = re.match(r"^/card\s+(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(\w+))?\s*$", text, flags=re.IGNORECASE|re.DOTALL)
    if not m: return None
    name = m.group(1).strip()
    metrics = m.group(2).strip()
    template = (m.group(3) or "single").strip().lower()
    stats: List[Tuple[str,str]] = []
    for chunk in [c.strip() for c in metrics.split(",") if c.strip()]:
        mm = re.match(r"^(\d+)\s*(.+)$", chunk)
        if mm: stats.append((mm.group(1), mm.group(2).strip()))
        else:  stats.append((chunk, ""))
    return name, stats, template

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

def _ask_color(chat_id: int, team_id: str, palette: List[str], ask_text: str, once_key: str, save_key: str):
    opts = palette[:3] if palette else ["#1D428A", "#FFC72C", "#0B8043"]
    kb = []
    # Авто
    kb.append([{"text": "Авто", "callback_data": f"{once_key}:{team_id}:AUTO"}])
    # Поштучно: «■ #HEX сейчас» и «★ #HEX сделать дефолтом»
    for hexv in opts:
        kb.append([{"text": f"■ {hexv} сейчас", "callback_data": f"{once_key}:{team_id}:{hexv}"}])
        kb.append([{"text": f"★ {hexv} сделать дефолтом", "callback_data": f"{save_key}:{team_id}:{hexv}"}])
    _tg_send_message_safe(chat_id, ask_text, reply_markup={"inline_keyboard": kb})

def _render_and_send_card(chat_id: int, p: Dict[str,Any], ru_name: str, stats: List[Tuple[str,str]], template: str, primary_override: Optional[str] = None) -> str:
    """Рендерит и отправляет PNG. Возвращает использованный HEX основного цвета."""
    _tg_send_chat_action(chat_id, "upload_photo")
    # голова
    head_url = ensure_headshot_png(p)
    head_img = open_headshot_variants(head_url) or Image.new("RGBA", (1040, 1040), (0,0,0,0))
    # бренд
    team_id = str(p.get("teamId") or "0")
    (primary, dark, light), logo_path, palette, _has_saved = get_team_brand(team_id)
    if primary_override and primary_override != "AUTO":
        primary = primary_override
        from team_brand import _shade, _hex_to_rgb, _hex as _tohex  # internal utils
        rgb = _hex_to_rgb(primary)
        dark = _tohex(_shade(rgb, 0.65))
        light = primary

    logo_img = None
    if logo_path and os.path.exists(logo_path):
        try: logo_img = Image.open(logo_path).convert("RGBA")
        except Exception: logo_img = None

    name_to_use = (ru_name or "").strip() or display_name_for(p)
    png = render_card(
        template=template,
        player_name=name_to_use,
        team_name=team_id,
        team_logo_img=logo_img,
        team_colors=(primary, dark, light),
        head_img=head_img,
        stats=stats,
        note=None,
    )
    ok = _tg_send_document_png_safe(chat_id, png, filename="card.png")
    if not ok.get("ok"):
        _log("sendDocument failed:", ok)
        _tg_send_message_safe(chat_id, "Не смог отправить PNG. Проверьте размер/соединение.")
    # подпись про цвет
    _tg_send_message_safe(chat_id, f"Цвет: {color_name_ru(primary)} ({primary})")
    return primary

def _ensure_ru_and_color_then_card(chat_id: int, message_id: int, p: Dict[str,Any], stats: List[Tuple[str,str]], template: str):
    pid = str(p.get("personId"))
    team_id = str(p.get("teamId") or "0")
    ov = get_overrides().get(pid) or {}
    saved_ru = str(ov.get("ruName") or "").strip()

    (primary, dark, light), logo_path, palette, has_saved_primary = get_team_brand(team_id)

    # 1) спросим ru-имя, если ещё нет
    if not saved_ru:
        en = display_name_for(p)
        msg = _tg_send_message_safe(
            chat_id,
            f"Как подписать игрока <b>{en}</b> на плашке?\nОтветьте на это сообщение русским именем.",
            reply_to_message_id=message_id, reply_markup=_force_reply()
        )
        if msg.get("ok"):
            _set_dialog(chat_id, {"mode":"set_ru_then_color", "pid": pid, "stats": stats, "template": template})
            return
        else:
            saved_ru = en  # fallback — не зависаем

    # 2) про цвет: по ТЗ — спрашиваем КАЖДЫЙ раз
    ask = "Выберите основной цвет плашки (можно сохранить для команды по умолчанию):"
    _ask_color(chat_id, team_id, palette, ask_text=ask, once_key="pickcol", save_key="savecol")
    _set_dialog(chat_id, {"mode":"wait_color_then_card", "pid": pid, "stats": stats, "template": template, "ru": saved_ru})
    return

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

            if data.startswith("pick:"):
                pid = data.split(":",1)[1]
                p = get_players_index().get(pid)
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                if not p:
                    _tg_send_message_safe(chat_id, "Игрок не найден в базе, обновите игроков и попробуйте снова.")
                    return PlainTextResponse("OK")
                _ensure_ru_and_color_then_card(chat_id, msg_id, p, stats=[], template="single")
                return PlainTextResponse("OK")

            if data.startswith("addcustom:"):
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                _tg_send_message_safe(chat_id, "Введите числовой PERSON_ID для этого игрока (как в NBA stats).", reply_to_message_id=msg_id, reply_markup=_force_reply())
                _set_dialog(chat_id, {"mode":"add_custom_pid"})
                return PlainTextResponse("OK")

            # Выбор цвета один раз
            if data.startswith("pickcol:"):
                _, team_id, hexv = data.split(":")
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                dlg = _get_dialog(chat_id) or {}
                mode = dlg.get("mode")
                if mode not in ("wait_color_then_card", "color_only"):
                    return PlainTextResponse("OK")
                if mode == "color_only":
                    # просто сохранять не нужно, это разовая установка — покажем подтверждение
                    if hexv == "AUTO":
                        _tg_send_message_safe(chat_id, "Ок, цвет: Авто (разово).")
                    else:
                        _tg_send_message_safe(chat_id, f"Ок, цвет разово: {hexv}.")
                    _clear_dialog(chat_id)
                    return PlainTextResponse("OK")
                # карта
                pid = dlg.get("pid"); p = get_players_index().get(str(pid))
                if not p:
                    _tg_send_message_safe(chat_id, "Игрок не найден. Попробуйте ещё раз.")
                    _clear_dialog(chat_id)
                    return PlainTextResponse("OK")
                ru = dlg.get("ru") or display_name_for(p)
                stats = dlg.get("stats") or []
                template = dlg.get("template") or "single"
                primary_override = None if hexv == "AUTO" else hexv
                _clear_dialog(chat_id)
                _render_and_send_card(chat_id, p, ru, stats, template, primary_override=primary_override)
                return PlainTextResponse("OK")

            # Сохранить цвет дефолтом
            if data.startswith("savecol:"):
                _, team_id, hexv = data.split(":")
                _ = _tg_post_json("answerCallbackQuery", {"callback_query_id": cb["id"]})
                ok = set_team_primary_color(team_id, hexv)  # hexv может быть AUTO — тогда снимем override
                if ok:
                    if hexv == "AUTO":
                        _tg_send_message_safe(chat_id, f"Сбросил дефолтный цвет для команды {team_id} → Авто.")
                    else:
                        _tg_send_message_safe(chat_id, f"Сохранил основной цвет <b>{hexv}</b> для команды {team_id}.")
                else:
                    _tg_send_message_safe(chat_id, "Не удалось сохранить цвет. Формат: #RRGGBB или AUTO.")
                # если это было в процессе карточки — дорендерим с сохранённым дефолтом
                dlg = _get_dialog(chat_id) or {}
                if dlg.get("mode") == "wait_color_then_card":
                    pid = dlg.get("pid"); p = get_players_index().get(str(pid))
                    if p:
                        ru = dlg.get("ru") or display_name_for(p)
                        stats = dlg.get("stats") or []
                        template = dlg.get("template") or "single"
                        _clear_dialog(chat_id)
                        _render_and_send_card(chat_id, p, ru, stats, template, primary_override=None)
                    else:
                        _clear_dialog(chat_id)
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
                        _clear_dialog(chat_id)
                        _tg_send_message_safe(chat_id, "Игрок не найден после обновления.")
                        return PlainTextResponse("OK")
                    team_id = str(p.get("teamId") or "0")
                    (_primary, _dark, _light), _logo, palette, _has_saved = get_team_brand(team_id)
                    _ask_color(chat_id, team_id, palette, ask_text="Выберите основной цвет плашки:", once_key="pickcol", save_key="savecol")
                    _set_dialog(chat_id, {"mode":"wait_color_then_card", "pid": pid, "stats": dlg.get("stats") or [], "template": dlg.get("template") or "single", "ru": ru})
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

                if mode == "add_custom_pid":
                    if not re.fullmatch(r"\d{2,}", text):
                        _tg_send_message_safe(chat_id, "Нужен числовой PERSON_ID. Ещё раз.", reply_to_message_id=m.get("message_id"), reply_markup=_force_reply())
                        return PlainTextResponse("OK")
                    pid = text
                    set_player_ru_name(pid, f"Игрок {pid}")
                    _clear_dialog(chat_id)
                    _tg_send_message_safe(chat_id, f"Добавлен базовый профиль pid={pid}. Теперь задайте /name или /team.")
                    return PlainTextResponse("OK")

            # команды
            if text.startswith("/start") or text.startswith("/help"):
                _tg_send_message_safe(chat_id, HELP_TEXT)
                return PlainTextResponse("OK")

            if text.startswith("/find"):
                q = text[len("/find"):].strip()
                if not q:
                    _tg_send_message_safe(chat_id, "Укажите часть имени: /find Doncic"); return PlainTextResponse("OK")
                res = find_player_by_name(q)
                if not res:
                    cand = _best_candidates(q)
                    if not cand:
                        _tg_send_message_safe(chat_id, "Ничего не найдено. Можете добавить вручную: нажмите кнопку ниже.",
                            reply_markup={"inline_keyboard":[[{"text":"Добавить вручную", "callback_data":"addcustom:"+q}]]})
                        return PlainTextResponse("OK")
                    kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get("personId"))}] for p in cand]
                    kb.append([{"text":"Добавить вручную", "callback_data":"addcustom:"+q}])
                    _tg_send_message_safe(chat_id, "Не нашёл точного совпадения. Выберите:", reply_markup={"inline_keyboard": kb})
                    return PlainTextResponse("OK")
                lines = [f"• {p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')}, teamId={p.get('teamId')})" for p in res[:10]]
                _tg_send_message_safe(chat_id, "\n".join(lines) if lines else "Пусто")
                return PlainTextResponse("OK")

            if text.startswith("/name"):
                q = text[len("/name"):].strip()
                if not q:
                    _tg_send_message_safe(chat_id, "Формат: /name <имя на англ/рус>\nЯ спрошу, как записать по-русски.")
                    return PlainTextResponse("OK")
                res = find_player_by_name(q)
                if not res:
                    cand = _best_candidates(q)
                    if not cand:
                        _tg_send_message_safe(chat_id, "Игрок не найден. Можно добавить вручную: /find сначала выберите PID.")
                        return PlainTextResponse("OK")
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
                    _tg_send_message_safe(chat_id, "Формат: /team <имя> — я попрошу ввести teamId (например 1610612756).")
                    return PlainTextResponse("OK")
                res = find_player_by_name(q)
                if not res:
                    _tg_send_message_safe(chat_id, "Игрок не найден. Сначала /find и выберите точного.")
                    return PlainTextResponse("OK")
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
                (_primary, _dark, _light), _logo, palette, _has_saved = get_team_brand(team_id)
                _ask_color(chat_id, team_id, palette, ask_text="Выберите основной цвет плашки:", once_key="pickcol", save_key="savecol")
                _clear_dialog(chat_id)
                _set_dialog(chat_id, {"mode":"color_only", "team_id": team_id})
                return PlainTextResponse("OK")

            if text.startswith("/card"):
                parsed = _parse_card_cmd(text)
                if not parsed:
                    _tg_send_message_safe(chat_id, "Формат: /card <имя> | <метрики через запятую> | [impact|single]")
                    return PlainTextResponse("OK")
                name, stats, template = parsed
                _tg_send_chat_action(chat_id, "typing")
                _tg_send_message_safe(chat_id, "Готовлю плашку…")
                res = find_player_by_name(name)
                if not res:
                    cand = _best_candidates(name)
                    kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get('personId'))}] for p in cand]
                    kb.append([{"text":"Добавить вручную", "callback_data":"addcustom:"+name}])
                    _tg_send_message_safe(chat_id, "Не нашёл точного игрока. Выберите ближайший:", reply_markup={"inline_keyboard": kb})
                    return PlainTextResponse("OK")
                p = res[0]
                _ensure_ru_and_color_then_card(chat_id, m.get("message_id"), p, stats, template)
                return PlainTextResponse("OK")

            _tg_send_message_safe(chat_id, HELP_TEXT)
            return PlainTextResponse("OK")

    except Exception as e:
        _log("webhook error", repr(e))
    return PlainTextResponse("OK")
