import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Dict, Any
import logging

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models.main import CheckoutIntakeDraft, ProductCustomerField, Goods
from bot.misc import encryption
from bot.misc.env import EnvKeys
from bot.misc.intake_validator import compute_schema_fingerprint
from bot.misc.utils import ensure_utc


class DraftServiceError(Exception):
    pass


async def get_or_create_draft(
    session: AsyncSession,
    user_id: int,
    goods_id: int,
    quantity: int,
    active_fields: List[ProductCustomerField]
) -> Tuple[CheckoutIntakeDraft, Optional[str]]:
    """
    Gets a pending draft or creates a new one.
    Invalidates any existing draft if quantity or schema fingerprint changed.
    Handles the zero-active-fields edgecase by creating an empty encrypted payload.
    """
    fingerprint = compute_schema_fingerprint(active_fields)
    
    # 1. Look for existing pending draft
    stmt = select(CheckoutIntakeDraft).where(
        CheckoutIntakeDraft.user_id == user_id,
        CheckoutIntakeDraft.goods_id == goods_id,
        CheckoutIntakeDraft.status == 'pending'
    )
    result = await session.execute(stmt)
    existing_draft = result.scalar_one_or_none()
    
    now = datetime.now(timezone.utc)
    
    incompatible_reason = None
    
    if existing_draft:
        # Check expiry
        if ensure_utc(existing_draft.expires_at) <= now:
            existing_draft.status = 'expired'
            existing_draft.invalidated_at = now
            incompatible_reason = 'expired'
            session.add(existing_draft)
            existing_draft = None
        # Check quantity and fingerprint
        elif existing_draft.quantity != quantity or existing_draft.schema_fingerprint != fingerprint:
            existing_draft.status = 'invalidated'
            existing_draft.invalidated_at = now
            incompatible_reason = 'mismatched'
            session.add(existing_draft)
            existing_draft = None
        else:
            # Validate payload and answer structure
            try:
                payload = encryption.decrypt_json(existing_draft.encrypted_payload, existing_draft.encryption_version)
                if not isinstance(payload, dict):
                    raise ValueError("Payload must be a dict")
                answers = payload.get("answers")
                if not isinstance(answers, list):
                    raise ValueError("Answers must be a list")
                if existing_draft.current_step != len(answers):
                    raise ValueError("current_step does not match actual answers length")
            except Exception as e:
                import traceback
                print(f"Payload validation failed: {e}")
                traceback.print_exc()
                # Treat payload/structure mismatch as invalidated
                existing_draft.status = 'invalidated'
                existing_draft.invalidated_at = now
                incompatible_reason = f'mismatched: {e}'
                session.add(existing_draft)
                existing_draft = None
            
    if existing_draft:
        return existing_draft, None
        
    # 2. Create new draft
    public_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=EnvKeys.CHECKOUT_INTAKE_DRAFT_TTL_HOURS)
    
    initial_payload = {
        "schema_fingerprint": fingerprint,
        "answers": []
    }
    
    # Needs encryption
    encrypted_dict = encryption.encrypt_json(initial_payload)
    
    new_draft = CheckoutIntakeDraft(
        public_token=public_token,
        user_id=user_id,
        goods_id=goods_id,
        quantity=quantity,
        status='pending',
        schema_fingerprint=fingerprint,
        encrypted_payload=encrypted_dict['ciphertext'],
        encryption_version=encrypted_dict['version'],
        current_step=0,
        expires_at=expires_at
    )
    
    session.add(new_draft)
    await session.flush()  # to get draft.id if needed
    
    return new_draft, incompatible_reason


async def get_draft_by_token(session: AsyncSession, public_token: str, user_id: int) -> Optional[CheckoutIntakeDraft]:
    """Retrieves a pending draft by token and enforces ownership."""
    stmt = select(CheckoutIntakeDraft).where(
        CheckoutIntakeDraft.public_token == public_token,
        CheckoutIntakeDraft.user_id == user_id,
        CheckoutIntakeDraft.status == 'pending'
    )
    result = await session.execute(stmt)
    draft = result.scalar_one_or_none()
    
    if draft and ensure_utc(draft.expires_at) <= datetime.now(timezone.utc):
        draft.status = 'expired'
        draft.invalidated_at = datetime.now(timezone.utc)
        session.add(draft)
        await session.flush()
        return None
        
    return draft


def get_expected_steps(active_fields: List[ProductCustomerField], quantity: int) -> List[Dict[str, Any]]:
    """
    Calculates the expected sequence of questions.
    Ordered by: all per_unit fields grouped by unit, then all per_order fields.
    """
    sorted_fields = sorted(active_fields, key=lambda f: (f.sort_order, f.id))
    
    per_unit_fields = [f for f in sorted_fields if f.scope == 'per_unit']
    per_order_fields = [f for f in sorted_fields if f.scope == 'per_order']
    
    steps = []
    
    for unit_idx in range(1, quantity + 1):
        for f in per_unit_fields:
            steps.append({
                "field": f,
                "unit_index": unit_idx
            })
            
    for f in per_order_fields:
        steps.append({
            "field": f,
            "unit_index": 0
        })
        
    return steps


async def save_draft_answer(
    session: AsyncSession, 
    draft: CheckoutIntakeDraft, 
    step_info: Dict[str, Any], 
    value: str
) -> None:
    """
    Decrypts the payload, updates/adds the answer for the current step,
    encrypts it back, and advances current_step.
    """
    payload = encryption.decrypt_json(draft.encrypted_payload, draft.encryption_version)
    
    field: ProductCustomerField = step_info["field"]
    unit_index: int = step_info["unit_index"]
    
    # Ensure answer doesn't already exist or replace if it does
    answers = payload.get("answers", [])
    
    # Filter out any existing answer for this field/unit combo just in case
    answers = [a for a in answers if not (a["field_key"] == field.field_key and a["unit_index"] == unit_index)]
    
    answers.append({
        "field_id": field.id,
        "field_key": field.field_key,
        "field_type": field.field_type,
        "scope": field.scope,
        "label_i18n": field.label_i18n,
        "is_sensitive": field.is_sensitive,
        "unit_index": unit_index,
        "value": value
    })
    
    payload["answers"] = answers
    
    encrypted_dict = encryption.encrypt_json(payload)
    draft.encrypted_payload = encrypted_dict['ciphertext']
    draft.encryption_version = encrypted_dict['version']
    draft.current_step += 1
    
    session.add(draft)
    await session.flush()
