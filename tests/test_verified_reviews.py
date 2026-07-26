import pytest
import pytest_asyncio
from decimal import Decimal
from sqlalchemy import select
from bot.database.models.main import Reviews, OrderItem, Order, Goods, User
from bot.database.methods.create import create_review
from bot.database.methods.read import get_eligible_order_item_for_review, get_global_reviews, get_review_by_order_item
from bot.database.methods.lazy_queries import query_item_reviews

@pytest.fixture
async def db_session():
    from bot.database.main import Database
    db = Database()
    async with db.session() as session:
        yield session

@pytest.fixture
async def fake_item_for_review(user_factory, item_factory):
    import uuid
    test_user = await user_factory(telegram_id=999991)
    goods = await item_factory(name="Test Review Item", price=10)
    
    from bot.database.models.main import Order, OrderItem
    from bot.database.main import Database
    db = Database()
    async with db.session() as db_session:
        order = Order(
            public_id=str(uuid.uuid4()),
            user_id=test_user['telegram_id'],
            status="completed",
            total=10,
            currency="USD"
        )
        db_session.add(order)
        await db_session.flush()

        order_item = OrderItem(
            order_id=order.id,
            item_id=goods.id,
            product_name_snapshot=goods.name,
            quantity=1,
            unit_price=10,
            subtotal=10,
            total=10,
            fulfillment_status="delivered"
        )
        db_session.add(order_item)
        await db_session.commit()
        
        return order_item, order, goods, test_user

@pytest.mark.asyncio
async def test_get_eligible_order_item_for_review(db_session, fake_item_for_review):
    order_item, order, goods, test_user = fake_item_for_review
    
    # Should be eligible
    record = await get_eligible_order_item_for_review(test_user['telegram_id'], order_item.id)
    assert record is not None
    assert record[0].id == order_item.id
    assert record[1].id == order.id
    assert record[2].id == goods.id

    # Wrong user
    record = await get_eligible_order_item_for_review(999999, order_item.id)
    assert record is None
    
    # Unpaid order
    order.status = "pending"
    db_session.add(order)
    await db_session.commit()
    
    record = await get_eligible_order_item_for_review(test_user['telegram_id'], order_item.id)
    assert record is None

@pytest.mark.asyncio
async def test_create_review(db_session, fake_item_for_review):
    order_item, order, goods, test_user = fake_item_for_review
    
    review_id = await create_review(
        test_user['telegram_id'],
        goods.id,
        order.id,
        order_item.id,
        goods.name,
        5,
        "Great product!"
    )
    assert review_id is not None
    
    # Check it's pending
    review = await get_review_by_order_item(order_item.id)
    assert review is not None
    assert review.status == "pending"
    assert review.rating == 5
    assert review.comment == "Great product!"

    # Duplicate should fail
    duplicate_id = await create_review(
        test_user['telegram_id'],
        goods.id,
        order.id,
        order_item.id,
        goods.name,
        4,
        "Great product!"
    )
    assert duplicate_id is None

@pytest.mark.asyncio
async def test_global_reviews_visibility(db_session, fake_item_for_review):
    order_item, order, goods, test_user = fake_item_for_review
    
    review_id = await create_review(
        test_user['telegram_id'], goods.id, order.id, order_item.id, goods.name, 5, "Nice"
    )
    
    # Not visible when pending
    reviews, total = await get_global_reviews()
    assert total == 0
    
    # Make it approved
    review = await db_session.get(Reviews, review_id)
    review.status = "approved"
    db_session.add(review)
    await db_session.commit()
    
    reviews, total = await get_global_reviews()
    assert total == 1
    assert reviews[0][0].id == review_id
    assert reviews[0][1] == test_user.get('first_name')
    assert reviews[0][2] == test_user.get('last_name')
    assert reviews[0][3] == test_user.get('telegram_username')


