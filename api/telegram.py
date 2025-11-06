# api/telegram.py
from __future__ import annotations
import json, os, re, io, time, uuid
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import urlencode
from PIL import Image

from data import (
    refresh_players, get_players, get_players_index, find_player_by_name,
    ensure_headshot_png, open_headshot_variants, display_name_for,
    set_player_ru_name, set_player_team, set_player_alias, get_team_brand,
)
from graphics import render_card

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ADMIN_IDS = [s.strip() for s in os.getenv("ADMIN_IDS", "").split(",") if s.strip()]

# --- STATE в /tmp (чтобы переживать несколько запросов) ---
STATE_PATH = "/tmp/tg_state.json"

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass

def _set_dialog(chat_id: int, obj: Dict[str, Any]):
    st = _load_state()
    st[str(chat_id)] = obj
    _save_state(st)

def _get_dialog(chat_id: int) -> Optional[Dict[str, Any]]:
    st = _load_state()
    return st.get(str(chat_id))

def _clear_dialog(chat_id: int):
    st = _load_state()
    if str(chat_id) in st:
        del st[str(chat_id)]
        _save_state(st)

# --- Telegram helpers ---
TBASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{TBASE}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = UrlRequest(url, data=body, headers={"Content-Type":"application/json"}, method="POST")
    with urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))

def _tg_send_message(chat_id: int, text: str, reply_to_message_id: Optional[int]=None, reply_markup: Optional[Dict[str,Any]]=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode":"HTML"}
    if reply_to_message_id: payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup: payload["reply_markup"] = reply_markup
    return _tg_post("sendMessage", payload)

def _tg_answer_callback(cb_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": cb_id}
    if text: payload["text"] = text
    if show_alert: payload["show_alert"] = True
    return _tg_post("answerCallbackQuery", payload)

def _tg_send_document_png(chat_id: int, png_bytes: bytes, filename: str = "card.png", caption: Optional[str]=None):
    # multipart/form-data вручную
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
    url = f"{TBASE}/sendDocument"
    req = UrlRequest(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

# --- parsing ---
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
    "• /help — это сообщение\n"
)

def _parse_card_cmd(text: str) -> Optional[Tuple[str, List[Tuple[str,str]], str]]:
    # /card Name | x очков, y передач | template
    m = re.match(r"^/card\s+(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(\w+))?\s*$", text, flags=re.IGNORECASE|re.DOTALL)
    if not m: return None
    name = m.group(1).strip()
    metrics = m.group(2).strip()
    template = (m.group(3) or "single").strip().lower()
    stats: List[Tuple[str,str]] = []
    for chunk in [c.strip() for c in metrics.split(",") if c.strip()]:
        # ожидаем "10 очков" -> value=10, label="ОЧКОВ"
        m2 = re.match(r"^(\d+)\s*(.+)$", chunk)
        if m2:
            stats.append((m2.group(1), m2.group(2).strip()))
        else:
            # оставим как есть
            stats.append((chunk, ""))
    return name, stats, template

def _best_candidates(query: str, limit: int = 4) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q: return []
    # 1) прямое
    found = find_player_by_name(q)
    if found: return found[:limit]
    # 2) слабый поиск: токены начинаются/содержат
    allp = get_players()
    scored: List[Tuple[int, Dict[str,Any]]] = []
    qparts = [p for p in re.split(r"\s+", q) if p]
    for p in allp:
        pid = str(p.get("personId") or "")
        dn = (p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}").strip().lower()
        ov = None
        score = 0
        if dn:
            if any(dn.startswith(t) for t in qparts): score += 3
            if any(t in dn for t in qparts): score += 2
        # ru/aliases
        # тянем разово для скорости
        # (можно ускорить: кэш вне цикла, но и так ок)
        # здесь без heavy fuzzy — достаточно частичных совпадений
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1].get("lastName",""), x[1].get("firstName","")))
    return [p for sc,p in scored if sc>0][:limit]

def _force_reply() -> Dict[str,Any]:
    return {"force_reply": True, "selective": True}

# --- CARD PIPELINE ---
def _render_and_send_card(chat_id: int, p: Dict[str,Any], ru_name: str, stats: List[Tuple[str,str]], template: str):
    # 1) голова
    head_url = ensure_headshot_png(p)
    head_img = open_headshot_variants(head_url)
    if head_img is None:
        # пустая заглушка
        head_img = Image.new("RGBA", (1040, 1040), (0,0,0,0))
    # 2) бренд команды
    team_id = (p.get("teamId") or "0")
    colors, logo_path = get_team_brand(team_id)
    logo_img = None
    if logo_path and os.path.exists(logo_path):
        try:
            logo_img = Image.open(logo_path).convert("RGBA")
        except Exception:
            logo_img = None
    # 3) имя
    name_to_use = ru_name.strip() if ru_name.strip() else display_name_for(p)
    # 4) отрисовать
    png = render_card(
        template=template,
        player_name=name_to_use,
        team_name=str(team_id),
        team_logo_img=logo_img,
        team_colors=colors,
        head_img=head_img,
        stats=stats,
        note=None,
    )
    _tg_send_document_png(chat_id, png, filename="card.png")

