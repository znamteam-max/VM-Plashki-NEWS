# api/telegram.py — стабильные статусы, память имён, рендер 1920x1080
from __future__ import annotations
import os, io, re, json, unicodedata, uuid, inspect
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, PlainTextResponse

from urllib.request import Request as UrlRequest, urlopen as http_urlopen

DEBUG = os.getenv("DEBUG", "1") in ("1","true","yes")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
API_ORIGIN = os.getenv("API_ORIGIN")

def _log(*a: Any) -> None:
    try: print(*a, flush=True)
    except: pass

def _safe_import(modname:str, names:List[str]):
    try:
        m = __import__(modname, fromlist=names)
        out = [getattr(m, n) for n in names]
        return m, out, None
    except Exception as e:
        return None, [], f"{e.__class__.__name__}: {e}"

# ---- deps: data OR overrides_store fallback
_data_mod, _data_objs, _data_err = _safe_import("data", [
    "get_players","refresh_players","find_player_by_name","display_name_for",
    "overrides_save_name_ru","overrides_get_name_ru",
    "ensure_headshot_png","ensure_team_logo_png",
])
(get_players, refresh_players, find_player_by_name, display_name_for,
 overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png) = ([_ for _ in _data_objs] + [None]*8)[:8]

# фолбэк-хранилище имён, если в data не реализовано
if overrides_save_name_ru is None or overrides_get_name_ru is None:
    _ov_mod, _ov_objs, _ov_err = _safe_import("overrides_store", [
        "overrides_save_name_ru", "overrides_get_name_ru"
    ])
    if not _ov_err and _ov_objs:
        overrides_save_name_ru, overrides_get_name_ru = _ov_objs

_brand_mod, _brand_objs, _brand_err = _safe_import("team_brand", [
    "get_team_brand","color_name_ru","set_team_primary_color",
])
(get_team_brand, color_name_ru, set_team_primary_color) = ([_ for _ in _brand_objs] + [None]*3)[:3]

_graphics_mod, _graphics_objs, _graphics_err = _safe_import("graphics", [
    "render_card","render_card2","render_card_special","render_card_bad",
])
(render_card, render_card2, render_card_special, render_card_bad) = ([_ for _ in _graphics_objs] + [None]*4)[:4]

app = FastAPI()

# ------------- Telegram HTTP -------------
def _tg_url(m:str)->str: return f"https://api.telegram.org/bot{BOT_TOKEN}/{m}"

def _http_json(url:str, payload:Dict[str,Any], timeout:int=25)->Dict[str,Any]:
    body = json.dumps(payload).encode("utf-8")
    req = UrlRequest(url, data=body, headers={"Content-Type":"application/json"})
    with http_urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok":False,"raw":raw.decode("utf-8","ignore")}

def _tg_post(m:str, payload:Dict[str,Any])->Dict[str,Any]:
    try:
        return _http_json(_tg_url(m), payload)
    except Exception as e:
        if DEBUG: _log("[tg] send error:", repr(e))
        return {"ok":False,"error":repr(e)}

def _tg_send_message(chat_id:int, text:str, *, reply_to:Optional[int]=None,
                     parse_mode:Optional[str]=None, reply_markup:Optional[Dict[str,Any]]=None)->Dict[str,Any]:
    payload={"chat_id":chat_id,"text":text,"disable_web_page_preview":True}
    if reply_to:
        payload["reply_to_message_id"]=reply_to
        payload["allow_sending_without_reply"]=True
    if parse_mode: payload["parse_mode"]=parse_mode
    if reply_markup: payload["reply_markup"]=reply_markup
    return _tg_post("sendMessage", payload)

def _tg_edit_message(chat_id:int, message_id:int, text:str, *, parse_mode:Optional[str]=None,
                     reply_markup:Optional[Dict[str,Any]]=None)->Dict[str,Any]:
    payload={"chat_id":chat_id,"message_id":message_id,"text":text,"disable_web_page_preview":True}
    if parse_mode: payload["parse_mode"]=parse_mode
    if reply_markup: payload["reply_markup"]=reply_markup
    return _tg_post("editMessageText", payload)

# multipart для PNG
def _mp_boundary()->str: return "----WebKitFormBoundary" + uuid.uuid4().hex
def _encode_mp(fields:Dict[str,str], files:Dict[str,Tuple[str,bytes,str]]):
    bnd = _mp_boundary(); lines=[]; CRLF=b"\r\n"
    for k,v in fields.items():
        lines += [b"--"+bnd.encode(), f'Content-Disposition: form-data; name="{k}"'.encode(), b"", v.encode("utf-8")]
    for k,(fn,content,ctype) in files.items():
        lines += [b"--"+bnd.encode(),
                  f'Content-Disposition: form-data; name="{k}"; filename="{fn}"'.encode(),
                  f"Content-Type: {ctype}".encode(), b"", content]
    lines.append(b"--"+bnd.encode()+b"--")
    body = CRLF.join(lines)
    return body, f"multipart/form-data; boundary={bnd}"

