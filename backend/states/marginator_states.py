from aiogram.fsm.state import State, StatesGroup


class CalcState(StatesGroup):
    select_mode = State()          # Выбор режима (Маркетплейс / B2B)
    upload_file = State()          # Ожидание загрузки файла
    input_commission = State()     # Ввод комиссии маркетплейса (%)
    input_logistics = State()      # Ввод стоимости логистики на единицу (₽)
    input_packaging = State()      # Ввод стоимости упаковки на единицу (₽)
    input_tax = State()            # Ввод налоговой ставки (%)
    # B2B-параметры
    input_freight = State()        # Фрахт / доставка на единицу (₽)
    input_manager_bonus = State()  # Бонус менеджера (%)
    confirm_params = State()       # Подтверждение и запуск расчета
