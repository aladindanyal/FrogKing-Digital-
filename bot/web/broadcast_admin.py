from sqladmin import BaseView, ModelView, expose
from starlette.requests import Request
from starlette.responses import RedirectResponse, PlainTextResponse, HTMLResponse

from bot.database import Database
from bot.database.models.main import BroadcastCampaign, BroadcastRecipient
from bot.web.admin import AuditModelView, handle_managed_image_upload, cleanup_orphaned_image
from bot.misc.env import EnvKeys
from bot.misc.services.broadcast_service import validate_payload, estimate_audience, create_draft, confirm_campaign, cancel_campaign, get_campaign_stats

import secrets
import hashlib
import time

def generate_csrf_token(request: Request) -> str:
    token = secrets.token_hex(32)
    request.session['csrf_token'] = token
    request.session['csrf_timestamp'] = time.time()
    return token

def verify_csrf_token(request: Request, token: str) -> bool:
    session_token = request.session.pop('csrf_token', None)
    session_timestamp = request.session.get('csrf_timestamp', 0)

    if not session_token or not token:
        return False

    if time.time() - session_timestamp > 3600:
        return False

    return secrets.compare_digest(session_token, token)

async def check_broadcast_permission(request: Request) -> bool:
    from bot.database.models.main import Permission, User
    from bot.database.main import Database
    from sqlalchemy.orm import selectinload

    user_id = request.session.get("authenticated_user_id")
    if not user_id:
        return False

    async with Database().session() as session:
        user = await session.get(User, user_id, options=[selectinload(User.role)])
        if not user or not user.role:
            return False
        return (user.role.permissions & Permission.BROADCAST) == Permission.BROADCAST

class BroadcastCenterView(BaseView):
    name = "Broadcast Center"
    icon = "fa-solid fa-bullhorn"

    @expose("/broadcast-center", methods=["GET"])
    async def index(self, request: Request):
        return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

    @expose("/broadcasts/new", methods=["GET", "POST"])
    async def new_campaign(self, request: Request):
        if not await check_broadcast_permission(request):
            return PlainTextResponse("Forbidden: BROADCAST permission required", status_code=403)

        if request.method == "GET":
            csrf_token = generate_csrf_token(request)
            from bot.i18n.registry import get_enabled_locales, LOCALE_METADATA
            locales = [(loc, LOCALE_METADATA[loc]["name"]) for loc in get_enabled_locales()]

            return await self.templates.TemplateResponse(
                request,
                "admin/broadcast_new.html",
                {
                    "csrf_token": csrf_token,
                    "locales": locales,
                    "error": request.session.pop("broadcast_error", None)
                }
            )

        form = await request.form()
        csrf_token = form.get("csrf_token")
        if not verify_csrf_token(request, csrf_token):
            return PlainTextResponse("Forbidden: Invalid CSRF token", status_code=403)

        target_locale = form.get("target_locale") or None
        message_text = form.get("message_text", "")

        photo_upload = form.get("photo_upload")
        photo_file_id = None

        if photo_upload and getattr(photo_upload, "filename", None):
            storage_chat = EnvKeys.BROADCAST_STORAGE_CHAT_ID or EnvKeys.DASHBOARD_ADMIN_TELEGRAM_ID or EnvKeys.OWNER_ID
            if not storage_chat:
                request.session["broadcast_error"] = "Media upload failed: No BROADCAST_STORAGE_CHAT_ID configured."
                return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

            content = await photo_upload.read()

            is_valid_image = False
            if len(content) >= 4:
                header = content[:4]
                if header.startswith(b"\xff\xd8\xff"): # JPEG
                    is_valid_image = True
                elif header == b"\x89PNG": # PNG
                    is_valid_image = True
                elif content.startswith(b"RIFF") and content[8:12] == b"WEBP": # WEBP
                    is_valid_image = True

            if not is_valid_image:
                await photo_upload.close()
                request.session["broadcast_error"] = "Media upload failed: Only JPEG, PNG, or WEBP are allowed."
                return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

            if len(content) > 5 * 1024 * 1024:
                await photo_upload.close()
                request.session["broadcast_error"] = "Media upload failed: Size exceeds 5MB limit."
                return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

            await photo_upload.close()
            from aiogram.types import BufferedInputFile
            from bot.misc.services.broadcast_dispatcher import broadcast_dispatcher
            input_file = BufferedInputFile(content, filename=photo_upload.filename)

            try:
                msg = await broadcast_dispatcher.bot.send_photo(chat_id=storage_chat, photo=input_file)
                photo_file_id = msg.photo[-1].file_id
            except Exception as e:
                import logging
                logging.error(f"Failed to upload broadcast media to Telegram: {e}")
                request.session["broadcast_error"] = "Media upload failed: Telegram API error. Check BROADCAST_STORAGE_CHAT_ID."
                return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

        is_valid, err_msg, safe_text = await validate_payload(message_text, photo_file_id, target_locale)
        if not is_valid:
            request.session["broadcast_error"] = err_msg
            return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

        admin_id = request.session.get("authenticated_user_id")
        campaign = await create_draft(admin_id, target_locale, safe_text, photo_file_id)

        return RedirectResponse(f"{request.url_for('admin:preview_campaign')}?id={campaign.id}", status_code=303)

