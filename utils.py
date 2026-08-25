import pandas as pd

def clean_numeric_value(value: str | int | float | None) -> float:
    """
    Преобразует произвольные данные ячейки в валидный float.
    Обрабатывает форматы: '1 500,50 ₽', '1000,00', None, NaN, 'N/A'.
    """
    if value is None or pd.isna(value):
        return 0.0
    
    if isinstance(value, (int, float)):
        return float(value)
    
    # Очистка строки от валютных символов и разделителей тысяч
    cleaned = (
        str(value)
        .replace(" ", "")
        .replace("\xa0", "")  # Неразрывный пробел
        .replace("₽", "")
        .replace("$", "")
        .replace(",", ".")
        .strip()
    )
    
    try:
        val = float(cleaned)
        return max(0.0, val)
    except ValueError:
        return 0.0
