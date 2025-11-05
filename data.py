# /api/telegram.py — Telegram webhook + админ-команды правок игроков
import os, re, json, requests, hashlib
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI()

BOT_TOKEN       = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "hook")
WORKER_ADMIN_URL = os.environ.get("WORKER_ADMIN_URL", "").rstrip("/")  # https://...workers.dev/admin
WORKER_ADMIN_TOKEN = os.environ.get("WORKER_ADMIN_TOKEN", "")
ADMIN_CHAT_IDS = set([int(x) for x in (os.environ.get("ADMIN_CHAT_IDS","").split(",")) if x.strip().isdigit()])

# Аббревиатуры → teamId
TEAM_ABBR = {
    "ATL":1610612737,"BOS":1610612738,"CLE":1610612739,"NOP":1610612740,"CHI":1610612741,"DAL":1610612742,
    "DEN":1610612743,"GSW":1610612744,"HOU":1610612745,"LAC":1610612746,"LAL":1610612747,"MIA":1610612748,
    "MIL":1610612749,"MIN":1610612750,"BKN":1610612751,"NYK":1610612752,"ORL":1610612753,"IND":1610612754,
    "PHI":1610612755,"PHX":1610612756,"POR":1610612757,"SAC":1610612758,"SAS":1610612759,"OKC":1610612760,
    "TOR":1610612761,"UTA":1610612762,"MEM":1610612763,"WAS":1610612764,"DET":1610612765,"CHA":1610612766,
}

STAT_PAIR_RE = re.compile(r"(?P<num>[-+]?\d+(?:[.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})")

def tg_send_png(chat_id: int, png_bytes: bytes, caption: str | None = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    files = {"document": ("card.png", png_bytes, "image/png")}
    data = {"chat_id": chat_id}
    if caption: data["caption"] = caption
    r = requests.post(url, data=data, files=files, timeout=30)
    return r.ok, r.text

def tg_send_message(chat_id: int, text: str, reply_to_message_id: int | None = None, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=15)

def tg_answer_callback(callback_id: str, text: str = ""):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                  json={"callback_query_id": callback_id, "text": text}, timeout=10)

