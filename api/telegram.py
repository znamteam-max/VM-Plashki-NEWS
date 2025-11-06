# api/telegram.py
# Полноценный Telegram webhook под FastAPI с интерактивом имен/команд/цветов и генерацией плашек PNG

from __future__ import annotations
import os, io, json, re, time, ssl, traceback
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# --- внешние модули проекта ---
# data: работа с реестром игроков/кэшем
from data import (
    get_players, get_players_index, find_player_by_name,
    refresh_players,
)
# team_brand: цвета клуба (формат гибко обрабатываем)
try:
    import team_brand
except Exception:
    team_brand = None  # переживём

# graphics: рендер PNG плашек
from graphics import (
    render_card, render_card2, render_card_bad, render_card_special,
    render_card_drN, render_card_dr,  # алиас на месте
)

# ------------------------------ CONFIG ------------------------------
APP = FastAPI()
BOT_TOKEN       = os.getenv("BOT_TOKEN","").strip()
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET","").strip() or "hook-123"

READ_TIMEOUT    = 20
CONNECT_TIMEOUT = 10

# Локальные «БД» для интерактива (переживают инстанс в пределах /tmp)
TMP_DIR = "/tmp"
RU_DB_PATH      = os.path.join(TMP_DIR, "ru_names.json")       # { personId: "Кевин Дюрэнт" }
ALIAS_DB_PATH   = os.path.join(TMP_DIR, "alias_map.json")      # { "дюрант": "201142", "durant": "201142" }
TEAM_OVR_PATH   = os.path.join(TMP_DIR, "team_override.json")  # { personId: "1610612756" }
COLOR_DB_PATH   = os.path.join(TMP_DIR, "team_colors.json")    # { teamId: "#RRGGBB" }  (если задан «свой цвет»)

LOGO_DIR = "assets/cache"  # реальные логотипы
POOP_ICON = "assets/icons/poop.png"

# ------------------------------ UTILS ------------------------------
def _log(*a: Any) -> None:
    try:
        print("[tg]", *a, flush=True)
    except Exception:
        pass

def _load_json(path: str) -> Any:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json(path: str, data: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        _log("save json err", path, e)

# Инициализация «БД»
RU_DB    : Dict[str, str] = _load_json(RU_DB_PATH)
ALIAS_DB : Dict[str, str] = _load_json(ALIAS_DB_PATH)
TEAM_OVR : Dict[str, str] = _load_json(TEAM_OVR_PATH)
COLOR_DB : Dict[str, str] = _load_json(COLOR_DB_PATH)

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _mk_aliases(display_name_ru: str) -> List[str]:
    # Простейшие алиасы: вся строка целиком, последняя фамилия,
    # без ё/е различий и без мягких знаков для расширенного поиска
    base = _norm(display_name_ru)
    tokens = [_norm(x) for x in re.split(r"[\s\-]+", display_name_ru) if x.strip()]
    alts = set([base])
    if tokens:
        alts.add(tokens[-1])
    # замены е/ё
    alts2 = set()
    for a in alts:
        alts2.add(a.replace("ё","е"))
        alts2.add(a.replace("e","ё"))  # на случай латиницы
    alts |= alts2
    return list(alts)

def _http_get(url: str, timeout: int = READ_TIMEOUT, headers: Optional[Dict[str,str]] = None) -> bytes:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (bot; like Gecko)",
        "Accept": "*/*",
        "Connection": "close",
        **(headers or {}),
    })
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()

def _open_logo(team_id: str) -> Optional[Any]:
    # ожидаем PNG в assets/cache/<teamId>.png
    pid = str(team_id)
    cand = [
        os.path.join(LOGO_DIR, f"{pid}.png"),
        os.path.join(LOGO_DIR, f"{pid}.webp"),
        os.path.join(LOGO_DIR, f"{pid}.jpg"),
    ]
    from PIL import Image
    for p in cand:
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except Exception:
                continue
    return None

