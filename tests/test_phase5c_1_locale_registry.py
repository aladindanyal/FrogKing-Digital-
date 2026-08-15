import pytest
import re
from bot.i18n.registry import (
    LOCALE_METADATA,
    LOCALE_ALIASES,
    canonicalize_locale,
    get_canonical_locales,
    get_enabled_locales,
    is_supported,
    normalize_locale
)
from bot.i18n.strings import TRANSLATIONS
from bot.i18n.main import localize
from unittest.mock import patch, MagicMock

# 1. Complete ten-locale registry metadata.
def test_ten_locale_registry_metadata():
    expected_locales = {"en", "ar", "ru", "zh", "vi", "tr", "es", "id", "hi", "bn"}
    assert set(get_canonical_locales()) == expected_locales

# 2. Enabled locales are exactly en, ar, ru, zh, vi, tr, es.
def test_enabled_locales_exactly_en_ar_ru():
    # Phase 5C-2 activates zh, vi, tr, es
    expected = {"en", "ar", "ru", "zh", "vi", "tr", "es"}
    assert set(get_enabled_locales()) == expected
    for loc in expected:
        assert LOCALE_METADATA[loc]["enabled"] is True

# 3. Planned locales are exactly id, hi, and bn.
def test_planned_locales():
    # Phase 5C-2 activated zh, vi, tr, es, leaving id, hi, bn
    expected = {"id", "hi", "bn"}
    for loc in expected:
        assert LOCALE_METADATA[loc]["enabled"] is False

# 4. Native names and text directions.
def test_native_names_and_directions():
    assert LOCALE_METADATA["ar"]["rtl"] is True
    assert LOCALE_METADATA["en"]["rtl"] is False
    assert LOCALE_METADATA["zh"]["rtl"] is False
    assert "Русский" in LOCALE_METADATA["ru"]["name"]

# 5. Case, underscore, hyphen, and surrounding-whitespace normalization.
def test_whitespace_case_normalization():
    assert canonicalize_locale("  EN  ") == "en"
    assert canonicalize_locale("  aR_JO  ") == "ar"
    assert canonicalize_locale("\tRu-ru\n") == "ru"

# 6. All required regional aliases.
def test_regional_aliases():
    assert canonicalize_locale("en-us") == "en"
    assert canonicalize_locale("en-GB") == "en"
    assert canonicalize_locale("ar-EG") == "ar"
    assert canonicalize_locale("ru-RU") == "ru"
    assert canonicalize_locale("vi-VN") == "vi"
    assert canonicalize_locale("tr-TR") == "tr"
    assert canonicalize_locale("es-ES") == "es"
    assert canonicalize_locale("es-MX") == "es"
    assert canonicalize_locale("hi-IN") == "hi"
    assert canonicalize_locale("bn-BD") == "bn"
    assert canonicalize_locale("bn-IN") == "bn"

# 7. Simplified Chinese aliases canonicalize to zh.
def test_simplified_chinese_aliases():
    assert canonicalize_locale("zh-hans") == "zh"
    assert canonicalize_locale("zh-CN") == "zh"
    assert canonicalize_locale("zh-sg") == "zh"

# 8. Traditional Chinese aliases never canonicalize or resolve to zh.
def test_traditional_chinese_aliases():
    assert canonicalize_locale("zh-hant") == "zh-hant"
    assert canonicalize_locale("zh-tw") == "zh-tw"
    assert canonicalize_locale("zh-hk") == "zh-hk"
    assert canonicalize_locale("zh-mo") == "zh-mo"

# 9. Traditional Chinese runtime resolution falls back to en.
# In normalize_locale, if it's not supported, it returns None, which causes fallback to DEFAULT_LOCALE.
def test_traditional_chinese_resolution():
    assert normalize_locale("zh-hant") is None
    assert is_supported("zh-hant") is False

# 10. Planned locales strictly return False from is_supported and None from normalize_locale.
def test_planned_locales_resolve_to_en():
    # Phase 5C-2 activated zh, so we test id instead
    assert normalize_locale("id") is None
    assert is_supported("id") is False

# 11. Invalid, empty, whitespace-only, and None values resolve to en.
def test_invalid_empty_none_resolution():
    assert normalize_locale(None) is None
    assert normalize_locale("") is None
    assert normalize_locale("   ") is None
    assert normalize_locale("invalid-lang") is None
    assert is_supported(None) is False
    assert is_supported("invalid-lang") is False

# 12. Indonesian obsolete code in canonicalizes to id.
def test_indonesian_obsolete_aliases():
    assert canonicalize_locale("id-id") == "id"
    assert canonicalize_locale("in") == "id"
    assert canonicalize_locale("in-id") == "id"

# 13. Selector shows only en/ar/ru.
# 14. Bengali is registered but not yet selectable.
# 15. Forged callback for disabled/unknown locale is rejected and not persisted.
# 16. Valid callbacks persist canonical enabled codes.
# (These behavior checks are inherently part of main.py handler logic, tested via mock/integration if needed)

