# api/telegram.py — стабильные статусы, память имён, рендер 1920x1080
from __future__ import annotations
import os, io, re, json, unicodedata, uuid, inspect
from difflib import SequenceMatcher
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
    "ensure_headshot_png","ensure_team_logo_png","save_custom_headshot",
])
(get_players, refresh_players, find_player_by_name, display_name_for,
 overrides_save_name_ru, overrides_get_name_ru,
 ensure_headshot_png, ensure_team_logo_png, save_custom_headshot) = ([_ for _ in _data_objs] + [None]*9)[:9]

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
_BOT_MENU_SET = False

try:
    from teams import TEAMS
except Exception:
    TEAMS = {}

def _ensure_bot_commands()->None:
    global _BOT_MENU_SET
    if _BOT_MENU_SET or not BOT_TOKEN:
        return
    commands=[
        {"command":"menu","description":"Все опции бота"},
        {"command":"find","description":"Найти игрока или выбрать команду"},
        {"command":"card","description":"Обычная плашка"},
        {"command":"card2","description":"Двойная плашка"},
        {"command":"cards","description":"Плашка с правым окном"},
        {"command":"cardbad","description":"BAD-плашка"},
        {"command":"refresh","description":"Обновить базу игроков"},
        {"command":"stop","description":"Сбросить текущий сценарий"},
    ]
    res=_tg_post("setMyCommands", {"commands":commands})
    _BOT_MENU_SET=bool(res.get("ok"))

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

def _tg_get_file_bytes(file_id:str)->Tuple[Optional[bytes], Optional[str]]:
    try:
        info=_tg_post("getFile", {"file_id":file_id})
        if not info.get("ok"):
            return None, info.get("description") or info.get("error") or "getFile failed"
        file_path=(info.get("result") or {}).get("file_path")
        if not file_path:
            return None, "Telegram не вернул file_path"
        with http_urlopen(_tg_url("").replace("/bot", "/file/bot") + file_path, timeout=35) as r:
            return r.read(), None
    except Exception as e:
        return None, repr(e)

# ------------- utils -------------
_RU_TO_LAT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})

def _normalize(s:str)->str:
    s=(s or "").strip().lower().replace("ё","е").replace("ë","e")
    s=unicodedata.normalize("NFKD", s)
    s="".join(ch for ch in s if not unicodedata.combining(ch))
    keep="abcdefghijklmnopqrstuvwxyzабвгдеежзийклмнопрстуфхцчшщьыъэюя -'0123456789+/%"
    s="".join(ch for ch in s if ch in keep)
    return " ".join(s.split())

def _translit_ru_to_lat(s:str)->str:
    return (s or "").lower().replace("ё","е").translate(_RU_TO_LAT)

def _query_variants(s:str)->set[str]:
    return {v for v in {_normalize(s), _normalize(_translit_ru_to_lat(s))} if v}

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
    ps=_get_players(False)
    if find_player_by_name:
        try:
            hits=find_player_by_name(q) or []
            if hits: return hits
        except Exception: pass
    out=[]
    qvars=_query_variants(q)
    for p in ps:
        dn=p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}"
        aliases=p.get("aliases") if isinstance(p.get("aliases"), list) else []
        hay=" ".join([dn] + [str(a) for a in aliases])
        blob=" ".join(_query_variants(hay))
        if dn and qvars and any(qv in blob for qv in qvars):
            out.append(p)
            if len(out)>=10: break
    return out

def _player_id(p:Dict[str,Any])->str:
    return str(p.get("personId") or p.get("id") or "").strip()

def _player_name(p:Dict[str,Any])->str:
    if display_name_for:
        try:
            return display_name_for(p)
        except Exception:
            pass
    return p.get("displayName") or f"{p.get('firstName','')} {p.get('lastName','')}".strip()

def _find_player_by_id(pid:str)->Optional[Dict[str,Any]]:
    pid=str(pid or "").strip()
    for p in _get_players(False):
        if _player_id(p)==pid:
            return dict(p)
    return None

def _team_label(team_id:str)->str:
    row = TEAMS.get(str(team_id or "")) if isinstance(TEAMS, dict) else None
    if isinstance(row, dict):
        abbr = (row.get("abbr") or "").upper()
        return f"{row.get('name') or team_id}" + (f" ({abbr})" if abbr else "")
    return str(team_id or "0")

def _effective_team_id(p:Dict[str,Any])->str:
    return str(p.get("logoTeamId") or p.get("teamId") or "0")

