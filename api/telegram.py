# /api/telegram.py — FastAPI webhook для Telegram (Vercel)
# Поддерживает:
#  GET  /api/telegram
#  GET  /api/telegram/healthz
#  GET  /api/telegram?action=refresh&secret=...&drop_cache=1&debug=1
#  GET  /api/debug/players_snapshot
#  GET  /api/telegram/selftest
#  POST /api/telegram?secret=...
#  POST /api/telegram/webhook/<secret>
#
# Команды в чате:
#  /card Имя | 25 очков, 12 подборов, 3 блокшота | impact | подпись(опц.)
#  /alias Неправильно = Правильное Имя

import os
import re
import json
import requests
import traceback
import importlib
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "hook")

# число + (возможный %) + ярлык
STAT_PAIR_RE = re.compile(
    r"(?P<num>[-+]?\d+(?:[.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})"
)

# ---------- утилита для компактного JSON-трейсбека ----------
def _json_exc(e: Exception):
    tb = traceback.format_exc().splitlines()[-12:]
    return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb}

# ---------- парсинг команд ----------

def parse_card(text: str):
    """
    /card Имя | 25 очков, 12 подборов, 3 блокшота | impact | подпись(опц.)
    -> (name, stats[:6], template, note, raw_stats_text)
    """
    if not text or not text.lower().startswith("/card"):
        return None
    body = text.split(" ", 1)[1] if " " in text else ""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2:
        return None
    name = parts[0]
    raw_stats = parts[1]
    template = (parts[2].strip().lower() if len(parts) >= 3 and parts[2] else "single")
    note = (parts[3].strip() if len(parts) >= 4 and parts[3] else None)

    stats = []
    for token in re.split(r"[,;/\n]", raw_stats):
        m = STAT_PAIR_RE.search(token.strip())
        if m:
            num = m.group("num").replace(",", ".")
            label = (m.group("label") or "").strip()
            # если написали "45%" без ярлыка — оставим как "%":
            if m.group(2) == "%" and not label:
                label = "%"
            stats.append((num, label))
    return name, stats[:6], template, note, raw_stats


def parse_alias(text: str):
    """
    /alias Неправильно = Правильное Имя
    -> (alias_text, correct_text)
    """
    if not text or not text.lower().startswith("/alias"):
        return None
    body = text.split(" ", 1)[1] if " " in text else ""
    m = re.split(r"\s*=\s*", body, maxsplit=1)
    if len(m) != 2:
        return None
    return m[0].strip(), m[1].strip()

# ---------- Telegram helpers ----------