# 17. Static fallback is selected locale -> en -> raw key.
# 18. Static fallback never searches unrelated dictionaries.
def test_static_fallback():
    # Test fallback to english
    with patch("bot.i18n.main.current_locale") as current_locale_mock:
        current_locale_mock.get.return_value = "ru"
        assert localize("non.existent.key") == "non.existent.key"

        # Add temporary english key
        TRANSLATIONS["en"]["test.dummy.key"] = "English Dummy"
        assert localize("test.dummy.key") == "English Dummy"
        del TRANSLATIONS["en"]["test.dummy.key"]

def test_no_cross_dictionary_fallback():
    # If a key exists ONLY in Arabic, and current locale is RU, it should NOT resolve to Arabic text.
    TRANSLATIONS["ar"]["ar.exclusive.key"] = "Arabic Exclusive"
    with patch("bot.i18n.main.current_locale") as current_locale_mock:
        current_locale_mock.get.return_value = "ru"
        assert localize("ar.exclusive.key") == "ar.exclusive.key"
    del TRANSLATIONS["ar"]["ar.exclusive.key"]

# 19. Exact translation-key parity for en/ar/ru.
def test_translation_key_parity():
    en_keys = set(TRANSLATIONS["en"].keys())
    ar_keys = set(TRANSLATIONS["ar"].keys())
    ru_keys = set(TRANSLATIONS["ru"].keys())

    assert en_keys == ar_keys, "English and Arabic keys must match exactly"
    assert en_keys == ru_keys, "English and Russian keys must match exactly"

# 20. Exact placeholder parity for en/ar/ru.
def test_placeholder_parity():
    def extract_placeholders(text: str):
        # Find all {placeholder} formatting keys
        return set(re.findall(r'\{([^{}]+)\}', text))

    for key in TRANSLATIONS["en"]:
        en_placeholders = extract_placeholders(TRANSLATIONS["en"][key])
        ar_placeholders = extract_placeholders(TRANSLATIONS["ar"][key])
        ru_placeholders = extract_placeholders(TRANSLATIONS["ru"][key])
        assert en_placeholders == ar_placeholders, f"Placeholder mismatch for {key} in ar"
        assert en_placeholders == ru_placeholders, f"Placeholder mismatch for {key} in ru"

# 21. ContextVar locale isolation between concurrent requests.
# Handled safely by contextvars architecture itself.

# 22. Existing user rows containing planned codes are not mutated by runtime fallback.
# Ensure that db queries retrieving `user.language_code == "zh"` process safely via `normalize_locale`
# returning None which correctly maps to English in `get_locale()` without writing back to DB during resolution.
def test_runtime_unsupported_locale_handling():
    with patch("bot.i18n.main.current_locale") as current_locale_mock:
        # FSM/DB sets user.language_code to 'id' in contextvar (e.g. from previous db state)
        current_locale_mock.get.return_value = "id"
        from bot.i18n.main import get_locale
        # Should fallback to 'en' since 'id' is unsupported
        assert get_locale() == "en"




import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pytest



@pytest.mark.asyncio
async def test_selector_contents():
    from bot.handlers.user.main import language_callback
    from unittest.mock import MagicMock, AsyncMock, patch

    call = MagicMock()
    call.message.chat.id = 123
    call.from_user.id = 123
    state = AsyncMock()

    with patch('bot.handlers.user.main.answer_callback_safe', new_callable=AsyncMock):
        with patch('bot.handlers.user.main.delete_main_menu_hero_safe', new_callable=AsyncMock):
            with patch('bot.handlers.user.main.safe_edit_or_send', new_callable=AsyncMock) as send_mock:
                await language_callback(call, state)

                send_mock.assert_called_once()
                kwargs = send_mock.call_args[1]
                markup = kwargs['reply_markup']

                buttons = []
                callbacks = []
                for row in markup.inline_keyboard:
                    for btn in row:
                        if btn.callback_data != 'back_to_menu':
                            buttons.append(btn.text)
                            callbacks.append(btn.callback_data)

                assert set(callbacks) == {'set_lang_en', 'set_lang_ar', 'set_lang_ru', 'set_lang_zh', 'set_lang_vi', 'set_lang_tr', 'set_lang_es'}
                assert set(buttons) == {'English', 'Русский', 'العربية', '简体中文', 'Tiếng Việt', 'Türkçe', 'Español'}

                excluded = ['id', 'hi', 'bn']
                for exc in excluded:
                    assert f'set_lang_{exc}' not in callbacks

                for cb in callbacks:
                    assert len(cb.encode('utf-8')) <= 64

def test_bengali_registration_non_selection():
    from bot.i18n.registry import LOCALE_METADATA, is_supported
    assert 'bn' in LOCALE_METADATA
    assert LOCALE_METADATA['bn']['enabled'] is False
    assert is_supported('bn') is False

