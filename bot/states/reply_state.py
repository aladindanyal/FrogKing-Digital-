from aiogram.fsm.state import State, StatesGroup

class OrderReplyState(StatesGroup):
    waiting_for_reply = State()
