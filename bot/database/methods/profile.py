from datetime import datetime, timezone
import logging

from bot.database.main import Database
from bot.database.models.main import User

logger = logging.getLogger(__name__)

def normalize_profile(username: str | None, first_name: str | None, last_name: str | None) -> tuple[str | None, str | None, str | None]:
    if username and username.strip():
        username = username.strip()
        if username.startswith('@'):
            username = username[1:]
        username = username[:64]
    else:
        username = None

    if first_name and first_name.strip():
        first_name = first_name.strip()[:255]
    else:
        first_name = None

    if last_name and last_name.strip():
        last_name = last_name.strip()[:255]
    else:
        last_name = None

    return username, first_name, last_name

async def sync_telegram_user_profile(
    telegram_id: int, 
    username: str | None, 
    first_name: str | None, 
    last_name: str | None
) -> None:
    """
    Syncs the incoming Telegram user's profile metadata into the database.
    Updates only if there's a difference to minimize write ops.
    """
    if not telegram_id:
        return

    try:
        username, first_name, last_name = normalize_profile(username, first_name, last_name)

        async with Database().session() as session:
            db_user = await session.get(User, telegram_id)
            if not db_user:
                return

            changed_fields = []
            
            if db_user.telegram_username != username:
                db_user.telegram_username = username
                changed_fields.append("telegram_username")
            
            if db_user.first_name != first_name:
                db_user.first_name = first_name
                changed_fields.append("first_name")
            
            if db_user.last_name != last_name:
                db_user.last_name = last_name
                changed_fields.append("last_name")

            if changed_fields:
                db_user.profile_updated_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"Profile updated for user {telegram_id}: {', '.join(changed_fields)}")

    except Exception as e:
        logger.error(f"Failed to sync profile for user {telegram_id}: {e}")
