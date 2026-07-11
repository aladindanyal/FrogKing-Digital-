from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

async def safe_edit_or_send(call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = 'HTML'):
    """
    Safely edit a message or send a new one if editing fails (e.g. if the original message was a photo).
    """
    if call.message.photo or call.message.video or call.message.document:
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        try:
            await call.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                try:
                    await call.message.delete()
                except Exception:
                    pass
                await call.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
