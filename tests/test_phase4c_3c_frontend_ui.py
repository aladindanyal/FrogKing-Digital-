import pytest
import re
from pathlib import Path

TEMPLATE_PATH = Path("bot/web/templates/fulfillment/workspace.html")

def test_frontend_ui_requirements():
    assert TEMPLATE_PATH.exists(), "workspace.html does not exist"

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Customer Conversation has an independent scroll container
    assert 'class="conversation-panel"' in content, "conversation-panel class missing"
    assert 'class="conversation-messages"' in content, "conversation-messages class missing"
    assert 'overflow-y: auto' in content, "No overflow-y auto for independent scrolling"

    # 2. Composer is outside the dynamically replaced messages container
    composer_index = content.find('class="conversation-composer"')
    messages_index = content.find('id="conversation-container"')
    assert composer_index > messages_index, "Composer should be below the messages container"

    # 3. Composer remains sticky
    assert 'position: sticky;' in content and 'bottom: 0;' in content, "Composer must be sticky"

    # 4. Messages have stable identifiers
    assert 'data-interaction-id' in content, "Stable data-interaction-id missing on messages"

    # 5. Admin messages align right, Customer left
    assert 'align-self: flex-end;' in content, "Admin message missing align-self right"
    assert 'align-self: flex-start;' in content, "Customer message missing align-self left"

    # 6. Bubbles have max-width
    assert 'max-width: 72%;' in content, "Message bubbles missing max-width 72%"

    # 7. Long text wraps safely
    assert 'overflow-wrap: anywhere;' in content, "Missing overflow-wrap: anywhere"
    assert 'white-space: pre-wrap;' in content, "Missing white-space: pre-wrap"

    # 8. dir="auto" is used
    assert 'dir="auto"' in content, "Missing dir=auto"

    # 9. No innerHTML for message content
    assert '.textContent =' in content, "Must use textContent for dynamic message insertion to avoid XSS"

    # 10. Initial load scrolls to bottom
    assert 'scrollConversationToBottom(false);' in content, "Initial load scroll not found"

    # 11. Unchanged polling state does not trigger scrolling
    assert 'lastConversationMessageId' in content, "Missing duplicate scroll prevention logic"

    # 12. New messages overlay no longer exists
    assert 'btn-scroll-down' not in content, "btn-scroll-down still exists"

    # 13. Remove Reply to Customer from message cards
    assert 'data-action="reply-to-customer"' not in content, "Reply to Customer button still exists"

    # 14. Collapse Operational Timeline & Notifications
    assert '<details class="fc-panel" id="timeline-accordion"' in content, "Timeline is not a details element"
    assert '<details class="fc-panel" id="notifications-panel"' in content, "Notifications is not a details element"

    # Ensure they start collapsed (no 'open' attribute on details by default)
    assert '<details class="fc-panel" id="timeline-accordion" open' not in content, "Timeline should start collapsed"
    assert '<details class="fc-panel" id="notifications-panel" style="padding: 0; {% if not job.notifications %}display:none;{% endif %}" open' not in content, "Notifications should start collapsed"

    # 15. Increase Conversation Inbox height
    assert 'clamp(560px, 66vh, 760px)' in content, "Conversation panel missing correct clamp height"

    # 16. Username inspection and safe display
    # The requirement: "Customer username appears only when genuinely stored"
    # "Customer: ID: 1521963946"
    assert 'ID: {{ job.telegram_id }}' in content, "Missing Customer ID fallback format"
    assert 'User {{ job.telegram_id }}' not in content, "Legacy User format still present"
