from bot.keyboards.inline import (
    main_menu, profile_keyboard, wallet_keyboard, simple_buttons, back, close, item_info, payment_menu,
    get_payment_choice, question_buttons, check_sub, referral_system_keyboard,
    admin_console_keyboard,
)
from dataclasses import dataclass


@dataclass
class MockBtn:
    row_order: int
    column_order: int
    id: int
    is_enabled: bool
    owner_only: bool
    action_key: str
    label_en: str
    label_ar: str


def get_mock_config():
    return [
        MockBtn(1, 1, 1, True, False, "shop", "Shop", ""),
        MockBtn(1, 2, 2, True, False, "profile", "Profile", ""),
        MockBtn(2, 1, 3, True, False, "rules", "Rules", ""),
        MockBtn(3, 1, 4, True, True, "admin", "Admin", ""),
        MockBtn(4, 1, 5, True, False, "support", "Support", "")
    ]


def _all_callback_data(markup):
    """Extract all callback_data values from markup."""
    result = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                result.append(btn.callback_data)
    return result


def _all_button_texts(markup):
    """Extract all button texts from markup."""
    result = []
    for row in markup.inline_keyboard:
        for btn in row:
            result.append(btn.text)
    return result


def _has_url_button(markup):
    """Check if any button has a URL."""
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.url:
                return True
    return False


class TestMainMenu:

    def test_basic_buttons_present(self):
        markup = main_menu(role=1, buttons_config=get_mock_config(), locale="en")
        cbs = _all_callback_data(markup)
        assert "shop" in cbs
        assert "rules" in cbs
        assert "profile" in cbs

    def test_no_admin_for_regular_user(self):
        markup = main_menu(role=1, buttons_config=get_mock_config(), locale="en")
        cbs = _all_callback_data(markup)
        assert "console" not in cbs

    def test_admin_button_for_admin(self):
        # 127 gives owner permissions
        markup = main_menu(role=127, buttons_config=get_mock_config(), locale="en")
        cbs = _all_callback_data(markup)
        assert "console" in cbs

    def test_helper_button(self):
        markup = main_menu(role=1, buttons_config=get_mock_config(), locale="en", helper="12345")
        assert _has_url_button(markup)

    def test_no_helper(self):
        markup = main_menu(role=1, buttons_config=get_mock_config(), locale="en")
        assert not _has_url_button(markup)


class TestProfileKeyboard:
    def test_buttons_present(self):
        markup = profile_keyboard()
        cbs = _all_callback_data(markup)
        assert "orders:list:0" in cbs
        assert "operation_history" in cbs
        assert "back_to_menu" in cbs


class TestWalletKeyboard:
    def test_replenish_always_present(self):
        markup = wallet_keyboard(referral_percent=0)
        cbs = _all_callback_data(markup)
        assert "replenish_balance" in cbs

    def test_referral_button_when_percent_nonzero(self):
        markup = wallet_keyboard(referral_percent=10)
        cbs = _all_callback_data(markup)
        assert "referral_system" in cbs

    def test_no_referral_button_when_zero(self):
        markup = wallet_keyboard(referral_percent=0)
        cbs = _all_callback_data(markup)
        assert "referral_system" not in cbs

    def test_back_button_present(self):
        markup = wallet_keyboard(referral_percent=0)
        cbs = _all_callback_data(markup)
        assert "back_to_menu" in cbs


class TestPaymentMenu:

    def test_payment_menu_has_pay_url(self):
        markup = payment_menu("https://example.com/pay")
        has_url = False
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.url == "https://example.com/pay":
                    has_url = True
        assert has_url

    def test_payment_menu_has_check(self):
        markup = payment_menu("https://example.com/pay")
        cbs = _all_callback_data(markup)
        assert "check" in cbs