def parse_card(text: str):
    if not text or not text.lower().startswith("/card"): return None
    body = text.split(" ", 1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2: return None
    name = parts[0]; raw_stats = parts[1]
    template = (parts[2].strip().lower() if len(parts) >= 3 and parts[2] else "single")
    note = (parts[3].strip() if len(parts) >= 4 and parts[3] else None)
    stats = []
    for token in re.split(r"[,;/\n]", raw_stats):
        m = STAT_PAIR_RE.search(token.strip())
        if m:
            num = m.group("num").replace(",", ".")
            label = (m.group("label") or "").strip()
            if m.group(2) == "%" and not label: label = "%"
            stats.append((num, label))
    return name, stats[:6], template, note, raw_stats

def parse_alias(text: str):
    if not text or not text.lower().startswith("/alias"): return None
    body = text.split(" ", 1)[1] if " " in text else ""
    m = re.split(r"\s*=\s*", body, maxsplit=1)
    if len(m) != 2: return None
    return m[0].strip(), m[1].strip()

def parse_setru(text: str):
    if not text.lower().startswith("/setru"): return None
    body = text.split(" ",1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) != 2: return None
    return parts[0], parts[1]  # original_name, ru_name

def parse_setteam(text: str):
    if not text.lower().startswith("/setteam"): return None
    body = text.split(" ",1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) != 2: return None
    return parts[0], parts[1]  # name, team token

def parse_addplayer(text: str):
    # /addplayer Имя Фамилия | TEAM | Русское Имя(опц.) | personId(опц.)
    if not text.lower().startswith("/addplayer"): return None
    body = text.split(" ",1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2: return None
    name = parts[0]
    team_token = parts[1]
    ru_name = parts[2] if len(parts) >= 3 and parts[2] else None
    pid = parts[3] if len(parts) >= 4 and parts[3] else None
    return name, team_token, ru_name, pid

def require_admin(chat_id: int) -> bool:
    return (not ADMIN_CHAT_IDS) or (chat_id in ADMIN_CHAT_IDS)

def team_token_to_id(token: str) -> int | None:
    t = token.strip().upper()
    if t.isdigit(): return int(t)
    if t in TEAM_ABBR: return TEAM_ABBR[t]
    # попробовать полное англ. имя
    try:
        from data import TEAM_NAMES
        for tid, nm in TEAM_NAMES.items():
            if nm.strip().upper() == t: return tid
    except Exception:
        pass
    return None

def synthetic_person_id(full_name: str) -> int:
    # стабильный отрицательный ID
    h = int(hashlib.sha1(full_name.encode("utf-8")).hexdigest()[:8], 16)
    return - (10_000_000 + (h % 9_000_000))

def worker_admin_call(path: str, payload: dict) -> tuple[bool, str]:
    if not WORKER_ADMIN_URL or not WORKER_ADMIN_TOKEN:
        return False, "Worker admin URL/TOKEN not configured"
    url = WORKER_ADMIN_URL + path
    headers = {"Content-Type":"application/json","X-Admin-Token":WORKER_ADMIN_TOKEN}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        return r.ok, (r.text or "")
    except Exception as e:
        return False, repr(e)

def local_refresh_index():
    # моментально обновим индекс внутри текущего воркера (без HTTP)
    try:
        from data import drop_players_cache, _ensure_index, players_count
        drop_players_cache()
        _ensure_index(force=True)
        return players_count()
    except Exception:
        return -1

async def handle_update(update: dict) -> JSONResponse:
    from graphics import render_card
    from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png, get_player_by_id, suggest_players, add_alias

    # callbacks
    if "callback_query" in update:
        cq = update["callback_query"]; data = cq.get("data") or ""; chat_id = cq["message"]["chat"]["id"]
        if data.startswith("pick:"):
            try: pid = int(data.split(":",1)[1])
            except Exception:
                tg_answer_callback(cq["id"], "Ошибка выбора"); return JSONResponse({"ok": True})
            origin = cq["message"].get("reply_to_message")
            if not origin or not origin.get("text"):
                tg_answer_callback(cq["id"], "Не нашёл исходную команду — отправьте /card заново."); return JSONResponse({"ok": True})
            parsed = parse_card(origin["text"])
            if not parsed:
                tg_answer_callback(cq["id"], "Не смог разобрать исходную команду."); return JSONResponse({"ok": True})
            _name, stats, template, note, _raw = parsed
            player = get_player_by_id(pid)
            if not player:
                tg_answer_callback(cq["id"], "Игрок не найден по ID."); return JSONResponse({"ok": True})
            logo_path, team_colors = ensure_team_logo_png(player["team_id"])
            head_path = ensure_headshot_png(player["id"], player["full_name"])
            png_bytes = render_card(template=template, player_name=player["display"] or player["full_name"],
                                    team_name=player["team_name"], team_logo_path=logo_path, team_colors=team_colors,
                                    headshot_path=head_path, stats=stats, note=note)
            tg_answer_callback(cq["id"], f"Выбрано: {player['display']}"); tg_send_png(chat_id, png_bytes)
            return JSONResponse({"ok": True})
        tg_answer_callback(cq["id"]); return JSONResponse({"ok": True})

    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg: return JSONResponse({"ok": True})
    chat_id = msg["chat"]["id"]; text = msg.get("text") or ""

    # --- админ-команды ---
    if text.startswith("/addplayer") or text.startswith("/setteam") or text.startswith("/setru"):
        if not require_admin(chat_id):
            tg_send_message(chat_id, "Недостаточно прав."); return JSONResponse({"ok": True})

        # /addplayer
        p = parse_addplayer(text)
        if p:
            name, team_tok, ru_name, pid = p
            tid = team_token_to_id(team_tok)
            if not tid:
                tg_send_message(chat_id, "Неверная команда. Укажи команду числом или аббревиатурой (например, IND, GSW).")
                return JSONResponse({"ok": True})
            parts = [w for w in name.split(" ") if w]
            first, last = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (name, "")
            if not pid or not str(pid).strip().lstrip("-").isdigit():
                pid = synthetic_person_id(name)
            else:
                pid = int(str(pid))
            ok, resp = worker_admin_call("/add", {"personId": pid, "firstName": first, "lastName": last, "teamId": tid, "ruName": ru_name})
            if ok:
                cnt = local_refresh_index()
                tg_send_message(chat_id, f"✅ Игрок добавлен: {name} (id {pid}), teamId={tid}. Индекс: {cnt}.")
            else:
                tg_send_message(chat_id, f"❌ Не удалось добавить: {resp}")
            return JSONResponse({"ok": True})

        # /setteam
        p = parse_setteam(text)
        if p:
            name, team_tok = p
            player = find_player_by_name(name)
            if not player:
                tg_send_message(chat_id, "Игрок не найден по имени. Можно сначала добавить через /addplayer")
                return JSONResponse({"ok": True})
            tid = team_token_to_id(team_tok)
            if not tid:
                tg_send_message(chat_id, "Неверная команда. Укажи команду числом или аббревиатурой (например, IND, GSW).")
                return JSONResponse({"ok": True})
            ok, resp = worker_admin_call("/setteam", {"personId": player["id"], "teamId": tid})
            if ok:
                cnt = local_refresh_index()
                tg_send_message(chat_id, f"✅ Обновил команду: {player['display']} → teamId {tid}. Индекс: {cnt}.")
            else:
                tg_send_message(chat_id, f"❌ Не удалось обновить: {resp}")
            return JSONResponse({"ok": True})

        # /setru
        p = parse_setru(text)
        if p:
            name, ru = p
            player = find_player_by_name(name)
            payload = {"ruName": ru}
            if player: payload["personId"] = player["id"]
            else: payload["fullName"] = name
            ok, resp = worker_admin_call("/setru", payload)
            if ok:
                cnt = local_refresh_index()
                who = player["display"] if player else name
                tg_send_message(chat_id, f"✅ Обновил русское имя: {who} → «{ru}». Индекс: {cnt}.")
            else:
                tg_send_message(chat_id, f"❌ Не удалось обновить: {resp}")
            return JSONResponse({"ok": True})

    # --- /alias ---
    alias_pair = parse_alias(text)
    if alias_pair:
        from data import find_player_by_name, add_alias
        alias_text, correct_text = alias_pair
        target = find_player_by_name(correct_text)
        if not target:
            tg_send_message(chat_id, "Не нашёл игрока справа от '='. Пример:\n/alias Швед = Alexey Shved")
            return JSONResponse({"ok": True})
        ok = add_alias(alias_text, target["full_name"])
        tg_send_message(chat_id, f"{'Готово' if ok else 'Ошибка'}. Теперь «{alias_text}» = {target['display']}.")
        return JSONResponse({"ok": True})

    # --- /card ---
    parsed = parse_card(text)
    if not parsed:
        hint = ("Формат:\n"
                "/card Имя | 25 очков, 12 подборов, 3 блокшота | impact | подпись\n"
                "/addplayer Имя Фамилия | IND | Егор Дёмин | -90000001\n"
                "/setteam Pascal Siakam | IND\n"
                "/setru John Tonje | Джон Тонджей\n"
                "Шаблоны: single, pair, single_note, impact, bad")
        tg_send_message(chat_id, hint); return JSONResponse({"ok": True})

    name, stats, template, note, _raw = parsed
    from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png, suggest_players
    player = find_player_by_name(name)
    if not player:
        suggestions = suggest_players(name, limit=4)
        kb_rows = [[{"text": s["display"], "callback_data": f"pick:{s['id']}"}] for s in suggestions]
        kb_rows.append([{"text": "➕ Добавить вручную (/addplayer)", "callback_data": "noop"}])
        kb = {"inline_keyboard": kb_rows}
        tg_send_message(chat_id, "Игрок не найден. Возможно, вы имели в виду:", reply_to_message_id=msg.get("message_id"), reply_markup=kb)
        return JSONResponse({"ok": True})

    logo_path, team_colors = ensure_team_logo_png(player["team_id"])
    head_path = ensure_headshot_png(player["id"], player["full_name"])
    png_bytes = render_card(template=template, player_name=player["display"] or player["full_name"],
                            team_name=player["team_name"], team_logo_path=logo_path, team_colors=team_colors,
                            headshot_path=head_path, stats=stats, note=note)
    ok, resp = tg_send_png(chat_id, png_bytes)
    if not ok: tg_send_message(chat_id, f"Ошибка отправки изображения: {resp}")
    return JSONResponse({"ok": True})

@app.get("/")
@app.get("/api/telegram")
async def health(action: str = Query(default=""), secret: str = Query(default=""), drop_cache: int = Query(default=0), debug: int = Query(default=0)):
    if action == "refresh":
        if secret != WEBHOOK_SECRET: raise HTTPException(status_code=404, detail="Not found")
        try:
            from data import drop_players_cache, _ensure_index, players_count
            if drop_cache: drop_players_cache()
            _ensure_index(force=True); cnt = players_count()
            return {"ok": True, "refreshed": True, "players_indexed": cnt}
        except Exception as e:
            return {"ok": False, "error": repr(e)}
    else:
        try:
            from data import players_count; cnt = players_count()
        except Exception: cnt = -1
        return {"ok": True, "players_indexed": cnt, "endpoints": [
            "GET  /api/telegram (action=refresh&secret=...)",
            "POST /api/telegram?secret=...  (Telegram webhook)"
        ]}

@app.post("/")
@app.post("/api/telegram")
async def webhook_query(request: Request, secret: str = Query(default="")):
    if not BOT_TOKEN: raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET: raise HTTPException(status_code=404, detail="Not found")
    try: update = await request.json()
    except Exception: return JSONResponse({"ok": True})
    return await handle_update(update)

@app.post("/webhook/{secret}")
@app.post("/api/telegram/webhook/{secret}")
async def webhook_path(request: Request, secret: str):
    if not BOT_TOKEN: raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET: raise HTTPException(status_code=404, detail="Not found")
    try: update = await request.json()
    except Exception: return JSONResponse({"ok": True})
    return await handle_update(update)
