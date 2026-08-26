from aiogram.fsm.state import State, StatesGroup


class CalcState(StatesGroup):
    select_mode = State()
    upload_file = State()
    confirm_mapping = State()
    map_pick_field = State()
    map_pick_column = State()
    select_price_col = State()
    input_commission = State()
    input_logistics = State()
    input_packaging = State()
    input_tax = State()
    input_freight = State()
    input_manager_bonus = State()
    input_fx_rate = State()          # курс: USD 95 / 95 / CNY 13
    input_target_margin = State()
    confirm_params = State()
    save_preset_name = State()
    compare_upload_a = State()
    compare_upload_b = State()
