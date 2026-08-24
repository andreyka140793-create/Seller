from aiogram.fsm.state import State, StatesGroup

class CalcState(StatesGroup):
    select_mode = State()         # Выбор режима (Маркетплейс / B2B)
    upload_file = State()         # Ожидание загрузки файла
    input_commission = State()    # Ввод комиссии маркетплейса (%)
    input_logistics = State()     # Ввод стоимости логистики на единицу (₽)
    input_tax = State()           # Ввод налоговой ставки (%)
    confirm_params = State()      # Подтверждение и запуск расчета
