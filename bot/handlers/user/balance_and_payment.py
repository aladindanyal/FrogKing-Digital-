import json
import uuid
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, SuccessfulPayment
from bot.misc.utils import answer_callback_safe
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.database.methods import get_user_referral, buy_item_transaction, process_payment_with_referral, create_pending_payment
from bot.misc.utils import safe_edit_or_send
from bot.keyboards import back, payment_menu, close, get_payment_choice
from bot.logger_mesh import logger
from bot.database.methods.audit import log_audit
from bot.misc import EnvKeys, ItemPurchaseRequest, validate_telegram_id, validate_money_amount, PaymentRequest, \
    sanitize_html
from bot.handlers.other import _any_payment_method_enabled, is_safe_item_name
from bot.misc.metrics import get_metrics
from bot.misc.services import CryptoPayAPI, CryptoPayAPIError, send_stars_invoice, send_fiat_invoice
from bot.misc.services.payment import _minor_units_for
from bot.filters import ValidAmountFilter
from bot.i18n import localize
from bot.states import BalanceStates

router = Router()


async def _notify_referrer_bonus(bot, user_id: int, amount: int, payer_name: str, payer_id: int):
    """Send referral bonus notification to the referrer if applicable."""
    referral_id = await get_user_referral(user_id)
    if not referral_id or not EnvKeys.REFERRAL_PERCENT:
        return
    try:
        bonus = int(Decimal(EnvKeys.REFERRAL_PERCENT) / Decimal(100) * Decimal(amount))
        if bonus > 0:
            await bot.send_message(
                referral_id,
                localize('payments.referral.bonus',
                         amount=bonus, name=payer_name,
                         id=payer_id, currency=EnvKeys.PAY_CURRENCY),
                reply_markup=close()
            )
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.error(f"Failed to send referral notification to user {referral_id}: {e}")


@router.callback_query(F.data == "replenish_balance")
async def replenish_balance_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """Ask user for the amount if at least one payment method is enabled."""
    if not _any_payment_method_enabled():
        await answer_callback_safe(call, localize("payments.not_configured"), show_alert=True)
        return

    await safe_edit_or_send(call,
        localize("payments.replenish_prompt", currency=EnvKeys.PAY_CURRENCY),
        reply_markup=back('profile')
    )
    await state.set_state(BalanceStates.waiting_amount)


@router.message(BalanceStates.waiting_amount, ValidAmountFilter())
async def replenish_balance_amount(message: Message, state: FSMContext):
    """Store amount and show payment methods."""
    try:
        # Validate amount using Pydantic
        amount = validate_money_amount(
            message.text,
            min_amount=Decimal(EnvKeys.MIN_AMOUNT),
            max_amount=Decimal(EnvKeys.MAX_AMOUNT)
        )

        await state.update_data(amount=int(amount))

        await message.answer(
            localize("payments.method_choose"),
            reply_markup=get_payment_choice()
        )
        await state.set_state(BalanceStates.waiting_payment)

    except ValueError as e:
        await message.answer(
            localize("payments.replenish_invalid",
                     min_amount=EnvKeys.MIN_AMOUNT,
                     max_amount=EnvKeys.MAX_AMOUNT,
                     currency=EnvKeys.PAY_CURRENCY),
            reply_markup=back('replenish_balance')
        )


@router.message(BalanceStates.waiting_amount)
async def invalid_amount(message: Message, state: FSMContext):
    """
    Tell user the amount is invalid.
    """
    await message.answer(
        localize("payments.replenish_invalid",
                 min_amount=EnvKeys.MIN_AMOUNT,
                 max_amount=EnvKeys.MAX_AMOUNT,
                 currency=EnvKeys.PAY_CURRENCY),
        reply_markup=back('replenish_balance')
    )


