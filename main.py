import os
import json
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
from retailcrm_api import get_order_by_id, get_order_history_by_dates, create_task, update_order_comment
from openai_processor import analyze_comment_with_openai

load_dotenv()

# Устанавливаем часовой пояс Москвы
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
MARKER = '✅'


def get_time_window_and_timezone() -> tuple:
    """
    Определяет временное окно для анализа заказов в зависимости от текущего времени по МСК.
    Возвращает начальную и конечную дату в формате UTC.
    """
    now_msk = datetime.now(MOSCOW_TZ)
    now_utc = datetime.now(pytz.utc)

    # Запуск в 12:00
    # Окно: с 20:00 (предыдущий день) до 11:59 (текущий день)
    if now_msk.hour == 12:
        start_msk = now_msk.replace(hour=20, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end_msk = now_msk.replace(hour=11, minute=59, second=59, microsecond=999999)
        if now_msk.minute < 30:  # Запас времени на запуск
            start_msk -= timedelta(minutes=1)

    # Запуск в 20:00
    # Окно: с 12:00 до 19:59 (текущий день)
    elif now_msk.hour == 20:
        start_msk = now_msk.replace(hour=12, minute=0, second=0, microsecond=0)
        end_msk = now_msk.replace(hour=19, minute=59, second=59, microsecond=999999)
        if now_msk.minute < 30:  # Запас времени на запуск
            start_msk -= timedelta(minutes=1)

    else:
        # Если скрипт запускается не в 12 или 20 часов, возвращаем пустой диапазон
        return None, None

    # Конвертируем временные окна в формат UTC для API
    start_utc = start_msk.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    end_utc = end_msk.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

    return start_utc, end_utc


def extract_last_entry(comment: str) -> str:
    """
    Извлекает последнюю запись из комментария менеджера.
    """
    lines = comment.strip().split('\n')
    last_entry = ""
    for line in reversed(lines):
        if line.strip():
            last_entry = line.strip()
            break
    return last_entry


def process_order(order_data: dict):
    """
    Обрабатывает один заказ: анализирует последнюю запись комментария и создает задачи.
    """
    order_id = order_data.get('id')
    operator_comment = order_data.get('managerComment', '')
    manager_id = order_data.get('managerId')

    print(f"Обработка заказа ID: {order_id}")

    if not operator_comment:
        print(f"  В заказе {order_id} нет комментария менеджера. Пропускаем.")
        return

    if not manager_id:
        print(f"  В заказе {order_id} не указан ответственный менеджер. Пропускаем.")
        return

    # Извлекаем последнюю запись для анализа
    last_entry_to_analyze = extract_last_entry(operator_comment)

    # Проверяем, был ли комментарий уже обработан
    if last_entry_to_analyze.strip().endswith(MARKER):
        print(f"  {MARKER} Последняя запись уже обработана. Пропускаю заказ.")
        return

    print(f"  Анализирую только последнюю запись: {last_entry_to_analyze}")

    # Отправляем последнюю запись на анализ в OpenAI
    tasks_to_create = analyze_comment_with_openai(last_entry_to_analyze)

    # Создаем задачи в RetailCRM
    if tasks_to_create:
        print("  ✅ OpenAI успешно нашел следующие задачи. Попытка их создания...")
        for i, task_info in enumerate(tasks_to_create):
            try:
                task_date_str = task_info.get('date_time') or task_info.get('task_datetime')
                task_text = task_info.get('task') or task_info.get('task_text')
                task_comment = task_info.get('commentary') or task_info.get('additional_comment') or task_info.get(
                    'task_comment')

                if not task_date_str:
                    print(f"    В ответе OpenAI отсутствует дата для задачи #{i + 1}, пропускаем.")
                    continue

                task_date = datetime.strptime(task_date_str, '%Y-%m-%d %H:%M')

                if task_date < datetime.now():
                    print(f"    Задача #{i + 1} имеет прошедшую дату ({task_date_str}), пропускаем.")
                    continue

                task_data = {
                    'text': task_text,
                    'commentary': task_comment,
                    'datetime': task_date_str,
                    'performerId': manager_id,
                    'order': {'id': order_id}
                }

                response = create_task(task_data)

                if response.get('success'):
                    task_id = response.get('id')
                    print(f"    Задача #{i + 1} успешно создана! ID задачи: {task_id}")
                    # Обновляем комментарий, добавляя ✅ к последней строке
                    new_comment = operator_comment.strip() + ' ' + MARKER
                    update_response = update_order_comment(order_id, new_comment)
                    if update_response.get('success'):
                        print(f"    ✅ Комментарий к заказу успешно обновлен.")
                    else:
                        print(f"    ❌ Ошибка при обновлении комментария: {update_response}")
                else:
                    print(f"    ❌ Ошибка при создании задачи #{i + 1}: {response}")

            except ValueError:
                print(f"    Ошибка парсинга даты: {task_date_str}. Пропускаем задачу #{i + 1}.")

    else:
        print("  ❌ OpenAI не нашел явных задач в комментарии.")
    print("-" * 50)


def main():
    """Главная функция для запуска периодической обработки."""
    print("Запускаю периодическую проверку новых заказов...")

    # Шаг 1: Определяем временное окно
    start_date_utc, end_date_utc = get_time_window_and_timezone()

    if start_date_utc is None:
        print("Текущее время не соответствует запланированному запуску (12:00 или 20:00 МСК). Завершение работы.")
        return

    # Шаг 2: Получаем историю изменений в заданном окне
    history_data = get_order_history_by_dates(start_date_utc, end_date_utc)

    if not history_data.get('success'):
        print("Ошибка при получении истории заказов. Завершение работы.")
        return

    changes = history_data.get('history', [])

    if not changes:
        print("Нет новых изменений в заказах за указанный период. Завершение работы.")
        return

    # Шаг 3: Извлекаем уникальные ID заказов, чтобы избежать дублирования
    unique_order_ids = set(
        [change['order']['id'] for change in changes if 'order' in change and 'id' in change['order']])

    print(f"Найдено {len(unique_order_ids)} уникальных заказов с изменениями.")

    # Шаг 4: Обрабатываем каждый уникальный заказ
    for order_id in unique_order_ids:
        order_data = get_order_by_id(order_id)
        if order_data:
            process_order(order_data)
        else:
            print(f"Не удалось получить полные данные для заказа ID: {order_id}. Пропускаю.")

    print("Обработка завершена.")


if __name__ == "__main__":
    main()