def _ensure_headshot_image(person_id: str):
    # возвращает PIL.Image или None
    from PIL import Image
    # варианты CDN
    cdn = [
        f"https://cdn.nba.com/headshots/nba/latest/1040x760/{person_id}.png",
        f"https://cdn.nba.com/headshots/nba/latest/260x190/{person_id}.png",
    ]
    for u in cdn:
        try:
            raw = _http_get(u, timeout=12)
            bio = io.BytesIO(raw)
            return Image.open(bio).convert("RGBA")
        except Exception as e:
            _log("headshot get error", u, e)
            continue
    # вариант через прокси (если настроен)
    proxy = os.getenv("PLAYERS_IMG_PROXY","").strip()
    if proxy:
        u = f"{proxy}?u={person_id}"
        try:
            raw = _http_get(u, timeout=12)
            bio = io.BytesIO(raw)
            return Image.open(bio).convert("RGBA")
        except Exception as e:
            _log("headshot proxy error", u, e)
    return None

def _brand_tuple_from_any(brand_obj: Any) -> Tuple[str,str,str]:
    # Нормализуем что угодно в (primary, dark, light)
    if isinstance(brand_obj, dict):
        p = brand_obj.get("primary") or "#007ACC"
        d = brand_obj.get("dark")    or p
        l = brand_obj.get("light")   or p
        return (p, d, l)
    if isinstance(brand_obj, (list, tuple)):
        if len(brand_obj) == 3:
            return (brand_obj[0], brand_obj[1], brand_obj[2])
        if len(brand_obj) == 2:
            return (brand_obj[0], brand_obj[1], brand_obj[0])
        if len(brand_obj) == 1:
            return (brand_obj[0], brand_obj[0], brand_obj[0])
    return ("#007ACC", "#005A99", "#5FB4FF")

def _colors_for_team(team_id: str, prefer_hex: Optional[str] = None) -> Tuple[str,str,str]:
    # "Свой цвет" (всегда главный)
    if prefer_hex:
        return (prefer_hex, prefer_hex, prefer_hex)
    # заданный ранее выбор «свой»
    if team_id and team_id in COLOR_DB:
        hexc = COLOR_DB[team_id]
        return (hexc, hexc, hexc)
    # team_brand модуль
    if team_brand:
        try:
            # пробуем несколько API
            if hasattr(team_brand, "get"):
                return _brand_tuple_from_any(team_brand.get(team_id))
            if hasattr(team_brand, "brand_for_team"):
                return _brand_tuple_from_any(team_brand.brand_for_team(team_id))
            if hasattr(team_brand, "TEAM_BRAND"):
                return _brand_tuple_from_any(team_brand.TEAM_BRAND.get(team_id))
        except Exception as e:
            _log("team_brand err", e)
    # дефолт
    return ("#007ACC", "#005A99", "#5FB4FF")

def _player_display_name(player: Dict[str,Any]) -> str:
    pid = str(player.get("personId",""))
    if pid in RU_DB and RU_DB[pid]:
        return RU_DB[pid]
    # fallback: англ
    fn = (player.get("firstName") or "").strip()
    ln = (player.get("lastName") or "").strip()
    disp = (player.get("displayName") or "").strip()
    if disp:
        return disp
    return (fn + " " + ln).strip()

def _resolve_player_by_query(query: str) -> Optional[Dict[str,Any]]:
    # 1) точное совпадение с алиасами
    key = _norm(query)
    if key in ALIAS_DB:
        pid = ALIAS_DB[key]
        idx = get_players_index()
        return idx.get(str(pid))

    # 2) поиск по игрокам (en/ru)
    qs = _norm(query)
    best: Optional[Dict[str,Any]] = None
    for p in get_players():
        cand = _norm(p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}")
        if not cand:
            continue
        if qs in cand:
            best = p
            break
        # если есть русское имя — тоже проверим
        pid = str(p.get("personId",""))
        if pid in RU_DB:
            r = _norm(RU_DB[pid])
            if qs in r:
                best = p
                break
    if best:
        return best

    # 3) fuzzy via find_player_by_name (по displayName)
    arr = find_player_by_name(query) or []
    return arr[0] if arr else None