@router.callback_query(
    BalanceStates.waiting_payment,
    F.data.in_(["pay_cryptopay", "pay_stars", "pay_fiat"])
)
async def process_replenish_balance(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """Create an invoice for the chosen payment method."""
    data = await state.get_data()
    amount = data.get('amount')

    if amount is None:
        await answer_callback_safe(call, localize("payments.session_expired"), show_alert=True)
        await safe_edit_or_send(call, localize("menu.title"), reply_markup=back('back_to_menu'))
        await state.clear()
        return

    # Map callback data to provider
    provider_map = {
        "pay_cryptopay": "cryptopay",
        "pay_stars": "stars",
        "pay_fiat": "fiat"
    }
    provider = provider_map.get(call.data)

    try:
        # Validate payment request
        payment_request = PaymentRequest(
            amount=Decimal(amount),
            currency=EnvKeys.PAY_CURRENCY,
            provider=provider
        )

        amount_dec = payment_request.amount
        ttl_seconds = int(EnvKeys.PAYMENT_TIME)

        if call.data == "pay_cryptopay":
            if not EnvKeys.CRYPTO_PAY_TOKEN:
                await answer_callback_safe(call, localize("payments.not_configured"), show_alert=True)
                return

            try:
                crypto = CryptoPayAPI()
                invoice = await crypto.create_invoice(
                    amount=float(amount_dec),
                    expires_in=ttl_seconds,
                    currency=payment_request.currency,
                    accepted_assets="TON,USDT,BTC,ETH",
                    payload=str(call.from_user.id),
                )
            except CryptoPayAPIError as e:
                await log_audit("cryptopay_error", level="ERROR", user_id=call.from_user.id, resource_type="Payment", details=f"[{e.code}] {e.name}")
                await answer_callback_safe(call, localize("payments.crypto.api_error", error=e.name), show_alert=True)
                return
            except Exception as e:
                await log_audit("cryptopay_invoice_fail", level="ERROR", user_id=call.from_user.id, resource_type="Payment", details=str(e))
                await answer_callback_safe(call, localize("payments.crypto.create_fail", error=str(e)), show_alert=True)
                return

            pay_url = invoice.get("mini_app_invoice_url")
            invoice_id = invoice.get("invoice_id")

            await create_pending_payment(
                provider="cryptopay",
                external_id=str(invoice_id),
                user_id=call.from_user.id,
                amount=int(amount_dec),
                currency=payment_request.currency,
            )

            await state.update_data(invoice_id=invoice_id, payment_type="cryptopay")

            await safe_edit_or_send(call,
                localize("payments.invoice.summary",
                         amount=int(amount_dec),
                         minutes=int(ttl_seconds / 60),
                         button=localize("btn.check_payment"),
                         currency=payment_request.currency),
                reply_markup=payment_menu(pay_url)
            )

        elif call.data == "pay_stars":
            if EnvKeys.STARS_PER_VALUE > 0:
                try:
                    await send_stars_invoice(
                        bot=call.message.bot,
                        chat_id=call.from_user.id,
                        amount=int(amount_dec),
                    )
                except Exception as e:
                    await log_audit("stars_invoice_fail", level="ERROR", user_id=call.from_user.id, resource_type="Payment", details=str(e))
                    await answer_callback_safe(call, localize("payments.stars.create_fail", error=str(e)), show_alert=True)
                    return
                await state.clear()
            else:
                await answer_callback_safe(call, localize("payments.not_configured"), show_alert=True)
                return

        elif call.data == "pay_fiat":
            if not EnvKeys.TELEGRAM_PROVIDER_TOKEN:
                await answer_callback_safe(call, localize("payments.not_configured"), show_alert=True)
                return

            try:
                await send_fiat_invoice(
                    bot=call.message.bot,
                    chat_id=call.from_user.id,
                    amount=int(amount_dec),
                )
            except Exception as e:
                await log_audit("fiat_invoice_fail", level="ERROR", user_id=call.from_user.id, resource_type="Payment", details=str(e))
                await answer_callback_safe(call, localize("payments.fiat.create_fail", error=str(e)), show_alert=True)
                return
            await state.clear()

    except Exception as e:
        logger.error(f"Payment processing error: {e}")
        await state.clear()
        await answer_callback_safe(call, localize("errors.something_wrong"), show_alert=True)


@router.callback_query(F.data == "check")
async def checking_payment(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """
    Check CryptoPay invoice status and credit balance if paid.
    """
    user_id = call.from_user.id
    data = await state.get_data()
    payment_type = data.get("payment_type")

    if not payment_type:
        await answer_callback_safe(call, localize("payments.no_active_invoice"), show_alert=True)
        return

    if payment_type == "cryptopay":
        invoice_id = data.get("invoice_id")
        if not invoice_id:
            await answer_callback_safe(call, localize("payments.invoice_not_found"), show_alert=True)
            await state.clear()
            return

        try:
            crypto = CryptoPayAPI()
            info = await crypto.get_invoice(invoice_id)
        except CryptoPayAPIError as e:
            await log_audit("cryptopay_check_error", level="ERROR", user_id=user_id, resource_type="Payment", details=f"[{e.code}] {e.name}")
            await answer_callback_safe(call, localize("payments.crypto.api_error", error=e.name), show_alert=True)
            return
        except Exception as e:
            await log_audit("cryptopay_get_fail", level="ERROR", user_id=user_id, resource_type="Payment", details=str(e))
            await answer_callback_safe(call, localize("payments.crypto.check_fail", error=str(e)), show_alert=True)
            return

        status = info.get("status")
        if status == "paid":
            balance_amount = int(Decimal(str(info.get("amount", "0"))).quantize(Decimal("1.")))

            # Use transactional payment processing
            success, error_msg = await process_payment_with_referral(
                user_id=user_id,
                amount=Decimal(balance_amount),
                provider="cryptopay",
                external_id=str(invoice_id),
                referral_percent=EnvKeys.REFERRAL_PERCENT
            )

            if not success:
                if error_msg == "already_processed":
                    await answer_callback_safe(call, localize("payments.already_processed"), show_alert=True)
                else:
                    await answer_callback_safe(call, localize("errors.general_error", e=error_msg), show_alert=True)
                return

            metrics = get_metrics()
            if metrics:
                metrics.track_event("payment", user_id, {"amount": balance_amount, "provider": "cryptopay"})

            # Send a notification to the referrer
            await _notify_referrer_bonus(call.bot, user_id, balance_amount, call.from_user.first_name, call.from_user.id)

            await safe_edit_or_send(call,
                localize("payments.topped_simple",
                         amount=balance_amount,
                         currency=EnvKeys.PAY_CURRENCY),
                reply_markup=back('profile')
            )
            await state.clear()

            # Audit log
            try:
                user_info = await call.bot.get_chat(user_id)
                await log_audit(
                    "balance_replenish",
                    user_id=user_id,
                    resource_type="Payment",
                    details=f"name={user_info.first_name}, amount={balance_amount} {EnvKeys.PAY_CURRENCY}, provider=cryptopay",
                )
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                await log_audit("balance_replenish", level="ERROR", user_id=user_id, resource_type="Payment", details=f"log_failed: {e}")

        elif status == "active":
            await answer_callback_safe(call, localize("payments.not_paid_yet"))
        else:
            await answer_callback_safe(call, localize("payments.expired"), show_alert=True)


@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    """Validate the payment before Telegram processes it."""
    try:
        payload = json.loads(query.invoice_payload or "{}")
    except Exception:
        await query.answer(ok=False, error_message="Invalid payload")
        return

    amount = int(payload.get("amount", 0) or payload.get("amount_rub", 0))
    if amount <= 0:
        await query.answer(ok=False, error_message="Invalid amount")
        return

    if amount > int(EnvKeys.MAX_AMOUNT):
        await query.answer(ok=False, error_message="Amount exceeds maximum")
        return

    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    """
    Handle successful payment:
    - XTR (Stars): total_amount is ⭐. take CURRENCY from payload (amount) or convert ⭐ → CURRENCY.
    - Fiat: total_amount is minor units; divide by 100 (or 1 for JPY/KRW).
    """
    sp: SuccessfulPayment = message.successful_payment
    user_id = message.from_user.id

    payload = {}
    try:
        if sp.invoice_payload:
            payload = json.loads(sp.invoice_payload)
    except Exception:
        payload = {}

    amount = 0

    if sp.currency == "XTR":
        # Stars
        if "amount" in payload:
            amount = int(payload["amount"])
        else:
            amount = int(
                (Decimal(int(sp.total_amount)) / Decimal(str(EnvKeys.STARS_PER_VALUE)))
                .to_integral_value(rounding=ROUND_HALF_UP)
            )
    else:
        # Fiat
        currency = sp.currency.upper()
        multiplier = _minor_units_for(currency)
        amount = int(Decimal(sp.total_amount) / Decimal(multiplier))

    if amount <= 0:
        await message.answer(localize("payments.unable_determine_amount"), reply_markup=close())
        return

    # Idempotence
    provider = "telegram" if sp.currency != "XTR" else "stars"
    external_id = sp.telegram_payment_charge_id or sp.provider_payment_charge_id or f"{provider}:{user_id}:{uuid.uuid4().hex}"

    success, error_msg = await process_payment_with_referral(
        user_id=user_id,
        amount=Decimal(amount),
        provider=provider,
        external_id=external_id,
        referral_percent=EnvKeys.REFERRAL_PERCENT
    )

    if not success:
        if error_msg == "already_processed":
            await message.answer(localize("payments.already_processed"), reply_markup=close())
        else:
            await message.answer(localize("payments.processing_error"), reply_markup=close())
        return

    # Sending notification to referrer
    await _notify_referrer_bonus(message.bot, user_id, amount, message.from_user.first_name, message.from_user.id)

    metrics = get_metrics()
    if metrics:
        metrics.track_event("payment", user_id, {"amount": amount, "provider": provider})

    suffix = localize("payments.success_suffix.stars") if sp.currency == "XTR" else localize(
        "payments.success_suffix.tg")
    await message.answer(
        localize('payments.topped_with_suffix', amount=amount, suffix=suffix, currency=EnvKeys.PAY_CURRENCY),
        reply_markup=back('profile')
    )

    # audit log
    try:
        user_info = await message.bot.get_chat(user_id)
        await log_audit(
            "balance_replenish",
            user_id=user_id,
            resource_type="Payment",
            details=f"name={user_info.first_name}, amount={amount} {EnvKeys.PAY_CURRENCY}, provider={suffix}",
        )
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await log_audit("balance_replenish", level="ERROR", user_id=user_id, resource_type="Payment", details=f"log_failed: {e}")


@router.callback_query(F.data == "buy")
async def legacy_buy_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call, "This menu is outdated. Please return to the shop and try again.", show_alert=True)

@router.callback_query(F.data.startswith("confirm_purchase:"))
async def buy_item_callback_handler(call: CallbackQuery, state: FSMContext):
    await answer_callback_safe(call)
    """Processing the purchase of goods with full transactional security."""
    try:
        item_id_str = call.data.split(':')[1]

        # Get item name from state (stored when viewing item info)
        data = await state.get_data()
        raw_item_name = data.get('csrf_item')

        if not raw_item_name or str(data.get('item_id')) != item_id_str:
            await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
            return

        from bot.database.methods import get_item_info_cached
        item_info_data = await get_item_info_cached(raw_item_name)
        if not item_info_data:
            await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
            return

        if item_info_data.get("fulfillment_mode") == "manual":
            # Stage 4C-3A Readiness Guard
            msg = localize("shop.item.manual_unavailable_guard", default="This product requires manual fulfillment and is temporarily unavailable while configuration is being completed.")
            await answer_callback_safe(call, msg, show_alert=True)
            return

        metrics = get_metrics()

        current_qty = data.get('item_quantity', 1)

        # Validation via Pydantic
        purchase_request = ItemPurchaseRequest(
            item_name=raw_item_name,
            user_id=call.from_user.id,
            quantity=current_qty
        )

        # Additional check for SQL injection
        if not is_safe_item_name(purchase_request.item_name):
            await call.answer(
                localize("errors.invalid_item_name"),
                show_alert=True
            )
            await log_audit("suspicious_item_name", level="WARNING", user_id=call.from_user.id, resource_type="Item", details=raw_item_name)
            return

        # User_id validation
        try:
            user_id = validate_telegram_id(call.from_user.id)
        except ValueError as e:
            await answer_callback_safe(call, localize("errors.invalid_user"), show_alert=True)
            return

        # Show the processing indicator
        await answer_callback_safe(call, localize("shop.purchase.processing"))

        # Get promo code from state if applied
        promo_code = data.get('applied_promo')

        # Execute a transactional purchase
        success, message, purchase_data = await buy_item_transaction(
            user_id,
            purchase_request.item_name,
            promo_code=promo_code,
            quantity=purchase_request.quantity,
        )

        if not success:
            # Error handling
            error_messages = {
                "user_not_found": "shop.purchase.fail.user_not_found",
                "item_not_found": "shop.item.not_found",
                "insufficient_funds": "shop.insufficient_funds",
                "out_of_stock": "shop.out_of_stock"
            }

            error_text = localize(
                error_messages.get(message, "shop.purchase.fail.general"),
                message=message
            )

            await safe_edit_or_send(call,
                error_text,
                reply_markup=back('back_to_item')
            )

            if message not in error_messages:
                await log_audit("purchase_error", level="ERROR", user_id=user_id, resource_type="Item", resource_id=purchase_request.item_name, details=message)
            return

        # Successful purchase - sanitize the output

        if metrics:
            metrics.track_event("purchase", call.from_user.id, {
                "item": purchase_request.item_name,
                "price": purchase_data['price']
            })
            metrics.track_conversion("purchase_funnel", "purchase", call.from_user.id)

        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass

        unit_price = Decimal(str(purchase_data['unit_price'])).quantize(Decimal("0.01"))
        total_discount = Decimal(str(purchase_data.get('discount_total', 0))).quantize(Decimal("0.01"))
        total_paid = Decimal(str(purchase_data['total'])).quantize(Decimal("0.01"))
        currency = purchase_data.get('currency', EnvKeys.PAY_CURRENCY)
        
        from datetime import datetime
        dt = datetime.fromisoformat(purchase_data['purchase_timestamp'])
        purchased_time = dt.strftime("%Y-%m-%d %H:%M:%S")

        public_order_id = purchase_data.get('public_order_id', purchase_data['unique_id'])

        receipt_header = (
            f"✅ <b>Order Completed</b>\n\n"
            f"🧾 <b>Order ID:</b> <code>{public_order_id}</code>\n"
            f"📦 <b>Product:</b> {sanitize_html(purchase_request.item_name)}\n"
            f"🔢 <b>Quantity:</b> {purchase_data['quantity']}\n"
            f"💵 <b>Unit Price:</b> {unit_price} {currency}\n"
        )
        if total_discount > 0:
            receipt_header += f"🏷 <b>Discount:</b> {total_discount} {currency}\n"
            
        receipt_header += (
            f"💰 <b>Total Paid:</b> {total_paid} {currency}\n"
            f"🕒 <b>Purchased:</b> {purchased_time}\n\n"
        )

        delivered_values = purchase_data.get('delivered_values', [purchase_data.get('value', '')])
        messages_to_send = []
        current_msg = receipt_header + "🔑 <b>Delivered Value:</b>\n<code>\n"
        
        if purchase_data['quantity'] > 1:
            for i, val in enumerate(delivered_values, 1):
                val_str = f"{i}) {sanitize_html(val)}\n"
                if len(current_msg) + len(val_str) + 100 > 4000:
                    current_msg += "</code>"
                    messages_to_send.append(current_msg)
                    current_msg = f"🧾 <b>Order ID:</b> {public_order_id} (Part {(len(messages_to_send) + 1)})\n\n🔑 <b>Delivered Value (Continued):</b>\n<code>\n{val_str}"
                else:
                    current_msg += val_str
        else:
            safe_value = sanitize_html(delivered_values[0] if delivered_values else purchase_data.get('value', ''))
            current_msg += f"{safe_value}\n"
            
        current_msg += "</code>\n\n⚠️ Keep this message for future reference and support."
        messages_to_send.append(current_msg)
        
        for msg in messages_to_send:
            await call.message.answer(msg, parse_mode='HTML')

        from bot.keyboards.inline import simple_buttons
        
        action_buttons = []
        if 'order_id' in purchase_data:
            action_buttons.append(("📦 View Order", f"orders:view:{purchase_data['order_id']}:p"))
            
        action_buttons.append(("🔁 Buy Again", f"buy_again:{item_id_str}"))
        
        if EnvKeys.HELPER_ID:
            action_buttons.append(("🆘 Support for This Order", f"support_order:{public_order_id}"))
            
        action_buttons.append(("🏠 Home", "back_to_menu"))
        
        await call.message.answer(
            "What would you like to do next?",
            reply_markup=simple_buttons(action_buttons)
        )

        # Secure logging
        try:
            user_info = await call.bot.get_chat(user_id)
            await log_audit(
                "purchase",
                user_id=user_id,
                resource_type="Item",
                resource_id=purchase_request.item_name[:100],
                details=f"name={user_info.first_name[:50]}, price={purchase_data['price']} {EnvKeys.PAY_CURRENCY}, qty={purchase_request.quantity}, unique_id={purchase_data['unique_id']}",
            )
        except Exception as e:
            await log_audit("purchase", level="ERROR", user_id=user_id, resource_type="Item", details=f"log_failed: {e}")

    except Exception as e:
        logger.error(f"Critical error in purchase handler: {e}")
        await call.answer(
            localize("errors.something_wrong"),
            show_alert=True
        )
