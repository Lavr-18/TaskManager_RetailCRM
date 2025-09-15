import os
from datetime import datetime
from dotenv import load_dotenv
from retailcrm_api import get_order_by_id, create_task, update_order_comment
from openai_processor import analyze_comment_with_openai

load_dotenv()

# Указываем ID заказа для тестирования
ORDER_ID_TO_TEST = 24420
MARKER = '✅'


def extract_last_entries(comment: str, num_entries: int = 3) -> str:
    """
    Извлекает последние записи из комментария менеджера, которые ещё не обработаны.
    Возвращает строку, объединяя эти записи.
    """
    lines = [line.strip() for line in comment.strip().split('\n') if line.strip()]

    unprocessed_lines = []
    for line in reversed(lines):
        if not line.endswith(MARKER):
            unprocessed_lines.insert(0, line)
        else:
            break  # Останавливаемся, как только находим обработанную строку

    # Возвращаем последние 'num_entries' необработанных строк
    return '\n'.join(unprocessed_lines[-num_entries:])


def test_single_order():
    """
    Тестирует обработку комментария для одного конкретного заказа и создает задачи.
    """
    print(f"Запускаю тестирование для заказа с ID: {ORDER_ID_TO_TEST}")

    # Шаг 1: Получаем полные данные заказа
    order_data = get_order_by_id(ORDER_ID_TO_TEST)

    if not order_data:
        print(f"Ошибка: не удалось получить данные для заказа {ORDER_ID_TO_TEST}.")
        return

    # Шаг 2: Извлекаем комментарий и ID менеджера
    operator_comment = order_data.get('managerComment', '')
    manager_id = order_data.get('managerId')

    if not operator_comment:
        print(f"В заказе {ORDER_ID_TO_TEST} нет комментария менеджера. Тестирование не может быть выполнено.")
        return

    if not manager_id:
        print(f"В заказе {ORDER_ID_TO_TEST} не указан ответственный менеджер. Пропускаем.")
        return

    print("-" * 50)
    print(f"Найден комментарий менеджера:")
    print(operator_comment)
    print("-" * 50)

    # Шаг 3: Извлекаем последние записи для анализа
    last_entries_to_analyze = extract_last_entries(operator_comment)

    if not last_entries_to_analyze:
        print(f"✅ Все последние записи уже обработаны. Пропускаю заказ.")
        return

    print(f"Анализирую только последние записи:\n{last_entries_to_analyze}")

    # Шаг 4: Отправляем последнюю запись на анализ в OpenAI
    tasks_to_create = analyze_comment_with_openai(last_entries_to_analyze)

    # Шаг 5: Создаем задачи в RetailCRM
    if tasks_to_create:
        print("✅ OpenAI успешно нашел следующие задачи. Попытка их создания...")
        for i, task_info in enumerate(tasks_to_create):
            try:
                # Надежный поиск даты, текста и комментария
                task_date_str = task_info.get('date_time') or task_info.get('task_datetime')
                task_text = task_info.get('task') or task_info.get('task_text')
                task_comment = task_info.get('commentary') or task_info.get('additional_comment') or task_info.get(
                    'task_comment')

                if not task_date_str:
                    print(f"  В ответе OpenAI отсутствует дата для задачи #{i + 1}, пропускаем.")
                    continue

                task_date = datetime.strptime(task_date_str, '%Y-%m-%d %H:%M')

                # Проверяем, не является ли дата прошедшей
                if task_date < datetime.now():
                    print(f"  Задача #{i + 1} имеет прошедшую дату ({task_date_str}), пропускаем.")
                    continue

                task_data = {
                    'text': task_text,
                    'commentary': task_comment,
                    'datetime': task_date_str,
                    'performerId': manager_id,
                    'order': {'id': ORDER_ID_TO_TEST}
                }

                # Создаем задачу
                response = create_task(task_data)

                if response.get('success'):
                    task_id = response.get('id')
                    print(f"  Задача #{i + 1} успешно создана! ID задачи: {task_id}")
                    # Обновляем комментарий, добавляя ✅ к последней строке
                    new_comment = operator_comment.strip() + ' ' + MARKER
                    update_response = update_order_comment(ORDER_ID_TO_TEST, new_comment)
                    if update_response.get('success'):
                        print(f"  ✅ Комментарий к заказу успешно обновлен.")
                    else:
                        print(f"  ❌ Ошибка при обновлении комментария: {update_response}")
                else:
                    print(f"  Ошибка при создании задачи #{i + 1}: {response}")

            except ValueError:
                print(f"  Ошибка парсинга даты: {task_date_str}. Пропускаем задачу #{i + 1}.")

    else:
        print("❌ OpenAI не нашел явных задач в комментарии.")

    print("Тест завершен.")


if __name__ == "__main__":
    test_single_order()