class BroadcastActionView(BaseView):
    name = "Broadcast Actions"

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/broadcasts/preview", methods=["GET"])
    async def preview_campaign(self, request: Request):
        if not await check_broadcast_permission(request):
            return PlainTextResponse("Forbidden: BROADCAST permission required", status_code=403)

        campaign_id = request.query_params.get("id")
        if not campaign_id:
            return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

        async with Database().session() as session:
            campaign = await session.get(BroadcastCampaign, int(campaign_id))
            if not campaign or campaign.status != "draft":
                return RedirectResponse(request.url_for("admin:new_campaign"), status_code=303)

        estimated_count = await estimate_audience(campaign.target_locale)
        csrf_token = generate_csrf_token(request)

        return await self.templates.TemplateResponse(
            request,
            "admin/broadcast_preview.html",
            {
                "csrf_token": csrf_token,
                "campaign": campaign,
                "estimated_count": estimated_count,
                "error": request.session.pop("broadcast_error", None)
            }
        )

    @expose("/broadcasts/confirm", methods=["POST"])
    async def confirm_campaign_post(self, request: Request):
        if not await check_broadcast_permission(request):
            return PlainTextResponse("Forbidden: BROADCAST permission required", status_code=403)

        form = await request.form()
        csrf_token = form.get("csrf_token")
        if not verify_csrf_token(request, csrf_token):
            return PlainTextResponse("Forbidden: Invalid CSRF token", status_code=403)

        campaign_id = int(request.query_params.get("id"))

        success, msg, campaign, count = await confirm_campaign(campaign_id)
        if not success:
            request.session["broadcast_error"] = msg
            return RedirectResponse(f"{request.url_for('admin:preview_campaign')}?id={campaign_id}", status_code=303)

        from bot.misc.services.broadcast_dispatcher import broadcast_dispatcher
        broadcast_dispatcher.wake_up()

        return RedirectResponse(request.url_for("admin:list", identity="broadcastcampaign"), status_code=303)

    @expose("/broadcasts/cancel", methods=["POST"])
    async def cancel_campaign_post(self, request: Request):
        if not await check_broadcast_permission(request):
            return PlainTextResponse("Forbidden: BROADCAST permission required", status_code=403)

        form = await request.form()
        csrf_token = form.get("csrf_token")
        if not verify_csrf_token(request, csrf_token):
            return PlainTextResponse("Forbidden: Invalid CSRF token", status_code=403)

        campaign_id = int(request.query_params.get("id"))
        admin_id = request.session.get("authenticated_user_id")

        success, msg = await cancel_campaign(campaign_id, admin_id)
        if not success:
            request.session["broadcast_error"] = msg

        return RedirectResponse(request.url_for("admin:list", identity="broadcastcampaign"), status_code=303)


class BroadcastCampaignAdmin(AuditModelView, model=BroadcastCampaign):
    name = "Broadcast"
    name_plural = "Broadcasts"
    icon = "fa-solid fa-bullhorn"
    can_create = False
    can_edit = False
    can_delete = False

    column_list = [
        BroadcastCampaign.id,
        BroadcastCampaign.status,
        BroadcastCampaign.created_at,
        BroadcastCampaign.target_locale,


    ]

    column_searchable_list = [BroadcastCampaign.status]
    column_sortable_list = [BroadcastCampaign.created_at, BroadcastCampaign.status]


class BroadcastRecipientAdmin(AuditModelView, model=BroadcastRecipient):
    name = "Recipient"
    name_plural = "Recipients"
    icon = "fa-solid fa-user-check"
    can_create = False
    can_edit = False
    can_delete = False

    column_list = [
        BroadcastRecipient.id,
        BroadcastRecipient.campaign_id,
        BroadcastRecipient.user_id,
        BroadcastRecipient.status,
        BroadcastRecipient.sent_at
    ]

    column_searchable_list = [
        BroadcastRecipient.campaign_id,
        BroadcastRecipient.user_id,
        BroadcastRecipient.status
    ]

def register_broadcast_views(admin):
    admin.add_view(BroadcastCenterView)
    admin.add_view(BroadcastCampaignAdmin)
    admin.add_view(BroadcastRecipientAdmin)
