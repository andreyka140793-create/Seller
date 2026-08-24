import hashlib
import hmac
import json
from urllib.parse import parse_qsl

def verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет подлинность строки initData от Telegram WebApp.
    Возвращает распарсенный словарь данных или None, если подпись подделана.
    """
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
            
        hash_to_check = parsed_data.pop("hash")
        
        # Сортировка по алфавиту и формирование контрольной строки
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed_data.items(), key=lambda x: x[0])
        )
        
        # Генерация секретного ключа из токена бота
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()
        
        # Сравнение хэшей
        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
        
        if hmac.compare_digest(calculated_hash, hash_to_check):
            if "user" in parsed_data:
                parsed_data["user"] = json.loads(parsed_data["user"])
            return parsed_data
        return None
    except Exception:
        return None
