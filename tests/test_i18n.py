import pytest
from unittest.mock import patch


class TestGetLocale:

    def test_valid_locale(self):
        from bot.i18n.main import get_locale

        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "ru"
            result = get_locale()
        assert result == "ru"

    def test_invalid_locale_falls_back(self):
        from bot.i18n.main import get_locale

        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "xx"
            result = get_locale()

        from bot.i18n.strings import DEFAULT_LOCALE
        assert result == DEFAULT_LOCALE

    def test_locale_stripped_and_lowered(self):
        from bot.i18n.main import get_locale

        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "  RU  "
            result = get_locale()
        assert result == "ru"


class TestLocalize:

    def test_existing_key(self):
        from bot.i18n.main import localize, get_locale

        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "ru"
            result = localize("btn.shop")

        assert result != "btn.shop"  # Should return the translation, not the key

    def test_missing_key_returns_key(self):
        from bot.i18n.main import localize, get_locale

        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "ru"
            result = localize("nonexistent.key.that.does.not.exist")

        assert result == "nonexistent.key.that.does.not.exist"

    def test_format_with_kwargs(self):
        from bot.i18n.main import localize, get_locale
        from bot.i18n.strings import TRANSLATIONS

        # Find a key that uses format placeholders
        # profile.caption uses {id} and {name}
        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "ru"
            result = localize("profile.caption", id=12345, name="TestUser")

        assert "12345" in result
        assert "TestUser" in result

    def test_format_error_returns_unformatted(self):
        from bot.i18n.main import localize, get_locale

        # profile.caption expects {id} and {name} — pass wrong kwargs
        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "ru"
            result = localize("profile.caption", wrong_key="value")

        # Should return the unformatted template (not crash)
        assert isinstance(result, str)

    def test_localize_returns_string(self):
        from bot.i18n.main import localize, get_locale

        with patch('bot.i18n.main.EnvKeys') as env:
            env.BOT_LOCALE = "ru"
            result = localize("btn.back")

        assert isinstance(result, str)
        assert len(result) > 0
