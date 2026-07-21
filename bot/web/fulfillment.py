import json
from datetime import datetime
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route, Mount
from starlette.templating import Jinja2Templates
from jinja2 import FileSystemLoader, ChoiceLoader
import sqladmin
import os

from bot.database.main import Database
from bot.database.models.main import (
    ManualFulfillmentJob, Order, OrderItem, User, OrderCustomerInput,
    ManualOrderInteraction, ManualOrderNotification, AuditLog
)
from bot.misc.encryption import decrypt_text
from bot.misc.services.outbox_dispatcher import outbox_dispatcher
import logging

logger = logging.getLogger(__name__)

sqladmin_templates = os.path.join(os.path.dirname(sqladmin.__file__), "templates")
templates = Jinja2Templates(directory="bot/web/templates")
templates.env.loader = ChoiceLoader([
    FileSystemLoader("bot/web/templates"),
    FileSystemLoader(sqladmin_templates)
])

def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))

def resolve_admin_actor(request: Request) -> tuple[int | None, str]:
    admin_id_raw = request.session.get("admin_id")
    try:
        actor_id = int(admin_id_raw)
    except (ValueError, TypeError):
        actor_id = None

    actor_label = str(admin_id_raw) if admin_id_raw else "authenticated_admin"
    return actor_id, actor_label

def get_fulfillment_actions(status: str) -> dict:
    return {
        "start_processing": status == 'queued',
        "message_customer": status in ('in_progress', 'waiting_customer'),
        "request_verification": status == 'in_progress',
        "resume_processing": status == 'waiting_customer',
        "complete_order": status == 'in_progress',
        "retry_notification": False
    }

def render_fulfillment_template(request: Request, template_name: str, context: dict = None):
    if context is None:
        context = {}
    context["admin"] = request.app.state.admin
    return templates.TemplateResponse(request, template_name, context)