def _with_team_override(p:Dict[str,Any], team_id:str)->Dict[str,Any]:
    out=dict(p or {})
    team_id=str(team_id or "0")
    out["teamId"]=team_id
    out["logoTeamId"]=team_id
    row=TEAMS.get(team_id) if isinstance(TEAMS, dict) else None
    if isinstance(row, dict):
        out["teamName"]=row.get("name")
    return out

def _players_for_team(team_id:str)->List[Dict[str,Any]]:
    team_id=str(team_id or "0")
    rows=[]
    seen=set()
    for p in _get_players(False):
        if str(p.get("teamId") or "0") != team_id:
            continue
        pid=_player_id(p)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        rows.append(dict(p))
    rows.sort(key=lambda p: _normalize(_player_name(p)))
    return rows

def _similar_players(q:str, limit:int=8)->List[Dict[str,Any]]:
    qvars=_query_variants(q)
    if not qvars:
        return []
    scored=[]
    for p in _get_players(False):
        name=_player_name(p)
        aliases=p.get("aliases") if isinstance(p.get("aliases"), list) else []
        variants=_query_variants(" ".join([name] + [str(a) for a in aliases]))
        score=0.0
        for qv in qvars:
            for nv in variants:
                if qv and qv in nv:
                    score=max(score, 1.0)
                elif qv and nv:
                    score=max(score, SequenceMatcher(None, qv, nv).ratio())
                    for part in nv.split():
                        score=max(score, SequenceMatcher(None, qv, part).ratio())
        if score >= 0.55:
            scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(p) for _,p in scored[:limit]]

def _team_keyboard(prefix:str, *, back_data:Optional[str]=None):
    rows=[]; row=[]
    for team_id, info in sorted((TEAMS or {}).items(), key=lambda kv: (kv[1].get("name","") if isinstance(kv[1],dict) else kv[0])):
        label=(info.get("abbr") or team_id).upper() if isinstance(info, dict) else team_id
        row.append({"text":label, "callback_data":f"{prefix}:{team_id}"})
        if len(row)==3:
            rows.append(row); row=[]
    if row:
        rows.append(row)
    if back_data:
        rows.append([{"text":"Назад", "callback_data":back_data}])
    return {"inline_keyboard":rows}

def _player_keyboard(players:List[Dict[str,Any]], prefix:str, *, max_items:int=30, back_data:Optional[str]=None):
    rows=[]
    for p in players[:max_items]:
        pid=_player_id(p)
        if not pid:
            continue
        rows.append([{"text":f"{_player_name(p)} · {_team_label(str(p.get('teamId') or '0'))}", "callback_data":f"{prefix}:{pid}"}])
    if back_data:
        rows.append([{"text":"Назад", "callback_data":back_data}])
    return {"inline_keyboard":rows} if rows else None

def _candidate_keyboard(players:List[Dict[str,Any]], prefix:str):
    rows=[]
    for p in players[:8]:
        pid=_player_id(p)
        if pid:
            rows.append([{"text":f"{_player_name(p)} · {_team_label(str(p.get('teamId') or '0'))}", "callback_data":f"{prefix}:{pid}"}])
    rows.append([{"text":"Выбрать команду", "callback_data":"pick:teams"}])
    rows.append([{"text":"Назад", "callback_data":"menu:main"}])
    return {"inline_keyboard":rows}

def _store_pending_pick(st:Dict[str,Any], mode:str, stats, *, info:str="", query:str=""):
    st["pending_pick"]={"mode":mode, "stats":stats or [], "info":info or "", "query":query or ""}

def _ask_player_not_found(chat_id:int, name_q:str, mode:str, stats, *, info:str=""):
    st=_ctx(chat_id)
    _store_pending_pick(st, mode, stats, info=info, query=name_q)
    similar=_similar_players(name_q)
    if similar:
        _status_update(chat_id, f"Не нашёл точное совпадение: {name_q}\nВыберите похожего игрока или команду.", keep_kb=_candidate_keyboard(similar, "pick:player"))
    else:
        _status_update(chat_id, f"Не нашёл игрока: {name_q}\nВыберите команду, потом игрока из состава.", keep_kb=_team_keyboard("pick:team", back_data="menu:main"))

