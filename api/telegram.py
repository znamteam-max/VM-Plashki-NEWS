# api/telegram.py — стабильная версия (revert-friendly)
# Поддержка плашек: /card, /card2, /cards, /cardbad
# Поиск — только по локальному кэшу (norm), без passthrough-хаков.
# Грузим игроков через data.get_players()/refresh_players().

from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

# ----------------- ENV / DEBUG -----------------
DEBUG = os.getenv("DEBUG", "1") in ("1", "true", "yes")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ORIGIN = os.getenv("API_ORIGIN")

def _log(*a: Any) -> None:
    try:
        print(*a, flush=True)
    except:
        pass

def _safe_import(modname: str, names: List[str]):
    try:
        m = __import__(modname, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, out, None
    except Exception as e:
        return None, [], f"{e.__class__.__name__}: {e}"

# ----------------- OPTIONAL IMPORTS -----------------
# data
_data_mod, _data_objs, _data_err = _safe_import("data", [
    "get_players", "refresh_players", "find_player_by_name", "display_name_for",
    "overrides_save_name_ru", "overrides_get_name_ru",
    "ensure_headshot_png", "ensure_team_logo_png",
])
if _data_err and DEBUG: _log("[boot] data import error:", _data_err)
(
    get_players, refresh_players, find_player_by_name, display_name_for,
    overrides_save_name_ru, overrides_get_name_ru,
    ensure_headshot_png, ensure_team_logo_png,
) = ([_ for _ in _data_objs] + [None]*8)[:8]

# team_brand
_brand_mod, _brand_objs, _brand_err = _safe_import("team_brand", [
    "get_team_brand", "color_name_ru", "set_team_primary_color",
])
if _brand_err and DEBUG: _log("[boot] team_brand import error:", _brand_err)
(get_team_brand, color_name_ru, set_team_primary_color) = ([_ for _ in _brand_objs] + [None]*3)[:3]

# graphics
_graphics_mod, _graphics_objs, _graphics_err = _safe_import("graphics", [
    "render_card", "render_card2", "render_card_special", "render_card_bad",
])
if _graphics_err and DEBUG: _log("[boot] graphics import error:", _graphics_err)
(render_card, render_card2, render_card_special, render_card_bad) = ([_ for _ in _graphics_objs] + [None]*4)[:4]

app = FastAPI()

# ----------------- TELEGRAM HTTP -----------------
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
        return {"ok": False, "raw": raw.decode("utf-8", "ignore")}

def _tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _http_json(_tg_url(method), payload)
    except HTTPError as e:
        if DEBUG: _log("[tg] HTTPError", method, e)
        return {"ok": False, "error": f"HTTPError {e.code}"}
    except URLError as e:
        if DEBUG: _log("[tg] URLError", method, e)
        return {"ok": False, "error": "URLError"}
    except Exception as e:
        if DEBUG: _log("[tg] send error:", repr(e))
        return {"ok": False, "error": repr(e)}

def _tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None, parse_mode: Optional[str] = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _tg_post("sendMessage", payload)

def _tg_edit_message(chat_id: int, message_id: int, text: str, parse_mode: Optional[str] = None, reply_markup: Optional[Dict[str,Any]] = None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _tg_post("editMessageText", payload)

def _multipart_boundary() -> str:
    return "----WebKitFormBoundary" + uuid.uuid4().hex

def _encode_multipart(fields: Dict[str, str], files: Dict[str, Tuple[str, bytes, str]]) -> Tuple[bytes, str]:
    boundary = _multipart_boundary()
    lines: List[bytes] = []
    CRLF = b"\r\n"
    for name, value in fields.items():
        lines.append(b"--" + boundary.encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(value.encode("utf-8"))
    for field_name, (filename, content, content_type) in files.items():
        lines.append(b"--" + boundary.encode())
        lines.append(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(b"--" + boundary.encode() + b"--")
    body = CRLF.join(lines)
    ctype = f"multipart/form-data; boundary={boundary}"
    return body, ctype

def _tg_send_png_as_document(chat_id: int, png_bytes: bytes, filename: str = "card.png", caption: Optional[str] = None):
    url = _tg_url("sendDocument")
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    files = {"document": (filename, png_bytes, "image/png")}
    body, ctype = _encode_multipart(fields, files)
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    try:
        with http_urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": False, "raw": raw}
    except Exception as e:
        if DEBUG: _log("[tg] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# ----------------- NORMALIZATION / STATS -----------------
def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("ё", "е")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    keep = "abcdefghijklmnopqrstuvwxyzабвгдеэжзийклмнопрстуфхцчшщьыъэюя -'0123456789+/%"
    s = "".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

LABEL_TOKENS = [
    ("плюс/минус", "+/-"), ("plus/minus", "+/-"), ("pm", "+/-"), ("+/-", "+/-"),
    ("% трех", "3P%"), ("% трёх", "3P%"), ("3p%", "3P%"), ("3pt%", "3P%"), ("3 %", "3P%"),
    ("fg%", "FG%"), ("% бросков", "FG%"), ("% с игры", "FG%"),
    ("броски с игры", "С ИГРЫ"), ("с игры", "С ИГРЫ"), ("fgm-a", "С ИГРЫ"), ("fg", "С ИГРЫ"),
    ("трехочков", "3-ОЧКОВЫЕ"), ("трёхочков", "3-ОЧКОВЫЕ"), ("3-очков", "3-ОЧКОВЫЕ"),
    ("3 очк", "3-ОЧКОВЫЕ"), ("трешки", "3-ОЧКОВЫЕ"), ("трёшки", "3-ОЧКОВЫЕ"),
    ("3pt", "3-ОЧКОВЫЕ"), ("3pm", "3-ОЧКОВЫЕ"), ("stocks", "СТИЛОБЛОКИ"), ("стилоблок", "СТИЛОБЛОКИ"),
    ("перехват", "ПЕРЕХВАТЫ"), ("stl", "ПЕРЕХВАТЫ"),
    ("блок", "БЛОКИ"), ("blk", "БЛОКИ"), ("блокшот", "БЛОКИ"), ("блок-шот", "БЛОКИ"),
    ("передач", "ПЕРЕДАЧИ"), ("ast", "ПЕРЕДАЧИ"),
    ("подбор", "ПОДБОРЫ"), ("reb", "ПОДБОРЫ"), ("rebs", "ПОДБОРЫ"),
    ("очк", "ОЧКИ"), ("pts", "ОЧКИ"), ("points", "ОЧКИ"),
    ("минут", "МИНУТЫ"), ("мин", "МИНУТЫ"), ("min", "МИНУТЫ"),
    ("фол", "ФОЛЫ"), ("pf", "ФОЛЫ"),
    ("потер", "ПОТЕРИ"), ("tov", "ПОТЕРИ"), ("to", "ПОТЕРИ"),
]
STAT_TOKEN_MAP = {
    "очк":"ОЧКИ","pts":"ОЧКИ","points":"ОЧКИ",
    "подбор":"ПОДБОРЫ","reb":"ПОДБОРЫ","rebs":"ПОДБОРЫ",
    "передач":"ПЕРЕДАЧИ","ast":"ПЕРЕДАЧИ",
    "перехват":"ПЕРЕХВАТЫ","stl":"ПЕРЕХВАТЫ",
    "блок":"БЛОКИ","blk":"БЛОКИ","блокшот":"БЛОКИ","блок-шот":"БЛОКИ",
    "стилоблок":"СТИЛОБЛОКИ","stocks":"СТИЛОБЛОКИ",
    "трехочков":"3-ОЧКОВЫЕ","трех":"3-ОЧКОВЫЕ","3-очков":"3-ОЧКОВЫЕ","3 очк":"3-ОЧКОВЫЕ",
    "трешк":"3-ОЧКОВЫЕ","трёшк":"3-ОЧКОВЫЕ","3pt":"3-ОЧКОВЫЕ","3pm":"3-ОЧКОВЫЕ",
    "броски с игры":"С ИГРЫ","с игры":"С ИГРЫ","fgm-a":"С ИГРЫ","fg":"С ИГРЫ",
    "% трех":"3P%","% трёх":"3P%","3p%":"3P%","3pt%":"3P%","3 %":"3P%",
    "fg%":"FG%","% бросков":"FG%","% с игры":"FG%",
    "минут":"МИНУТЫ","мин":"МИНУТЫ","min":"МИНУТЫ",
    "фол":"ФОЛЫ","pf":"ФОЛЫ",
    "потер":"ПОТЕРИ","tov":"ПОТЕРИ","to":"ПОТЕРИ",
    "plus/minus":"+/-","плюс/минус":"+/-","pm":"+/-","+/-":"+/-",
}
def _strip_quotes(s: str) -> str:
    s = s.strip()
    pairs = [('"','"'), ("'","'"), ("«","»"), ("“","”"), ("(",")")]
    for a,b in pairs:
        if s.startswith(a) and s.endswith(b) and len(s) >= 2:
            return s[1:-1].strip()
    return s

def parse_stats_list(raw: str) -> List[Tuple[str,str]]:
    if not raw: return []
    parts = [p for p in (x.strip() for x in raw.split(",")) if p]
    out: List[Tuple[str,str]] = []
    for p in parts:
        seg = _strip_quotes(p)
        low = seg.lower().replace("ё","е")
        found = None; found_pos = None
        for tok, canon in LABEL_TOKENS:
            pos = low.find(tok)
            if pos != -1 and (found_pos is None or pos < found_pos):
                found, found_pos = (tok, canon), pos
        if found:
            value = seg[:found_pos].strip(" ,–—-")
            label = found[1]
            if not value:
                tail = seg[found_pos+len(found[0]):]
                m = re.search(r'([+\-]?\d+(?:\s*из\s*\d+)?|[+\-]?\d+/\d+|[+\-]?\d+(?:\.\d+)?%?)', tail)
                value = m.group(1) if m else ""
            if not value: value = "0"
            out.append((value, label)); continue
        lbl = "СТАТ"
        for k, v in STAT_TOKEN_MAP.items():
            if k in _normalize(seg):
                lbl = v; break
        m = re.search(r'([+\-]?\d+(?:\s*из\s*\d+)?|[+\-]?\d+/\d+|[+\-]?\d+(?:\.\d+)?%?)', seg)
        value = m.group(1) if m else seg.strip()
        out.append((value, lbl))
    return out

# ----------------- PLAYERS CACHE / SEARCH -----------------
PLAYERS_READY = False
PLAYERS: List[Dict[str,Any]] = []

def _call_get_players(force: bool) -> List[Dict[str,Any]]:
    if not get_players:
        return []
    try:
        return get_players(force_refresh=bool(force))
    except TypeError:
        try:
            return get_players()
        except Exception:
            return []

def ensure_players_loaded(force: bool = False) -> List[Dict[str,Any]]:
    """Никаких pt-поисков тут. Только локальный кэш norm."""
    global PLAYERS_READY, PLAYERS
    try:
        ps = _call_get_players(force)
        if not ps or len(ps) < 50:
            _log("[players] empty -> refresh()")
            if refresh_players:
                try:
                    res = refresh_players()
                    _log("[players] refresh:", res if isinstance(res, (dict, tuple)) else {"res": str(res)})
                except Exception as e:
                    _log("[players] refresh error:", repr(e))
                ps = _call_get_players(False)
        PLAYERS = ps or []
        PLAYERS_READY = bool(PLAYERS and len(PLAYERS) >= 50)
        _log(f"[players] ready={PLAYERS_READY} count={len(PLAYERS)}")
        return PLAYERS
    except Exception as e:
        _log("[players] ensure failed:", repr(e))
        PLAYERS_READY = False
        return []

def _display_name_for(p: Dict[str,Any]) -> str:
    pid = str(p.get("personId") or p.get("id") or "")
    try:
        if overrides_get_name_ru:
            ru = overrides_get_name_ru(pid)
            if ru: return ru
    except Exception:
        pass
    if display_name_for:
        try:
            return display_name_for(p)
        except Exception:
            pass
    return p.get("displayName") or f"{p.get('firstName','').strip()} {p.get('lastName','').strip()}".strip()

def search_players_loose(q: str) -> List[Dict[str,Any]]:
    """Только локальный поиск по кэшу + data.find_player_by_name (если есть)."""
    qn = _normalize(q)
    ps = ensure_players_loaded(False)
    if not ps: return []
    if find_player_by_name:
        try:
            hits = find_player_by_name(q) or []
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

# ----------------- TEAM / IMAGES -----------------
def _ensure_headshot_image(p: Dict[str,Any]):
    from PIL import Image
    try:
        hs = ensure_headshot_png(p) if ensure_headshot_png else None
        if hs is None: return None
        if isinstance(hs, bytes):
            return Image.open(io.BytesIO(hs)).convert("RGBA")
        if isinstance(hs, str):
            return Image.open(hs).convert("RGBA")
        return hs.convert("RGBA")
    except Exception as e:
        _log("[tg] headshot ensure err", p.get("personId"), repr(e))
        return None

def _ensure_team_logo_image(team_id: str):
    from PIL import Image
    try:
        path = ensure_team_logo_png(team_id) if ensure_team_logo_png else None
        if path and os.path.exists(path):
            return Image.open(path).convert("RGBA")
        brand = get_team_brand(team_id) if get_team_brand else None
        if brand:
            _, logo_path, _, _ = brand
            if logo_path and os.path.exists(logo_path):
                return Image.open(logo_path).convert("RGBA")
        return None
    except Exception as e:
        _log("[tg] team logo ensure err", team_id, repr(e))
        return None

def _team_colors(team_id: str) -> Tuple[str,str,str]:
    try:
        colors, _, _, _ = get_team_brand(team_id) if get_team_brand else (("#007ACC", "#005C99", "#007ACC"), None, [], False)
        return colors
    except Exception as e:
        _log("[tg] team_brand err", team_id, repr(e))
        return ("#007ACC", "#005C99", "#007ACC")

# ----------------- SIMPLE STATE -----------------
CTX: Dict[int, Dict[str,Any]] = {}  # chat_id -> state

def _ctx(chat_id: int) -> Dict[str,Any]:
    return CTX.setdefault(chat_id, {})

def _ctx_clear(chat_id: int) -> None:
    CTX.pop(chat_id, None)

# ----------------- HELP -----------------
HELP_TEXT = (
    "Привет! Я онлайн 🤖\n\n"
    "Команды:\n"
    "• /find <имя/фамилия>\n"
    "• /card <имя> | <метрики через запятую>\n"
    "  пример: /card wembanyama | 25 очков, 15 подборов, \"3 из 5\" трёшки, 12/20 с игры, +18 плюс/минус\n"
    "• /card2 <имя1> | <статы1> || <имя2> | <статы2>\n"
    "• /cards <имя> | <статы> | <короткий текст справа>\n"
    "• /cardbad <имя> | <статы>\n"
)

# ----------------- AUTH -----------------
def _check_secret(request: Request) -> Optional[PlainTextResponse]:
    secret = (request.query_params.get("secret") or "").strip()
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return PlainTextResponse("bad secret", status_code=401)
    return None

# ----------------- GET ROUTES -----------------
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
            "errors": {"data": _data_err, "graphics": _graphics_err, "team_brand": _brand_err},
            "api_origin": API_ORIGIN or None,
        })
    if action == "refresh":
        try:
            # доверяем возвращаемым данным от data.refresh_players()
            cnt, info = 0, {}
            if refresh_players:
                res = refresh_players()
                if isinstance(res, tuple) and len(res) >= 2:
                    cnt, info = res[0], res[1] or {}
                elif isinstance(res, dict):
                    info = res
                    cnt = int(info.get("count") or 0)

            # берём актуальный список после refresh
            ps = _call_get_players(False)
            count_now = len(ps) if isinstance(ps, list) else int(cnt)

            src = None; src_url = None
            if isinstance(info, dict):
                src = info.get("src") or info.get("source") or info.get("source_name")
                src_url = info.get("url") or info.get("source_url")

            _log(f"[data] final players count: {count_now} (source={src or 'unknown'}) url: {src_url or 'n/a'}")
            return JSONResponse({
                "ok": True, "refreshed": True,
                "players_indexed": count_now,
                "source": src or "unknown",
                "source_url": src_url or None,
            })
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)
    if action == "test_find":
        q = request.query_params.get("q") or ""
        hits = [{"personId": h.get("personId"), "displayName": h.get("displayName"), "teamId": h.get("teamId")} for h in search_players_loose(q)]
        return JSONResponse({"ok": True, "q": q, "players_ready": PLAYERS_READY, "hits": hits})
    return PlainTextResponse("ok")

# ----------------- UI ASKERS -----------------
def _send_typing(chat_id: int, t: str = "typing"):
    _tg_post("sendChatAction", {"chat_id": chat_id, "action": t})

def _ask_ru_name(chat_id: int, pid: str, display_name: str, reply_to: Optional[int] = None):
    txt = f"Как подписать игрока <b>{display_name}</b> на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]"
    _tg_send_message(chat_id, txt, reply_to=reply_to, parse_mode="HTML")

def _ask_color(chat_id: int, team_id: str, i_label: str = "1"):
    kb = {
        "inline_keyboard": [[
            {"text": f"Цвет команды {i_label}: авто", "callback_data": f"color:auto:{team_id}:{i_label}"},
            {"text": f"Цвет команды {i_label}: свой HEX", "callback_data": f"color:ask:{team_id}:{i_label}"},
        ]]
    }
    _tg_post("sendMessage", {"chat_id": chat_id, "text": "Выберите цвет плашки:", "reply_markup": kb})

def _valid_hex(s: str) -> bool:
    return bool(re.fullmatch(r"#?[0-9A-Fa-f]{6}", s.strip()))

def _fix_hex(s: str) -> str:
    s = s.strip().upper()
    if not s.startswith("#"): s = "#" + s
    return s

def _stats_text(stats: List[Tuple[str,str]]) -> str:
    parts = []
    for v, l in stats:
        parts.append(f"{v} {l}" if l else f"{v}")
    return ", ".join(parts)

# ----------------- MAIN WEBHOOK -----------------
@app.post("/api/telegram")
async def webhook_query(request: Request):
    bad = _check_secret(request)
    if bad: return bad

    rid = f"[RID={int(time.time()*1000)}-{uuid.uuid4().hex[:6]}]"
    try:
        body = await request.body()
        raw = body.decode("utf-8", "ignore")
        if DEBUG: _log("[tg]", rid, "POST", request.url, "\nbody:", raw)
        update = json.loads(raw)
    except Exception as e:
        if DEBUG: _log("[tg]", rid, "json error:", repr(e))
        return PlainTextResponse("OK")

    ensure_players_loaded(False)

    # ---------- CALLBACKS (цвет) ----------
    cb = update.get("callback_query")
    if cb:
        chat_id = cb["from"]["id"]
        data = cb.get("data") or ""
        if data.startswith("color:"):
            try:
                _, kind, team_id, i_label = data.split(":", 3)
            except ValueError:
                kind, team_id, i_label = "auto", "0", "1"

            if kind == "auto":
                if set_team_primary_color:
                    set_team_primary_color(team_id, "AUTO")
                _tg_send_message(chat_id, f"Цвет команды {i_label}: авто ✅")
            elif kind == "ask":
                # ждём HEX в следующем сообщении
                st = _ctx(chat_id)
                st["waiting_hex"] = {"team_id": team_id, "i_label": i_label}
                _tg_send_message(chat_id, f"Пришлите HEX для команды {i_label} (например, #FDB927):")
                return PlainTextResponse("OK")

            # После выбора цвета — ищем, что рендерить из контекста
            st = _ctx(chat_id)
            try:
                mode = st.get("mode")
                if mode == "single":
                    p = st.get("p1") or {}
                    stats = st.get("stats1") or []
                    ru = st.get("ru1") or _display_name_for(p)
                    team_id = str(p.get("teamId") or "0")
                    head = _ensure_headshot_image(p)
                    if head is None:
                        _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                        _ctx_clear(chat_id); return PlainTextResponse("OK")
                    logo = _ensure_team_logo_image(team_id)
                    colors = _team_colors(team_id)
                    _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
                    png = render_card("single", ru, "", logo, colors, head, stats)
                    _tg_send_png_as_document(chat_id, png, filename=f"card_{p.get('personId','x')}.png",
                                             caption=_stats_text(stats))
                    _ctx_clear(chat_id); return PlainTextResponse("OK")

                if mode == "duo":
                    # сначала спросим цвет второй, если ещё нет
                    if not _ctx(chat_id).get("color1_done"):
                        _ctx(chat_id)["color1_done"] = True
                        p2 = st.get("p2") or {}
                        _ask_color(chat_id, str(p2.get("teamId") or "0"), i_label="2")
                        return PlainTextResponse("OK")
                    p1, p2 = st.get("p1") or {}, st.get("p2") or {}
                    stats1, stats2 = st.get("stats1") or [], st.get("stats2") or []
                    ru1 = st.get("ru1") or _display_name_for(p1)
                    ru2 = st.get("ru2") or _display_name_for(p2)
                    t1, t2 = str(p1.get("teamId") or "0"), str(p2.get("teamId") or "0")
                    h1, h2 = _ensure_headshot_image(p1), _ensure_headshot_image(p2)
                    if h1 is None or h2 is None:
                        _tg_send_message(chat_id, "Не удалось получить фото одного из игроков.")
                        _ctx_clear(chat_id); return PlainTextResponse("OK")
                    l1, l2 = _ensure_team_logo_image(t1), _ensure_team_logo_image(t2)
                    c1, c2 = _team_colors(t1), _team_colors(t2)
                    _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
                    png = render_card2(ru1, l1, c1, h1, stats1, ru2, l2, c2, h2, stats2)
                    _tg_send_png_as_document(chat_id, png, filename=f"card2_{p1.get('personId','x')}_{p2.get('personId','y')}.png",
                                             caption=f"{_stats_text(stats1)}  |  {_stats_text(stats2)}")
                    _ctx_clear(chat_id); return PlainTextResponse("OK")

                if mode == "special":
                    p = st.get("p1") or {}
                    stats = st.get("stats1") or []
                    info_text = st.get("info") or ""
                    ru = st.get("ru1") or _display_name_for(p)
                    team_id = str(p.get("teamId") or "0")
                    head = _ensure_headshot_image(p)
                    if head is None:
                        _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                        _ctx_clear(chat_id); return PlainTextResponse("OK")
                    logo = _ensure_team_logo_image(team_id)
                    colors = _team_colors(team_id)
                    _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
                    png = render_card_special(ru, logo, colors, head, stats, info_text)
                    _tg_send_png_as_document(chat_id, png, filename=f"cards_{p.get('personId','x')}.png",
                                             caption=_stats_text(stats))
                    _ctx_clear(chat_id); return PlainTextResponse("OK")

                if mode == "bad":
                    p = st.get("p1") or {}
                    stats = st.get("stats1") or []
                    ru = st.get("ru1") or _display_name_for(p)
                    t = str(p.get("teamId") or "0")
                    head = _ensure_headshot_image(p)
                    logo = _ensure_team_logo_image(t)
                    if head is None:
                        _tg_send_message(chat_id, "Не удалось получить фото игрока.")
                        _ctx_clear(chat_id); return PlainTextResponse("OK")
                    _tg_send_message(chat_id, "_Готовлю плашку…_", parse_mode="Markdown")
                    png = render_card_bad(ru, head, stats, team_logo_img=logo)
                    _tg_send_png_as_document(chat_id, png, filename=f"cardBAD_{p.get('personId','x')}.png",
                                             caption=_stats_text(stats))
                    _ctx_clear(chat_id); return PlainTextResponse("OK")

            except Exception as e:
                _tg_send_message(chat_id, f"Ошибка рендера: {repr(e)}")
                _ctx_clear(chat_id)
        return PlainTextResponse("OK")

    # ---------- MESSAGE ----------
    msg = update.get("message") or update.get("edited_message")
    if not msg: return PlainTextResponse("OK")
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    st = _ctx(chat_id)

    # ожидание HEX
    if st.get("waiting_hex"):
        hx = text.strip()
        wh = st["waiting_hex"]
        team_id = wh.get("team_id") or "0"
        i_label = wh.get("i_label") or "1"
        if not _valid_hex(hx):
            _tg_send_message(chat_id, "HEX некорректен. Пример: #FDB927")
            return PlainTextResponse("OK")
        if set_team_primary_color:
            set_team_primary_color(team_id, _fix_hex(hx))
        _tg_send_message(chat_id, f"Цвет команды {i_label}: {_fix_hex(hx)} ✅")
        st.pop("waiting_hex", None)
        # эмулируем коллбек auto → общий рендер
        update["callback_query"] = {"from":{"id":chat_id},"data":f"color:auto:{team_id}:{i_label}"}
        return await webhook_query(request)

    # Реплай на запрос имени
    rpl = msg.get("reply_to_message")
    if rpl and text:
        rtxt = (rpl.get("text") or "") + " " + (rpl.get("caption") or "")
        m = re.search(r"\[setname:(\d+)\]", rtxt)
        if m and overrides_save_name_ru:
            pid = m.group(1)
            name_ru = text.strip()
            try:
                overrides_save_name_ru(pid, name_ru)
                _tg_send_message(chat_id, f"Сохранил имя для {pid}: {name_ru}", reply_to=msg.get("message_id"))
            except Exception as e:
                _tg_send_message(chat_id, f"Не удалось сохранить имя: {repr(e)}", reply_to=msg.get("message_id"))
                return PlainTextResponse("OK")
            if st.get("mode") == "duo":
                # если у второй стороны имени нет — спросим
                if st.get("p2") and not st.get("ru2"):
                    p2 = st.get("p2") or {}
                    _ask_ru_name(chat_id, str(p2.get("personId") or ""), p2.get("displayName") or "", reply_to=None)
                    return PlainTextResponse("OK")
            # затем спросим цвет 1
            p1 = st.get("p1") or {}
            _ask_color(chat_id, str(p1.get("teamId") or "0"), i_label="1")
            return PlainTextResponse("OK")

    # Команды
    if text.startswith("/start"):
        _tg_send_message(chat_id, "Я здесь. Готов работать 💼")
        return PlainTextResponse("OK")

    if text.startswith("/help"):
        _tg_send_message(chat_id, HELP_TEXT)
        return PlainTextResponse("OK")

    if text.startswith("/find"):
        q = text[len("/find"):].strip()
        hits = search_players_loose(q)
        if not hits:
            _tg_send_message(chat_id, "Ничего не нашёл 🤷")
            return PlainTextResponse("OK")
        lines = []
        for h in hits[:8]:
            lines.append(f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})")
        _tg_send_message(chat_id, "\n".join(lines))
        return PlainTextResponse("OK")

    # /card
    if text.startswith("/card "):
        try:
            args = text[len("/card"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _tg_send_message(chat_id, "Формат: /card <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            name_q = parts[0]
            stats_raw = parts[1]
            stats = parse_stats_list(stats_raw)

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            _send_typing(chat_id)

            hits = search_players_loose(name_q)
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            if len(hits) > 1:
                menu = "\n".join([f"{i+1}. {h.get('displayName')} (id={h.get('personId')})" for i, h in enumerate(hits[:6])])
                _tg_send_message(chat_id, "Нашёл несколько вариантов:\n" + menu + "\nУточните запрос.")
                return PlainTextResponse("OK")

            p = hits[0]
            pid = str(p.get("personId") or "")
            st.clear()
            st.update({"mode":"single", "p1":p, "stats1":stats})

            # RU имя?
            ru = None
            try:
                ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception:
                ru = None
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=None)
                return PlainTextResponse("OK")
            st["ru1"] = ru
            _ask_color(chat_id, str(p.get("teamId") or "0"), i_label="1")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /card2
    if text.startswith("/card2 "):
        try:
            args = text[len("/card2"):].strip()
            sides = [s.strip() for s in args.split("||")]
            if len(sides) != 2:
                _tg_send_message(chat_id, "Формат: /card2 <имя1> | <статы1> || <имя2> | <статы2>")
                return PlainTextResponse("OK")

            def parse_side(s: str) -> Tuple[str, List[Tuple[str,str]]]:
                parts = [p.strip() for p in s.split("|")]
                if len(parts) < 2: return parts[0] if parts else "", []
                return parts[0], parse_stats_list(parts[1])

            n1, st1 = parse_side(sides[0])
            n2, st2 = parse_side(sides[1])

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            _send_typing(chat_id)

            h1 = search_players_loose(n1)
            h2 = search_players_loose(n2)
            if not h1 or not h2:
                _tg_send_message(chat_id, "Не нашёл одного из игроков, уточните имена.")
                return PlainTextResponse("OK")

            p1, p2 = h1[0], h2[0]
            st.clear()
            st.update({"mode":"duo", "p1":p1, "stats1":st1, "p2":p2, "stats2":st2})

            pid1 = str(p1.get("personId") or "")
            pid2 = str(p2.get("personId") or "")

            ru1 = ru2 = None
            try: ru1 = overrides_get_name_ru(pid1) if overrides_get_name_ru else None
            except Exception: ru1 = None
            try: ru2 = overrides_get_name_ru(pid2) if overrides_get_name_ru else None
            except Exception: ru2 = None

            if not ru1:
                _ask_ru_name(chat_id, pid1, p1.get("displayName") or "", reply_to=None)
                return PlainTextResponse("OK")
            st["ru1"] = ru1
            if not ru2:
                _ask_ru_name(chat_id, pid2, p2.get("displayName") or "", reply_to=None)
                return PlainTextResponse("OK")
            st["ru2"] = ru2
            _ask_color(chat_id, str(p1.get("teamId") or "0"), i_label="1")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /cards
    if text.startswith("/cards "):
        try:
            args = text[len("/cards"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 3:
                _tg_send_message(chat_id, "Формат: /cards <имя> | <статы> | <короткий текст справа>")
                return PlainTextResponse("OK")
            n, stats_raw, info_text = parts[0], parts[1], parts[2]
            stats = parse_stats_list(stats_raw)

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            _send_typing(chat_id)

            hits = search_players_loose(n)
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {n}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")

            st.clear()
            st.update({"mode":"special", "p1":p, "stats1":stats, "info":info_text})

            ru = None
            try: ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception: ru = None
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=None)
                return PlainTextResponse("OK")
            st["ru1"] = ru
            _ask_color(chat_id, str(p.get("teamId") or "0"), i_label="1")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /cardbad
    if text.startswith("/cardbad "):
        try:
            args = text[len("/cardbad"):].strip()
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _tg_send_message(chat_id, "Формат: /cardbad <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            n, stats_raw = parts[0], parts[1]
            stats = parse_stats_list(stats_raw)

            _tg_send_message(chat_id, "_Уточнения…_", parse_mode="Markdown")
            _send_typing(chat_id)

            hits = search_players_loose(n)
            if not hits:
                _tg_send_message(chat_id, f"Не нашёл игрока: {n}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")

            st.clear()
            st.update({"mode":"bad", "p1":p, "stats1":stats})

            ru = None
            try: ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception: ru = None
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=None)
                return PlainTextResponse("OK")
            st["ru1"] = ru
            _ask_color(chat_id, str(p.get("teamId") or "0"), i_label="1")
        except Exception as e:
            _tg_send_message(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    _tg_send_message(chat_id, HELP_TEXT)
    return PlainTextResponse("OK")
