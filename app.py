
import os
import io
import re
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

from data import find_player_by_name, ensure_headshot_png, ensure_team_logo_png
from graphics import render_card

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

ASK_PLAYER, ASK_STATS, ASK_TEMPLATE, ASK_NOTE = range(4)

TEMPLATES = {
    "single": "Одиночный",
    "pair": "Парный",
    "single_note": "Одиночный с уточнением",
    "impact": "Делает разницу",
    "bad": "Плохо сыграл",
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли имя игрока NBA — например:\n\n"
        "`Виктор Вембаньяма`\n\n"
        "Дальше я попрошу статистику и предложу шаблон.",
        parse_mode="Markdown",
    )
    return ASK_PLAYER

async def ask_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    player = find_player_by_name(name)
    if not player:
        await update.message.reply_text("Не нашёл такого игрока. Попробуй точнее (имя фамилия).")
        return ASK_PLAYER

    context.user_data["player"] = player
    await update.message.reply_text(
        "Окей! Теперь пришли статистику одной строкой. Примеры:\n"
        "`25 очков, 12 подборов, 10 блокшотов`\n"
        "`34 оч, 7 подб, 50% с игры, 38.5% из-за дуги`\n"
        "Можно писать любые метрики — я вытащу числа и подписи.",
        parse_mode="Markdown",
    )
    return ASK_STATS

STAT_PAIR_RE = re.compile(r"(?P<num>[-+]?\d+(?:[\.,]\d+)?)\s*([%]?)\s*(?P<label>[A-Za-zА-Яа-яёЁ+\-/ ]{0,20})")

async def ask_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text
    stats = []
    for token in re.split(r"[,;/\n]", raw):
        m = STAT_PAIR_RE.search(token.strip())
        if not m:
            continue
        num = m.group("num").replace(",", ".")
        label = m.group("label").strip() or ""
        if m.group(2) == "%" and not label:
            label = "%"
        stats.append((num, label))
    if not stats:
        await update.message.reply_text("Не понял статистику. Пришли в виде: `25 очков, 12 подборов, 3 блокшота`", parse_mode="Markdown")
        return ASK_STATS

    context.user_data["stats"] = stats[:6]
    buttons = [[InlineKeyboardButton(title, callback_data=key)] for key, title in TEMPLATES.items()]
    await update.message.reply_text("Выбери шаблон:", reply_markup=InlineKeyboardMarkup(buttons))
    return ASK_TEMPLATE

async def ask_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_key = query.data
    context.user_data["template"] = template_key
    if template_key == "single_note":
        await query.edit_message_text("Добавь уточнение (коротко): например, `Лучший дебют 76-х с 1959`.")
        return ASK_NOTE
    return await _finalize_and_send(query, context)

async def ask_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["note"] = update.message.text.strip()
    return await _finalize_and_send(update, context)

async def _finalize_and_send(carrier, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    player = user_data["player"]
    stats = user_data["stats"]
    template_key = user_data["template"]
    note = user_data.get("note")

    head_path = ensure_headshot_png(player["id"], player["full_name"])
    logo_path, team_colors = ensure_team_logo_png(player["team_id"])

    png_bytes = render_card(
        template=template_key,
        player_name=player["display"] or player["full_name"],
        team_name=player["team_name"],
        team_logo_path=logo_path,
        team_colors=team_colors,
        headshot_path=head_path,
        stats=stats,
        note=note,
    )

    bio = io.BytesIO(png_bytes); bio.name = "card.png"
    if hasattr(carrier, "message") and carrier.message:
        await carrier.message.reply_photo(photo=InputFile(bio))
    else:
        await carrier.edit_message_text("Готово. Высылаю файл…")
        await carrier.message.reply_photo(photo=InputFile(bio))

    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено. Напиши /start, чтобы начать заново.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("new", start), CommandHandler("card", start)],
        states={
            ASK_PLAYER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_player)],
            ASK_STATS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_stats)],
            ASK_TEMPLATE: [CallbackQueryHandler(ask_template)],
            ASK_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