def _continue_with_player(chat_id:int, p:Dict[str,Any]):
    st=_ctx(chat_id)
    pending=st.get("pending_pick") or {}
    mode=pending.get("mode") or st.get("mode") or "single"
    stats=pending.get("stats") or st.get("stats1") or []
    info=pending.get("info") or st.get("info") or ""
    keep_status=st.get("status_mid")
    last_cmd=st.get("last_cmd_msg_id")
    st.clear()
    st.update({"mode":mode, "p1":dict(p), "stats1":stats, "info":info, "status_mid":keep_status, "last_cmd_msg_id":last_cmd})
    pid=_player_id(p)
    ru=None
    try:
        ru = overrides_get_name_ru(pid) if overrides_get_name_ru else None
    except Exception:
        pass
    if not ru:
        _ask_ru_name(chat_id, pid, _player_name(p), reply_to=st.get("last_cmd_msg_id"))
        _status_update(chat_id,"Жду русское имя… Ответьте на сообщение выше.")
        return
    st["ru1"]=ru
    if mode=="bad":
        _render_bad(chat_id, p, ru, stats)
    elif mode=="special":
        _render_special(chat_id, p, ru, stats, info)
    else:
        _render_single(chat_id, p, ru, stats)

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

def _menu_keyboard():
    return {"inline_keyboard":[
        [{"text":"Обычная card","callback_data":"menu:card"},
         {"text":"Двойная card2","callback_data":"menu:card2"}],
        [{"text":"BAD cardbad","callback_data":"menu:cardbad"},
         {"text":"Особая cards","callback_data":"menu:cards"}],
        [{"text":"Поиск игрока","callback_data":"menu:find"},
         {"text":"Обновить базу","callback_data":"menu:refresh"}],
    ]}

def _menu_text()->str:
    return (
        "Меню бота:\n"
        "• /card <игрок> | <статы> — обычная плашка, длина зависит от количества стат.\n"
        "• /card2 <игрок1> | <статы1> || <игрок2> | <статы2> — двойная плашка на всю ширину.\n"
        "• /cardbad <игрок> | <статы> — коричневая BAD-плашка с иконкой.\n"
        "• /cards <игрок> | <статы> | <текст справа> — особая плашка с дополнительным окном.\n"
        "• /find <имя> — поиск игрока, похожие варианты, текущие и исторические игроки.\n"
        "• /refresh — обновить базу игроков.\n"
        "• /stop — сброс текущего сценария.\n\n"
        "После генерации можно исправить имя, цвет, команду/логотип или заменить фото игрока."
    )

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
        team_id=_effective_team_id(p)
        if team_id in seen: continue
        seen.add(team_id)
        label=_team_label(team_id)
        rows.append([{"text":f"Команда {slot}: {label}", "callback_data":f"fix:color:team:{team_id}"}])
    return {"inline_keyboard":rows} if rows else None

def _fix_team_player_keyboard(st:Dict[str,Any]):
    rows=[]
    for slot,p in _ctx_players(st):
        rows.append([{"text":f"Игрок {slot}: {_player_name(p)}", "callback_data":f"fix:teamplayer:{slot}"}])
    rows.append([{"text":"Назад", "callback_data":"fix:menu"}])
    return {"inline_keyboard":rows} if rows else None

def _set_ctx_player_team(chat_id:int, slot:str, team_id:str):
    st=_ctx(chat_id)
    key="p2" if slot=="2" else "p1"
    if not st.get(key):
        _status_update(chat_id,"Не вижу игрока для исправления. Сделайте новую плашку.")
        return
    st[key]=_with_team_override(st[key], team_id)
    _status_update(chat_id,f"Поставил логотип/команду: {_team_label(team_id)}. Пересобираю плашку…")
    _render_ctx(chat_id)

def _fix_photo_player_keyboard(st:Dict[str,Any]):
    rows=[]
    for slot,p in _ctx_players(st):
        rows.append([{"text":f"Игрок {slot}: {_player_name(p)}", "callback_data":f"fix:photoplayer:{slot}"}])
    rows.append([{"text":"Назад", "callback_data":"fix:menu"}])
    return {"inline_keyboard":rows} if rows else None

def _ask_custom_photo(chat_id:int, slot:str):
    st=_ctx(chat_id)
    key="p2" if slot=="2" else "p1"
    p=st.get(key)
    if not p:
        _status_update(chat_id,"Не вижу игрока для замены фото. Сделайте новую плашку.")
        return
    st["waiting_photo_slot"]=slot
    _status_update(
        chat_id,
        f"Пришлите новое фото для {_player_name(p)} как изображение или документ PNG/JPG/JPEG/WEBP. Я поставлю его вместо основного и пересоберу плашку.",
        keep_kb={"inline_keyboard":[[{"text":"Назад","callback_data":"fix:menu"}]]}
    )