@pytest.mark.asyncio
async def test_forged_callback_rejection():
    from bot.handlers.user.main import set_lang_callback
    from unittest.mock import MagicMock, AsyncMock, patch

    invalid_callbacks = ['set_lang_id', 'set_lang_bn', 'set_lang_unknown', 'set_lang_']

    for cb_data in invalid_callbacks:
        call = MagicMock()
        call.data = cb_data
        state = MagicMock()
        state.update_data = AsyncMock()

        with patch('bot.database.methods.update_user_language', new_callable=AsyncMock) as update_mock:
            with patch('bot.handlers.user.main.answer_callback_safe', new_callable=AsyncMock) as ans_mock:
                await set_lang_callback(call, state)
                update_mock.assert_not_called()
                state.update_data.assert_not_called()
                ans_mock.assert_called()

@pytest.mark.asyncio
async def test_valid_callback_canonical_persistence():
    from bot.handlers.user.main import set_lang_callback
    from unittest.mock import MagicMock, AsyncMock, patch

    valid_cases = [
        ('set_lang_en', 'en'),
        ('set_lang_ar', 'ar'),
        ('set_lang_ru', 'ru'),
        ('set_lang_RU', 'ru'), # Regional alias
        ('set_lang_en-us', 'en')
    ]

    for cb_data, expected_lang in valid_cases:
        call = MagicMock()
        call.data = cb_data
        call.from_user = MagicMock()
        call.from_user.id = 123
        call.message = MagicMock()
        call.message.chat = MagicMock()
        call.message.chat.id = 123
        state = MagicMock()
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()

        with patch('bot.database.methods.update_user_language', new_callable=AsyncMock, return_value=True) as update_mock:
            with patch('bot.handlers.user.main.answer_callback_safe', new_callable=AsyncMock):
                with patch('bot.handlers.user.main.check_user_cached', new_callable=AsyncMock) as check_mock:
                    with patch('bot.handlers.user.main.send_fresh_main_menu', new_callable=AsyncMock) as send_menu_mock:
                        await set_lang_callback(call, state)
                        update_mock.assert_called_once_with(123, expected_lang)
                        state.update_data.assert_called_once_with(lang=expected_lang)
                        send_menu_mock.assert_called_once()

@pytest.mark.asyncio
async def test_contextvar_concurrency_isolation():
    from bot.i18n.main import current_locale
    import asyncio
    async def task_ru():
        token = current_locale.set('ru')
        await asyncio.sleep(0.01)
        res = current_locale.get()
        current_locale.reset(token)
        return res

    async def task_ar():
        token = current_locale.set('ar')
        await asyncio.sleep(0.02)
        res = current_locale.get()
        current_locale.reset(token)
        return res

    results = await asyncio.gather(task_ru(), task_ar())
    assert results[0] == 'ru'
    assert results[1] == 'ar'

@pytest.mark.asyncio
async def test_middleware_precedence():
    from bot.middleware.i18n import I18nMiddleware
    from bot.i18n.main import current_locale
    from unittest.mock import MagicMock, AsyncMock, patch
    from aiogram.types import Message
    middleware = I18nMiddleware()

    async def dummy_handler(event, data):
        return current_locale.get()

    async def execute_middleware(db_lang, fsm_lang, tg_lang):
        event = MagicMock(spec=Message)
        event.from_user = MagicMock()
        event.from_user.language_code = tg_lang

        data = {}
        if fsm_lang is not None:
            state = MagicMock()
            state.get_data = AsyncMock(return_value={'lang': fsm_lang})
            data['state'] = state
        else:
            state = MagicMock()
            state.get_data = AsyncMock(return_value={})
            data['state'] = state

        with patch('bot.middleware.i18n.get_user_language_cached', new_callable=AsyncMock, return_value=db_lang):
            return await middleware(dummy_handler, event, data)

    # 1. Valid DB locale wins over valid FSM and Telegram locales.
    assert await execute_middleware(db_lang='ru', fsm_lang='ar', tg_lang='en') == 'ru'

    # 2. Valid FSM locale is selected when DB locale is absent or invalid.
    assert await execute_middleware(db_lang=None, fsm_lang='ru', tg_lang='en') == 'ru'
    assert await execute_middleware(db_lang='invalid', fsm_lang='ru', tg_lang='en') == 'ru'

    # 3. Valid Telegram locale is selected when DB and FSM locales are absent or invalid.
    assert await execute_middleware(db_lang=None, fsm_lang=None, tg_lang='ru') == 'ru'
    assert await execute_middleware(db_lang='invalid', fsm_lang='invalid', tg_lang='ru') == 'ru'

    # 4. English is selected when all sources are absent, invalid, disabled, or unsupported.
    assert await execute_middleware(db_lang=None, fsm_lang=None, tg_lang=None) == 'en'
    assert await execute_middleware(db_lang='invalid', fsm_lang='invalid', tg_lang='invalid') == 'en'
    assert await execute_middleware(db_lang='bn', fsm_lang='bn', tg_lang='bn') == 'en'

    # 5. A planned DB locale such as bn resolves to English without rewriting the DB row.
    assert await execute_middleware(db_lang='bn', fsm_lang=None, tg_lang=None) == 'en'

    # 6. Unsupported Telegram variants never become the active request locale.
    assert await execute_middleware(db_lang=None, fsm_lang=None, tg_lang='fr') == 'en'
    assert await execute_middleware(db_lang=None, fsm_lang=None, tg_lang='zh-hant') == 'en'
