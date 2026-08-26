from aiogram.fsm.state import State, StatesGroup


class CalcState(StatesGroup):
    select_mode = State()
    upload_file = State()
    select_price_col = State()   # выбор ступени B2B / колонки цены
    input_commission = State()
    input_logistics = State()
    input_packaging = State()
    input_tax = State()
    input_freight = State()
    input_manager_bonus = State()
    input_target_margin = State()  # целевая маржинальность %
    confirm_params = State()
    save_preset_name = State()   # имя пресета