def _tg_send_png_as_document(chat_id:int, png:bytes, filename:str="card.png", caption:Optional[str]=None):
    url = _tg_url("sendDocument")
    fields={"chat_id":str(chat_id)}
    if caption: fields["caption"]=caption
    files={"document":(filename, png, "image/png")}
    body,ctype = _encode_mp(fields, files)
    req = UrlRequest(url, data=body, headers={"Content-Type":ctype})
    with http_urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8","ignore")
        try: return json.loads(raw)
        except Exception: return {"ok":False,"raw":raw}

# ------------- utils -------------
def _normalize(s:str)->str:
    s=(s or "").strip().lower().replace("ё","е")
    s=unicodedata.normalize("NFKD", s)
    s="".join(ch for ch in s if not unicodedata.combining(ch))
    keep="abcdefghijklmnopqrstuvwxyzабвгдеэжзийклмнопрстуфхцчшщьыъэюя -'0123456789+/%"
    s="".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

LABEL_TOKENS=[
    ("плюс/минус","+/-"),("plus/minus","+/-"),("pm","+/-"),("+/-","+/-"),
    ("% трех","3P%"),("% трёх","3P%"),("3p%","3P%"),("3pt%","3P%"),("3 %","3P%"),
    ("fg%","FG%"),("% бросков","FG%"),("% с игры","FG%"),
    ("броски с игры","С ИГРЫ"),("с игры","С ИГРЫ"),("fgm-a","С ИГРЫ"),("fg","С ИГРЫ"),
    ("трехочков","3-ОЧКОВЫЕ"),("трёхочков","3-ОЧКОВЫЕ"),("3-очков","3-ОЧКОВЫЕ"),
    ("3 очк","3-ОЧКОВЫЕ"),("трешки","3-ОЧКОВЫЕ"),("трёшки","3-ОЧКОВЫЕ"),
    ("3pt","3-ОЧКОВЫЕ"),("3pm","3-ОЧКОВЫЕ"),("stocks","СТИЛОБЛОКИ"),("стилоблок","СТИЛОБЛОКИ"),
    ("перехват","ПЕРЕХВАТЫ"),("stl","ПЕРЕХВАТЫ"),
    ("блок","БЛОКИ"),("blk","БЛОКИ"),
    ("передач","ПЕРЕДАЧИ"),("ast","ПЕРЕДАЧИ"),
    ("подбор","ПОДБОРЫ"),("reb","ПОДБОРЫ"),("rebs","ПОДБОРЫ"),
    ("очк","ОЧКИ"),("pts","ОЧКИ"),("points","ОЧКИ"),
    ("минут","МИНУТЫ"),("мин","МИНУТЫ"),("min","МИНУТЫ"),
    ("фол","ФОЛЫ"),("pf","ФОЛЫ"),
    ("потер","ПОТЕРИ"),("tov","ПОТЕРИ"),("to","ПОТЕРИ"),
]
STAT_TOKEN_MAP = {k:v for k,v in LABEL_TOKENS}
STAT_TOKEN_MAP.update({"трех":"3-ОЧКОВЫЕ","трёх":"3-ОЧКОВЫЕ","3-очков":"3-ОЧКОВЫЕ","3 очк":"3-ОЧКОВЫЕ"})

import re
VAL_RX = re.compile(r'([+\-]?\d+(?:\s*из\s*\d+)?|[+\-]?\d+/\d+|[+\-]?\d+\s*-\s*\d+|[+\-]?\d+(?:\.\d+)?%?)', re.IGNORECASE)
def _strip_quotes(s:str)->str:
    s=s.strip()
    for a,b in [('"','"'),("'","'"),("«","»"),("“","”"),("(",")")]:
        if s.startswith(a) and s.endswith(b) and len(s)>=2: return s[1:-1].strip()
    return s