def _ensure_ru_name_before_card(chat_id: int, message_id: int, p: Dict[str,Any], stats: List[Tuple[str,str]], template: str):
    pid = str(p.get("personId"))
    # Проверяем overrides
    # если ruName уже есть — сразу рисуем
    from data import get_overrides
    ov = get_overrides().get(pid) or {}
    ru = str(ov.get("ruName") or "").strip()
    if ru:
        _render_and_send_card(chat_id, p, ru, stats, template)
        return
    # иначе спросим
    en = display_name_for(p)  # тут будет EN
    msg = _tg_send_message(chat_id, f"Как подписать игрока <b>{en}</b> на плашке?\nОтветьте на это сообщение русским именем.", reply_to_message_id=message_id, reply_markup=_force_reply())
    # запоминаем состояние
    _set_dialog(chat_id, {"mode":"set_ru_for_card", "pid": pid, "stats": stats, "template": template, "ask_msg_id": msg["result"]["message_id"]})

# --- ROUTES ---
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
        raise HTTPException(status_code=403, detail="bad secret")
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="No BOT_TOKEN")

    upd = await request.json()
    # callback?
    if "callback_query" in upd:
        cb = upd["callback_query"]; data = cb.get("data") or ""
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        if data.startswith("pick:"):
            pid = data.split(":",1)[1]
            idx = get_players_index()
            p = idx.get(pid)
            _tg_answer_callback(cb["id"])
            if not p:
                _tg_send_message(chat_id, "Игрок не найден в базе, обновите игроков и попробуйте снова.")
                return PlainTextResponse("OK")
            # спросим RU-имя перед карточкой
            _ensure_ru_name_before_card(chat_id, msg_id, p, stats=[], template="single")
            return PlainTextResponse("OK")
        elif data.startswith("addcustom:"):
            # создаём пустую запись в overrides, попросим PID
            _tg_answer_callback(cb["id"])
            _tg_send_message(chat_id, "Введите числовой PERSON_ID для этого игрока (как в NBA stats).", reply_to_message_id=msg_id, reply_markup=_force_reply())
            _set_dialog(chat_id, {"mode":"add_custom_pid"})
            return PlainTextResponse("OK")
        else:
            _tg_answer_callback(cb["id"])
            return PlainTextResponse("OK")

    # message?
    if "message" in upd:
        m = upd["message"]
        chat_id = m["chat"]["id"]
        text = (m.get("text") or "").strip()

        # ответы на force_reply
        dlg = _get_dialog(chat_id)
        if dlg and m.get("reply_to_message"):
            mode = dlg.get("mode")
            if mode == "set_ru_for_card":
                pid = dlg["pid"]; ru = text
                set_player_ru_name(pid, ru)
                _clear_dialog(chat_id)
                p = get_players_index().get(pid)
                if not p:
                    _tg_send_message(chat_id, "Игрок не найден после обновления. Попробуйте позже.")
                else:
                    _render_and_send_card(chat_id, p, ru, dlg.get("stats") or [], dlg.get("template") or "single")
                return PlainTextResponse("OK")
            elif mode == "set_ru_interactive":
                pid = dlg["pid"]; ru = text
                set_player_ru_name(pid, ru)
                _clear_dialog(chat_id)
                _tg_send_message(chat_id, f"Ок! Имя сохранено: <b>{ru}</b> (pid={pid})")
                return PlainTextResponse("OK")
            elif mode == "set_team_interactive":
                pid = dlg["pid"]
                team_text = text.strip()
                if not re.fullmatch(r"\d{9,}", team_text):
                    _tg_send_message(chat_id, "Ожидаю числовой teamId (например: 1610612756). Попробуйте ещё раз.", reply_to_message_id=m["message_id"], reply_markup=_force_reply())
                    return PlainTextResponse("OK")
                set_player_team(pid, team_text)
                _clear_dialog(chat_id)
                _tg_send_message(chat_id, f"Команда обновлена: <b>{team_text}</b> для pid={pid}")
                return PlainTextResponse("OK")
            elif mode == "add_custom_pid":
                if not re.fullmatch(r"\d{2,}", text):
                    _tg_send_message(chat_id, "Нужен числовой PERSON_ID. Ещё раз.", reply_to_message_id=m["message_id"], reply_markup=_force_reply())
                    return PlainTextResponse("OK")
                pid = text
                set_player_ru_name(pid, f"Игрок {pid}")
                _clear_dialog(chat_id)
                _tg_send_message(chat_id, f"Добавлен базовый профиль pid={pid}. Теперь задайте /name или /team.")
                return PlainTextResponse("OK")

        # команды
        if text.startswith("/start") or text.startswith("/help"):
            _tg_send_message(chat_id, HELP_TEXT); return PlainTextResponse("OK")

        if text.startswith("/find"):
            q = text[len("/find"):].strip()
            if not q:
                _tg_send_message(chat_id, "Укажите часть имени: /find Doncic"); return PlainTextResponse("OK")
            res = find_player_by_name(q)
            if not res:
                # покажем кандидатов-угадайку
                cand = _best_candidates(q)
                if not cand:
                    _tg_send_message(chat_id, "Ничего не найдено. Можете добавить вручную: нажмите кнопку ниже.",
                                     reply_markup={"inline_keyboard":[
                                         [{"text":"Добавить вручную", "callback_data":"addcustom:" + q}]
                                     ]})
                    return PlainTextResponse("OK")
                kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get("personId"))}] for p in cand]
                kb.append([{"text":"Добавить вручную", "callback_data":"addcustom:"+q}])
                _tg_send_message(chat_id, "Не нашёл точного совпадения. Выберите:",
                                 reply_markup={"inline_keyboard": kb})
                return PlainTextResponse("OK")
            # покажем до 10
            lines = []
            for p in res[:10]:
                lines.append(f"• {p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')}, teamId={p.get('teamId')})")
            _tg_send_message(chat_id, "\n".join(lines) if lines else "Пусто")
            return PlainTextResponse("OK")

        if text.startswith("/name"):
            # /name <имя> — найдём игрока и спросим русское
            q = text[len("/name"):].strip()
            if not q:
                _tg_send_message(chat_id, "Формат: /name <имя на англ/рус>\nЯ спрошу, как записать по-русски.")
                return PlainTextResponse("OK")
            res = find_player_by_name(q)
            if not res:
                cand = _best_candidates(q)
                if not cand:
                    _tg_send_message(chat_id, "Игрок не найден. Можно добавить вручную: /find сначала выберите PID.")
                    return PlainTextResponse("OK")
                kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get("personId"))}] for p in cand]
                _tg_send_message(chat_id, "Выберите игрока:", reply_markup={"inline_keyboard": kb})
                return PlainTextResponse("OK")
            p = res[0]
            en = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
            msg = _tg_send_message(chat_id, f"Как подписать игрока <b>{en}</b> на русском?\nОтветьте на это сообщение именем.", reply_to_message_id=m["message_id"], reply_markup=_force_reply())
            _set_dialog(chat_id, {"mode":"set_ru_interactive", "pid": str(p.get("personId")), "ask_msg_id": msg["result"]["message_id"]})
            return PlainTextResponse("OK")

        if text.startswith("/team"):
            q = text[len("/team"):].strip()
            if not q:
                _tg_send_message(chat_id, "Формат: /team <имя> — я попрошу ввести teamId (например 1610612756).")
                return PlainTextResponse("OK")
            res = find_player_by_name(q)
            if not res:
                _tg_send_message(chat_id, "Игрок не найден. Сначала /find и выберите точного.")
                return PlainTextResponse("OK")
            p = res[0]
            en = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
            msg = _tg_send_message(chat_id, f"Введите числовой teamId для <b>{en}</b> (например 1610612756).", reply_to_message_id=m["message_id"], reply_markup=_force_reply())
            _set_dialog(chat_id, {"mode":"set_team_interactive", "pid": str(p.get("personId")), "ask_msg_id": msg["result"]["message_id"]})
            return PlainTextResponse("OK")

        if text.startswith("/alias"):
            # /alias <pid> <алиас>
            rest = text[len("/alias"):].strip()
            m2 = re.match(r"^(\d+)\s+(.+)$", rest)
            if not m2:
                _tg_send_message(chat_id, "Формат: /alias <pid> <алиас>"); return PlainTextResponse("OK")
            pid, alias = m2.group(1), m2.group(2)
            set_player_alias(pid, alias)
            _tg_send_message(chat_id, f"Алиас добавлен: <b>{alias}</b> для pid={pid}")
            return PlainTextResponse("OK")

        if text.startswith("/card"):
            parsed = _parse_card_cmd(text)
            if not parsed:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики через запятую> | [impact|single]")
                return PlainTextResponse("OK")
            name, stats, template = parsed
            res = find_player_by_name(name)
            if not res:
                # варианты + добавить вручную
                cand = _best_candidates(name)
                kb = [[{"text": f"{p.get('firstName','')} {p.get('lastName','')} (pid={p.get('personId')})", "callback_data": "pick:"+str(p.get("personId"))}] for p in cand]
                kb.append([{"text":"Добавить вручную", "callback_data":"addcustom:"+name}])
                _tg_send_message(chat_id, "Не нашёл точного игрока. Выберите ближайший:", reply_markup={"inline_keyboard": kb})
                return PlainTextResponse("OK")
            p = res[0]
            # спросим RU имя если его нет
            _ensure_ru_name_before_card(chat_id, m["message_id"], p, stats, template)
            return PlainTextResponse("OK")

        # если текст не распознан
        _tg_send_message(chat_id, HELP_TEXT); return PlainTextResponse("OK")

    return PlainTextResponse("OK")
