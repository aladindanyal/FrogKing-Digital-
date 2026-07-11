from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Dict, Any, Awaitable
from bot.i18n.main import current_locale

class I18nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        state = data.get('state')
        if state:
            state_data = await state.get_data()
            lang = state_data.get('lang')
            if lang:
                token = current_locale.set(lang)
                try:
                    return await handler(event, data)
                finally:
                    current_locale.reset(token)
        return await handler(event, data)