def parse_stats_list(raw:str)->List[Tuple[str,str]]:
    if not raw: return []
    parts = [p for p in (x.strip() for x in raw.split(",")) if p]
    out=[]
    for p in parts:
        seg=_strip_quotes(p); low=seg.lower().replace("ё","е")
        found=None; found_pos=None
        for tok,canon in LABEL_TOKENS:
            pos=low.find(tok)
            if pos!=-1 and (found_pos is None or pos<found_pos):
                found,(found_pos)=( (tok,canon), pos )
        if found:
            value=seg[:found_pos].strip(" ,–—-")
            if not value:
                tail=seg[found_pos+len(found[0]):]
                m=VAL_RX.search(tail); value=m.group(1) if m else ""
            if not value: value="0"
            out.append((value, found[1])); continue
        m=VAL_RX.search(seg); value=m.group(1) if m else seg.strip()
        lbl="СТАТ"; nseg=_normalize(seg)
        for k,v in STAT_TOKEN_MAP.items():
            if k in nseg: lbl=v; break
        out.append((value,lbl))
    return out

# ---------- players ----------
PLAYERS_READY=False
def _get_players(force=False):
    try:
        ps = get_players(force_refresh=bool(force)) if get_players else []
    except TypeError:
        ps = get_players() if get_players else []
    if (not ps) and refresh_players:
        try:
            refresh_players()
            ps = get_players() if get_players else []
        except Exception as e:
            _log("[players] refresh error:", repr(e))
    return ps or []

def search_players_loose(q:str):
    qn=_normalize(q)
    ps=_get_players(False)
    if find_player_by_name:
        try:
            hits=find_player_by_name(q) or []
            if hits: return hits
        except Exception: pass
    out=[]
    for p in ps:
        dn=p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        if dn and qn in _normalize(dn):
            out.append(p)
            if len(out)>=10: break
    return out

# ---------- images/brand ----------
def _ensure_headshot_image(p:Dict[str,Any]):
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
        _log("[headshot err]", p.get("personId"), repr(e)); return None

def _ensure_team_logo_image(team_id:str):
    from PIL import Image
    try:
        path = ensure_team_logo_png(team_id) if ensure_team_logo_png else None
        if path and os.path.exists(path):
            return Image.open(path).convert("RGBA")
        if get_team_brand:
            colors, logo_path, _, _ = get_team_brand(team_id)
            if logo_path and os.path.exists(logo_path):
                return Image.open(logo_path).convert("RGBA")
        return None
    except Exception as e:
        _log("[team logo err]", team_id, repr(e)); return None

def _team_colors(team_id:str)->Tuple[str,str,str]:
    try:
        colors,_,_,_ = get_team_brand(team_id) if get_team_brand else (("#7F7F7F","#4A4A4A","#111111"),None,[],False)
        return colors
    except Exception:
        return ("#7F7F7F","#4A4A4A","#111111")

# ---------- helpers ----------
def _call_render(func, *a, **kw):
    # безопасно отбрасываем неизвестные параметры
    try:
        sig = inspect.signature(func)
        allowed = {k:v for k,v in kw.items() if k in sig.parameters}
        return func(*a, **allowed)
    except Exception:
        return func(*a)

def _stats_text(stats:List[Tuple[str,str]]):
    stats = [(str(v), str(l)) for (v,l) in (stats or [])]
    return ", ".join((f"{v} {l}" if l else f"{v}") for v,l in stats)

# ---------- state + statuses ----------
CTX: Dict[int, Dict[str,Any]] = {}
def _ctx(chat_id:int)->Dict[str,Any]: return CTX.setdefault(chat_id, {})
def _ctx_clear(chat_id:int)->None: CTX.pop(chat_id, None)

def _status_update(chat_id:int, text:str, *, parse_mode:Optional[str]=None, keep_kb:Optional[Dict[str,Any]]=None):
    st=_ctx(chat_id)
    mid=st.get("status_mid")
    if mid:
        r=_tg_edit_message(chat_id, mid, text, parse_mode=parse_mode, reply_markup=keep_kb)
        if not r.get("ok"):
            sent=_tg_send_message(chat_id, text, parse_mode=parse_mode, reply_markup=keep_kb)
            if sent.get("ok"): st["status_mid"]=sent["result"]["message_id"]
    else:
        sent=_tg_send_message(chat_id, text, parse_mode=parse_mode, reply_markup=keep_kb)
        if sent.get("ok"): st["status_mid"]=sent["result"]["message_id"]

def _fail(chat_id:int, human:str): _status_update(chat_id, f"❌ {human}")

def _ask_ru_name(chat_id:int, pid:str, display_name:str, reply_to:Optional[int]):
    st=_ctx(chat_id)
    txt=f"Как подписать игрока <b>{display_name}</b> на плашке?\nОтветьте на это сообщение русским именем.\n[setname:{pid}]"
    sent=_tg_send_message(chat_id, txt, reply_to=reply_to, parse_mode="HTML")
    st["waiting_name_pid"]=pid
    st["waiting_name_msg_id"]= sent["result"]["message_id"] if sent.get("ok") else None

