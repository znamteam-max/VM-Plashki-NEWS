# api/telegram.py — safe render kwargs, anti-loop, statuses, error ❌
from __future__ import annotations
import os, io, re, json, time, unicodedata, uuid, inspect
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen
from urllib.error import HTTPError, URLError

DEBUG = os.getenv("DEBUG", "1") in ("1", "true", "yes")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ORIGIN = os.getenv("API_ORIGIN")

def _log(*a: Any) -> None:
    try: print(*a, flush=True)
    except: pass

def _safe_import(modname: str, names: List[str]):
    try:
        m = __import__(modname, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, out, None
    except Exception as e:
        return None, [], f"{e.__class__.__name__}: {e}"

# ---- deps
_data_mod, _data_objs, _data_err = _safe_import("data", [
    "get_players", "refresh_players", "find_player_by_name", "display_name_for",
    "overrides_save_name_ru", "overrides_get_name_ru",
    "ensure_headshot_png", "ensure_team_logo_png",
])
(get_players, refresh_players, find_player_by_name, display_name_for,
 overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png) = ([_ for _ in _data_objs] + [None]*8)[:8]

_brand_mod, _brand_objs, _brand_err = _safe_import("team_brand", [
    "get_team_brand", "color_name_ru", "set_team_primary_color",
])
(get_team_brand, color_name_ru, set_team_primary_color) = ([_ for _ in _brand_objs] + [None]*3)[:3]

_graphics_mod, _graphics_objs, _graphics_err = _safe_import("graphics", [
    "render_card", "render_card2", "render_card_special", "render_card_bad",
])
(render_card, render_card2, render_card_special, render_card_bad) = ([_ for _ in _graphics_objs] + [None]*4)[:4]

app = FastAPI()

# ----------------- Telegram HTTP helpers -----------------
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
    except Exception as e:
        if DEBUG: _log("[tg] send error:", repr(e))
        return {"ok": False, "error": repr(e)}

def _tg_send_message(chat_id: int, text: str, *, reply_to: Optional[int]=None,
                     parse_mode: Optional[str]=None, reply_markup: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = reply_markup
    return _tg_post("sendMessage", payload)

def _tg_edit_message(chat_id: int, message_id: int, text: str,
                     *, parse_mode: Optional[str]=None,
                     reply_markup: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
    payload = {"chat_id": chat_id, "message_id": message_id,
               "text": text, "disable_web_page_preview": True}
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = reply_markup
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
    if caption: fields["caption"] = caption
    files = {"document": (filename, png_bytes, "image/png")}
    body, ctype = _encode_multipart(fields, files)
    req = UrlRequest(url, data=body, headers={"Content-Type": ctype})
    try:
        with http_urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            try: return json.loads(raw)
            except Exception: return {"ok": False, "raw": raw}
    except Exception as e:
        if DEBUG: _log("[tg] sendDocument error:", repr(e))
        return {"ok": False, "error": repr(e)}

# ----------------- Normalize & stats -----------------
def _normalize(s: str) -> str:
    s = (s or "").strip().lower().replace("ё","е")
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
STAT_TOKEN_MAP = {k:v for k,v in LABEL_TOKENS}
STAT_TOKEN_MAP.update({
    "трех":"3-ОЧКОВЫЕ","трёх":"3-ОЧКОВЫЕ","3-очков":"3-ОЧКОВЫЕ","3 очк":"3-ОЧКОВЫЕ",
})

def _strip_quotes(s: str) -> str:
    s = s.strip()
    pairs = [('"','"'), ("'","'"), ("«","»"), ("“","”"), ("(",")")]
    for a,b in pairs:
        if s.startswith(a) and s.endswith(b) and len(s) >= 2:
            return s[1:-1].strip()
    return s

VAL_RX = re.compile(
    r'([+\-]?\d+(?:\s*из\s*\d+)?|[+\-]?\d+/\d+|[+\-]?\d+\s*-\s*\d+|[+\-]?\d+(?:\.\d+)?%?)',
    re.IGNORECASE
)

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
            if not value:
                tail = seg[found_pos+len(found[0]):]
                m = VAL_RX.search(tail)
                value = m.group(1) if m else ""
            if not value: value = "0"
            out.append((value, found[1])); continue
        m = VAL_RX.search(seg)
        value = m.group(1) if m else seg.strip()
        lbl = "СТАТ"
        nseg = _normalize(seg)
        for k, v in STAT_TOKEN_MAP.items():
            if k in nseg: lbl = v; break
        out.append((value, lbl))
    return out

# ----------------- Players/search -----------------
PLAYERS_READY = False
PLAYERS: List[Dict[str,Any]] = []

def _call_get_players(force: bool) -> List[Dict[str,Any]]:
    if not get_players: return []
    try: return get_players(force_refresh=bool(force))
    except TypeError:
        try: return get_players()
        except Exception: return []

def ensure_players_loaded(force: bool=False) -> List[Dict[str,Any]]:
    global PLAYERS_READY, PLAYERS
    try:
        ps = _call_get_players(force)
        if not ps or len(ps) < 50:
            _log("[players] empty -> refresh()")
            if refresh_players:
                try:
                    res = refresh_players()
                    _log("[players] refresh:", res if isinstance(res,(dict,tuple)) else {"res":str(res)})
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
    except Exception: pass
    if display_name_for:
        try: return display_name_for(p)
        except Exception: pass
    return p.get("displayName") or f"{p.get('firstName','').strip()} {p.get('lastName','').strip()}".strip()

def search_players_loose(q: str) -> List[Dict[str,Any]]:
    qn = _normalize(q)
    ps = ensure_players_loaded(False)
    if not ps: return []
    if find_player_by_name:
        try:
            hits = find_player_by_name(q) or []
            if hits: return hits
        except Exception: pass
    out = []
    for p in ps:
        dn = p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if dn and qn in _normalize(dn):
            out.append(p)
            if len(out) >= 10: break
    return out

# ----------------- Team/colors/logo -----------------
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
        _log("[tg] headshot ensure err", p.get("personId"), repr(e)); return None

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
        _log("[tg] team logo ensure err", team_id, repr(e)); return None

def _team_colors(team_id: str) -> Tuple[str,str,str]:
    try:
        colors, _, _, _ = get_team_brand(team_id) if get_team_brand else (("#007ACC","#005C99","#007ACC"),None,[],False)
        return colors
    except Exception:
        return ("#007ACC","#005C99","#007ACC")

# ----------------- Render-safe wrapper -----------------
def _call_render(func, *args, **kwargs):
    """
    Фильтрует kwargs по сигнатуре целевой функции, чтобы не было unexpected keyword.
    """
    try:
        sig = inspect.signature(func)
        allowed = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return func(*args, **allowed)
    except Exception:
        # пробуем без kwargs совсем
        return func(*args)

# ----------------- State + statuses -----------------
CTX: Dict[int, Dict[str,Any]] = {}

def _ctx(chat_id: int) -> Dict[str,Any]:
    return CTX.setdefault(chat_id, {})

def _ctx_clear(chat_id: int) -> None:
    CTX.pop(chat_id, None)

def _status_update(chat_id: int, text: str, *, parse_mode: Optional[str]=None, keep_kb: Optional[Dict[str,Any]]=None):
    st = _ctx(chat_id)
    mid = st.get("status_mid")
    if mid:
        r = _tg_edit_message(chat_id, mid, text, parse_mode=parse_mode, reply_markup=keep_kb)
        if not r.get("ok"):
            sent = _tg_send_message(chat_id, text, parse_mode=parse_mode, reply_markup=keep_kb)
            if sent.get("ok"): st["status_mid"] = sent["result"]["message_id"]
    else:
        sent = _tg_send_message(chat_id, text, parse_mode=parse_mode, reply_markup=keep_kb)
        if sent.get("ok"): st["status_mid"] = sent["result"]["message_id"]

def _fail(chat_id: int, human_text: str):
    _status_update(chat_id, f"❌ {human_text}")

def _ask_ru_name(chat_id: int, pid: str, display_name: str, reply_to: Optional[int], *, mark_wait=True):
    st = _ctx(chat_id)
    text = f"Как подписать игрока <b>{display_name}</b> на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]"
    sent = _tg_send_message(chat_id, text, reply_to=reply_to, parse_mode="HTML")
    if mark_wait:
        st["waiting_name_pid"] = pid
        st["waiting_name_msg_id"] = sent["result"]["message_id"] if sent.get("ok") else None
        st.setdefault("saved_names", set())

# ----------------- Keyboards -----------------
def _kb_ok_or_fix() -> Dict[str,Any]:
    return {"inline_keyboard":[
        [{"text":"Всё ок ✅","callback_data":"fix:ok"},
         {"text":"Нужно исправить ✏️","callback_data":"fix:menu"}]
    ]}

def _kb_fix_menu(mode: str) -> Dict[str,Any]:
    rows = [[{"text":"Имена игроков","callback_data":"fix:names"},
             {"text":"Цвет плашки","callback_data":"fix:color"}]]
    rows.append([{"text":"Команды","callback_data":"fix:teams"}])
    return {"inline_keyboard": rows}

def _kb_color_which(mode: str) -> Dict[str,Any]:
    if mode == "duo":
        return {"inline_keyboard":[
            [{"text":"Цвет команды 1","callback_data":"colorwhich:1"},
             {"text":"Цвет команды 2","callback_data":"colorwhich:2"}]
        ]}
    return {"inline_keyboard":[[{"text":"Цвет команды","callback_data":"colorwhich:1"}]]}

def _valid_hex(s: str) -> bool:
    return bool(re.fullmatch(r"#?[0-9A-Fa-f]{6}", s.strip()))
def _fix_hex(s: str) -> str:
    s = s.strip().upper()
    if not s.startswith("#"): s = "#" + s
    return s

def _stats_text(stats: List[Tuple[str,str]]) -> str:
    return ", ".join((f"{v} {l}" if l else f"{v}") for v,l in stats)

# ----------------- Secret -----------------
def _check_secret(request: Request) -> Optional[PlainTextResponse]:
    secret = (request.query_params.get("secret") or "").strip()
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return PlainTextResponse("bad secret", status_code=401)
    return None

# ----------------- GET routes -----------------
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
            cnt, info = 0, {}
            if refresh_players:
                res = refresh_players()
                if isinstance(res, tuple) and len(res) >= 2:
                    cnt, info = res[0], res[1] or {}
                elif isinstance(res, dict):
                    info = res; cnt = int(info.get("count") or 0)
            ps = (get_players() if get_players else []) or []
            count_now = len(ps) if isinstance(ps, list) else int(cnt)
            src = (info.get("src") or info.get("source") or info.get("source_name")) if isinstance(info, dict) else None
            src_url = (info.get("url") or info.get("source_url")) if isinstance(info, dict) else None
            _log(f"[data] final players count: {count_now} (source={src or 'unknown'}) url: {src_url or 'n/a'}")
            return JSONResponse({"ok": True,"refreshed": True,"players_indexed": count_now,
                                 "source": src or "unknown","source_url": src_url or None})
        except Exception as e:
            return JSONResponse({"ok": False, "refreshed": False, "error": repr(e)}, status_code=500)
    if action == "test_find":
        q = request.query_params.get("q") or ""
        hits = [{"personId": h.get("personId"), "displayName": h.get("displayName"), "teamId": h.get("teamId")} for h in search_players_loose(q)]
        return JSONResponse({"ok": True, "q": q, "players_ready": PLAYERS_READY, "hits": hits})
    return PlainTextResponse("ok")

# ----------------- Render wrappers (safe) -----------------
def _render_single(chat_id:int, p:Dict[str,Any], ru:str, stats:List[Tuple[str,str]], ask_corrections:bool=True):
    try:
        _status_update(chat_id, "Готовлю плашку…")
        team_id = str(p.get("teamId") or "0")
        head = _ensure_headshot_image(p)
        if head is None:
            _fail(chat_id, "Не удалось получить фото игрока."); return
        logo = _ensure_team_logo_image(team_id)
        colors = _team_colors(team_id)
        # желаемые kwargs (будут отфильтрованы по сигнатуре)
        want = dict(
            round_left=False, round_right=True,
            name_stat_center=True,
            right_panel_width_ratio=None,
            text_topmost=True
        )
        png = _call_render(
            render_card,
            "single", ru, "", logo, colors, head, stats,
            **want
        )
        sent = _tg_send_png_as_document(chat_id, png, filename=f"card_{p.get('personId','x')}.png", caption=_stats_text(stats))
        if not sent.get("ok"):
            _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}")
            return
        if ask_corrections:
            _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

def _render_bad(chat_id:int, p:Dict[str,Any], ru:str, stats:List[Tuple[str,str]]):
    try:
        _status_update(chat_id, "Готовлю коричневую BAD-плашку…")
        head = _ensure_headshot_image(p)
        if head is None:
            _fail(chat_id, "Не удалось получить фото игрока."); return
        logo = _ensure_team_logo_image(str(p.get("teamId") or "0"))
        want = dict(
            round_left=False, round_right=True,
            poop_larger=True, poop_lower=18,
            name_stat_center=True
        )
        png = _call_render(
            render_card_bad,
            ru, head, stats,
            team_logo_img=logo, **want
        )
        sent = _tg_send_png_as_document(chat_id, png, filename=f"cardBAD_{p.get('personId','x')}.png", caption=_stats_text(stats))
        if not sent.get("ok"):
            _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

def _render_duo(chat_id:int, p1:Dict[str,Any], ru1:str, st1:List[Tuple[str,str]],
                p2:Dict[str,Any], ru2:str, st2:List[Tuple[str,str]]):
    try:
        _status_update(chat_id, "Готовлю двойную плашку…")
        t1, t2 = str(p1.get("teamId") or "0"), str(p2.get("teamId") or "0")
        h1, h2 = _ensure_headshot_image(p1), _ensure_headshot_image(p2)
        if h1 is None or h2 is None:
            _fail(chat_id, "Не удалось получить фото одного из игроков."); return
        l1, l2 = _ensure_team_logo_image(t1), _ensure_team_logo_image(t2)
        c1, c2 = _team_colors(t1), _team_colors(t2)
        want = dict(
            round_left=False, round_right=False, round_all=False,
            name_stat_center=True,
            duo_name_delta_plus=2,
            duo_autofit_names=True
        )
        png = _call_render(
            render_card2,
            ru1, l1, c1, h1, st1,
            ru2, l2, c2, h2, st2,
            **want
        )
        sent = _tg_send_png_as_document(chat_id, png, filename=f"card2_{p1.get('personId','x')}_{p2.get('personId','y')}.png",
                                        caption=f"{_stats_text(st1)}  |  {_stats_text(st2)}")
        if not sent.get("ok"):
            _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

def _render_special(chat_id:int, p:Dict[str,Any], ru:str, stats:List[Tuple[str,str]], info_text:str):
    try:
        _status_update(chat_id, "Готовлю плашку с правой колонкой…")
        t = str(p.get("teamId") or "0")
        head = _ensure_headshot_image(p)
        if head is None:
            _fail(chat_id, "Не удалось получить фото игрока."); return
        logo = _ensure_team_logo_image(t)
        colors = _team_colors(t)
        want = dict(
            round_left=False, round_right=True,
            right_panel_half_width=True,
            right_wrap=True, text_topmost=True,
            name_stat_center=True
        )
        png = _call_render(
            render_card_special,
            ru, logo, colors, head, stats, info_text,
            **want
        )
        sent = _tg_send_png_as_document(chat_id, png, filename=f"cards_{p.get('personId','x')}.png", caption=_stats_text(stats))
        if not sent.get("ok"):
            _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

# ----------------- POST webhook -----------------
@app.post("/api/telegram")
async def webhook_query(request: Request):
    bad = _check_secret(request)
    if bad: return bad

    try:
        body = await request.body()
        raw = body.decode("utf-8","ignore")
        update = json.loads(raw)
        if DEBUG: _log("[tg] ", raw)
    except Exception:
        return PlainTextResponse("OK")

    # callbacks
    cb = update.get("callback_query")
    if cb:
        try:
            chat_id = cb["from"]["id"]
            data = cb.get("data") or ""
            st = _ctx(chat_id)

            if data == "fix:ok":
                _ctx_clear(chat_id)
                _tg_send_message(chat_id, "Готово ✅")
                return PlainTextResponse("OK")

            if data == "fix:menu":
                _status_update(chat_id, "Что исправить?", keep_kb=_kb_fix_menu(st.get("mode") or ""))
                return PlainTextResponse("OK")

            if data == "fix:names":
                if st.get("mode") == "duo":
                    st["fix_wait"] = "name_which"
                    _status_update(chat_id, "Чьё имя исправить? Напишите: 1=<имя> или 2=<имя>")
                else:
                    st["fix_wait"] = "name_one"
                    _status_update(chat_id, "Как записать имя игрока? Напишите: 1=<имя>")
                return PlainTextResponse("OK")

            if data == "fix:color":
                st["fix_wait"] = "color_which"
                # в duo получим выбор 1/2; в single/special — один вариант
                kb = {"inline_keyboard":[
                    [{"text":"Цвет команды 1","callback_data":"colorwhich:1"}] +
                    ([{"text":"Цвет команды 2","callback_data":"colorwhich:2"}] if st.get("mode")=="duo" else [])
                ]}
                _status_update(chat_id, "Для какой команды изменить цвет?", keep_kb=kb)
                return PlainTextResponse("OK")

            if data == "fix:teams":
                st["fix_wait"] = "teams_map"
                _status_update(chat_id, "Задайте команды. Пример: 1=1610612747, 2=1610612744")
                return PlainTextResponse("OK")

            if data.startswith("colorwhich:"):
                which = data.split(":",1)[1]
                st["waiting_hex"] = {"which": which}
                _status_update(chat_id, f"Введите HEX для команды {which} (например, #FDB927):")
                return PlainTextResponse("OK")

            return PlainTextResponse("OK")
        except Exception as e:
            _fail(cb["from"]["id"], f"Ошибка: {repr(e)}")
            return PlainTextResponse("OK")

    # messages
    msg = update.get("message") or update.get("edited_message")
    if not msg: return PlainTextResponse("OK")

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    st = _ctx(chat_id)
    st.setdefault("last_cmd_msg_id", msg.get("message_id"))

    # /stop — экстренный выход
    if text.lower().startswith("/stop"):
        _ctx_clear(chat_id)
        _tg_send_message(chat_id, "Остановил сценарий и очистил контекст. ✅")
        return PlainTextResponse("OK")

    # шаг: ожидание HEX
    if st.get("waiting_hex"):
        try:
            hx = text.strip()
            if not _valid_hex(hx):
                _status_update(chat_id, "HEX некорректен. Пример: #FDB927")
                return PlainTextResponse("OK")
            which = (st["waiting_hex"] or {}).get("which") or "1"
            p = st["p2"] if (st.get("mode") == "duo" and which == "2") else st.get("p1")
            tid = str((p or {}).get("teamId") or "0")
            if set_team_primary_color: set_team_primary_color(tid, _fix_hex(hx))
            st.pop("waiting_hex", None)

            mode = st.get("mode")
            if mode == "single":
                _render_single(chat_id, st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [])
            elif mode == "duo":
                _render_duo(chat_id,
                            st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [],
                            st["p2"], st.get("ru2") or _display_name_for(st["p2"]), st.get("stats2") or [])
            elif mode == "special":
                _render_special(chat_id, st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [], st.get("info") or "")
            elif mode == "bad":
                _render_bad(chat_id, st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [])
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # шаги исправлений (имена/команды)
    if st.get("fix_wait"):
        try:
            fw = st["fix_wait"]; s = text.strip()
            if fw == "name_which":
                m = re.match(r'^\s*([12])\s*=\s*(.+?)\s*$', s)
                if not m:
                    _status_update(chat_id, "Формат: 1=Имя или 2=Имя"); return PlainTextResponse("OK")
                idx, name_ru = m.group(1), m.group(2).strip()
                p = st["p1"] if idx == "1" else st["p2"]
                pid = str((p or {}).get("personId") or "")
                try:
                    if overrides_save_name_ru and pid: overrides_save_name_ru(pid, name_ru)
                except Exception: pass
                st["ru1" if idx=="1" else "ru2"] = name_ru
                st["fix_wait"] = None
            elif fw == "name_one":
                m = re.match(r'^\s*1\s*=\s*(.+?)\s*$', s)
                if not m:
                    _status_update(chat_id, "Формат: 1=Имя"); return PlainTextResponse("OK")
                name_ru = m.group(1).strip()
                p = st.get("p1") or {}
                pid = str(p.get("personId") or "")
                try:
                    if overrides_save_name_ru and pid: overrides_save_name_ru(pid, name_ru)
                except Exception: pass
                st["ru1"] = name_ru
                st["fix_wait"] = None
            elif fw == "teams_map":
                for part in s.split(","):
                    part = part.strip()
                    if not part: continue
                    m = re.match(r'^\s*([12])\s*=\s*(\d+)\s*$', part)
                    if not m: continue
                    idx, team_id = m.group(1), m.group(2)
                    if idx == "1" and st.get("p1"): st["p1"]["teamId"] = int(team_id)
                    if idx == "2" and st.get("p2"): st["p2"]["teamId"] = int(team_id)
                st["fix_wait"] = None

            mode = st.get("mode")
            if mode == "single":
                _render_single(chat_id, st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [])
            elif mode == "duo":
                _render_duo(chat_id,
                            st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [],
                            st["p2"], st.get("ru2") or _display_name_for(st["p2"]), st.get("stats2") or [])
            elif mode == "special":
                _render_special(chat_id, st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [], st.get("info") or "")
            elif mode == "bad":
                _render_bad(chat_id, st["p1"], st.get("ru1") or _display_name_for(st["p1"]), st.get("stats1") or [])
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # Reply с русским именем
    rpl = msg.get("reply_to_message")
    if rpl and text:
        try:
            rtxt = (rpl.get("text") or "") + " " + (rpl.get("caption") or "")
            m = re.search(r"\[setname:(\d+)\]", rtxt)
            if m and overrides_save_name_ru:
                pid = m.group(1)
                name_ru = text.strip()
                try:
                    overrides_save_name_ru(pid, name_ru)
                    _status_update(chat_id, f"Сохранил имя для {pid}: {name_ru}")
                except Exception as e:
                    _fail(chat_id, f"Не удалось сохранить имя: {repr(e)}"); return PlainTextResponse("OK")

                mode = st.get("mode")
                if mode == "duo":
                    if str((st.get("p1") or {}).get("personId")) == pid: st["ru1"] = name_ru
                    if str((st.get("p2") or {}).get("personId")) == pid: st["ru2"] = name_ru
                    if not st.get("ru1"):
                        p1 = st.get("p1") or {}
                        _ask_ru_name(chat_id, str(p1.get("personId") or ""), p1.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                        _status_update(chat_id, "Жду русское имя для игрока 1…")
                        return PlainTextResponse("OK")
                    if not st.get("ru2"):
                        p2 = st.get("p2") or {}
                        _ask_ru_name(chat_id, str(p2.get("personId") or ""), p2.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                        _status_update(chat_id, "Жду русское имя для игрока 2…")
                        return PlainTextResponse("OK")
                    _render_duo(chat_id, st["p1"], st["ru1"], st.get("stats1") or [], st["p2"], st["ru2"], st.get("stats2") or [])
                else:
                    st["ru1"] = name_ru
                    if mode == "single":
                        _render_single(chat_id, st["p1"], st["ru1"], st.get("stats1") or [])
                    elif mode == "special":
                        _render_special(chat_id, st["p1"], st["ru1"], st.get("stats1") or [], st.get("info") or "")
                    elif mode == "bad":
                        _render_bad(chat_id, st["p1"], st["ru1"], st.get("stats1") or [])
                return PlainTextResponse("OK")
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
            return PlainTextResponse("OK")

    # ---- Commands (case-insensitive) ----
    low = text.lower()

    if low.startswith("/start"):
        _status_update(chat_id, "Я здесь. Готов работать 💼")
        return PlainTextResponse("OK")

    if low.startswith("/help"):
        _status_update(chat_id,
            "Команды:\n"
            "• /find <имя>\n"
            "• /card <имя> | <статы>\n"
            "• /card2 <имя1> | <статы1> || <имя2> | <статы2>\n"
            "• /cards <имя> | <статы> | <текст справа>\n"
            "• /cardbad <имя> | <статы> (или /bad)\n"
            "• /stop — сброс сценария\n"
        )
        return PlainTextResponse("OK")

    if low.startswith("/find"):
        try:
            q = text[text.find(" "):].strip() if " " in text else ""
            hits = search_players_loose(q)
            if not hits:
                _status_update(chat_id, "Ничего не нашёл 🤷"); return PlainTextResponse("OK")
            lines = [f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})" for h in hits[:8]]
            _status_update(chat_id, "\n".join(lines))
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /card
    if re.match(r"^/(card)\b", low):
        try:
            args = text.split(" ",1)[1] if " " in text else ""
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _status_update(chat_id, "Формат: /card <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            name_q, stats_raw = parts[0], parts[1]
            stats = parse_stats_list(stats_raw)
            _status_update(chat_id, "Ищу игрока…")
            hits = search_players_loose(name_q)
            if not hits:
                _status_update(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")
            st.clear(); st.update({"mode":"single","p1":p,"stats1":stats,"last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": st.get("status_mid")})
            ru = None
            try: ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception: pass
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id, "Жду русское имя… Ответьте на сообщение выше.")
                return PlainTextResponse("OK")
            st["ru1"] = ru
            _render_single(chat_id, p, ru, stats)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /card2
    if re.match(r"^/(card2)\b", low):
        try:
            args = text.split(" ",1)[1] if " " in text else ""
            sides = [s.strip() for s in args.split("||")]
            if len(sides) != 2:
                _status_update(chat_id, "Формат: /card2 <имя1> | <статы1> || <имя2> | <статы2>")
                return PlainTextResponse("OK")

            def _side(s: str) -> Tuple[str, List[Tuple[str,str]]]:
                parts = [p.strip() for p in s.split("|")]
                return (parts[0] if parts else ""), (parse_stats_list(parts[1]) if len(parts) > 1 else [])

            n1, st1 = _side(sides[0])
            n2, st2 = _side(sides[1])
            _status_update(chat_id, "Ищу игроков…")
            h1, h2 = search_players_loose(n1), search_players_loose(n2)
            if not h1 or not h2:
                _status_update(chat_id, "Не нашёл одного из игроков, уточните имена.")
                return PlainTextResponse("OK")
            p1, p2 = h1[0], h2[0]
            st.clear(); st.update({
                "mode":"duo","p1":p1,"stats1":st1,"p2":p2,"stats2":st2,
                "last_cmd_msg_id": msg.get("message_id"),
                "status_mid": st.get("status_mid")
            })

            pid1, pid2 = str(p1.get("personId") or ""), str(p2.get("personId") or "")
            ru1 = ru2 = None
            try: ru1 = overrides_get_name_ru(pid1) if overrides_get_name_ru else None
            except Exception: pass
            try: ru2 = overrides_get_name_ru(pid2) if overrides_get_name_ru else None
            except Exception: pass

            if not ru1:
                _ask_ru_name(chat_id, pid1, p1.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id, "Жду русское имя для игрока 1…")
                return PlainTextResponse("OK")
            st["ru1"] = ru1
            if not ru2:
                _ask_ru_name(chat_id, pid2, p2.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id, "Жду русское имя для игрока 2…")
                return PlainTextResponse("OK")
            st["ru2"] = ru2
            _render_duo(chat_id, p1, ru1, st1, p2, ru2, st2)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /cards
    if re.match(r"^/(cards)\b", low):
        try:
            args = text.split(" ",1)[1] if " " in text else ""
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 3:
                _status_update(chat_id, "Формат: /cards <имя> | <статы> | <короткий текст справа>")
                return PlainTextResponse("OK")
            name_q, stats_raw, info_text = parts[0], parts[1], parts[2]
            stats = parse_stats_list(stats_raw)
            _status_update(chat_id, "Ищу игрока…")
            hits = search_players_loose(name_q)
            if not hits:
                _status_update(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")
            st.clear(); st.update({"mode":"special","p1":p,"stats1":stats,"info":info_text,
                                   "last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": st.get("status_mid")})
            ru = None
            try: ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception: pass
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id, "Жду русское имя… Ответьте на сообщение выше.")
                return PlainTextResponse("OK")
            st["ru1"] = ru
            _render_special(chat_id, p, ru, stats, info_text)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # /cardbad или /bad
    if re.match(r"^/(cardbad|bad)\b", low):
        try:
            args = text.split(" ",1)[1] if " " in text else ""
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                _status_update(chat_id, "Формат: /cardbad <имя> | <метрики через запятую>")
                return PlainTextResponse("OK")
            name_q, stats_raw = parts[0], parts[1]
            stats = parse_stats_list(stats_raw)
            _status_update(chat_id, "Ищу игрока…")
            hits = search_players_loose(name_q)
            if not hits:
                _status_update(chat_id, f"Не нашёл игрока: {name_q}")
                return PlainTextResponse("OK")
            p = hits[0]
            pid = str(p.get("personId") or "")
            st.clear(); st.update({"mode":"bad","p1":p,"stats1":stats,
                                   "last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": st.get("status_mid")})
            ru = None
            try: ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception: pass
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id, "Жду русское имя… Ответьте на сообщение выше.")
                return PlainTextResponse("OK")
            st["ru1"] = ru
            _render_bad(chat_id, p, ru, stats)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    # fallback
    _status_update(chat_id,
        "Команды:\n"
        "• /find <имя>\n"
        "• /card <имя> | <статы>\n"
        "• /card2 <имя1> | <статы1> || <имя2> | <статы2>\n"
        "• /cards <имя> | <статы> | <текст справа>\n"
        "• /cardbad <имя> | <статы> (или /bad)\n"
        "• /stop — сброс сценария\n")
    return PlainTextResponse("OK")
