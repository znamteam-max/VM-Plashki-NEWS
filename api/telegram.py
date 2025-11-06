# api/telegram.py — FastAPI вебхук и утилиты
import os, json, re, io, mimetypes, uuid
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from data import (
    refresh_players, find_player_by_name, get_players_index, get_players,
    ensure_headshot_png, display_name_for, open_headshot_variants,
    set_player_ru_name, set_player_team, get_overrides
)
from graphics import render_card, parse_metrics
from teams import team_name, team_primary_color
from PIL import Image

BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS","").replace(";",",").split(",") if x.strip().isdigit()]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()

# ------------- Telegram helpers -------------
async def tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None):
    import urllib.request
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    req = urllib.request.Request(f"{TG_API}/sendMessage",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        _ = resp.read()

async def tg_send_document(chat_id: int, filename: str, content: bytes, caption: Optional[str] = None, reply_to: Optional[int] = None):
    # multipart/form-data вручную (без requests)
    import urllib.request
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    parts: List[bytes] = []
    def add_field(name: str, value: str):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode())
        parts.append(b"\r\n")
    def add_file(name: str, filename: str, data: bytes, ctype: str):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        parts.append(data)
        parts.append(b"\r\n")
    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)
        add_field("parse_mode", "HTML")
    if reply_to:
        add_field("reply_to_message_id", str(reply_to))
        add_field("allow_sending_without_reply", "true")
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    add_file("document", filename, content, ctype)
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{TG_API}/sendDocument", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        _ = resp.read()

def is_admin(user_id: Optional[int]) -> bool:
    if not ADMIN_IDS:
        return True  # если не задано — разрешаем всем
    if user_id is None:
        return False
    return int(user_id) in ADMIN_IDS

# ------------- Routing -------------
def _check_secret(req: Request):
    secret = req.query_params.get("secret", "")
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        raise HTTPException(403, "bad secret")

@app.get("/api/telegram")
async def telegram_get(request: Request):
    _check_secret(request)
    action = request.query_params.get("action", "").strip().lower()
    if action == "refresh":
        count, meta = refresh_players(drop_cache=False)
        return JSONResponse({"ok": bool(meta.get("ok", False)), "refreshed": True, "players_indexed": meta.get("players_indexed", 0), **meta})
    elif action == "health":
        return JSONResponse({"ok": True, "route": "telegram-health"})
    return JSONResponse({"ok": True, "route": "telegram-get"})

@app.post("/api/telegram")
async def webhook(request: Request):
    _check_secret(request)
    upd = await request.json()
    return await handle_update(upd)