# ---------- keyboards ----------
def _kb_ok_or_fix():
    return {"inline_keyboard":[
        [{"text":"Всё ок ✅","callback_data":"fix:ok"},
         {"text":"Нужно исправить ✏️","callback_data":"fix:menu"}]
    ]}

def _ctx_players(st:Dict[str,Any])->List[Tuple[str,Dict[str,Any]]]:
    out=[]
    if st.get("p1"): out.append(("1", st["p1"]))
    if st.get("mode")=="duo" and st.get("p2"): out.append(("2", st["p2"]))
    return out

def _render_ctx(chat_id:int):
    st=_ctx(chat_id)
    mode=st.get("mode")
    if mode=="single":
        _render_single(chat_id, st["p1"], st.get("ru1") or "", st.get("stats1") or [])
    elif mode=="special":
        _render_special(chat_id, st["p1"], st.get("ru1") or "", st.get("stats1") or [], st.get("info") or "")
    elif mode=="bad":
        _render_bad(chat_id, st["p1"], st.get("ru1") or "", st.get("stats1") or [])
    elif mode=="duo":
        _render_duo(chat_id, st["p1"], st.get("ru1") or "", st.get("stats1") or [],
                    st["p2"], st.get("ru2") or "", st.get("stats2") or [])

def _color_menu_for_team(team_id:str):
    if not (get_team_brand and set_team_primary_color):
        return None
    try:
        _, _, palette, has_saved = get_team_brand(team_id)
    except Exception:
        palette, has_saved = [], False
    rows=[]
    for hexv in (palette or [])[:6]:
        clean=hexv.strip().upper().lstrip("#")
        label_name = color_name_ru(hexv) if color_name_ru else "цвет"
        rows.append([{"text":f"{label_name} #{clean}", "callback_data":f"fix:color:set:{team_id}:{clean}"}])
    rows.append([{"text":"Ввести HEX", "callback_data":f"fix:color:manual:{team_id}"}])
    if has_saved:
        rows.append([{"text":"AUTO", "callback_data":f"fix:color:set:{team_id}:AUTO"}])
    return {"inline_keyboard":rows}

def _color_team_choice_keyboard(st:Dict[str,Any]):
    seen=set(); rows=[]
    for slot,p in _ctx_players(st):
        team_id=str(p.get("teamId") or "0")
        if team_id in seen: continue
        seen.add(team_id)
        label=p.get("teamName") or p.get("teamId") or team_id
        rows.append([{"text":f"Команда {slot}: {label}", "callback_data":f"fix:color:team:{team_id}"}])
    return {"inline_keyboard":rows} if rows else None

# ---------- secret ----------
from starlette.responses import PlainTextResponse
def _check_secret(request:Request):
    secret=(request.query_params.get("secret") or "").strip()
    if not WEBHOOK_SECRET or secret!=WEBHOOK_SECRET:
        return PlainTextResponse("bad secret", status_code=401)
    return None

# ---------- GET ----------
@app.get("/api/telegram")
async def telegram_get(request:Request):
    bad=_check_secret(request)
    if bad: return bad
    action=(request.query_params.get("action") or "").strip()

    if action=="diag":
        return JSONResponse({
            "ok":True,
            "py":".".join(map(str,__import__("sys").version_info[:3])),
            "platform": __import__("platform").system().lower(),
            "has_bot_token": bool(BOT_TOKEN),
            "modules":{
                "data":"ok" if _data_err is None else "error",
                "graphics":"ok" if _graphics_err is None else "error",
                "team_brand":"ok" if _brand_err is None else "error",
            },
            "errors":{"data":_data_err,"graphics":_graphics_err,"team_brand":_brand_err},
            "api_origin": API_ORIGIN or None,
        })

    if action=="refresh":
        try:
            if refresh_players: refresh_players()
            ps = get_players() if get_players else []
            cnt = len(ps) if isinstance(ps, list) else 0
            return JSONResponse({"ok":True,"refreshed":True,"players_indexed":cnt,"source":"custom","source_url":"merged"})
        except Exception as e:
            return JSONResponse({"ok":False,"refreshed":False,"error":repr(e)}, status_code=500)

    if action=="test_find":
        q=request.query_params.get("q") or ""
        hits=[{"personId":h.get("personId"),"displayName":h.get("displayName"),"teamId":h.get("teamId")} for h in search_players_loose(q)]
        return JSONResponse({"ok":True,"q":q,"hits":hits})
    return PlainTextResponse("ok")

