import os
import json
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
# Обновленный импорт: добавлена новая функция get_orders_by_delivery_date
from retailcrm_api import get_recent_orders, create_task, update_order_comment, get_orders_by_delivery_date
from openai_processor import analyze_comment_with_openai

load_dotenv()

# Устанавливаем часовой пояс Москвы
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
MARKER = ' 📅'

# СТАТУСЫ, по которым НУЖНО создавать задачи.
ALLOWED_STATUSES = [
    "new",
    "gotovo-k-soglasovaniiu",
    "soglasovat-sostav",
    "agree-absence",
    "novyi-predoplachen",
    "novyi-oplachen",
    "availability-confirmed",
    "client-confirmed",
    "offer-analog",
    "ne-dozvonilis",
    "perezvonit-pozdnee",
    "otpravili-varianty-na-pochtu",
    "otpravili-varianty-v-vatsap",
    "ready-to-wait",
    "waiting-for-arrival",
    "klient-zhdet-foto-s-zakupki",
    "vizit-v-shourum",
    "ozhidaet-oplaty",
    "gotovim-kp",
    "kp-gotovo-k-zashchite",
    "soglasovanie-kp",
    "proekt-visiak",
    "soglasovano",
    "oplacheno",
    "prepayed",
    "soglasovan-ozhidaet-predoplaty",
    "vyezd-biologa-oplachen",
    "vyezd-biologa-zaplanirovano",
    "predoplata-poluchena",
    "oplata-ne-proshla",
    "proverka-nalichiia",
    "obsluzhivanie-zaplanirovano",
    "obsluzhivanie-soglasovanie",
    "predoplachen-soglasovanie",
    "servisnoe-obsluzhivanie-oplacheno",
    "zakaz-obrabotan-soglasovanie",
    "vyezd-biologa-soglasovanie"
]
# МЕТОДЫ, которые НУЖНО исключить.
EXCLUDED_METHODS = ['servisnoe-obsluzhivanie', 'komus']

# НОВЫЕ КОНСТАНТЫ для логики доставки
UNDELIVERED_CODES = ["self-delivery", "storonniaia-dostavka"]
DELIVERED_STATUSES = ["send-to-delivery", "dostavlen"]


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


def process_undelivered_orders(orders_list: list, now_moscow: datetime):
    """
    НОВАЯ ФУНКЦИЯ.
    Обрабатывает список заказов с сегодняшней датой доставки.
    Ставит задачу, если код доставки целевой, а статус не 'доставлен'.
    """

    print("\n--- Проверка заказов с сегодняшней датой доставки ---")

    for order_data in orders_list:
        order_id = order_data.get('id')
        manager_id = order_data.get('managerId')
        delivery_code = order_data.get('delivery', {}).get('code')
        order_status = order_data.get('status')

        print(f"Проверка доставки заказа ID: {order_id}")

        if not manager_id:
            print(f"  В заказе {order_id} не указан ответственный менеджер. Пропускаем.")
            continue

        # 1. Фильтр по коду доставки
        if delivery_code not in UNDELIVERED_CODES:
            print(f"  Код доставки '{delivery_code}' нецелевой. Пропускаем.")
            continue

        # 2. Фильтр по статусу (если статус не "доставлен" или "отправлен")
        if order_status not in DELIVERED_STATUSES:
            print(
                f"  ⚠️ Заказ ID: {order_id} имеет код доставки '{delivery_code}', но статус '{order_status}'. Создаю задачу.")

            # Задача ставится на завтра в 10:00 (так как сейчас 21:00)
            tomorrow_10am = now_moscow + timedelta(days=1)
            tomorrow_10am = tomorrow_10am.replace(hour=10, minute=0, second=0, microsecond=0)
            task_datetime_str = tomorrow_10am.strftime('%Y-%m-%d %H:%M')

            task_data = {
                'text': "Актуализировать дату доставки",
                'commentary': f"Заказ со способом доставки '{delivery_code}' должен был быть доставлен сегодня, но имеет статус '{order_status}'. Необходимо актуализировать дату или статус.",
                'datetime': task_datetime_str,
                'performerId': manager_id,
                'order': {'id': order_id}
            }

            response = create_task(task_data)

            if response.get('success'):
                print(f"  ✅ Задача 'Актуализировать дату доставки' успешно создана! ID задачи: {response.get('id')}")
            else:
                print(f"  ❌ Ошибка при создании задачи 'Актуализировать дату доставки': {response}")
        else:
            print(f"  Статус '{order_status}' указывает на доставку. Пропускаем.")

        print("-" * 50)


