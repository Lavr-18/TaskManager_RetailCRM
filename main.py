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
MARKER = ' ✅'


def get_time_window_and_timezone() -> tuple:
    """
    Определяет временное окно для анализа заказов в зависимости от текущего времени по МСК.
    Возвращает начальную и конечную дату в формате UTC.
    """
    now_msk = datetime.now(MOSCOW_TZ)

    # Запуск в 12:00
    # Окно: с 20:00 (предыдущий день) до 11:59 (текущий день)
    if now_msk.hour == 12:
        start_msk = now_msk.replace(hour=20, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end_msk = now_msk.replace(hour=11, minute=59, second=59, microsecond=999999)

    # Запуск в 20:00
    # Окно: с 12:00 до 19:59 (текущий день)
    elif now_msk.hour == 20:
        start_msk = now_msk.replace(hour=12, minute=0, second=0, microsecond=0)
        end_msk = now_msk.replace(hour=19, minute=59, second=59, microsecond=999999)

    else:
        # Если скрипт запускается не в 12 или 20 часов, возвращаем пустой диапазон
        return None, None

    # Конвертируем временные окна в формат UTC для API
    start_utc = start_msk.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    end_utc = end_msk.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

    print(f"Запрос истории изменений с {start_utc} до {end_utc}...")
    return start_utc, end_utc


def extract_last_entries(comment: str, num_entries: int = 3) -> str:
    """
    Извлекает последние записи из комментария менеджера, которые ещё не обработаны.
    Возвращает строку, объединяя эти записи.
    """
    lines = [line.strip() for line in comment.strip().split('\n') if line.strip()]

    unprocessed_lines = []
    for line in reversed(lines):
        if not line.endswith(MARKER.strip()):
            unprocessed_lines.insert(0, line)
        else:
            break  # Останавливаемся, как только находим обработанную строку

    # Возвращаем последние 'num_entries' необработанных строк
    return '\n'.join(unprocessed_lines[-num_entries:])


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

    # Извлекаем последние 3 записи для анализа
    last_entries_to_analyze = extract_last_entries(operator_comment)

    # Проверяем, есть ли что-то для анализа
    if not last_entries_to_analyze:
        print(f"  ✅ Все последние записи уже обработаны. Пропускаю заказ.")
        return

    print(f"  Анализирую только последние записи:\n{last_entries_to_analyze}")

    # Отправляем последние записи на анализ в OpenAI
    tasks_to_create = analyze_comment_with_openai(last_entries_to_analyze)

    # Создаем задачи в RetailCRM
    if tasks_to_create:
        print("  ✅ OpenAI успешно нашел следующие задачи. Попытка их создания...")
        for i, task_info in enumerate(tasks_to_create):
            try:
                task_date_str = task_info.get('date_time')
                task_text = task_info.get('task')
                task_comment = task_info.get('commentary')

                if not (task_date_str and task_text):
                    print(
                        f"    В ответе OpenAI отсутствуют обязательные поля (task, date_time). Пропускаем задачу #{i + 1}.")
                    continue

                task_date = datetime.strptime(task_date_str, '%Y-%m-%d %H:%M')

                # Проверка и корректировка года
                current_year = datetime.now().year
                if task_date.year < current_year:
                    task_date = task_date.replace(year=current_year)

                if task_date < datetime.now():
                    print(
                        f"    Задача #{i + 1} имеет прошедшую дату ({task_date.strftime('%Y-%m-%d %H:%M')}), пропускаем.")
                    continue

                task_data = {
                    'text': task_text,
                    'commentary': task_comment,
                    'datetime': task_date.strftime('%Y-%m-%d %H:%M'),
                    'performerId': manager_id,
                    'order': {'id': order_id}
                }

                response = create_task(task_data)

                if response.get('success'):
                    task_id = response.get('id')
                    print(f"    Задача #{i + 1} успешно создана! ID задачи: {task_id}")

                    # Находим строку для обновления и добавляем маркер
                    line_to_mark = task_info.get('marked_line')
                    new_comment = operator_comment.replace(line_to_mark, f"{line_to_mark}{MARKER}")

                    update_response = update_order_comment(order_id, new_comment)
                    if update_response.get('success'):
                        print(f"    ✅ Комментарий к заказу успешно обновлен.")
                    else:
                        print(f"    ❌ Ошибка при обновлении комментария: {update_response}")
                else:
                    print(f"    ❌ Ошибка при создании задачи #{i + 1}: {response}")

            except (ValueError, TypeError) as e:
                print(f"    Ошибка при обработке задачи #{i + 1}: {e}. Пропускаем.")

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