@pytest.mark.asyncio
async def test_submit_review_handler(db_session, fake_item_for_review):
    from bot.handlers.user.shop_and_goods import submit_review_handler
    order_item, order, goods, test_user = fake_item_for_review

    class MockUser:
        id = test_user['telegram_id']

    class MockMessage:
        async def edit_text(self, text, reply_markup=None, **kwargs):
            self.text = text
        async def answer(self, text, reply_markup=None, **kwargs):
            self.text = text

    class MockBot:
        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            pass

    class MockCall:
        def __init__(self):
            self.from_user = MockUser()
            self.message = MockMessage()
            self.bot = MockBot()
            self.data = "submit_review"
            self.answered = False
            self.answered_text = None

        async def answer(self, text=None, show_alert=False, **kwargs):
            self.answered = True
            self.answered_text = text

    class MockState:
        def __init__(self, data):
            self._data = data
            self.cleared = False
        async def get_data(self):
            return self._data
        async def clear(self):
            self.cleared = True

    # 1. Successful submission
    state_data = {
        'review_order_item_id': order_item.id,
        'review_product_id': goods.id,
        'review_order_id': order.id,
        'review_product_name': goods.name,
        'review_rating': 5,
        'review_text': "Great!"
    }
    call = MockCall()
    state = MockState(state_data)

    await submit_review_handler(call, state)

    assert state.cleared is True
    # Review should be inserted in pending status
    review = await get_review_by_order_item(order_item.id)
    assert review is not None
    assert review.status == "pending"
    assert review.rating == 5
    assert review.comment == "Great!"
    assert review.product_id == goods.id

    # 2. Duplicate submission idempotency
    call2 = MockCall()
    state2 = MockState(state_data)
    await submit_review_handler(call2, state2)
    assert state2.cleared is False
    # Should say already exists
    assert "Review already exists." in call2.answered_text


@pytest.fixture
def mock_bot():
    from aiogram import Bot
    class MockBot(Bot):
        def __init__(self):
            super().__init__(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
            self.sent_messages = []
            self.edited_messages = []
            self.answered_callbacks = []
        async def __call__(self, method, *args, **kwargs):
            method_name = method.__class__.__name__
            if method_name == 'SendMessage':
                self.sent_messages.append(method)
                from aiogram.types import Message, Chat
                from datetime import datetime, timezone
                return Message(message_id=999, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))
            elif method_name == 'EditMessageText':
                self.edited_messages.append(method)
                from aiogram.types import Message, Chat
                from datetime import datetime, timezone
                return Message(message_id=999, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))
            elif method_name == 'AnswerCallbackQuery':
                self.answered_callbacks.append(method)
                return True
            return True
    return MockBot()

@pytest.mark.asyncio
async def test_full_review_flow_dispatcher(test_dp, mock_bot, fake_item_for_review):
    import os
    os.environ["REVIEWS_ENABLED"] = "1"
    
    order_item, order, goods, test_user = fake_item_for_review
    user_id = test_user['telegram_id']
    
    from aiogram.types import Update, CallbackQuery, Message, Chat, User as AiogramUser
    from datetime import datetime, timezone
    
    aiogram_user = AiogramUser(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    msg = Message(message_id=1001, date=datetime.now(timezone.utc), chat=chat, from_user=aiogram_user, text="Msg")
    
    # 1. Start Review
    call = CallbackQuery(
        id="c1",
        from_user=aiogram_user,
        chat_instance="chat1",
        message=msg,
        data=f"review:start:{order_item.id}:p:{order.id}"
    )
    update = Update(update_id=1, callback_query=call)
    await test_dp.feed_update(mock_bot, update)
    
    # 2. Rate 5 stars
    call = CallbackQuery(
        id="c2",
        from_user=aiogram_user,
        chat_instance="chat1",
        message=msg,
        data="review:rate:5"
    )
    update = Update(update_id=2, callback_query=call)
    await test_dp.feed_update(mock_bot, update)
    
    # 3. Enter Text
    msg2 = Message(
        message_id=1002,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=aiogram_user,
        text="Absolutely amazing product!"
    )
    update = Update(update_id=3, message=msg2)
    await test_dp.feed_update(mock_bot, update)
    
    # 4. Submit
    call = CallbackQuery(
        id="c4",
        from_user=aiogram_user,
        chat_instance="chat1",
        message=msg,
        data="review:submit"
    )
    update = Update(update_id=4, callback_query=call)
    await test_dp.feed_update(mock_bot, update)
    
    # 5. Verify it's saved
    from bot.database.methods.read import get_review_by_order_item
    review = await get_review_by_order_item(order_item.id)
    assert review is not None
    assert review.rating == 5
    assert review.comment == "Absolutely amazing product!"
    assert review.status == "pending"

