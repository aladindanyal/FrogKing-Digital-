from aiogram.filters.state import StatesGroup, State


class BroadcastFSM(StatesGroup):
    """FSM state for the broadcast message"""
    waiting_target = State()
    waiting_message = State()
    waiting_confirmation = State()