# ---------- render wrappers ----------
def _render_single(chat_id:int, p:Dict[str,Any], ru:str, stats:List[Tuple[str,str]], ask=True):
    try:
        from graphics import render_card
        _status_update(chat_id, "Готовлю плашку…")
        head=_ensure_headshot_image(p)
        if head is None: _fail(chat_id,"Не удалось получить фото игрока."); return
        logo=_ensure_team_logo_image(str(p.get("teamId") or "0"))
        colors=_team_colors(str(p.get("teamId") or "0"))
        # приводим статы
        stats=[(str(v), str(l)) for (v,l) in (stats or [])]
        png=_call_render(render_card, "single", ru, "", logo, colors, head, stats)
        sent=_tg_send_png_as_document(chat_id, png, filename=f"card_{p.get('personId','x')}.png", caption=_stats_text(stats))
        if not sent.get("ok"): _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        if ask: _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

def _render_bad(chat_id:int, p:Dict[str,Any], ru:str, stats:List[Tuple[str,str]]):
    try:
        from graphics import render_card_bad
        _status_update(chat_id, "Готовлю BAD-плашку…")
        head=_ensure_headshot_image(p)
        if head is None: _fail(chat_id,"Не удалось получить фото игрока."); return
        logo=_ensure_team_logo_image(str(p.get("teamId") or "0"))
        stats=[(str(v), str(l)) for (v,l) in (stats or [])]
        png=_call_render(render_card_bad, ru, head, stats, team_logo_img=logo)
        sent=_tg_send_png_as_document(chat_id, png, filename=f"cardBAD_{p.get('personId','x')}.png", caption=_stats_text(stats))
        if not sent.get("ok"): _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

def _render_duo(chat_id:int, p1:Dict[str,Any], ru1:str, st1, p2:Dict[str,Any], ru2:str, st2):
    try:
        from graphics import render_card2
        _status_update(chat_id, "Готовлю двойную плашку…")
        h1=_ensure_headshot_image(p1); h2=_ensure_headshot_image(p2)
        if h1 is None or h2 is None: _fail(chat_id,"Не удалось получить фото одного из игроков."); return
        l1=_ensure_team_logo_image(str(p1.get("teamId") or "0"))
        l2=_ensure_team_logo_image(str(p2.get("teamId") or "0"))
        c1=_team_colors(str(p1.get("teamId") or "0")); c2=_team_colors(str(p2.get("teamId") or "0"))
        st1=[(str(v), str(l)) for (v,l) in (st1 or [])]
        st2=[(str(v), str(l)) for (v,l) in (st2 or [])]
        png=_call_render(render_card2, ru1, l1, c1, h1, st1, ru2, l2, c2, h2, st2)
        sent=_tg_send_png_as_document(chat_id, png, filename=f"card2_{p1.get('personId','x')}_{p2.get('personId','y')}.png",
                                      caption=f"{_stats_text(st1)}  |  {_stats_text(st2)}")
        if not sent.get("ok"): _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

def _render_special(chat_id:int, p:Dict[str,Any], ru:str, stats, info_text:str):
    try:
        from graphics import render_card_special
        _status_update(chat_id, "Готовлю плашку с правой колонкой…")
        head=_ensure_headshot_image(p)
        if head is None: _fail(chat_id,"Не удалось получить фото игрока."); return
        logo=_ensure_team_logo_image(str(p.get("teamId") or "0"))
        colors=_team_colors(str(p.get("teamId") or "0"))
        stats=[(str(v), str(l)) for (v,l) in (stats or [])]
        png=_call_render(render_card_special, ru, logo, colors, head, stats, (info_text or ""))
        sent=_tg_send_png_as_document(chat_id, png, filename=f"cards_{p.get('personId','x')}.png", caption=_stats_text(stats))
        if not sent.get("ok"): _fail(chat_id, f"Ошибка отправки PNG: {sent.get('error') or sent}"); return
        _status_update(chat_id, "Готово. Всё ок или нужно исправить?", keep_kb=_kb_ok_or_fix())
    except Exception as e:
        _fail(chat_id, f"Ошибка рендера: {repr(e)}")