async def load_fulfillment_job(session, job_id: int, *, for_update: bool = False):
    if for_update:
        # Step 1: Lock the base row exclusively to prevent outer-join locking errors
        await session.execute(
            select(ManualFulfillmentJob.id)
            .filter(ManualFulfillmentJob.id == job_id)
            .with_for_update()
        )

    # Step 2: Load the full graph
    stmt = (
        select(ManualFulfillmentJob)
        .options(
            joinedload(ManualFulfillmentJob.order_item).joinedload(OrderItem.order).joinedload(Order.user),
            joinedload(ManualFulfillmentJob.order_item).joinedload(OrderItem.item),
            selectinload(ManualFulfillmentJob.order_item, OrderItem.customer_inputs),
            selectinload(ManualFulfillmentJob.interactions),
            selectinload(ManualFulfillmentJob.notifications)
        )
        .filter(ManualFulfillmentJob.id == job_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

def build_fulfillment_workspace_view(job: ManualFulfillmentJob) -> dict:
    order_item = job.order_item
    order = order_item.order
    user = order.user

    customer_inputs = []
    for inp in order_item.customer_inputs:
        is_sensitive = inp.is_sensitive
        if inp.field_type_snapshot in ("email", "text", "username", "url", "phone", "select"):
            is_sensitive = False

        preview = inp.masked_preview
        if not is_sensitive:
            try:
                preview = decrypt_text(inp.encrypted_value, inp.encryption_version)
            except Exception:
                preview = "Decryption error"

        customer_inputs.append({
            "id": inp.id,
            "field_key_snapshot": inp.field_key_snapshot,
            "is_sensitive": is_sensitive,
            "masked_preview": preview
        })

    conversation_messages = []
    fulfillment_events = []
    has_unread = False
    unread_reply_count = 0

    sorted_interactions = sorted(job.interactions, key=lambda x: (x.created_at or datetime.min, x.id))

    for ia in sorted_interactions:
        is_unread = (ia.direction == 'customer_to_admin' and ia.read_at is None)
        if is_unread:
            has_unread = True
            if ia.kind == 'customer_reply':
                unread_reply_count += 1

        ia_dict = {
            "id": ia.id,
            "direction": ia.direction,
            "kind": ia.kind,
            "safe_preview": ia.safe_preview,
            "is_sensitive": ia.is_sensitive,
            "is_unread": is_unread,
            "created_at_fmt": ia.created_at.strftime('%Y-%m-%d %H:%M:%S') if ia.created_at else "",
            "read_at": ia.read_at.isoformat() if ia.read_at else None
        }

        if ia.kind in ('message', 'verification_request', 'customer_reply'):
            conversation_messages.append(ia_dict)
        else:
            fulfillment_events.append(ia_dict)

    # Sort conversation_messages ascending (already ascending from the sorted loop)
    # Sort fulfillment_events descending
    fulfillment_events.reverse()

    notifications = []
    sorted_notifications = sorted(job.notifications, key=lambda x: x.id, reverse=True)
    for notif in sorted_notifications:
        safe_preview = "System notification"
        if notif.idempotency_key:
            if notif.idempotency_key.startswith("msg_"):
                safe_preview = "Message to customer"
            elif notif.idempotency_key.startswith("verify_"):
                safe_preview = "Verification request"
            elif notif.idempotency_key.startswith("comp_"):
                safe_preview = "Completion notification"

        notifications.append({
            "id": notif.id,
            "status": notif.status,
            "attempts": notif.attempts,
            "safe_preview": safe_preview,
            "last_error": notif.last_error or "",
            "created_at_fmt": notif.created_at.strftime('%Y-%m-%d %H:%M:%S') if notif.created_at else ""
        })

    return {
        "job_id": job.id,
        "public_id": order.public_id,
        "product_name_snapshot": order_item.product_name_snapshot,
        "quantity": order_item.quantity,
        "status": job.status,
        "status_label": job.status.replace("_", " ").title(),
        "paid_at": order.created_at.isoformat() if order.created_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "telegram_id": user.telegram_id if user else None,
        "customer_first_name": user.first_name if user else None,
        "customer_last_name": user.last_name if user else None,
        "customer_username": user.telegram_username if user else None,
        "has_unread": has_unread,
        "unread_reply_count": unread_reply_count,
        "inputs": customer_inputs,
        "conversation_messages": conversation_messages,
        "fulfillment_events": fulfillment_events,
        "notifications": notifications,
        "actions": get_fulfillment_actions(job.status),
        "updated_at": job.updated_at.isoformat() if job.updated_at else ""
    }

async def fulfillment_dashboard(request: Request):
    if not is_authenticated(request):
        return HTMLResponse("Unauthorized", status_code=401)

    return render_fulfillment_template(request, "fulfillment/dashboard.html")

async def api_queue(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        async with Database().session() as session:
            result = await session.execute(
                select(ManualFulfillmentJob)
                .options(
                    joinedload(ManualFulfillmentJob.order_item).joinedload(OrderItem.order).joinedload(Order.user),
                    joinedload(ManualFulfillmentJob.order_item).joinedload(OrderItem.item),
                    selectinload(ManualFulfillmentJob.interactions)
                )
                .filter(ManualFulfillmentJob.status != 'cancelled')
            )
            jobs = result.scalars().all()

            queue = []
            for job in jobs:
                order = job.order_item.order
                user = order.user

                # Keep customer display logic in template but pass plain values
                display_name = (
                    getattr(user, "first_name", None)
                    or str(getattr(user, "telegram_id", None) or getattr(user, "id", None) or "Unknown")
                )

                # Check for unread replies
                unread_reply = any(
                    i.direction == 'customer_to_admin' and i.read_at is None
                    for i in job.interactions
                )

                queue.append({
                    "id": job.id,
                    "order_id": job.order_item.order_id,
                    "public_order_id": order.public_id,
                    "product": job.order_item.product_name_snapshot,
                    "customer": display_name,
                    "customer_first_name": user.first_name if user else None,
                    "customer_last_name": user.last_name if user else None,
                    "customer_username": user.telegram_username if user else None,
                    "telegram_id": order.user_id,
                    "quantity": job.order_item.quantity,
                    "status": job.status,
                    "paid_at": order.created_at.isoformat() if order.created_at else None,
                    "created_at": job.created_at.isoformat(),
                    "unread_reply": unread_reply
                })

            return JSONResponse({"queue": queue})
    except Exception as e:
        logger.error(f"api_queue error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def fulfillment_workspace(request: Request):
    if not is_authenticated(request):
        return HTMLResponse("Unauthorized", status_code=401)

    try:
        job_id = int(request.path_params["id"])
        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id)
            if not job:
                return HTMLResponse("Not Found", status_code=404)

            view_model = build_fulfillment_workspace_view(job)
            return render_fulfillment_template(request, "fulfillment/workspace.html", {"job": view_model})
    except Exception as e:
        logger.error(f"workspace error: {e}")
        return HTMLResponse("Internal Server Error", status_code=500)

async def api_workspace_state(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id)
            if not job:
                return JSONResponse({"ok": False, "error": "not_found", "message": "Job not found"}, status_code=404)

            view_model = build_fulfillment_workspace_view(job)
            view_model["ok"] = True
            return JSONResponse(view_model)
    except Exception as e:
        logger.error(f"api_workspace_state error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Server error fetching live state"}, status_code=500)

async def api_reveal(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        data = await request.json()
        input_id = int(data.get("input_id", 0))
        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            result = await session.execute(
                select(OrderCustomerInput)
                .join(OrderItem)
                .join(ManualFulfillmentJob)
                .filter(ManualFulfillmentJob.id == job_id, OrderCustomerInput.id == input_id)
            )
            customer_input = result.scalar_one_or_none()

            if not customer_input or not customer_input.is_sensitive:
                return JSONResponse({"ok": False, "error": "invalid_input", "message": "Invalid input or not sensitive"}, status_code=400)

            plaintext = decrypt_text(customer_input.encrypted_value, customer_input.encryption_version)

            # Transactional audit log insert
            audit = AuditLog(
                user_id=actor_id,
                action="secret_revealed",
                resource_type="OrderCustomerInput",
                resource_id=str(input_id),
                details=json.dumps({
                    "job_id": job_id,
                    "field_key": customer_input.field_key_snapshot,
                    "actor_label": actor_label
                }),
                ip_address=request.client.host
            )
            session.add(audit)
            try:
                await session.commit()
            except IntegrityError as e:
                logger.error(f"Audit failed to save: {e}")
                return JSONResponse({"ok": False, "error": "audit_failed", "message": "Action failed. Please try again."}, status_code=500)

            response = JSONResponse({"ok": True, "plaintext": plaintext})
            response.headers["Cache-Control"] = "no-store"
            return response
    except Exception as e:
        logger.error(f"api_reveal error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_reveal_interaction(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        interaction_id = int(request.path_params["interaction_id"])
        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            result = await session.execute(
                select(ManualOrderInteraction, ManualFulfillmentJob, Order)
                .join(ManualFulfillmentJob, ManualOrderInteraction.fulfillment_job_id == ManualFulfillmentJob.id)
                .join(OrderItem, ManualFulfillmentJob.order_item_id == OrderItem.id)
                .join(Order, OrderItem.order_id == Order.id)
                .filter(
                    ManualFulfillmentJob.id == job_id,
                    ManualOrderInteraction.id == interaction_id
                )
            )
            row = result.first()
            if not row:
                return JSONResponse({"ok": False, "error": "invalid_interaction", "message": "This reply cannot be displayed."}, status_code=400)

            interaction, job, order = row

            if interaction.direction != 'customer_to_admin' or interaction.kind != 'customer_reply' or not interaction.encrypted_content:
                return JSONResponse({"ok": False, "error": "invalid_interaction", "message": "This reply cannot be displayed."}, status_code=400)

            try:
                env = json.loads(interaction.encrypted_content)
                plaintext = decrypt_text(env["ciphertext"], env["version"])
            except Exception as e:
                logger.error(f"api_reveal_interaction decryption error: {e}")
                return JSONResponse({"ok": False, "error": "decryption_failed", "message": "This reply cannot be displayed."}, status_code=500)

            audit = AuditLog(
                user_id=actor_id,
                action="customer_reply_revealed",
                resource_type="ManualOrderInteraction",
                resource_id=str(interaction_id),
                details=json.dumps({
                    "job_id": job_id,
                    "order_id": order.public_id,
                    "interaction_id": interaction_id,
                    "actor_label": actor_label
                }),
                ip_address=request.client.host
            )
            session.add(audit)

            from datetime import datetime, timezone
            if interaction.read_at is None:
                interaction.read_at = datetime.now(timezone.utc)

            try:
                await session.commit()
            except IntegrityError as e:
                logger.error(f"Audit failed to save in reveal_interaction: {e}")
                await session.rollback()
                return JSONResponse({"ok": False, "error": "audit_failed", "message": "Action failed. Please try again."}, status_code=500)

            response = JSONResponse({
                "ok": True,
                "interaction_id": interaction_id,
                "reply": plaintext
            })
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

    except Exception as e:
        logger.error(f"api_reveal_interaction error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_start(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id, for_update=True)

            if not job or job.status not in ('queued', 'in_progress'):
                return JSONResponse({"ok": False, "error": "invalid_status", "message": "This order cannot be started from its current status."}, status_code=400)

            if job.status == 'in_progress':
                return JSONResponse({"ok": True, "status": job.status, "message": "Already in progress"})

            if job.status == 'queued':
                job.status = 'in_progress'
                job.started_at = func.now()
                job.started_by = actor_id

                job.order_item.fulfillment_status = 'processing'
                if job.order_item.order.status in ('pending', 'paid'):
                    job.order_item.order.status = 'processing'
                    job.order_item.order.processing_started_at = func.now()


                interaction = ManualOrderInteraction(
                    order_id=job.order_item.order_id,
                    fulfillment_job_id=job.id,
                    direction='system',
                    kind='status_change',
                    safe_preview="Processing started",
                    created_by=actor_id
                )
                session.add(interaction)

            status_to_return = job.status
            await session.commit()
            return JSONResponse({"ok": True, "status": status_to_return, "message": "Processing started"})
    except Exception as e:
        logger.error(f"api_start error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_resume(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id, for_update=True)
            if not job or job.status not in ('waiting_customer', 'in_progress'):
                return JSONResponse({"ok": False, "error": "invalid_state", "message": "Invalid state for resume"}, status_code=400)

            if job.status == 'waiting_customer':
                job.status = 'in_progress'

                interaction = ManualOrderInteraction(
                    order_id=job.order_item.order_id,
                    fulfillment_job_id=job.id,
                    direction='system',
                    kind='status_change',
                    safe_preview="Processing resumed",
                    created_by=actor_id
                )
                session.add(interaction)

            status_to_return = job.status
            await session.commit()
            return JSONResponse({"ok": True, "status": status_to_return})
    except Exception as e:
        logger.error(f"api_resume error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_message(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        data = await request.json()
        message_text = data.get("message")
        if not message_text:
            return JSONResponse({"ok": False, "error": "missing_message", "message": "Message text is required."}, status_code=400)

        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id)
            if not job:
                return JSONResponse({"ok": False, "error": "not_found", "message": "Job not found"}, status_code=404)

            telegram_id = job.order_item.order.user.telegram_id
            public_id = job.order_item.order.public_id

            full_message = f"💬 Message About Your Order\n\nOrder ID:\n{public_id}\n\n{message_text}"

            interaction = ManualOrderInteraction(
                order_id=job.order_item.order_id,
                fulfillment_job_id=job.id,
                direction='admin_to_customer',
                kind='message',
                safe_preview=message_text[:100],
                created_by=actor_id
            )
            session.add(interaction)
            await session.flush()

            import uuid
            notif = ManualOrderNotification(
                order_id=job.order_item.order_id,
                fulfillment_job_id=job.id,
                idempotency_key=f"msg_{interaction.id}_{uuid.uuid4().hex[:8]}",
                status='pending'
            )
            session.add(notif)

            try:
                await session.commit()
                outbox_dispatcher.wake_up()
            except Exception as e:
                logger.error(f"api_message commit error: {type(e).__name__} {e}")
                raise

            return JSONResponse({"ok": True, "notification_status": "pending", "message": "Message queued for delivery."})
    except Exception as e:
        logger.error(f"api_message error: {type(e).__name__} {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_request_verification(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        data = await request.json()
        message_text = data.get("message")
        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id, for_update=True)
            if not job or job.status not in ('in_progress', 'waiting_customer'):
                return JSONResponse({"ok": False, "error": "invalid_state", "message": "Invalid state"}, status_code=400)

            if job.status == 'in_progress':
                telegram_id = job.order_item.order.user.telegram_id

                interaction = ManualOrderInteraction(
                    order_id=job.order_item.order_id,
                    fulfillment_job_id=job.id,
                    direction='admin_to_customer',
                    kind='verification_request',
                    safe_preview=message_text[:100] if message_text else "Verification required",
                    created_by=actor_id
                )
                session.add(interaction)

                # Change status
                job.status = 'waiting_customer'
                job.waiting_customer_at = func.now()

                await session.flush()

                import uuid
                notif = ManualOrderNotification(
                    order_id=job.order_item.order_id,
                    fulfillment_job_id=job.id,
                    idempotency_key=f"verify_{interaction.id}_{uuid.uuid4().hex[:8]}",
                    status='pending'
                )
                session.add(notif)
            status_to_return = job.status
            await session.commit()
            if job.status == 'waiting_customer':
                outbox_dispatcher.wake_up()
            return JSONResponse({"ok": True, "status": status_to_return})
    except Exception as e:
        logger.error(f"api_request_verification error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_complete(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])
        data = await request.json()
        completion_note = data.get("note", "")
        actor_id, actor_label = resolve_admin_actor(request)

        async with Database().session() as session:
            job = await load_fulfillment_job(session, job_id, for_update=True)
            if not job:
                return JSONResponse({"ok": False, "error": "not_found", "message": "Not found"}, status_code=404)

            if job.status == 'completed':
                return JSONResponse({"ok": True, "status": job.status})

            job.status = 'completed'
            job.completed_at = func.now()
            job.completed_by = actor_id

            job.order_item.status = 'completed'

            # Check if all order items are completed to complete the order
            all_completed = True
            order_items_result = await session.execute(
                select(OrderItem).filter(OrderItem.order_id == job.order_item.order_id)
            )
            order_items = order_items_result.scalars().all()

            for oi in order_items:
                if oi.id != job.order_item.id and oi.status != 'completed':
                    all_completed = False
                    break
            if all_completed:
                job.order_item.order.status = 'completed'

            interaction = ManualOrderInteraction(
                order_id=job.order_item.order_id,
                fulfillment_job_id=job.id,
                direction='system',
                kind='completion',
                safe_preview=completion_note[:200] if completion_note else "Order completed",
                created_by=actor_id
            )
            session.add(interaction)
            await session.flush()

            # Close active conversation session
            from bot.database.models.main import ManualOrderConversationSession
            active_sessions = await session.execute(
                select(ManualOrderConversationSession).filter(
                    ManualOrderConversationSession.order_id == job.order_item.order_id,
                    ManualOrderConversationSession.status == 'active'
                )
            )
            for s in active_sessions.scalars():
                s.status = 'closed'
                s.closed_at = func.now()


            # Outbox notification
            import uuid
            notif = ManualOrderNotification(
                order_id=job.order_item.order_id,
                fulfillment_job_id=job.id,
                idempotency_key=f"comp_{interaction.id}_{uuid.uuid4().hex[:8]}",
                status='pending'
            )
            session.add(notif)
            await session.flush()

            # The OutboxDispatcher handles sending it.
            status_to_return = job.status
            await session.commit()
            outbox_dispatcher.wake_up()
            return JSONResponse({"ok": True, "status": status_to_return})
    except Exception as e:
        logger.error(f"api_complete error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

async def api_retry_notification(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized", "message": "Unauthorized"}, status_code=401)

    try:
        job_id = int(request.path_params["id"])

        async with Database().session() as session:
            # Step 1: lock base row
            lock_stmt = select(ManualOrderNotification.id).filter(
                ManualOrderNotification.fulfillment_job_id == job_id,
                ManualOrderNotification.status == 'failed'
            ).with_for_update()

            notif_id_res = await session.execute(lock_stmt)
            notif_id = notif_id_res.scalar_first()
            if not notif_id:
                return JSONResponse({"ok": False, "error": "not_found", "message": "No failed notifications found"}, status_code=404)

            # Step 2: load full graph
            result = await session.execute(
                select(ManualOrderNotification)
                .options(joinedload(ManualOrderNotification.order).joinedload(Order.user), joinedload(ManualOrderNotification.job).joinedload(ManualFulfillmentJob.order_item))
                .filter(ManualOrderNotification.id == notif_id)
            )
            notif = result.scalars().first()
            if not notif:
                return JSONResponse({"ok": False, "error": "not_found", "message": "No failed notifications found"}, status_code=404)

            notif.status = 'pending'
            notif.attempts = 0
            notif.next_attempt_at = func.now()

            await session.commit()
            outbox_dispatcher.wake_up()
            return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"api_retry_notification error: {e}")
        return JSONResponse({"ok": False, "error": "server_error", "message": "Action failed. Please try again."}, status_code=500)

fulfillment_routes = [
    Route("/admin/fulfillment", fulfillment_dashboard, methods=["GET"]),
    Route("/admin/fulfillment/api/queue", api_queue, methods=["GET"]),
    Route("/admin/fulfillment/order/{id:int}", fulfillment_workspace, methods=["GET"]),
    Route("/admin/fulfillment/api/order/{id:int}/reveal", api_reveal, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/interaction/{interaction_id:int}/reveal", api_reveal_interaction, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/state", api_workspace_state, methods=["GET"]),
    Route("/admin/fulfillment/api/order/{id:int}/start", api_start, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/resume", api_resume, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/message", api_message, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/request-verification", api_request_verification, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/complete", api_complete, methods=["POST"]),
    Route("/admin/fulfillment/api/order/{id:int}/retry-notification", api_retry_notification, methods=["POST"])
]