def _parse_stats(block: str) -> List[Tuple[str,str]]:
    # "10 очков, 12 передач, 15 подборов, 1 стилоблок"
    out: List[Tuple[str,str]] = []
    for chunk in re.split(r"[;,|]+", block):
        t = chunk.strip()
        if not t:
            continue
        # value + label
        m = re.match(r"^\s*([+\-]?\d+)\s*(.*)$", t)
        if m:
            out.append((m.group(1), m.group(2).strip()))
        else:
            out.append((t, ""))  # на всякий
    return out

def _tg_post(method: str, payload: Dict[str,Any]) -> Dict[str,Any]:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": "tg-bot/1.0",
        "Connection": "close",
    })
    with urlopen(req, timeout=25) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "raw": raw.decode("utf-8","ignore")}

def _tg_post_multipart(method: str, fields: Dict[str,Any], files: Dict[str,Tuple[str,bytes,str]]) -> Dict[str,Any]:
    # files = { fieldname: (filename, data_bytes, mime) }
    boundary = "----tgform" + str(int(time.time()*1000))
    body = io.BytesIO()
    def w(s: str): body.write(s.encode("utf-8"))
    for k, v in fields.items():
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{k}"\r\n\r\n')
        w(f"{v}\r\n")
    for fname, (filename, data, mime) in files.items():
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{fname}"; filename="{filename}"\r\n')
        w(f"Content-Type: {mime}\r\n\r\n")
        body.write(data)
        w("\r\n")
    w(f"--{boundary}--\r\n")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = Request(url, data=body.getvalue(), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "tg-bot/1.0",
        "Connection": "close",
    })
    with urlopen(req, timeout=35) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "raw": raw.decode("utf-8","ignore")}

def _send_text(chat_id: int, text: str, reply_to: Optional[int] = None, parse: str = "HTML") -> None:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse:
        payload["parse_mode"] = parse
    try:
        _tg_post("sendMessage", payload)
    except Exception as e:
        _log("sendMessage error", e)

def _send_png(chat_id: int, png: bytes, filename: str = "card.png", caption: Optional[str] = None, reply_to: Optional[int] = None) -> None:
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
        fields["parse_mode"] = "HTML"
    if reply_to:
        fields["reply_to_message_id"] = str(reply_to)
        fields["allow_sending_without_reply"] = "true"
    try:
        _tg_post_multipart("sendDocument", fields, {"document": (filename, png, "image/png")})
    except Exception as e:
        _log("sendDocument error", e)

# ------------------------------ INTERACTIVE PROMPTS ------------------------------
def _ask_russian_name(chat_id: int, msg_id: int, player: Dict[str,Any]) -> None:
    pid = str(player.get("personId",""))
    base_disp = (player.get("displayName") or f"{player.get('firstName','')} {player.get('lastName','')}")
    text = (
        f"Как подписать игрока <b>{base_disp}</b> на плашке?\n"
        f"Ответьте <u>на это сообщение</u> русским именем.\n"
        f"[setname:{pid}]"
    )
    _send_text(chat_id, text, reply_to=msg_id)

def _ask_team_override(chat_id: int, msg_id: int, player: Dict[str,Any]) -> None:
    pid = str(player.get("personId",""))
    cur = str(player.get("teamId","0") or "0")
    text = (
        f"Если нужно, укажите правильный <b>teamId</b> (число) для игрока "
        f"<b>{_player_display_name(player)}</b>. Ответьте на это сообщение числом.\n"
        f"Текущий: <code>{cur}</code>\n"
        f"[setteam:{pid}]"
    )
    _send_text(chat_id, text, reply_to=msg_id)

def _ask_color_choice(chat_id: int, msg_id: int, team_id: str) -> None:
    # два варианта: Авто и Свой цвет
    text = (
        "<i>Уточнения…</i>\n"
        "Выберите цвет плашки:\n"
        "• Авто — по цветам команды\n"
        "• Свой цвет — пришлите HEX вида <code>#RRGGBB</code>.\n"
        f"[setcolor:{team_id}]"
    )
    _send_text(chat_id, text, reply_to=msg_id)