def _save_uploaded_photo(chat_id:int, msg:Dict[str,Any])->bool:
    st=_ctx(chat_id)
    slot=str(st.get("waiting_photo_slot") or "1")
    key="p2" if slot=="2" else "p1"
    p=st.get(key)
    if not p:
        st.pop("waiting_photo_slot", None)
        _status_update(chat_id,"Не вижу игрока для замены фото. Сделайте новую плашку.")
        return True

    file_id=None; filename="headshot"
    photos=msg.get("photo") or []
    if photos:
        file_id=photos[-1].get("file_id")
        filename="photo.jpg"
    doc=msg.get("document") or {}
    if doc:
        mime=(doc.get("mime_type") or "").lower()
        name=(doc.get("file_name") or "headshot").lower()
        if mime.startswith("image/") or name.endswith((".png",".jpg",".jpeg",".webp")):
            file_id=doc.get("file_id")
            filename=doc.get("file_name") or filename
    if not file_id:
        _status_update(chat_id,"Жду фото PNG/JPG/JPEG/WEBP как изображение или документ.")
        return True

    _status_update(chat_id,"Загружаю новое фото…")
    raw, err=_tg_get_file_bytes(file_id)
    if not raw:
        _fail(chat_id, f"Не удалось скачать фото: {err}")
        return True
    if not save_custom_headshot:
        _fail(chat_id, "Сохранение фото сейчас недоступно.")
        return True
    pid=_player_id(p)
    ok=save_custom_headshot(pid, raw, filename)
    if not ok:
        _fail(chat_id,"Не смог обработать фото. Попробуйте PNG/JPG/JPEG/WEBP без сжатия.")
        return True
    st.pop("waiting_photo_slot", None)
    _status_update(chat_id,"Фото заменено. Пересобираю плашку…")
    _render_ctx(chat_id)
    return True

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
        team_id=_effective_team_id(p)
        logo=_ensure_team_logo_image(team_id)
        colors=_team_colors(team_id)
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
        logo=_ensure_team_logo_image(_effective_team_id(p))
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
        t1=_effective_team_id(p1); t2=_effective_team_id(p2)
        l1=_ensure_team_logo_image(t1)
        l2=_ensure_team_logo_image(t2)
        c1=_team_colors(t1); c2=_team_colors(t2)
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
        team_id=_effective_team_id(p)
        logo=_ensure_team_logo_image(team_id)
        colors=_team_colors(team_id)
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
            if data=="menu:main":
                _status_update(chat_id, _menu_text(), keep_kb=_menu_keyboard()); return PlainTextResponse("OK")
            if data.startswith("menu:"):
                topic=data.split(":",1)[1]
                hints={
                    "card":"Обычная card:\n/card lebron | 30 очков, 11 подборов, 10 передач",
                    "card2":"Двойная card2:\n/card2 lebron | 30 очков || doncic | 28 очков",
                    "cardbad":"BAD cardbad:\n/cardbad reaves | 5 очков, 5-18 с игры, 3 потери",
                    "cards":"Особая cards:\n/cards doncic | 30 очков, 10 передач | лидер лиги по трехочковым",
                    "find":"Поиск:\n/find shai\nЕсли точного совпадения нет, появятся похожие игроки и выбор команды.",
                    "refresh":"Обновляю базу игроков…",
                }
                if topic=="refresh":
                    try:
                        cnt, src = refresh_players() if refresh_players else (0, "n/a")
                        _status_update(chat_id, f"База обновлена: {cnt} игроков ({src}).", keep_kb=_menu_keyboard())
                    except Exception as e:
                        _fail(chat_id, f"Не удалось обновить базу: {repr(e)}")
                else:
                    _status_update(chat_id, hints.get(topic, _menu_text()), keep_kb={"inline_keyboard":[[{"text":"Назад","callback_data":"menu:main"}]]})
                return PlainTextResponse("OK")
            if data=="fix:ok":
                _ctx_clear(chat_id); _tg_send_message(chat_id,"Готово ✅"); return PlainTextResponse("OK")
            if data=="fix:menu":
                _status_update(chat_id,"Что исправить?", keep_kb={"inline_keyboard":[
                    [{"text":"Имена игроков","callback_data":"fix:names"},
                     {"text":"Цвет плашки","callback_data":"fix:color"}],
                    [{"text":"Команда / логотип","callback_data":"fix:teams"},
                     {"text":"Фото игрока","callback_data":"fix:photo"}],
                    [{"text":"Назад","callback_data":"menu:main"}]
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
                players=_ctx_players(st)
                if not players:
                    _status_update(chat_id,"Не вижу последнюю плашку для исправления. Сделайте новую командой /card."); return PlainTextResponse("OK")
                if len(players)==1:
                    _status_update(chat_id,"Выберите логотип/команду для игрока.", keep_kb=_team_keyboard("fix:teamset:1", back_data="fix:menu"))
                else:
                    _status_update(chat_id,"Для какого игрока поменять логотип/команду?", keep_kb=_fix_team_player_keyboard(st))
                return PlainTextResponse("OK")
            if data.startswith("fix:teamplayer:"):
                slot=data.rsplit(":",1)[-1]
                _status_update(chat_id,"Выберите логотип/команду.", keep_kb=_team_keyboard(f"fix:teamset:{slot}", back_data="fix:teams"))
                return PlainTextResponse("OK")
            if data.startswith("fix:teamset:"):
                parts=data.split(":")
                if len(parts)>=4:
                    _set_ctx_player_team(chat_id, parts[2], parts[3])
                return PlainTextResponse("OK")
            if data=="fix:photo":
                players=_ctx_players(st)
                if not players:
                    _status_update(chat_id,"Не вижу последнюю плашку для замены фото. Сделайте новую командой /card."); return PlainTextResponse("OK")
                if len(players)==1:
                    _ask_custom_photo(chat_id, "1")
                else:
                    _status_update(chat_id,"Для какого игрока заменить фото?", keep_kb=_fix_photo_player_keyboard(st))
                return PlainTextResponse("OK")
            if data.startswith("fix:photoplayer:"):
                _ask_custom_photo(chat_id, data.rsplit(":",1)[-1])
                return PlainTextResponse("OK")
            if data=="pick:back":
                pending=st.get("pending_pick") or {}
                q=pending.get("query") or ""
                if q:
                    _ask_player_not_found(chat_id, q, pending.get("mode") or "single", pending.get("stats") or [], info=pending.get("info") or "")
                else:
                    _status_update(chat_id, _menu_text(), keep_kb=_menu_keyboard())
                return PlainTextResponse("OK")
            if data=="pick:teams":
                _status_update(chat_id,"Выберите команду, потом игрока из состава.", keep_kb=_team_keyboard("pick:team", back_data="pick:back"))
                return PlainTextResponse("OK")
            if data.startswith("pick:team:"):
                team_id=data.rsplit(":",1)[-1]
                players=_players_for_team(team_id)
                kb=_player_keyboard(players, "pick:player", back_data="pick:teams")
                if players:
                    _status_update(chat_id,f"{_team_label(team_id)}: выберите игрока.", keep_kb=kb)
                else:
                    _status_update(chat_id,f"Не нашёл игроков команды {_team_label(team_id)} в текущей базе.", keep_kb=kb)
                return PlainTextResponse("OK")
            if data.startswith("pick:player:"):
                pid=data.rsplit(":",1)[-1]
                p=_find_player_by_id(pid)
                if not p:
                    _status_update(chat_id,"Не смог открыть выбранного игрока. Попробуйте /refresh или /find.")
                    return PlainTextResponse("OK")
                _continue_with_player(chat_id, p)
                return PlainTextResponse("OK")
            if data.startswith("find:team:"):
                team_id=data.rsplit(":",1)[-1]
                players=_players_for_team(team_id)
                if players:
                    lines=[f"{_player_name(p)} (id={_player_id(p)}, teamId={p.get('teamId')})" for p in players[:40]]
                    _status_update(chat_id, f"{_team_label(team_id)}:\n" + "\n".join(lines), keep_kb={"inline_keyboard":[[{"text":"Назад","callback_data":"menu:main"}]]})
                else:
                    _status_update(chat_id,f"Не нашёл игроков команды {_team_label(team_id)}.", keep_kb={"inline_keyboard":[[{"text":"Назад","callback_data":"menu:main"}]]})
                return PlainTextResponse("OK")
            return PlainTextResponse("OK")
        except Exception as e:
            _fail(cb["from"]["id"], f"Ошибка: {repr(e)}"); return PlainTextResponse("OK")

    msg=update.get("message") or update.get("edited_message")
    if not msg: return PlainTextResponse("OK")
    chat_id=msg["chat"]["id"]; text=(msg.get("text") or "").strip()
    st=_ctx(chat_id); st.setdefault("last_cmd_msg_id", msg.get("message_id"))
    _ensure_bot_commands()

    if st.get("waiting_photo_slot") and (msg.get("photo") or msg.get("document")):
        _save_uploaded_photo(chat_id, msg)
        return PlainTextResponse("OK")

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
    if low.startswith("/") and not any(low.startswith(x) for x in ("/start", "/menu", "/help", "/stop")):
        _status_update(chat_id, "Думаю…")

    if low.startswith("/start") or low.startswith("/menu"):
        _status_update(chat_id, _menu_text(), keep_kb=_menu_keyboard()); return PlainTextResponse("OK")

    if low.startswith("/help"):
        _status_update(chat_id, _menu_text(), keep_kb=_menu_keyboard()); return PlainTextResponse("OK")

    if low.startswith("/refresh"):
        try:
            cnt, src = refresh_players() if refresh_players else (0, "n/a")
            _status_update(chat_id, f"База обновлена: {cnt} игроков ({src}).", keep_kb=_menu_keyboard())
        except Exception as e:
            _fail(chat_id, f"Не удалось обновить базу: {repr(e)}")
        return PlainTextResponse("OK")

    if low.startswith("/find"):
        q = text[text.find(" "):].strip() if " " in text else ""
        hits=search_players_loose(q)
        if not hits:
            similar=_similar_players(q)
            if similar:
                lines=[f"{_player_name(h)} (id={_player_id(h)}, teamId={h.get('teamId')})" for h in similar[:8]]
                _status_update(chat_id, "Точного совпадения нет. Похожие:\n" + "\n".join(lines) + "\n\nМожно выбрать команду:", keep_kb=_team_keyboard("find:team", back_data="menu:main"))
            else:
                _status_update(chat_id,"Ничего не нашёл. Можно выбрать команду:", keep_kb=_team_keyboard("find:team", back_data="menu:main"))
            return PlainTextResponse("OK")
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
            if not hits:
                _ask_player_not_found(chat_id, name_q, "single", stats)
                return PlainTextResponse("OK")
            p=hits[0]; pid=str(p.get("personId") or "")
            keep_status=st.get("status_mid")
            st.clear(); st.update({"mode":"single","p1":p,"stats1":stats,"last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": keep_status})
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
                missing = n1 if not h1 else n2
                similar=_similar_players(missing)
                if similar:
                    lines=[f"{_player_name(h)} (id={_player_id(h)}, teamId={h.get('teamId')})" for h in similar[:8]]
                    _status_update(chat_id, f"Не нашёл игрока: {missing}\nПохожие:\n" + "\n".join(lines) + "\n\nМожно выбрать команду через /find.")
                else:
                    _status_update(chat_id,f"Не нашёл игрока: {missing}. Можно посмотреть состав через /find и выбор команды.")
                return PlainTextResponse("OK")
            p1, p2 = h1[0], h2[0]
            keep_status=st.get("status_mid")
            st.clear(); st.update({"mode":"duo","p1":p1,"stats1":st1,"p2":p2,"stats2":st2,
                                   "last_cmd_msg_id":msg.get("message_id"),"status_mid": keep_status})
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
            if not hits:
                _ask_player_not_found(chat_id, name_q, "special", stats, info=info_text)
                return PlainTextResponse("OK")
            p=hits[0]; pid=str(p.get("personId") or "")
            keep_status=st.get("status_mid")
            st.clear(); st.update({"mode":"special","p1":p,"stats1":stats,"info":info_text,
                                   "last_cmd_msg_id":msg.get("message_id"),"status_mid": keep_status})
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
            if not hits:
                _ask_player_not_found(chat_id, name_q, "bad", stats)
                return PlainTextResponse("OK")
            p=hits[0]; pid=str(p.get("personId") or "")
            keep_status=st.get("status_mid")
            st.clear(); st.update({"mode":"bad","p1":p,"stats1":stats,"last_cmd_msg_id":msg.get("message_id"),
                                   "status_mid": keep_status})
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
    _status_update(chat_id, _menu_text(), keep_kb=_menu_keyboard())
    return PlainTextResponse("OK")
