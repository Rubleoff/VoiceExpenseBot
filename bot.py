import asyncio
import logging
import os
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    add_expense,
    delete_expense_by_id,
    delete_last_expense,
    get_last_expenses,
    get_summary,
    init_db,
)
from parser import ExpenseParseError, parse_expense
from speech import (
    SpeechRecognitionError,
    is_voice_recognition_available,
    recognize_ogg,
)


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "expenses.db")
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-ru-0.22")


VOICE_CONFIRM_YES = "voice_confirm_yes"
VOICE_CONFIRM_REWRITE = "voice_confirm_rewrite"
VOICE_CONFIRM_MANUAL = "voice_confirm_manual"


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Проверь файл .env")


def format_money(amount: float) -> str:
    """
    Красиво форматирует сумму.
    """
    if amount == int(amount):
        return f"{int(amount)} ₽"

    return f"{amount:.2f} ₽"


def get_voice_confirmation_keyboard() -> InlineKeyboardMarkup:
    """
    Создаёт inline-кнопки для подтверждения распознанного голосового сообщения.
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Да", callback_data=VOICE_CONFIRM_YES),
        ],
        [
            InlineKeyboardButton("🔄 Перезаписать", callback_data=VOICE_CONFIRM_REWRITE),
            InlineKeyboardButton("✍️ Ввести вручную", callback_data=VOICE_CONFIRM_MANUAL),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = """
Привет! Я бот для учёта расходов.

Основной формат:

250 еда кофе
1200 транспорт такси
500 продукты

То есть:

сумма категория комментарий

Комментарий можно не указывать.

Я также умею принимать голосовые сообщения.
Например, можно голосом сказать:

двести пятьдесят еда кофе

После голосового сообщения я сначала покажу расшифровку и попрошу подтвердить её.

Команды:

/add 250 еда кофе — добавить расход
/today — расходы за сегодня
/week — расходы за последние 7 дней
/month — расходы за текущий месяц
/history — последние 10 расходов
/delete_last — удалить последний расход
/delete 3 — удалить расход по ID
/voice_status — проверить работу голосового ввода
/help — помощь
""".strip()

    if update.message:
        await update.message.reply_text(text)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await start_command(update, context)


async def add_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    raw_text = " ".join(context.args)

    if not raw_text:
        await update.message.reply_text(
            "Напиши расход после команды.\n\n"
            "Пример:\n"
            "/add 250 еда кофе"
        )
        return

    try:
        amount, category, comment = parse_expense(raw_text)
    except ExpenseParseError as error:
        await update.message.reply_text(str(error))
        return

    add_expense(
        db_path=DB_PATH,
        user_id=user_id,
        amount=amount,
        category=category,
        comment=comment,
    )

    response = f"Расход добавлен:\n{format_money(amount)} — {category}"

    if comment:
        response += f"\nКомментарий: {comment}"

    await update.message.reply_text(response)


async def add_expense_from_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Добавляет расход из обычного текстового сообщения.

    Также используется после кнопки:
    ✍️ Ввести вручную
    """
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    raw_text = update.message.text or ""

    is_manual_voice_input = context.user_data.get("manual_voice_input", False)

    try:
        amount, category, comment = parse_expense(raw_text)
    except ExpenseParseError:
        if is_manual_voice_input:
            await update.message.reply_text(
                "Не получилось записать расход вручную.\n\n"
                "Напиши в формате:\n"
                "250 еда кофе\n\n"
                "Или отправь новое голосовое сообщение."
            )
        else:
            await update.message.reply_text(
                "Я не понял сообщение.\n\n"
                "Чтобы добавить расход, напиши так:\n"
                "250 еда кофе\n\n"
                "Или используй команду:\n"
                "/add 250 еда кофе"
            )

        return

    add_expense(
        db_path=DB_PATH,
        user_id=user_id,
        amount=amount,
        category=category,
        comment=comment,
    )

    context.user_data.pop("manual_voice_input", None)
    context.user_data.pop("pending_voice_expense", None)

    if is_manual_voice_input:
        response = f"Расход добавлен вручную:\n{format_money(amount)} — {category}"
    else:
        response = f"Расход добавлен:\n{format_money(amount)} — {category}"

    if comment:
        response += f"\nКомментарий: {comment}"

    await update.message.reply_text(response)


async def send_period_summary(
    update: Update,
    start_date: date,
    end_date: date,
    title: str,
) -> None:
    """
    Отправляет пользователю статистику за период.
    """
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id

    summary = get_summary(
        db_path=DB_PATH,
        user_id=user_id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )

    total = summary["total"]
    count = summary["count"]
    categories = summary["categories"]

    if count == 0:
        await update.message.reply_text(f"{title}\n\nРасходов пока нет.")
        return

    lines = [
        title,
        "",
        f"Всего записей: {count}",
        f"Общая сумма: {format_money(total)}",
        "",
        "По категориям:",
    ]

    for category, category_total in categories:
        lines.append(f"— {category}: {format_money(category_total)}")

    await update.message.reply_text("\n".join(lines))