# ------------------------------ COMMANDS ------------------------------
HELP = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /start — проверка связи\n"
    "• /help — помощь\n"
    "• /find <имя/фамилия> — найти игрока (на англ/рус)\n"
    "• /name <имя> — задать русское имя (ответом на запрос)\n"
    "• /team <имя> — задать/исправить teamId (ответом на запрос)\n"
    "• /card <игрок> | <статистика> — обычная плашка\n"
    "• /card2 <игрок1> | <стат> | <игрок2> | <стат> — парная плашка\n"
    "• /cardBAD <игрок> | <статистика> — «плохо сыграл» (коричневый, 💩)\n"
    "• /cardS <игрок> | <статистика> | <информация> — плашка с доп. блоком\n"
    "• /cardDR3|/cardDR4|/cardDR5 <игрок> | <статистика> — по шаблону DR\n"
)

def _parse_card_args(text: str, expect_parts: int) -> List[str]:
    # Разделяем по « | »
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < expect_parts:
        # попробуем альтернативные разделители
        parts = [p.strip() for p in re.split(r"\s+\|\s+|\s+\|\s*", text)]
    return parts

# ------------------------------ RENDER HELPERS ------------------------------
from PIL import Image

def _render_single_card(player: Dict[str,Any], stats_text: str, color_hex: Optional[str]) -> bytes:
    pid = str(player.get("personId",""))
    team_id = str(TEAM_OVR.get(pid, player.get("teamId","0") or "0"))
    head = _ensure_headshot_image(pid) or Image.new("RGBA", (1040,760), (0,0,0,0))
    logo = _open_logo(team_id)
    colors = _colors_for_team(team_id, prefer_hex=color_hex)
    disp  = _player_display_name(player)
    png = render_card("single", disp, "", logo, colors, head, _parse_stats(stats_text))
    return png

def _render_duo_card(p1: Dict[str,Any], s1: str, p2: Dict[str,Any], s2: str) -> bytes:
    pid1 = str(p1.get("personId","")); pid2 = str(p2.get("personId",""))
    team1 = str(TEAM_OVR.get(pid1, p1.get("teamId","0") or "0"))
    team2 = str(TEAM_OVR.get(pid2, p2.get("teamId","0") or "0"))
    h1 = _ensure_headshot_image(pid1) or Image.new("RGBA", (1040,760), (0,0,0,0))
    h2 = _ensure_headshot_image(pid2) or Image.new("RGBA", (1040,760), (0,0,0,0))
    l1 = _open_logo(team1)
    l2 = _open_logo(team2)
    c1 = _colors_for_team(team1)
    c2 = _colors_for_team(team2)
    d1 = _player_display_name(p1)
    d2 = _player_display_name(p2)
    return render_card2(d1, l1, c1, h1, _parse_stats(s1), d2, l2, c2, h2, _parse_stats(s2))

def _render_bad_card(player: Dict[str,Any], stats_text: str) -> bytes:
    pid = str(player.get("personId",""))
    head = _ensure_headshot_image(pid) or Image.new("RGBA", (1040,760), (0,0,0,0))
    disp = _player_display_name(player)
    return render_card_bad(disp, head, _parse_stats(stats_text))

def _render_special_card(player: Dict[str,Any], stats_text: str, info: str, color_hex: Optional[str]) -> bytes:
    pid = str(player.get("personId",""))
    team_id = str(TEAM_OVR.get(pid, player.get("teamId","0") or "0"))
    head = _ensure_headshot_image(pid) or Image.new("RGBA", (1040,760), (0,0,0,0))
    logo = _open_logo(team_id)
    colors = _colors_for_team(team_id, prefer_hex=color_hex)
    disp = _player_display_name(player)
    return render_card_special(disp, logo, colors, head, _parse_stats(stats_text), info)