# ------------- Update handling -------------
async def handle_update(update: Dict[str, Any]):
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
    if not msg:
        return JSONResponse({"ok": True, "skip": True})
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    from_user = msg.get("from") or {}
    user_id = from_user.get("id")
    text = (msg.get("text") or "").strip()

    # reply-сценарии для setname/setteam
    if msg.get("reply_to_message"):
        replied = msg["reply_to_message"]
        replied_text = (replied.get("text") or "")
        # setname
        m = re.search(r"\[setname:(\d+)\]", replied_text)
        if m and is_admin(user_id):
            pid = m.group(1)
            ru = text.strip()
            set_player_ru_name(pid, ru, alias=None)
            # добавим разумные алиасы
            parts = ru.split()
            last = parts[-1].lower() if parts else ru.lower()
            simple = (last.replace("ё","е").replace("й","и").replace("ю","у").replace("я","а").replace("э","е"))
            if simple and simple != last:
                set_player_ru_name(pid, ru, alias=simple)
            idx = get_players_index()
            en_last = (idx.get(pid, {}) or {}).get("lastName", "")
            if en_last:
                set_player_ru_name(pid, ru, alias=en_last.lower())
            await tg_send_message(chat_id, f"✔ Имя сохранено для {pid}: {ru}", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})

        # setteam
        m = re.search(r"\[setteam:(\d+)\]", replied_text)
        if m and is_admin(user_id):
            pid = m.group(1)
            t = re.sub(r"[^\d]", "", text)
            if t:
                set_player_team(pid, t)
                await tg_send_message(chat_id, f"✔ Команда сохранена для {pid}: {t}", reply_to=msg.get("message_id"))
                return JSONResponse({"ok": True})

    # команды
    lower = text.lower()
    if lower.startswith("/start") or lower.startswith("/help"):
        return await _cmd_help(chat_id)

    if lower.startswith("/find"):
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if not q:
            await tg_send_message(chat_id, "Использование: <code>/find Имя</code>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        ps = find_player_by_name(q)
        if not ps:
            await tg_send_message(chat_id, f"Не нашёл: <b>{q}</b>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        lines = []
        for p in ps[:20]:
            pid = p.get("personId")
            nm = display_name_for(p)
            lines.append(f"• <b>{nm}</b> — id <code>{pid}</code>, teamId <code>{p.get('teamId','0')}</code>")
        await tg_send_message(chat_id, "\n".join(lines), reply_to=msg.get("message_id"))
        return JSONResponse({"ok": True})

    if lower.startswith("/name"):
        if not is_admin(user_id):
            await tg_send_message(chat_id, "Недостаточно прав для /name", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if not q:
            await tg_send_message(chat_id, "Использование: <code>/name Kevin Durant</code>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        ps = find_player_by_name(q)
        if not ps:
            await tg_send_message(chat_id, f"Не нашёл игрока: <b>{q}</b>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        p = ps[0]
        pid = p.get("personId")
        base_name = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
        await tg_send_message(chat_id, f"Как подписать игрока {base_name} на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]")
        return JSONResponse({"ok": True})

    if lower.startswith("/team"):
        if not is_admin(user_id):
            await tg_send_message(chat_id, "Недостаточно прав для /team", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        q = text.split(" ", 1)[1].strip() if " " in text else ""
        if not q:
            await tg_send_message(chat_id, "Использование: <code>/team Kevin Durant</code>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        ps = find_player_by_name(q)
        if not ps:
            await tg_send_message(chat_id, f"Не нашёл игрока: <b>{q}</b>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        p = ps[0]
        pid = p.get("personId")
        await tg_send_message(chat_id, f"Введите teamId для игрока id {pid}.\nОтветьте числом.\n[setteam:{pid}]")
        return JSONResponse({"ok": True})

    if lower.startswith("/setname"):
        if not is_admin(user_id):
            await tg_send_message(chat_id, "Недостаточно прав", reply_to=msg.get("message_id")); return JSONResponse({"ok": True})
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            await tg_send_message(chat_id, "Исп: /setname <personId> <Русское Имя>", reply_to=msg.get("message_id")); return JSONResponse({"ok": True})
        pid, ru = parts[1], parts[2].strip()
        set_player_ru_name(pid, ru, alias=None)
        await tg_send_message(chat_id, f"✔ Имя сохранено для {pid}: {ru}", reply_to=msg.get("message_id"))
        return JSONResponse({"ok": True})

    if lower.startswith("/setteam"):
        if not is_admin(user_id):
            await tg_send_message(chat_id, "Недостаточно прав", reply_to=msg.get("message_id")); return JSONResponse({"ok": True})
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or (not parts[1].isdigit()) or (not parts[2].isdigit()):
            await tg_send_message(chat_id, "Исп: /setteam <personId> <teamId>", reply_to=msg.get("message_id")); return JSONResponse({"ok": True})
        pid, tid = parts[1], parts[2]
        set_player_team(pid, tid)
        await tg_send_message(chat_id, f"✔ Команда сохранена для {pid}: {tid}", reply_to=msg.get("message_id"))
        return JSONResponse({"ok": True})

    if lower.startswith("/alias"):
        if not is_admin(user_id):
            await tg_send_message(chat_id, "Недостаточно прав", reply_to=msg.get("message_id")); return JSONResponse({"ok": True})
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            await tg_send_message(chat_id, "Исп: /alias <personId> <синоним>", reply_to=msg.get("message_id")); return JSONResponse({"ok": True})
        pid, alias = parts[1], parts[2].strip()
        ov = get_overrides()
        ru = (ov.get(pid) or {}).get("ruName", "")
        if not ru:
            idx = get_players_index()
            base_name = ((idx.get(pid, {}) or {}).get("firstName","") + " " + (idx.get(pid, {}) or {}).get("lastName","")).strip()
            ru = base_name or "Имя не задано"
        set_player_ru_name(pid, ru, alias=alias.lower())
        await tg_send_message(chat_id, f"✔ Алиас добавлен для {pid}: {alias}", reply_to=msg.get("message_id"))
        return JSONResponse({"ok": True})

    if lower.startswith("/card"):
        # /card <имя> | <метрики> | [impact|single]
        body = text[len("/card"):].strip()
        parts = [x.strip() for x in body.split("|")]
        if len(parts) < 2:
            await tg_send_message(chat_id, "Формат: <code>/card Имя | 10 очков, 12 передач | impact</code>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        qname = parts[0]
        metrics = parse_metrics(parts[1])
        template = "single"
        if len(parts) >= 3:
            t = parts[2].lower()
            if "impact" in t: template = "impact"
        players = find_player_by_name(qname)
        if not players:
            await tg_send_message(chat_id, f"Не нашёл игрока: <b>{qname}</b>", reply_to=msg.get("message_id"))
            return JSONResponse({"ok": True})
        p = players[0]
        pid = p.get("personId")
        # имя для подписи
        disp = display_name_for(p)
        # команда
        team_id = str(p.get("teamId") or "0")
        ov = get_overrides().get(str(pid)) or {}
        if ov.get("teamId"):
            team_id = str(ov["teamId"])
        team_nm = team_name(team_id) if team_id and team_id.isdigit() else "Free Agent"
        # headshot (с попытками разных размеров)
        head_url = ensure_headshot_png(p, size="1040x760")
        head_im = open_headshot_variants(head_url)
        if not head_im:
            # финальный fallback — прозрачная заглушка
            head_im = Image.new("RGBA", (1040, 1040), (0,0,0,0))
        # собираем карточку
        png = render_card(
            template=template,
            player_name=disp,
            team_name=team_nm,
            team_logo=team_id,          # передаём teamId — внутри подберутся рабочие URL
            team_colors=None,           # пусть сам вычислит по teamId
            headshot_image=head_im,
            stats=metrics,
            note=None,
        )
        # отправляем как документ (PNG с альфой сохранится)
        cap = f"{disp} • {team_nm}"
        await tg_send_document(chat_id, f"card_{pid}.png", png, caption=cap, reply_to=msg.get("message_id"))
        return JSONResponse({"ok": True})

    # иначе — help
    return await _cmd_help(chat_id)

async def _cmd_help(chat_id: int):
    HELP = (
        "Привет! Я онлайн 🤖\n\n"
        "Команды:\n"
        "• /start — проверка связи\n"
        "• /find <имя/фамилия> — найти игрока (например: /find Doncic)\n"
        "• /card <имя> | <метрики через запятую> | [impact|single]\n"
        "  пример: <code>/card wembanyama | 10 очков, 12 передач | impact</code>\n"
        "• /name <имя> — интерактивно задать русское имя (ответом на сообщение)\n"
        "• /team <имя> — интерактивно задать/переопределить teamId\n"
        "• /setname <personId> <Русское Имя> — прямое сохранение (работает в группах)\n"
        "• /setteam <personId> <teamId> — прямое сохранение teamId\n"
        "• /alias <personId> <вариант> — добавить синоним для поиска\n"
        "• /help — это сообщение"
    )
    await tg_send_message(chat_id, HELP)
    return JSONResponse({"ok": True})