async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    today = date.today()

    await send_period_summary(
        update=update,
        start_date=today,
        end_date=today,
        title="Расходы за сегодня",
    )


async def week_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    today = date.today()
    start_date = today - timedelta(days=6)

    await send_period_summary(
        update=update,
        start_date=start_date,
        end_date=today,
        title="Расходы за последние 7 дней",
    )


async def month_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    today = date.today()
    start_date = date(today.year, today.month, 1)

    last_day = monthrange(today.year, today.month)[1]
    end_date = date(today.year, today.month, last_day)

    await send_period_summary(
        update=update,
        start_date=start_date,
        end_date=end_date,
        title="Расходы за текущий месяц",
    )


async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id

    expenses = get_last_expenses(
        db_path=DB_PATH,
        user_id=user_id,
        limit=10,
    )

    if not expenses:
        await update.message.reply_text("Расходов пока нет.")
        return

    lines = ["Последние расходы:", ""]

    for expense in expenses:
        expense_id, amount, category, comment, created_at = expense

        line = f"#{expense_id}: {format_money(amount)} — {category}"

        if comment:
            line += f" — {comment}"

        line += f"\nДата: {created_at}"

        lines.append(line)

    await update.message.reply_text("\n\n".join(lines))


async def delete_last_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id

    deleted_expense = delete_last_expense(
        db_path=DB_PATH,
        user_id=user_id,
    )

    if deleted_expense is None:
        await update.message.reply_text("Удалять нечего. Расходов пока нет.")
        return

    expense_id, amount, category, comment = deleted_expense

    response = (
        "Последний расход удалён:\n"
        f"#{expense_id}: {format_money(amount)} — {category}"
    )

    if comment:
        response += f"\nКомментарий: {comment}"

    await update.message.reply_text(response)


async def delete_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return

    if not context.args:
        await update.message.reply_text(
            "Укажи ID расхода.\n\n"
            "Пример:\n"
            "/delete 3"
        )
        return

    try:
        expense_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Например: /delete 3")
        return

    deleted_expense = delete_expense_by_id(
        db_path=DB_PATH,
        user_id=update.effective_user.id,
        expense_id=expense_id,
    )

    if deleted_expense is None:
        await update.message.reply_text(
            "Расход с таким ID не найден."
        )
        return

    deleted_id, amount, category, comment = deleted_expense

    response = (
        "Расход удалён:\n"
        f"#{deleted_id}: {format_money(amount)} — {category}"
    )

    if comment:
        response += f"\nКомментарий: {comment}"

    await update.message.reply_text(response)


async def voice_status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message:
        return

    is_available, reason = is_voice_recognition_available(VOSK_MODEL_PATH)

    if is_available:
        await update.message.reply_text(
            "Голосовой ввод доступен.\n\n"
            f"Модель: {VOSK_MODEL_PATH}"
        )
    else:
        await update.message.reply_text(
            "Голосовой ввод сейчас недоступен.\n\n"
            f"Причина: {reason}"
        )