def _render_dr_card(n: int, player: Dict[str,Any], stats_text: str) -> bytes:
    pid = str(player.get("personId",""))
    team_id = str(TEAM_OVR.get(pid, player.get("teamId","0") or "0"))
    head = _ensure_headshot_image(pid) or Image.new("RGBA", (1040,760), (0,0,0,0))
    logo = _open_logo(team_id)
    disp = _player_display_name(player)
    return render_card_drN(n, disp, head, logo, _parse_stats(stats_text))

# ------------------------------ TELEGRAM WEBHOOK ------------------------------
def _is_my_secret(req: Request) -> bool:
    sec = req.query_params.get("secret") or ""
    return (sec == WEBHOOK_SECRET)

@APP.get("/api/telegram")
async def telegram_get(request: Request):
    if not _is_my_secret(request):
        return JSONResponse({"detail":"bad secret"}, status_code=401)
    action = (request.query_params.get("action") or "").strip().lower()
    if action == "refresh":
        n, info = refresh_players(drop_cache=False)
        return JSONResponse({"ok": bool(info.get("ok", True)), "refreshed": True, "players_indexed": n, **info})
    if action == "diag":
        err = {}
        try:
            import graphics as g
            # проверяем наличие критичных атрибутов
            getattr(g, "render_card")
            getattr(g, "render_card2")
            getattr(g, "render_card_bad")
            getattr(g, "render_card_special")
            getattr(g, "render_card_drN")
            getattr(g, "render_card_dr")
        except Exception as e:
            err["graphics"] = traceback.format_exc()
        return JSONResponse({
            "ok": len(err)==0,
            "py": ".".join([str(x) for x in list(__import__('sys').version_info)[:3]]),
            "platform": __import__('platform').platform(),
            "modules": {"data":"ok","team_brand":"ok" if team_brand else "missing","graphics":"ok","Pillow": __import__('PIL').__version__},
            "errors": err or None,
            "has_bot_token": bool(BOT_TOKEN),
        })
    # ping
    return JSONResponse({"ok": True, "route": "telegram-get"})

@APP.post("/api/telegram")
async def telegram_post(request: Request):
    if not _is_my_secret(request):
        return PlainTextResponse("NO", status_code=401)
    try:
        upd = await request.json()
    except Exception:
        return PlainTextResponse("bad json", status_code=400)

    try:
        await handle_update(upd)
        return PlainTextResponse("OK")
    except Exception as e:
        _log("webhook handle error", traceback.format_exc())
        return PlainTextResponse("ERR", status_code=200)

