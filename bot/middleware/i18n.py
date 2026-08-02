from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
from bot.i18n.main import current_locale, normalize_locale, is_supported
from bot.database.methods.read import get_user_language_cached
from bot.i18n.strings import DEFAULT_LOCALE

class I18nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
            
        lang = None

        # 1. DB User Language
        if user:
            db_lang = await get_user_language_cached(user.id)
            if db_lang and is_supported(db_lang):
                lang = db_lang

        # 2. FSM Context (Legacy)
        if not lang:
            state = data.get('state')
            if state:
                state_data = await state.get_data()
                fsm_lang = state_data.get('lang')
                if fsm_lang and is_supported(fsm_lang):
                    lang = fsm_lang

        # 3. Telegram Profile
        if not lang and user and user.language_code:
            tg_lang = normalize_locale(user.language_code)
            if tg_lang and is_supported(tg_lang):
                lang = tg_lang

        # 4. Default Locale
        if not lang:
            lang = DEFAULT_LOCALE
            
        token = current_locale.set(lang)
        try:
            return await handler(event, data)
        finally:
            current_locale.reset(token)