# ---------- webhook ----------
@app.post("/api/telegram")
async def webhook_query(request:Request):
    bad=_check_secret(request)
    if bad: return bad
    try:
        body=await request.body()
        raw=body.decode("utf-8","ignore")
        update=json.loads(raw)
        if DEBUG: _log("[tg] ", raw)
    except Exception:
        return PlainTextResponse("OK")

    # callback-кнопки
    cb=update.get("callback_query")
    if cb:
        try:
            chat_id=cb["from"]["id"]; data=cb.get("data") or ""; st=_ctx(chat_id)
            if data=="fix:ok":
                _ctx_clear(chat_id); _tg_send_message(chat_id,"Готово ✅"); return PlainTextResponse("OK")
            if data=="fix:menu":
                _status_update(chat_id,"Что исправить?", keep_kb={"inline_keyboard":[
                    [{"text":"Имена игроков","callback_data":"fix:names"},
                     {"text":"Цвет плашки","callback_data":"fix:color"}],
                    [{"text":"Команды","callback_data":"fix:teams"}]
                ]}); return PlainTextResponse("OK")
            if data=="fix:names":
                players=_ctx_players(st)
                if not players:
                    _status_update(chat_id,"Не вижу последнюю плашку для исправления. Сделайте новую командой /card."); return PlainTextResponse("OK")
                st.pop("waiting_color_team", None)
                if st.get("mode")=="duo":
                    st["ru1"]=None; st["ru2"]=None
                else:
                    st["ru1"]=None
                slot,p=players[0]
                _ask_ru_name(chat_id, str(p.get("personId") or ""), p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id,"Жду новое русское имя… Ответьте на сообщение выше."); return PlainTextResponse("OK")
            if data=="fix:color":
                kb=_color_team_choice_keyboard(st)
                if not kb:
                    _status_update(chat_id,"Не вижу команду для исправления цвета. Сделайте новую плашку."); return PlainTextResponse("OK")
                if len(kb["inline_keyboard"])==1:
                    cbdata=kb["inline_keyboard"][0][0]["callback_data"]
                    team_id=cbdata.rsplit(":",1)[-1]
                    pal_kb=_color_menu_for_team(team_id)
                    if pal_kb:
                        _status_update(chat_id,"Выберите цвет плашки или введите свой HEX.", keep_kb=pal_kb)
                    else:
                        _status_update(chat_id,"Сейчас не удалось открыть палитру команды.")
                    return PlainTextResponse("OK")
                _status_update(chat_id,"Для какой команды поменять цвет?", keep_kb=kb); return PlainTextResponse("OK")
            if data.startswith("fix:color:team:"):
                team_id=data.rsplit(":",1)[-1]
                kb=_color_menu_for_team(team_id)
                if kb:
                    _status_update(chat_id,"Выберите цвет плашки или введите свой HEX.", keep_kb=kb)
                else:
                    _status_update(chat_id,"Сейчас не удалось открыть палитру команды.")
                return PlainTextResponse("OK")
            if data.startswith("fix:color:manual:"):
                team_id=data.rsplit(":",1)[-1]
                st["waiting_color_team"]=team_id
                _status_update(chat_id,f"Пришлите HEX для teamId={team_id}, например #552583. Можно написать AUTO для сброса.")
                return PlainTextResponse("OK")
            if data.startswith("fix:color:set:"):
                parts=data.split(":")
                if len(parts)>=5 and set_team_primary_color:
                    team_id, raw = parts[3], parts[4]
                    value = "AUTO" if raw.upper()=="AUTO" else f"#{raw.upper().lstrip('#')}"
                    ok=set_team_primary_color(team_id, value)
                    if ok:
                        st.pop("waiting_color_team", None)
                        _status_update(chat_id,f"Сохранил цвет команды {team_id}: {value}. Пересобираю плашку…")
                        _render_ctx(chat_id)
                    else:
                        _status_update(chat_id,"Не смог сохранить цвет. Проверьте HEX: нужен формат #RRGGBB.")
                return PlainTextResponse("OK")
            if data=="fix:teams":
                _status_update(chat_id,"Команда берётся из базы NBA по игроку. Сейчас можно исправить имя игрока или цвет плашки.", keep_kb=_kb_ok_or_fix())
                return PlainTextResponse("OK")
            return PlainTextResponse("OK")
        except Exception as e:
            _fail(cb["from"]["id"], f"Ошибка: {repr(e)}"); return PlainTextResponse("OK")

    msg=update.get("message") or update.get("edited_message")
    if not msg: return PlainTextResponse("OK")
    chat_id=msg["chat"]["id"]; text=(msg.get("text") or "").strip()
    st=_ctx(chat_id); st.setdefault("last_cmd_msg_id", msg.get("message_id"))

    # аварийный выход
    if text.lower().startswith("/stop"):
        _ctx_clear(chat_id); _tg_send_message(chat_id,"Остановил сценарий и очистил контекст. ✅"); return PlainTextResponse("OK")

    # ответ-реплай на вопрос имени
    rpl=msg.get("reply_to_message")
    if rpl and text:
        rtxt=(rpl.get("text") or "") + " " + (rpl.get("caption") or "")
        m=re.search(r"\[setname:(\d+)\]", rtxt)
        if m and overrides_save_name_ru:
            pid=m.group(1); name_ru=text.strip()
            try:
                overrides_save_name_ru(pid, name_ru)
                _status_update(chat_id, f"Сохранил имя для {pid}: {name_ru}")
            except Exception as e:
                _fail(chat_id, f"Не удалось сохранить имя: {repr(e)}"); return PlainTextResponse("OK")

            # продолжим сценарий автоматически
            mode=st.get("mode")
            if mode=="duo":
                if str((st.get("p1") or {}).get("personId"))==pid: st["ru1"]=name_ru
                if str((st.get("p2") or {}).get("personId"))==pid: st["ru2"]=name_ru
                if not st.get("ru1"):
                    p1=st.get("p1") or {}
                    _ask_ru_name(chat_id, str(p1.get("personId") or ""), p1.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                    _status_update(chat_id,"Жду русское имя для игрока 1…"); return PlainTextResponse("OK")
                if not st.get("ru2"):
                    p2=st.get("p2") or {}
                    _ask_ru_name(chat_id, str(p2.get("personId") or ""), p2.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                    _status_update(chat_id,"Жду русское имя для игрока 2…"); return PlainTextResponse("OK")
                _render_duo(chat_id, st["p1"], st["ru1"], st.get("stats1") or [], st["p2"], st["ru2"], st.get("stats2") or [])
            else:
                st["ru1"]=name_ru
                if mode=="single": _render_single(chat_id, st["p1"], st["ru1"], st.get("stats1") or [])
                elif mode=="special": _render_special(chat_id, st["p1"], st["ru1"], st.get("stats1") or [], st.get("info") or "")
                elif mode=="bad": _render_bad(chat_id, st["p1"], st["ru1"], st.get("stats1") or [])
            return PlainTextResponse("OK")

    if st.get("waiting_color_team") and text:
        team_id=str(st.get("waiting_color_team") or "")
        raw=text.strip().upper()
        if raw=="AUTO":
            value="AUTO"
        else:
            if not raw.startswith("#"):
                raw="#"+raw
            value=raw
        if value!="AUTO" and not re.fullmatch(r"#[0-9A-F]{6}", value):
            _status_update(chat_id,"HEX не похож на #RRGGBB. Например: #552583. Можно написать AUTO.")
            return PlainTextResponse("OK")
        if not set_team_primary_color:
            _status_update(chat_id,"Сохранение цвета сейчас недоступно.")
            return PlainTextResponse("OK")
        try:
            ok=set_team_primary_color(team_id, value)
            if not ok:
                _status_update(chat_id,"Не смог сохранить цвет. Проверьте формат HEX.")
                return PlainTextResponse("OK")
            st.pop("waiting_color_team", None)
            _status_update(chat_id,f"Сохранил цвет команды {team_id}: {value}. Пересобираю плашку…")
            _render_ctx(chat_id)
        except Exception as e:
            _fail(chat_id, f"Не удалось сохранить цвет: {repr(e)}")
        return PlainTextResponse("OK")

    # команды
    low=text.lower()

    if low.startswith("/start"):
        _status_update(chat_id, "Я здесь. Готов работать 💼"); return PlainTextResponse("OK")

    if low.startswith("/help"):
        _status_update(chat_id,
            "Команды:\n"
            "• /find <имя>\n"
            "• /card <имя> | <статы>\n"
            "• /card2 <имя1> | <статы1> || <имя2> | <статы2>\n"
            "• /cards <имя> | <статы> | <текст справа>\n"
            "• /cardbad <имя> | <статы> (или /bad)\n"
            "• /stop — сброс сценария\n"
        ); return PlainTextResponse("OK")

    if low.startswith("/find"):
        q = text[text.find(" "):].strip() if " " in text else ""
        hits=search_players_loose(q)
        if not hits: _status_update(chat_id,"Ничего не нашёл 🤷"); return PlainTextResponse("OK")
        lines=[f"{h.get('displayName')} (id={h.get('personId')}, teamId={h.get('teamId')})" for h in hits[:8]]
        _status_update(chat_id, "\n".join(lines)); return PlainTextResponse("OK")

    if re.match(r"^/(card)\b", low):
        try:
            args=text.split(" ",1)[1] if " " in text else ""
            parts=[p.strip() for p in args.split("|")]
            if len(parts)<2: _status_update(chat_id,"Формат: /card <имя> | <метрики через запятую>"); return PlainTextResponse("OK")
            name_q, stats_raw = parts[0], parts[1]
            stats=parse_stats_list(stats_raw)
            _status_update(chat_id,"Ищу игрока…")
            hits=search_players_loose(name_q)
            if not hits: _status_update(chat_id,f"Не нашёл игрока: {name_q}"); return PlainTextResponse("OK")
            p=hits[0]; pid=str(p.get("personId") or "")
            st.clear(); st.update({"mode":"single","p1":p,"stats1":stats,"last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": st.get("status_mid")})
            ru=None
            try: ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            except Exception: pass
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id,"Жду русское имя… Ответьте на сообщение выше."); return PlainTextResponse("OK")
            st["ru1"]=ru
            _render_single(chat_id, p, ru, stats)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    if re.match(r"^/(card2)\b", low):
        try:
            args=text.split(" ",1)[1] if " " in text else ""
            sides=[s.strip() for s in args.split("||")]
            if len(sides)!=2:
                _status_update(chat_id,"Формат: /card2 <имя1> | <статы1> || <имя2> | <статы2>"); return PlainTextResponse("OK")
            def _side(s:str):
                ps=[p.strip() for p in s.split("|")]
                return (ps[0] if ps else ""), (parse_stats_list(ps[1]) if len(ps)>1 else [])
            n1, st1 = _side(sides[0]); n2, st2 = _side(sides[1])
            _status_update(chat_id,"Ищу игроков…")
            h1, h2 = search_players_loose(n1), search_players_loose(n2)
            if not h1 or not h2:
                _status_update(chat_id,"Не нашёл одного из игроков, уточните имена."); return PlainTextResponse("OK")
            p1, p2 = h1[0], h2[0]
            st.clear(); st.update({"mode":"duo","p1":p1,"stats1":st1,"p2":p2,"stats2":st2,
                                   "last_cmd_msg_id":msg.get("message_id"),"status_mid": st.get("status_mid")})
            pid1, pid2 = str(p1.get("personId") or ""), str(p2.get("personId") or "")
            ru1 = overrides_get_name_ru(pid1) if overrides_get_name_ru else None
            ru2 = overrides_get_name_ru(pid2) if overrides_get_name_ru else None
            if not ru1:
                _ask_ru_name(chat_id, pid1, p1.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id,"Жду русское имя для игрока 1…"); return PlainTextResponse("OK")
            st["ru1"]=ru1
            if not ru2:
                _ask_ru_name(chat_id, pid2, p2.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id,"Жду русское имя для игрока 2…"); return PlainTextResponse("OK")
            st["ru2"]=ru2
            _render_duo(chat_id, p1, ru1, st1, p2, ru2, st2)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    if re.match(r"^/(cards)\b", low):
        try:
            args=text.split(" ",1)[1] if " " in text else ""
            parts=[p.strip() for p in args.split("|")]
            if len(parts)<3: _status_update(chat_id,"Формат: /cards <имя> | <статы> | <короткий текст справа>"); return PlainTextResponse("OK")
            name_q, stats_raw, info_text = parts[0], parts[1], parts[2]
            stats=parse_stats_list(stats_raw)
            _status_update(chat_id,"Ищу игрока…")
            hits=search_players_loose(name_q)
            if not hits: _status_update(chat_id,f"Не нашёл игрока: {name_q}"); return PlainTextResponse("OK")
            p=hits[0]; pid=str(p.get("personId") or "")
            st.clear(); st.update({"mode":"special","p1":p,"stats1":stats,"info":info_text,
                                   "last_cmd_msg_id":msg.get("message_id"),"status_mid": st.get("status_mid")})
            ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id,"Жду русское имя… Ответьте на сообщение выше."); return PlainTextResponse("OK")
            st["ru1"]=ru
            _render_special(chat_id, p, ru, stats, info_text)
        except Exception as e:
            _fail(chat_id, f"Ошибка: {repr(e)}")
        return PlainTextResponse("OK")

    if re.match(r"^/(cardbad|bad)\b", low):
        try:
            args=text.split(" ",1)[1] if " " in text else ""
            parts=[p.strip() for p in args.split("|")]
            if len(parts)<2: _status_update(chat_id,"Формат: /cardbad <имя> | <метрики через запятую>"); return PlainTextResponse("OK")
            name_q, stats_raw = parts[0], parts[1]
            stats=parse_stats_list(stats_raw)
            _status_update(chat_id,"Ищу игрока…")
            hits=search_players_loose(name_q)
            if not hits: _status_update(chat_id,f"Не нашёл игрока: {name_q}"); return PlainTextResponse("OK")
            p=hits[0]; pid=str(p.get("personId") or "")
            st.clear(); st.update({"mode":"bad","p1":p,"stats1":stats,"last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": st.get("status_mid")})
            ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
            if not ru:
                _ask_ru_name(chat_id, pid, p.get("displayName") or "", reply_to=st.get("last_cmd_msg_id"))
                _status_update(chat_id,"Жду русское имя… Ответьте на сообщение выше."); return PlainTextResponse("OK")
            st["ru1"]=ru
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
