from typing import Optional, List, Tuple
from sqlalchemy import select, func, text, update
from sqlalchemy.orm import joinedload
from bot.database.main import Database
from bot.database.models.main import BroadcastCampaign, BroadcastRecipient, User
from bot.database.methods.audit import log_audit
from bot.i18n.registry import get_enabled_locales

async def validate_payload(text_content: str, photo_file_id: Optional[str], target_locale: Optional[str]) -> Tuple[bool, str, str]:
    from bot.misc import sanitize_html
    if not text_content and not photo_file_id:
        return False, "Payload cannot be completely empty.", ""
    if photo_file_id and len(text_content) > 1024:
        return False, 'Caption exceeds maximum length of 1024 characters.', ''
    if len(text_content) > 4000:
        return False, "Text payload exceeds maximum length of 4000 characters.", ""
    if target_locale and target_locale not in get_enabled_locales():
        return False, f"Invalid target locale: {target_locale}", ""
    safe_text = sanitize_html(text_content)
    return True, "", safe_text

async def estimate_audience(target_locale: Optional[str]) -> int:
    async with Database().session() as session:
        query = select(func.count()).select_from(User).where(User.is_blocked.is_(False))
        if target_locale:
            query = query.where(User.language_code == target_locale)
        return (await session.execute(query)).scalar()

async def create_draft(admin_id: int, target_locale: Optional[str], message_text: str, photo_file_id: Optional[str]) -> BroadcastCampaign:
    async with Database().session() as session:
        campaign = BroadcastCampaign(
            admin_id=admin_id,
            target_locale=target_locale,
            message_text=message_text,
            photo_file_id=photo_file_id,
            parse_mode="HTML",
            status="draft"
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        await log_audit("broadcast_draft_created", user_id=admin_id, resource_id=str(campaign.id))
        return campaign

async def confirm_campaign(campaign_id: int) -> Tuple[bool, str, Optional[BroadcastCampaign], int]:
    async with Database().session() as session:
        query = select(BroadcastCampaign).where(BroadcastCampaign.id == campaign_id).with_for_update()
        campaign = (await session.execute(query)).scalar_one_or_none()
        if not campaign:
            return False, "Campaign not found.", None, 0
        if campaign.status != "draft":
            return False, "Campaign is not in draft state.", campaign, 0
        active_count = (await session.execute(
            select(func.count()).select_from(BroadcastCampaign).where(BroadcastCampaign.status.in_(['confirmed', 'running']))
        )).scalar()
        if active_count > 0:
            return False, "Another campaign is currently active.", campaign, 0
        user_query = select(User.telegram_id).where(User.is_blocked.is_(False))
        if campaign.target_locale:
            user_query = user_query.where(User.language_code == campaign.target_locale)
        users = (await session.execute(user_query)).scalars().all()
        recipients = [BroadcastRecipient(campaign_id=campaign.id, user_id=uid) for uid in users]
        session.add_all(recipients)
        campaign.status = "confirmed"
        await session.commit()
        await log_audit("broadcast_confirmed", user_id=campaign.admin_id, resource_id=str(campaign.id), details=f"recipients={len(users)}")
        return True, "", campaign, len(users)

async def cancel_campaign(campaign_id: int, admin_id: int) -> Tuple[bool, str]:
    async with Database().session() as session:
        query = select(BroadcastCampaign).where(BroadcastCampaign.id == campaign_id).with_for_update()
        campaign = (await session.execute(query)).scalar_one_or_none()
        if not campaign:
            return False, "Campaign not found."
        if campaign.status in ["completed", "cancelled", "failed"]:
            return False, "Campaign is already finished."
        campaign.status = "cancelled"
        await session.execute(
            update(BroadcastRecipient)
            .where(BroadcastRecipient.campaign_id == campaign_id, BroadcastRecipient.status == 'pending')
            .values(status='cancelled', updated_at=func.now())
        )
        await session.commit()
        await log_audit("broadcast_cancelled", user_id=admin_id, resource_id=str(campaign.id))
        return True, ""

async def get_campaign_stats(campaign_id: int) -> dict:
    async with Database().session() as session:
        result = await session.execute(
            select(BroadcastRecipient.status, func.count())
            .where(BroadcastRecipient.campaign_id == campaign_id)
            .group_by(BroadcastRecipient.status)
        )
        stats = {row[0]: row[1] for row in result.all()}
        total = sum(stats.values())
        return {
            "total": total,
            "pending": stats.get("pending", 0),
            "sending": stats.get("sending", 0),
            "sent": stats.get("sent", 0),
            "failed": stats.get("failed", 0),
            "blocked": stats.get("blocked", 0),
            "cancelled": stats.get("cancelled", 0),
            "uncertain": stats.get("uncertain", 0),
        }