def process_order(order_data: dict):
    """
    Обрабатывает один заказ: анализирует последнюю запись комментария и создает задачи.
    Включает логику для фильтрации, пустых и неформализованных комментариев.
    """
    order_id = order_data.get('id')
    operator_comment = order_data.get('managerComment', '')
    manager_id = order_data.get('managerId')
    order_method = order_data.get('orderMethod')
    # Получаем статус заказа
    order_status = order_data.get('status')

    # Получаем текущее время запуска скрипта
    now_moscow = datetime.now(MOSCOW_TZ)

    print(f"Обработка заказа ID: {order_id}")

    # --- ЛОГИКА ФИЛЬТРАЦИИ ---
    # 1. Фильтрация по методу оформления (исключение)
    if order_method in EXCLUDED_METHODS:
        print(f"  В заказе {order_id} метод оформления '{order_method}'. Пропускаем по фильтру методов.")
        print("-" * 50)
        return

    # 2. Фильтрация по статусу (включение)
    if order_status not in ALLOWED_STATUSES:
        print(f"  В заказе {order_id} статус '{order_status}' не входит в список целевых. Пропускаем.")
        print("-" * 50)
        return
    # --- Конец фильтрации ---

    if not manager_id:
        print(f"  В заказе {order_id} не указан ответственный менеджер. Пропускаем.")
        return

    if not operator_comment:
        print(f"  ⚠️ В заказе {order_id} нет комментария менеджера. Создаю задачу на заполнение.")

        # --- НОВАЯ ЛОГИКА для ПУСТОГО комментария (Сценарий А) ---

        # Если время запуска до 17:00 (запуск в 12 или 16), ставим задачу на сегодня в 17:00
        if now_moscow.hour < 17:
            # Устанавливаем на сегодня, 17:00
            target_dt = now_moscow.replace(hour=17, minute=0, second=0, microsecond=0)

            # Дополнительная проверка: если сейчас уже после 17:00, ставим на завтра
            if target_dt < now_moscow:
                target_dt = now_moscow + timedelta(days=1)
                target_dt = target_dt.replace(hour=10, minute=0, second=0, microsecond=0)

        # Если время запуска 21:00 (или после 17:00)
        else:
            # Ставим задачу на завтра в 10:00 (как при запуске в 21:00)
            target_dt = now_moscow + timedelta(days=1)
            target_dt = target_dt.replace(hour=10, minute=0, second=0, microsecond=0)

        task_datetime_str = target_dt.strftime('%Y-%m-%d %H:%M')
        # --- КОНЕЦ НОВОЙ ЛОГИКИ Сценария А ---

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

    # --- Логика обработки при НЕПУСТОМ комментарии (Сценарий Б и В) ---

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
                # Используем now_moscow, определенный выше
                corrected_datetime_str = get_corrected_datetime(task_date_str, now_moscow)

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
        # --- ЛОГИКА: Неформализованный комментарий (Сценарий В) ---
        print("  ❌ OpenAI не нашел явных задач в строгом формате 'ДАТА - ДЕЙСТВИЕ'.")

        # Логика задачи: Запланировать дату касания на завтра в 10:00 (Остается без изменений)
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

    # Шаг 0: Определяем текущее время для условного запуска новой логики
    now_moscow = datetime.now(MOSCOW_TZ)
    current_time_str = now_moscow.strftime('%H:%M')
    is_evening_run = now_moscow.hour == 21  # Проверяем, что сейчас 21:xx

    # --- БЛОК 1: Проверка не доставленных сегодня заказов (только в 21:00) ---
    if is_evening_run:
        print(f"\n--- Запускаю проверку не доставленных заказов (Время: {current_time_str}) ---")

        today_date_str = now_moscow.strftime('%Y-%m-%d')

        undelivered_orders_data = get_orders_by_delivery_date(today_date_str)

        if undelivered_orders_data:
            undelivered_orders = undelivered_orders_data.get('orders', [])
            print(f"Найдено {len(undelivered_orders)} заказов с доставкой на сегодня.")
            process_undelivered_orders(undelivered_orders, now_moscow)
        else:
            print("Не найдено заказов с доставкой на сегодня.")
    else:
        print(f"\n--- Проверка не доставленных заказов пропущена (Запуск в {current_time_str}) ---")

    # --- БЛОК 2: Обработка последних 50 заказов ---
    print("\n--- Запускаю обработку последних 50 заказов для анализа комментариев ---")

    # Шаг 1: Получаем последние 50 заказов
    orders_data = get_recent_orders(limit=50)

    if not orders_data:
        print("Ошибка при получении списка последних заказов. Завершение работы первого блока.")
    else:
        orders = orders_data.get('orders', [])

        if not orders:
            print("Нет новых заказов для обработки. Завершение работы первого блока.")
        else:
            print(f"Найдено {len(orders)} последних заказов.")

            # Шаг 2: Обрабатываем каждый заказ из полученного списка
            for order_data in orders:
                process_order(order_data)

    print("\nОбработка завершена.")


if __name__ == "__main__":
    main()
