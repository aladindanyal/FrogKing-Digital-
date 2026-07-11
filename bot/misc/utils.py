from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InaccessibleMessage
from aiogram.exceptions import TelegramBadRequest

async def safe_edit_or_send(call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = 'HTML'):
    """
    Safely edit a message or send a new one if editing fails (e.g. if the original message was a photo or is inaccessible).
    """
    if isinstance(call.message, Message):
        if getattr(call.message, 'photo', None) or getattr(call.message, 'video', None) or getattr(call.message, 'document', None):
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.bot.send_message(
                chat_id=call.from_user.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            try:
                await call.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    try:
                        await call.message.delete()
                    except Exception:
                        pass
                    await call.bot.send_message(
                        chat_id=call.from_user.id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
    else:
        # call.message is InaccessibleMessage or None
        await call.bot.send_message(
            chat_id=call.from_user.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

async def answer_callback_safe(
    call: CallbackQuery,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    try:
        await call.answer(text=text, show_alert=show_alert)
    except Exception:
        pass