class TestItemInfoKeyboard:

    def test_has_quick_buy_and_no_back(self):
        # New design uses quick quantities instead of generic "buy"
        markup = item_info("Widget", "gp_0", stock=10, item_id=1)
        cbs = _all_callback_data(markup)
        # Should have quick quantities (qty:quick:1:1 etc)
        assert "qty:quick:1:1" in cbs


class TestSimpleButtons:

    def test_creates_buttons(self):
        markup = simple_buttons([("A", "a"), ("B", "b")])
        cbs = _all_callback_data(markup)
        assert "a" in cbs
        assert "b" in cbs

    def test_button_count(self):
        markup = simple_buttons([("A", "a"), ("B", "b"), ("C", "c")])
        total = sum(len(row) for row in markup.inline_keyboard)
        assert total == 3


class TestBackAndClose:

    def test_back_default(self):
        markup = back()
        cbs = _all_callback_data(markup)
        assert "menu" in cbs

    def test_back_custom_cb(self):
        markup = back("profile")
        cbs = _all_callback_data(markup)
        assert "profile" in cbs

    def test_close_button(self):
        markup = close()
        cbs = _all_callback_data(markup)
        assert "close" in cbs


class TestReferralSystemKeyboard:

    def test_no_referrals_no_earnings(self):
        markup = referral_system_keyboard(has_referrals=False, has_earnings=False)
        cbs = _all_callback_data(markup)
        assert "view_referrals" not in cbs
        assert "view_all_earnings" not in cbs
        assert "profile" in cbs  # back button

    def test_with_referrals(self):
        markup = referral_system_keyboard(has_referrals=True, has_earnings=False)
        cbs = _all_callback_data(markup)
        assert "view_referrals" in cbs

    def test_with_earnings(self):
        markup = referral_system_keyboard(has_referrals=False, has_earnings=True)
        cbs = _all_callback_data(markup)
        assert "view_all_earnings" in cbs

    def test_with_both(self):
        markup = referral_system_keyboard(has_referrals=True, has_earnings=True)
        cbs = _all_callback_data(markup)
        assert "view_referrals" in cbs
        assert "view_all_earnings" in cbs


class TestGetPaymentChoice:

    def test_has_all_methods(self):
        markup = get_payment_choice()
        cbs = _all_callback_data(markup)
        assert "pay_cryptopay" in cbs
        assert "pay_stars" in cbs
        assert "pay_fiat" in cbs
        assert "replenish_balance" in cbs  # back


class TestQuestionButtons:

    def test_has_yes_no_back(self):
        markup = question_buttons("confirm_delete", "shop")
        cbs = _all_callback_data(markup)
        assert "confirm_delete_yes" in cbs
        assert "confirm_delete_no" in cbs
        assert "shop" in cbs


class TestCheckSub:

    def test_has_channel_url(self):
        markup = check_sub("test_channel")
        has_url = False
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.url and "test_channel" in btn.url:
                    has_url = True
        assert has_url

    def test_has_check_callback(self):
        markup = check_sub("test_channel")
        cbs = _all_callback_data(markup)
        assert "sub_channel_done" in cbs


class TestAdminConsoleKeyboard:

    def test_has_roles_button(self):
        markup = admin_console_keyboard()
        cbs = _all_callback_data(markup)
        assert "role_mgmt" in cbs

    def test_has_all_admin_buttons(self):
        markup = admin_console_keyboard()
        cbs = _all_callback_data(markup)
        assert "shop_management" in cbs
        assert "goods_management" in cbs
        assert "categories_management" in cbs
        assert "user_management" in cbs
        assert "send_message" in cbs
        assert "role_mgmt" in cbs

    def test_maintenance_toggle(self):
        markup_on = admin_console_keyboard(maintenance_mode=True)
        markup_off = admin_console_keyboard(maintenance_mode=False)
        texts_on = _all_button_texts(markup_on)
        texts_off = _all_button_texts(markup_off)
        # The maintenance button text should differ between states
        assert texts_on != texts_off