async def voice_message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Обрабатывает голосовое сообщение:
    1. скачивает .ogg из Telegram;
    2. конвертирует .ogg в .wav через ffmpeg;
    3. распознаёт речь через Vosk;
    4. парсит текст как расход;
    5. показывает пользователю подтверждение;
    6. записывает в БД только после нажатия ✅ Да.
    """
    if not update.message or not update.effective_user:
        return

    voice = update.message.voice

    is_available, reason = is_voice_recognition_available(VOSK_MODEL_PATH)

    if not is_available:
        await update.message.reply_text(
            "Голосовой ввод сейчас недоступен.\n\n"
            f"Причина: {reason}\n\n"
            "Добавь расход текстом, например:\n"
            "250 еда кофе"
        )
        return

    if voice is None:
        await update.message.reply_text("Не удалось получить голосовое сообщение.")
        return

    await update.message.reply_text("Голосовое получено. Распознаю расход...")

    temp_dir = Path("temp")
    temp_dir.mkdir(exist_ok=True)

    ogg_path = temp_dir / f"{uuid4()}.ogg"
    wav_path = temp_dir / f"{uuid4()}.wav"

    try:
        telegram_file = await voice.get_file()
        await telegram_file.download_to_drive(custom_path=str(ogg_path))

        recognized_text = await asyncio.to_thread(
            recognize_ogg,
            str(ogg_path),
            str(wav_path),
            VOSK_MODEL_PATH,
        )

        if not recognized_text:
            await update.message.reply_text(
                "Я не смог распознать текст в голосовом сообщении.\n\n"
                "Попробуй записать голосовое ещё раз или введи расход текстом:\n"
                "250 еда кофе"
            )
            return

        try:
            amount, category, comment = parse_expense(recognized_text)
        except ExpenseParseError:
            context.user_data["last_voice_transcription"] = recognized_text

            await update.message.reply_text(
                "Я расшифровал голосовое сообщение так:\n\n"
                f"{recognized_text}\n\n"
                "Но не смог распознать это как расход.\n\n"
                "Нужен формат:\n"
                "250 еда кофе\n\n"
                "Что сделать?",
                reply_markup=get_voice_confirmation_keyboard(),
            )
            return

        context.user_data["pending_voice_expense"] = {
            "recognized_text": recognized_text,
            "amount": amount,
            "category": category,
            "comment": comment,
        }

        response = (
            "Я расшифровал голосовое сообщение так:\n\n"
            f"{recognized_text}\n\n"
            "Получается такой расход:\n\n"
            f"{format_money(amount)} — {category}"
        )

        if comment:
            response += f"\nКомментарий: {comment}"

        response += "\n\nВерно?"

        await update.message.reply_text(
            response,
            reply_markup=get_voice_confirmation_keyboard(),
        )

    except SpeechRecognitionError as error:
        logger.exception("Ошибка распознавания речи")

        await update.message.reply_text(
            "Ошибка распознавания голосового сообщения.\n\n"
            f"Причина: {error}"
        )

    except Exception as error:
        logger.exception("Неизвестная ошибка при обработке голосового")

        await update.message.reply_text(
            "Произошла неизвестная ошибка при обработке голосового сообщения.\n\n"
            f"Техническая информация: {error}"
        )

    finally:
        if ogg_path.exists():
            ogg_path.unlink()

        if wav_path.exists():
            wav_path.unlink()


async def voice_confirmation_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Обрабатывает inline-кнопки после распознавания голосового сообщения.
    """
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if not update.effective_user:
        await query.edit_message_text("Не удалось определить пользователя.")
        return

    user_id = update.effective_user.id
    action = query.data

    if action == VOICE_CONFIRM_YES:
        pending_expense = context.user_data.get("pending_voice_expense")

        if not pending_expense:
            await query.edit_message_text(
                "Нет расхода, который можно подтвердить.\n\n"
                "Отправь новое голосовое сообщение или введи расход текстом."
            )
            return

        amount = pending_expense["amount"]
        category = pending_expense["category"]
        comment = pending_expense["comment"]
        recognized_text = pending_expense["recognized_text"]

        add_expense(
            db_path=DB_PATH,
            user_id=user_id,
            amount=amount,
            category=category,
            comment=comment,
        )

        context.user_data.pop("pending_voice_expense", None)
        context.user_data.pop("last_voice_transcription", None)
        context.user_data.pop("manual_voice_input", None)

        response = (
            "Расход подтверждён и добавлен в базу данных.\n\n"
            f"Расшифровка: {recognized_text}\n\n"
            f"{format_money(amount)} — {category}"
        )

        if comment:
            response += f"\nКомментарий: {comment}"

        await query.edit_message_text(response)
        return

    if action == VOICE_CONFIRM_REWRITE:
        context.user_data.pop("pending_voice_expense", None)
        context.user_data.pop("last_voice_transcription", None)
        context.user_data.pop("manual_voice_input", None)

        await query.edit_message_text(
            "Хорошо, запиши голосовое сообщение заново.\n\n"
            "Лучше говорить коротко и чётко, например:\n"
            "двести пятьдесят еда кофе"
        )
        return

    if action == VOICE_CONFIRM_MANUAL:
        context.user_data.pop("pending_voice_expense", None)
        context.user_data["manual_voice_input"] = True

        await query.edit_message_text(
            "Хорошо, введи расход вручную обычным сообщением.\n\n"
            "Формат:\n"
            "250 еда кофе\n\n"
            "Примеры:\n"
            "500 продукты хлеб\n"
            "1200 транспорт такси"
        )
        return

    await query.edit_message_text(
        "Неизвестное действие. Отправь новое голосовое сообщение или введи расход текстом."
    )


async def unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message:
        await update.message.reply_text(
            "Неизвестная команда. Напиши /help, чтобы посмотреть список команд."
        )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception("Ошибка при обработке update", exc_info=context.error)


def main() -> None:
    init_db(DB_PATH)

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("month", month_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("delete_last", delete_last_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("voice_status", voice_status_command))

    application.add_handler(
        CallbackQueryHandler(
            voice_confirmation_callback,
            pattern="^voice_confirm_",
        )
    )

    application.add_handler(MessageHandler(filters.VOICE, voice_message_handler))

    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, add_expense_from_message)
    )

    application.add_error_handler(error_handler)

    print("Бот запущен...")
    application.run_polling()


if __name__ == "__main__":
    main()