# ------------------------------ UPDATE HANDLER ------------------------------
async def handle_update(update: Dict[str,Any]):
    msg = update.get("message") or update.get("edited_message")
    cb  = update.get("callback_query")
    if cb:
        chat_id = cb.get("message",{}).get("chat",{}).get("id")
        # сейчас не используем инлайн-кнопки — вся логика через reply-теги
        _send_text(chat_id, "<i>Эта версия использует ответы на сообщения (reply) для уточнений.</i>")
        return

    if not msg:
        return

    chat_id = msg.get("chat",{}).get("id")
    text    = (msg.get("text") or "").strip()

    # 1) обработка ответов на наши «теги» — setname/setteam/setcolor
    reply = msg.get("reply_to_message")
    if reply:
        rtext = reply.get("text") or ""
        # setname
        m = re.search(r"\[setname:(\d+)\]", rtext)
        if m:
            pid = m.group(1)
            ru = text.strip()
            if ru:
                RU_DB[pid] = ru
                for a in _mk_aliases(ru):
                    ALIAS_DB[a] = pid
                _save_json(RU_DB_PATH, RU_DB)
                _save_json(ALIAS_DB_PATH, ALIAS_DB)
                _send_text(chat_id, f"Готово. Теперь игрок будет подписан как: <b>{ru}</b>.")
            return
        # setteam
        m = re.search(r"\[setteam:(\d+)\]", rtext)
        if m:
            pid = m.group(1)
            tid = re.sub(r"[^\d]", "", text)
            if tid:
                TEAM_OVR[pid] = tid
                _save_json(TEAM_OVR_PATH, TEAM_OVR)
                _send_text(chat_id, f"Сохранил teamId <b>{tid}</b> для игрока <code>{pid}</code>.")
            return
        # setcolor
        m = re.search(r"\[setcolor:(\d+)\]", rtext)
        if m:
            team_id = m.group(1)
            hexc = (text or "").strip()
            if not re.search(r"#[0-9A-Fa-f]{6}", hexc):
                _send_text(chat_id, "HEX цвет должен быть в формате <code>#RRGGBB</code>. Попробуйте снова.", reply_to=reply.get("message_id"))
                return
            COLOR_DB[team_id] = hexc
            _save_json(COLOR_DB_PATH, COLOR_DB)
            _send_text(chat_id, f"Сохранил свой цвет для команды <b>{team_id}</b>: <code>{hexc}</code>.")
            return

    # 2) обычные команды
    if text.startswith("/start"):
        _send_text(chat_id, "Я здесь. Отправь /help для списка команд.")
        return

    if text.startswith("/help"):
        _send_text(chat_id, HELP)
        return

    if text.startswith("/find"):
        q = text[len("/find"):].strip()
        if not q:
            _send_text(chat_id, "Пример: <code>/find Doncic</code>")
            return
        p = _resolve_player_by_query(q)
        if not p:
            _send_text(chat_id, f"Не нашёл игрока: {q}")
            return
        _send_text(chat_id, f"{(p.get('displayName') or (p.get('firstName','')+' '+p.get('lastName',''))).strip()} (id={p.get('personId')}, teamId={p.get('teamId')})")
        return

    if text.startswith("/name"):
        q = text[len("/name"):].strip()
        if not q:
            _send_text(chat_id, "Пример: <code>/name Kevin Durant</code>")
            return
        p = _resolve_player_by_query(q)
        if not p:
            _send_text(chat_id, f"Не нашёл игрока: {q}")
            return
        _ask_russian_name(chat_id, msg.get("message_id"), p)
        return

    if text.startswith("/team"):
        q = text[len("/team"):].strip()
        if not q:
            _send_text(chat_id, "Пример: <code>/team LeBron James</code>")
            return
        p = _resolve_player_by_query(q)
        if not p:
            _send_text(chat_id, f"Не нашёл игрока: {q}")
            return
        _ask_team_override(chat_id, msg.get("message_id"), p)
        return

    # --- /card ---
    if text.startswith("/card "):
        try:
            body = text[len("/card "):].strip()
            parts = _parse_card_args(body, 2)
            if len(parts) < 2:
                _send_text(chat_id, "Формат: <code>/card игрок | статистика</code>")
                return
            qname, stats_text = parts[0], parts[1]
            p = _resolve_player_by_query(qname)
            if not p:
                _send_text(chat_id, f"Не нашёл игрока: {qname}")
                return
            # Русское имя?
            pid = str(p.get("personId",""))
            if pid not in RU_DB:
                _ask_russian_name(chat_id, msg.get("message_id"), p)
                return
            # Цвет?
            team_id = str(TEAM_OVR.get(pid, p.get("teamId","0") or "0"))
            _ask_color_choice(chat_id, msg.get("message_id"), team_id)
            _send_text(chat_id, "<i>Готовлю плашку…</i>")
            # если пользователь не ответит — пойдём по «авто»
            png = _render_single_card(p, stats_text, color_hex=None)
            _send_png(chat_id, png, filename="card.png")
        except Exception as e:
            _send_text(chat_id, f"Ошибка: {e}")
        return

    # --- /card2 ---
    if text.startswith("/card2 "):
        try:
            body = text[len("/card2 "):].strip()
            parts = _parse_card_args(body, 4)
            if len(parts) < 4:
                _send_text(chat_id, "Формат: <code>/card2 игрок1 | статистика | игрок2 | статистика</code>")
                return
            q1, s1, q2, s2 = parts[0], parts[1], parts[2], parts[3]
            p1 = _resolve_player_by_query(q1); p2 = _resolve_player_by_query(q2)
            if not p1 or not p2:
                _send_text(chat_id, "Не нашёл одного из игроков.")
                return
            # убедимся, что есть русские имена
            for p in (p1, p2):
                pid = str(p.get("personId",""))
                if pid not in RU_DB:
                    _ask_russian_name(chat_id, msg.get("message_id"), p)
                    return
            _send_text(chat_id, "<i>Готовлю плашку…</i>")
            png = _render_duo_card(p1, s1, p2, s2)
            _send_png(chat_id, png, filename="card2.png")
        except Exception as e:
            _send_text(chat_id, f"Ошибка: {e}")
        return

    # --- /cardBAD ---
    if text.startswith("/cardBAD "):
        try:
            body = text[len("/cardBAD "):].strip()
            parts = _parse_card_args(body, 2)
            if len(parts) < 2:
                _send_text(chat_id, "Формат: <code>/cardBAD игрок | статистика</code>")
                return
            qname, stats_text = parts[0], parts[1]
            p = _resolve_player_by_query(qname)
            if not p:
                _send_text(chat_id, f"Не нашёл игрока: {qname}")
                return
            pid = str(p.get("personId",""))
            if pid not in RU_DB:
                _ask_russian_name(chat_id, msg.get("message_id"), p)
                return
            _send_text(chat_id, "<i>Готовлю плашку…</i>")
            png = _render_bad_card(p, stats_text)
            _send_png(chat_id, png, filename="card_bad.png")
        except Exception as e:
            _send_text(chat_id, f"Ошибка: {e}")
        return

    # --- /cardS ---
    if text.startswith("/cardS "):
        try:
            body = text[len("/cardS "):].strip()
            parts = _parse_card_args(body, 3)
            if len(parts) < 3:
                _send_text(chat_id, "Формат: <code>/cardS игрок | статистика | информация</code>")
                return
            qname, stats_text, info = parts[0], parts[1], parts[2]
            p = _resolve_player_by_query(qname)
            if not p:
                _send_text(chat_id, f"Не нашёл игрока: {qname}")
                return
            pid = str(p.get("personId",""))
            if pid not in RU_DB:
                _ask_russian_name(chat_id, msg.get("message_id"), p)
                return
            team_id = str(TEAM_OVR.get(pid, p.get("teamId","0") or "0"))
            _ask_color_choice(chat_id, msg.get("message_id"), team_id)
            _send_text(chat_id, "<i>Готовлю плашку…</i>")
            png = _render_special_card(p, stats_text, info, color_hex=None)
            _send_png(chat_id, png, filename="cardS.png")
        except Exception as e:
            _send_text(chat_id, f"Ошибка: {e}")
        return

    # --- /cardDR3|4|5 ---
    m = re.match(r"^/(cardDR([345]))\s+(.*)$", text)
    if m:
        try:
            n = int(m.group(2))
            body = m.group(3).strip()
            parts = _parse_card_args(body, 2)
            if len(parts) < 2:
                _send_text(chat_id, f"Формат: <code>/{m.group(1)} игрок | статистика</code>")
                return
            qname, stats_text = parts[0], parts[1]
            p = _resolve_player_by_query(qname)
            if not p:
                _send_text(chat_id, f"Не нашёл игрока: {qname}")
                return
            pid = str(p.get("personId",""))
            if pid not in RU_DB:
                _ask_russian_name(chat_id, msg.get("message_id"), p)
                return
            _send_text(chat_id, "<i>Готовлю плашку…</i>")
            png = _render_dr_card(n, p, stats_text)
            _send_png(chat_id, png, filename=f"cardDR{n}.png")
        except Exception as e:
            _send_text(chat_id, f"Ошибка: {e}")
        return

    # Если мы тут — неизвестная команда
    _send_text(chat_id, HELP)
