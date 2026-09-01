"""Protected SQLAdmin view for immutable referral ledger adjustments."""

from decimal import Decimal, InvalidOperation
import secrets
import time

from sqlalchemy.orm import selectinload
from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from bot.database import Database
from bot.database.methods.referrals import admin_adjust_referral
from bot.database.models.main import Permission, User


def _new_form_tokens(request: Request) -> tuple[str, str]:
    csrf_token = secrets.token_hex(32)
    idempotency_key = secrets.token_urlsafe(32)
    request.session["referral_adjustment_csrf"] = csrf_token
    request.session["referral_adjustment_csrf_at"] = time.time()
    return csrf_token, idempotency_key


def _valid_csrf(request: Request, submitted: str | None) -> bool:
    expected = request.session.pop("referral_adjustment_csrf", None)
    issued_at = request.session.pop("referral_adjustment_csrf_at", 0)
    return bool(
        expected
        and submitted
        and time.time() - issued_at <= 3600
        and secrets.compare_digest(expected, submitted)
    )


async def _can_adjust(request: Request) -> bool:
    user_id = request.session.get("authenticated_user_id")
    if not user_id:
        return False
    async with Database().session() as session:
        user = await session.get(User, int(user_id), options=[selectinload(User.role)])
        return bool(
            user
            and user.role
            and (user.role.permissions & Permission.BALANCE_MANAGE)
            == Permission.BALANCE_MANAGE
        )


class ReferralAdjustmentView(BaseView):
    name = "Referral Adjustment"
    icon = "fa-solid fa-scale-balanced"

    def is_accessible(self, request: Request) -> bool:
        return bool(request.session.get("authenticated"))

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)

    @expose("/referrals/adjust", methods=["GET", "POST"])
    async def adjust(self, request: Request):
        if not await _can_adjust(request):
            return PlainTextResponse(
                "Forbidden: BALANCE_MANAGE permission required", status_code=403
            )

        if request.method == "GET":
            csrf_token, idempotency_key = _new_form_tokens(request)
            return await self.templates.TemplateResponse(
                request,
                "admin/referral_adjustment.html",
                {
                    "csrf_token": csrf_token,
                    "idempotency_key": idempotency_key,
                    "error": request.session.pop("referral_adjustment_error", None),
                    "success": request.session.pop("referral_adjustment_success", None),
                },
            )

        form = await request.form()
        if not _valid_csrf(request, form.get("csrf_token")):
            return PlainTextResponse("Forbidden: Invalid CSRF token", status_code=403)

        try:
            target_user_id = int(form.get("target_user_id", ""))
            amount = Decimal(str(form.get("amount", ""))).quantize(Decimal("0.01"))
        except (TypeError, ValueError, InvalidOperation):
            request.session["referral_adjustment_error"] = "Invalid user ID or amount."
            return RedirectResponse(request.url_for("admin:adjust"), status_code=303)

        reason = str(form.get("reason", "")).strip()
        idempotency_key = str(form.get("idempotency_key", "")).strip()
        admin_id = request.session.get("authenticated_user_id")
        success, message = await admin_adjust_referral(
            f"telegram:{admin_id}",
            target_user_id,
            amount,
            reason,
            idempotency_key,
        )
        if success:
            request.session["referral_adjustment_success"] = (
                f"Adjustment {amount} recorded for user {target_user_id}."
            )
        else:
            request.session["referral_adjustment_error"] = message
        return RedirectResponse(request.url_for("admin:adjust"), status_code=303)