def tg_send_png(chat_id: int, png_bytes: bytes, caption: Optional[str] = None):
    """Отправляем как документ — сохранит прозрачность PNG и не пережмёт JPG."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    files = {"document": ("card.png", png_bytes, "image/png")}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    r = requests.post(url, data=data, files=files, timeout=30)
    return r.ok, r.text


def tg_send_message(
    chat_id: int,
    text: str,
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[dict] = None,
):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=15,
    )


def tg_answer_callback(callback_id: str, text: str = ""):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
        json={"callback_query_id": callback_id, "text": text},
        timeout=10,
    )

# ---------- основной обработчик ----------

async def handle_update(update: dict) -> JSONResponse:
    # ленивые импорты
    from graphics import render_card
    from data import (
        find_player_by_name, ensure_headshot_png, ensure_team_logo_png,
        get_player_by_id, suggest_players, add_alias
    )

    # 1) callback-кнопки (выбор игрока из подсказок)
    if "callback_query" in update:
        cq = update["callback_query"]
        data_cb = cq.get("data") or ""
        chat_id = cq["message"]["chat"]["id"]

        if data_cb.startswith("pick:"):
            try:
                pid = int(data_cb.split(":", 1)[1])
            except Exception:
                tg_answer_callback(cq["id"], "Ошибка выбора")
                return JSONResponse({"ok": True})

            origin = cq["message"].get("reply_to_message")
            if not origin or not origin.get("text"):
                tg_answer_callback(cq["id"], "Не нашёл исходную команду — отправьте /card заново.")
                return JSONResponse({"ok": True})

            parsed = parse_card(origin["text"])
            if not parsed:
                tg_answer_callback(cq["id"], "Не смог разобрать исходную команду.")
                return JSONResponse({"ok": True})

            _name, stats, template, note, _raw = parsed
            player = get_player_by_id(pid)
            if not player:
                tg_answer_callback(cq["id"], "Игрок не найден по ID.")
                return JSONResponse({"ok": True})

            logo_path, team_colors = ensure_team_logo_png(player["team_id"])
            head_path = ensure_headshot_png(player["id"], player["full_name"])

            png_bytes = render_card(
                template=template,
                player_name=player["display"] or player["full_name"],
                team_name=player["team_name"],
                team_logo_path=logo_path,
                team_colors=team_colors,
                headshot_path=head_path,
                stats=stats,
                note=note,
            )
            tg_answer_callback(cq["id"], f"Выбрано: {player['display']}")
            tg_send_png(chat_id, png_bytes)
            return JSONResponse({"ok": True})

        # неизвестный callback
        tg_answer_callback(cq["id"])
        return JSONResponse({"ok": True})

    # 2) обычные сообщения
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return JSONResponse({"ok": True})

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    # 2a) /alias
    alias_pair = parse_alias(text)
    if alias_pair:
        alias_text, correct_text = alias_pair
        target = find_player_by_name(correct_text)
        if not target:
            tg_send_message(chat_id, "Не нашёл игрока справа от '='. Пример:\n/alias Швед = Alexey Shved")
            return JSONResponse({"ok": True})
        ok = add_alias(alias_text, target["full_name"])
        if ok:
            tg_send_message(chat_id, f"Готово. Теперь «{alias_text}» = {target['display']}.")
        else:
            tg_send_message(chat_id, "Не удалось сохранить алиас.")
        return JSONResponse({"ok": True})

    # 2b) /card
    parsed = parse_card(text)
    if not parsed:
        hint = ("Формат:\n"
                "/card Имя Игрока | 25 очков, 12 подборов, 3 блокшота | impact | подпись\n"
                "Шаблоны: single, pair, single_note, impact, bad\n"
                "Если не находит — бот предложит варианты или используйте /alias Неправильно = Правильное Имя")
        tg_send_message(chat_id, hint)
        return JSONResponse({"ok": True})

    name, stats, template, note, _raw = parsed
    player = find_player_by_name(name)
    if not player:
        # подсказки
        suggestions = suggest_players(name, limit=5)
        if not suggestions:
            tg_send_message(chat_id, "Игрок не найден. Уточните имя.\n"
                                     "Можно задать алиас: /alias Вася = Vasilije Micic")
            return JSONResponse({"ok": True})
        kb = {"inline_keyboard": [[{"text": s["display"], "callback_data": f"pick:{s['id']}"}] for s in suggestions]}
        tg_send_message(
            chat_id,
            "Игрок не найден. Возможно, вы имели в виду:",
            reply_to_message_id=msg.get("message_id"),
            reply_markup=kb
        )
        return JSONResponse({"ok": True})

    # нашли игрока — рендерим
    logo_path, team_colors = ensure_team_logo_png(player["team_id"])
    head_path = ensure_headshot_png(player["id"], player["full_name"])

    png_bytes = render_card(
        template=template,
        player_name=player["display"] or player["full_name"],
        team_name=player["team_name"],
        team_logo_path=logo_path,
        team_colors=team_colors,
        headshot_path=head_path,
        stats=stats,
        note=note,
    )
    ok, resp = tg_send_png(chat_id, png_bytes)
    if not ok:
        tg_send_message(chat_id, f"Ошибка отправки изображения: {resp}")
    return JSONResponse({"ok": True})

# ---------- health / debug ----------

@app.get("/")
@app.get("/api/telegram")
@app.get("/api/telegram/healthz")
def health(
    action: Optional[str] = Query(default=None),
    secret: str = Query(default=""),
    drop_cache: int = Query(default=0),
    debug: int = Query(default=0),
):
    """
    GET /api/telegram
    - без параметров: здоровье + players_indexed
    - ?action=refresh&secret=...&drop_cache=1 : удаляет /tmp кэш и перечитывает локальный assets/players.json
    - ?debug=1 : возвращает подробности ошибок в JSON
    """
    # пробуем посчитать игроков
    try:
        from data import players_count
        count_before = players_count()
    except Exception as e:
        return _json_exc(e) if debug else {"ok": False, "error": "players_count failed"}

    if action == "refresh":
        if secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=404, detail="Not found")
        try:
            import data, os
            if drop_cache:
                try:
                    os.remove(str(data.PLAYERS_CACHE))
                except Exception:
                    pass
            importlib.reload(data)
            cnt = data.players_count()
            return {"ok": True, "refreshed": True, "players_indexed": cnt}
        except Exception as e:
            return _json_exc(e) if debug else {"ok": False, "refreshed": False, "error": "refresh failed"}

    return {
        "ok": True,
        "players_indexed": count_before,
        "endpoints": [
            "GET  /api/telegram (action=refresh&secret=...&drop_cache=1&debug=1)",
            "GET  /api/debug/players_snapshot",
            "GET  /api/telegram/selftest",
            "POST /api/telegram?secret=...  (Telegram webhook)",
            "POST /api/telegram/webhook/<secret>",
        ],
    }

@app.get("/api/debug/players_snapshot")
def debug_players_snapshot():
    try:
        from pathlib import Path
        p = (Path(__file__).resolve().parent.parent / "assets" / "players.json")
        if not p.exists():
            return {"exists": False}
        return {"exists": True, "size": p.stat().st_size}
    except Exception as e:
        return _json_exc(e)

@app.get("/api/telegram/selftest")
def selftest():
    try:
        import data
        from pathlib import Path

        res = {}
        # наличие снапшота в билде
        p = (Path(__file__).resolve().parent.parent / "assets" / "players.json")
        res["assets_players_json_exists"] = p.exists()
        res["assets_players_json_size"]   = p.stat().st_size if p.exists() else 0

        # сколько игроков видит сейчас
        try:
            res["players_before"] = data.players_count()
        except Exception as e:
            res["players_before_error"] = repr(e)

        # принудительная перезагрузка модуля data
        importlib.reload(data)
        res["players_after"] = data.players_count()

        return {"ok": True, **res}
    except Exception as e:
        return _json_exc(e)

# ---------- webhook ----------

@app.post("/")
@app.post("/api/telegram")
async def webhook_query(request: Request, secret: str = Query(default="")):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    return await handle_update(update)

@app.post("/webhook/{secret}")
@app.post("/api/telegram/webhook/{secret}")
async def webhook_path(request: Request, secret: str):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    return await handle_update(update)
