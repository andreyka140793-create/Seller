"""
Точка входа для запуска на Amvera (и в любом другом окружении, где
корень репозитория и папка backend/ не совпадают).

Почему этот файл существует:
Флаг uvicorn --app-dir в некоторых окружениях (в частности, судя по всему,
на Amvera Python/pip) не добавляет вложенную папку backend/ в sys.path
так, как ожидается, из-за чего внутренние импорты вида
"from services.marginator.utils import ..." падают с
ModuleNotFoundError: No module named 'services'.

Вместо того чтобы полагаться на поведение CLI-флага, мы явно добавляем
путь к backend/ в sys.path programmatically, до импорта приложения.
Это гарантированно работает независимо от того, как именно платформа
вызывает python3 run.py.

Настройка в Amvera:
  run:
    scriptName: run.py
    containerPort: 8000
(поле run.command при этом должно быть пустым)
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import uvicorn  # noqa: E402  (импорт после правки sys.path — так и задумано)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
