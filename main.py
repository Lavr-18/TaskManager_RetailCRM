import os
import json
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
from retailcrm_api import get_recent_orders, create_task, update_order_comment
from openai_processor import analyze_comment_with_openai

load_dotenv()

# Устанавливаем часовой пояс Москвы
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
MARKER = ' 📅'


def get_corrected_datetime(ai_datetime_str: str, current_script_time: datetime) -> str:
    """
    Корректирует дату и время задачи, следуя правилам:
    1. Если дата в прошлом, возвращает ошибку, чтобы задача не была создана.
    2. Если в комментарии нет времени (OpenAI возвращает 10:00), использует +1 час от текущего времени.
    3. Если итоговое время попадает в нерабочее (после 20:00), переносит на завтра на 10:00.
    """
    try:
        # Получаем текущее время скрипта
        now_moscow = datetime.now(MOSCOW_TZ)

        # Парсим дату и время из ответа OpenAI
        task_dt = datetime.strptime(ai_datetime_str, '%Y-%m-%d %H:%M').replace(tzinfo=MOSCOW_TZ)

        # ПРАВИЛО 1: Если итоговая дата в прошлом, возвращаем ошибку, чтобы пропустить задачу.
        if task_dt.date() < now_moscow.date():
            raise ValueError("Задача относится к прошедшей дате и будет пропущена.")

        # ПРАВИЛО 2: Если OpenAI вернул время по умолчанию (10:00), используем +1 час
        if task_dt.hour == 10 and task_dt.minute == 0:
            task_dt = now_moscow + timedelta(hours=1)
            task_dt = task_dt.replace(second=0, microsecond=0)

        # ПРАВИЛО 3: Если время попадает в нерабочее (после 20:00), переносим на завтра
        if task_dt.hour >= 20:
            task_dt = now_moscow + timedelta(days=1)
            task_dt = task_dt.replace(hour=10, minute=0, second=0, microsecond=0)

        return task_dt.strftime('%Y-%m-%d %H:%M')

    except (ValueError, TypeError) as e:
        # Если что-то пошло не так, возвращаем исключение, чтобы обработать его на более высоком уровне
        raise e


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
    Включает логику для пустых и неформализованных комментариев.
    """
    order_id = order_data.get('id')
    operator_comment = order_data.get('managerComment', '')
    manager_id = order_data.get('managerId')

    print(f"Обработка заказа ID: {order_id}")

    if not manager_id:
        print(f"  В заказе {order_id} не указан ответственный менеджер. Пропускаем.")
        return

    if not operator_comment:
        print(f"  ⚠️ В заказе {order_id} нет комментария менеджера. Создаю задачу на заполнение.")

        # Логика для ПУСТОГО комментария: Заполнить комментарий оператора на завтра в 12:00
        now_moscow = datetime.now(MOSCOW_TZ)
        tomorrow_12pm = now_moscow + timedelta(days=1)
        tomorrow_12pm = tomorrow_12pm.replace(hour=12, minute=0, second=0, microsecond=0)
        task_datetime_str = tomorrow_12pm.strftime('%Y-%m-%d %H:%M')

        task_data = {
            'text': "Заполнить комментарий оператора",
            'commentary': "Комментарий менеджера был пуст при проверке. Необходимо внести актуальную информацию о заказе.",
            'datetime': task_datetime_str,
            'performerId': manager_id,
            'order': {'id': order_id}
        }

        response = create_task(task_data)

        if response.get('success'):
            print(f"  ✅ Задача 'Заполнить комментарий' успешно создана! ID задачи: {response.get('id')}")
        else:
            print(f"  ❌ Ошибка при создании задачи 'Заполнить комментарий': {response}")

        print("-" * 50)
        return  # Прекращаем обработку, т.к. комментарий пуст

    # --- Логика обработки при НЕПУСТОМ комментарии ---

    # Извлекаем последние 3 записи для анализа
    last_entries_to_analyze = extract_last_entries(operator_comment)

    # Проверяем, есть ли что-то для анализа
    if not last_entries_to_analyze:
        print(f"  ✅ Все последние записи уже обработаны. Пропускаю заказ.")
        print("-" * 50)
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

                # Дополнительная проверка на пустые значения
                if not (task_date_str and task_text and task_date_str.strip() and task_text.strip()):
                    print(
                        f"    В ответе OpenAI отсутствуют обязательные поля (task, date_time) или они пусты. Пропускаем задачу #{i + 1}.")
                    continue

                # Корректируем дату и время, если это необходимо
                corrected_datetime_str = get_corrected_datetime(task_date_str, datetime.now(MOSCOW_TZ))

                # ... остальной код для создания задачи

                task_data = {
                    'text': task_text,
                    'commentary': task_comment,
                    'datetime': corrected_datetime_str,
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
        # --- НОВАЯ ЛОГИКА: Неформализованный комментарий ---
        print("  ❌ OpenAI не нашел явных задач в строгом формате 'ДАТА - ДЕЙСТВИЕ'.")

        # Логика задачи: Запланировать дату касания на завтра в 10:00
        now_moscow = datetime.now(MOSCOW_TZ)
        tomorrow_10am = now_moscow + timedelta(days=1)
        tomorrow_10am = tomorrow_10am.replace(hour=10, minute=0, second=0, microsecond=0)
        task_datetime_str = tomorrow_10am.strftime('%Y-%m-%d %H:%M')

        task_data = {
            'text': "запланировать дату касания",
            'commentary': "В последних записях комментария не найдена задача в строгом формате 'ДАТА - ДЕЙСТВИЕ'. Запланируйте следующее касание.",
            'datetime': task_datetime_str,
            'performerId': manager_id,
            'order': {'id': order_id}
        }

        response = create_task(task_data)

        if response.get('success'):
            print(f"  ✅ Задача 'запланировать дату касания' успешно создана! ID задачи: {response.get('id')}")
        else:
            print(f"  ❌ Ошибка при создании задачи 'запланировать дату касания': {response}")

    print("-" * 50)


def main():
    """Главная функция для запуска периодической обработки."""
    print("Запускаю периодическую проверку новых заказов...")

    # Шаг 1: Получаем последние 70 заказов
    orders_data = get_recent_orders(limit=50)

    if not orders_data:
        print("Ошибка при получении списка заказов. Завершение работы.")
        return

    orders = orders_data.get('orders', [])

    if not orders:
        print("Нет новых заказов для обработки. Завершение работы.")
        return

    print(f"Найдено {len(orders)} последних заказов.")

    # Шаг 2: Обрабатываем каждый заказ из полученного списка
    for order_data in orders:
        process_order(order_data)

    print("Обработка завершена.")


if __name__ == "__main__":
    main()
