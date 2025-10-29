import os
import json
import pytz
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

from retailcrm_api import (
    get_recent_orders,
    create_task,
    update_order_comment,
    get_orders_by_delivery_date,
    get_orders_by_statuses,
    get_orders_by_method_and_date_range  # Импортируем новую функцию
)
from openai_processor import analyze_comment_with_openai  # Оставляем, так как эта логика работает для других заказов

load_dotenv()

# Устанавливаем часовой пояс Москвы
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
MARKER = ' 📅'  # Маркер для обработанных строк OpenAI (находится в конце строки)

# НОВЫЕ МАРКЕРЫ для предотвращения дублирования общих задач (проверяются в тексте комментария)
COMMENT_TASK_MARKER = ' [Task: Comment Needed]'  # Маркер для задачи "Заполнить комментарий оператора"
CONTACT_TASK_MARKER = ' [Task: Contact Needed]'  # Маркер для задачи "запланировать дату касания"
MISSED_CALL_TASK_MARKER = ' [Task: Missed Call Reglament Started]'  # Маркер для запущенного регламента НДЗ

# КОНСТАНТА ДЛЯ НОВОГО РЕГЛАМЕНТА
MISSED_CALL_METHOD = "vkhodiashchii-zvonok"

# НОВЫЕ КОНСТАНТЫ для отслеживания статусов
TRACKER_FILE = 'status_trackers.json'
STATUS_CONFIGS = {
    # Ключ: Символьный код статуса
    "klient-zhdet-foto-s-zakupki": {
        "max_days": 14,
        "task_text": "связаться с клиентом / уточнить актуальность / пересогласовать"
    },
    "vizit-v-shourum": {
        "max_days": 7,
        "task_text": "связаться с клиентом"
    },
    "ozhidaet-oplaty": {
        "max_days": 7,
        "task_text": "связаться с клиентом/актуализировать счет"
    }
}
TRACKED_STATUSES = list(STATUS_CONFIGS.keys())

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

# КОНСТАНТЫ для логики доставки
UNDELIVERED_CODES = ["self-delivery", "storonniaia-dostavka"]
DELIVERED_STATUSES = ["send-to-delivery", "dostavlen"]


# --- ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛОМ СОСТОЯНИЯ (ОСТАВЛЕНЫ БЕЗ ИЗМЕНЕНИЙ) ---

def load_trackers() -> Dict[str, Dict[str, str]]:
    """Загружает данные отслеживания статусов из JSON-файла."""
    # Словарь по умолчанию для случая отсутствия файла:
    # { 'status_code': { 'order_id': 'YYYY-MM-DD', ... } }
    default_trackers = {status: {} for status in TRACKED_STATUSES}

    if not os.path.exists(TRACKER_FILE):
        print(f"Файл {TRACKER_FILE} не найден. Создаю пустой трекер.")
        return default_trackers

    try:
        with open(TRACKER_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Убеждаемся, что все ключи статусов присутствуют
            for status in TRACKED_STATUSES:
                if status not in data:
                    data[status] = {}
            return data
    except (IOError, json.JSONDecodeError) as e:
        print(f"Ошибка при чтении или парсинге {TRACKER_FILE}: {e}. Использую пустой трекер.")
        return default_trackers


def save_trackers(data: Dict[str, Dict[str, str]]):
    """Сохраняет данные отслеживания статусов в JSON-файл."""
    try:
        with open(TRACKER_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        print(f"Трекер статусов успешно сохранен в {TRACKER_FILE}.")
    except IOError as e:
        print(f"Ошибка при записи в {TRACKER_FILE}: {e}")


# --- ОСНОВНАЯ ЛОГИКА ОТСЛЕЖИВАНИЯ СТАТУСОВ (ОСТАВЛЕНА БЕЗ ИЗМЕНЕНИЙ) ---

def process_status_trackers(now_moscow: datetime):
    """
    Проверяет заказы на "зависание" в целевых статусах, обновляет трекер и ставит задачи.
    """
    print("\n--- Запуск отслеживания 'зависших' статусов ---")

    # 1. Загрузка данных трекера
    tracker_data = load_trackers()
    today_date_str = now_moscow.strftime('%Y-%m-%d')

    # 2. Получение текущих заказов из CRM для всех целевых статусов
    crm_orders_data = get_orders_by_statuses(TRACKED_STATUSES)

    if not crm_orders_data or not crm_orders_data.get('orders'):
        print("Не удалось получить текущие заказы из CRM или список пуст. Сохраняю трекер без изменений.")
        save_trackers(tracker_data)
        print("-" * 50)
        return

    crm_orders_list = crm_orders_data['orders']
    # Создаем быстрый словарь {order_id: status} для удобной проверки
    crm_current_statuses = {str(order['id']): order['status'] for order in crm_orders_list}
    crm_manager_ids = {str(order['id']): order.get('managerId') for order in crm_orders_list}

    # Задача ставится на завтра в 10:00
    tomorrow_10am = (now_moscow + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    task_datetime_str = tomorrow_10am.strftime('%Y-%m-%d %H:%M')

    # 3. Обработка всех отслеживаемых статусов
    for status_code, config in STATUS_CONFIGS.items():
        max_days = config["max_days"]
        task_text = config["task_text"]

        print(f"\nОбработка статуса '{status_code}' (лимит: {max_days} дн.):")

        # --- Часть 3А: Проверка существующих заказов на превышение лимита и удаление ---
        orders_to_remove = []

        # Используем копию, чтобы можно было изменять словарь во время итерации
        current_tracker = tracker_data.get(status_code, {}).copy()

        for order_id, date_added_str in current_tracker.items():
            order_id_int = int(order_id)
            current_status = crm_current_statuses.get(order_id)
            manager_id = crm_manager_ids.get(order_id)

            # ПРОВЕРКА 1: Изменился ли статус?
            if current_status != status_code:
                # Статус изменился -> удаляем из трекера
                print(f"  Заказ {order_id} изменил статус на '{current_status}'. Удаляю из трекера.")
                orders_to_remove.append(order_id)
                continue

            # ПРОВЕРКА 2: Превышен ли лимит дней?
            if manager_id:
                try:
                    date_added = datetime.strptime(date_added_str, '%Y-%m-%d').replace(tzinfo=MOSCOW_TZ)
                    # Вычисляем количество дней с даты добавления (не строго)
                    days_in_status = (now_moscow.date() - date_added.date()).days

                    # Используем нестрогое сравнение: если days_in_status > max_days (т.е., 8 дней > 7 дней)
                    if days_in_status > max_days:
                        # Превышен лимит -> ставим задачу и удаляем из трекера
                        print(f"  ⚠️ Заказ {order_id} завис в статусе {days_in_status} дней! Ставлю задачу.")

                        commentary = (
                            f"Заказ находится в статусе '{status_code}' уже {days_in_status} дней. "
                            f"Лимит {max_days} дней превышен. Необходимо выполнить действие: {task_text}."
                        )

                        task_data = {
                            'text': task_text,
                            'commentary': commentary,
                            'datetime': task_datetime_str,  # Завтра в 10:00
                            'performerId': manager_id,
                            'order': {'id': order_id_int}
                        }

                        response = create_task(task_data)

                        if response.get('success'):
                            print(f"    ✅ Задача успешно создана! ID задачи: {response.get('id')}")
                        else:
                            print(f"    ❌ Ошибка при создании задачи: {response}")

                        # Удаляем из трекера, чтобы избежать повторной постановки задачи
                        orders_to_remove.append(order_id)
                    else:
                        print(f"  Заказ {order_id} находится в статусе {days_in_status} дней. ОК.")

                except ValueError:
                    print(f"  Ошибка парсинга даты '{date_added_str}' для заказа {order_id}. Удаляю.")
                    orders_to_remove.append(order_id)
            else:
                print(f"  У заказа {order_id} нет менеджера. Пропускаю проверку лимита.")

        # Применяем удаление к основному словарю
        for order_id in orders_to_remove:
            tracker_data[status_code].pop(order_id, None)

        # --- Часть 3Б: Добавление новых заказов в трекер ---

        # Получаем только те заказы, которые сейчас в CRM и находятся в текущем статусе
        new_orders_in_status = [
            str(order['id']) for order in crm_orders_list
            if order.get('status') == status_code
        ]

        for order_id in new_orders_in_status:
            if order_id not in tracker_data[status_code]:
                # Новый заказ -> добавляем в трекер с текущей датой
                tracker_data[status_code][order_id] = today_date_str
                print(f"  + Новый заказ {order_id} добавлен в трекер.")

    # 4. Сохранение обновленного трекера
    save_trackers(tracker_data)
    print("--- Отслеживание статусов завершено ---")


def get_corrected_datetime(ai_datetime_str: str, current_script_time: datetime) -> str:
    # ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ
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
    # ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ
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
    # ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ
    """
    Обрабатывает список заказов с сегодняшней датой доставки (только в 21:00).
    Ставит задачу, если код доставки целевой, а статус не 'доставлен'.
    """

    print("\n--- Проверка заказов с сегодняшней датой доставки ---")

    tomorrow_10am = (now_moscow + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    task_datetime_str = tomorrow_10am.strftime('%Y-%m-%d %H:%M')

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

            commentary = (
                f"Заказ со способом доставки '{delivery_code}' должен был быть доставлен сегодня, но имеет статус '{order_status}'. "
                f"Необходимо актуализировать дату или статус."
            )

            task_data = {
                'text': "Актуализировать дату доставки",
                'commentary': commentary,
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
    # ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ, ТАК КАК ЭТО ОСНОВНАЯ ЛОГИКА АНАЛИЗА КОММЕНТАРИЕВ
    """
    Обрабатывает один заказ: анализирует последнюю запись комментария и создает задачи.
    Включает логику для фильтрации, пустых и неформализованных комментариев, а также
    НОВУЮ ЛОГИКУ предотвращения дублирования общих задач.
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

    # --- НОВАЯ ЛОГИКА ПРЕДОТВРАЩЕНИЯ ДУБЛИРОВАНИЯ (Проверка маркеров) ---

    # 1. Если в комментарии уже есть маркер для задачи "Заполнить комментарий оператора", пропускаем
    if COMMENT_TASK_MARKER in operator_comment:
        print(f"  ✅ В заказе {order_id} обнаружен маркер {COMMENT_TASK_MARKER}. Пропускаю задачу на заполнение.")
        print("-" * 50)
        return

    # 2. Если в комментарии уже есть маркер для задачи "запланировать дату касания", пропускаем
    if CONTACT_TASK_MARKER in operator_comment:
        print(f"  ✅ В заказе {order_id} обнаружен маркер {CONTACT_TASK_MARKER}. Пропускаю задачу на дату касания.")
        print("-" * 50)
        return

    # --- КОНЕЦ ПРОВЕРКИ ДУБЛИРОВАНИЯ ---

    # --- Логика для ПУСТОГО комментария (Сценарий А) ---

    if not operator_comment:
        print(f"  ⚠️ В заказе {order_id} нет комментария менеджера. Создаю задачу на заполнение.")

        # Логика определения времени задачи (НЕ ИЗМЕНЯЛАСЬ)
        if now_moscow.hour < 17:
            target_dt = now_moscow.replace(hour=17, minute=0, second=0, microsecond=0)
            if target_dt < now_moscow:
                target_dt = now_moscow + timedelta(days=1)
                target_dt = target_dt.replace(hour=10, minute=0, second=0, microsecond=0)
        else:
            target_dt = now_moscow + timedelta(days=1)
            target_dt = target_dt.replace(hour=10, minute=0, second=0, microsecond=0)

        task_datetime_str = target_dt.strftime('%Y-%m-%d %H:%M')
        # КОНЕЦ логики определения времени

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

            # ДОБАВЛЕНИЕ МАРКЕРА: Добавляем маркер в комментарий.
            # Комментарий был пуст, поэтому он будет содержать ТОЛЬКО маркер.
            marker_with_timestamp = f"[{now_moscow.strftime('%Y-%m-%d %H:%M')}] {COMMENT_TASK_MARKER}"
            update_response = update_order_comment(order_id, marker_with_timestamp)
            if update_response.get('success'):
                print(f"  ✅ Комментарий к заказу обновлен маркером {COMMENT_TASK_MARKER}.")
            else:
                print(f"  ❌ Ошибка при обновлении комментария маркером {COMMENT_TASK_MARKER}: {update_response}")

        else:
            print(f"  ❌ Ошибка при создании задачи 'Заполнить комментарий': {response}")

        print("-" * 50)
        return  # Прекращаем обработку, т.к. комментарий был пуст и задача создана

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
                corrected_datetime_str = get_corrected_datetime(task_date_str, now_moscow)

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
                        operator_comment = new_comment  # Обновляем локальную переменную, чтобы избежать ошибок
                    else:
                        print(f"    ❌ Ошибка при обновлении комментария: {update_response}")
                else:
                    print(f"    ❌ Ошибка при создании задачи #{i + 1}: {response}")

            except (ValueError, TypeError) as e:
                print(f"    Ошибка при обработке задачи #{i + 1}: {e}. Пропускаем.")

    else:
        # --- ЛОГИКА: Неформализованный комментарий (Сценарий В) ---
        # Здесь мы уверены, что маркера CONTACT_TASK_MARKER нет, благодаря проверке в начале функции
        print("  ❌ OpenAI не нашел явных задач в строгом формате 'ДАТА - ДЕЙСТВИЕ'.")

        # Логика задачи: Запланировать дату касания на завтра в 10:00
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

            # ДОБАВЛЕНИЕ МАРКЕРА: Добавляем маркер в конец комментария.
            # Добавляем на новую строку для чистоты.
            new_comment = f"{operator_comment}\n[{now_moscow.strftime('%Y-%m-%d %H:%M')}] {CONTACT_TASK_MARKER}"
            update_response = update_order_comment(order_id, new_comment)
            if update_response.get('success'):
                print(f"    ✅ Комментарий к заказу обновлен маркером {CONTACT_TASK_MARKER}.")
            else:
                print(f"    ❌ Ошибка при обновлении комментария: {update_response}")

        else:
            print(f"  ❌ Ошибка при создании задачи 'запланировать дату касания': {response}")

    print("-" * 50)


# --- НОВАЯ ФУНКЦИЯ: РЕГЛАМЕНТ ДЛЯ ПРОПУЩЕННЫХ ЗВОНКОВ ---

def process_missed_call_reglament(orders_list: list, now_moscow: datetime):
    """
    Обрабатывает список заказов по регламенту "Входящий звонок":
    создает 9 задач на 3 дня, если заказ еще не был обработан.
    """
    print(f"\n--- Запуск регламента для {len(orders_list)} заказов ({MISSED_CALL_METHOD}) ---")

    # ЖЕСТКО ЗАКОДИРОВАННЫЙ ГРАФИК ЗАДАЧ
    # Время указано относительно СЕГОДНЯШНЕГО запуска (now_moscow)
    # 1. Задачи на сегодняшний день (1-й день)
    #    - Первая задача: Текущее время + 10 минут
    #    - Вторая задача: + 1 час от первой задачи
    #    - Третья задача: Конец рабочего дня (17:00)

    task_schedule = [
        # ДЕНЬ 1 (Начинается с Now + 10 мин)
        {
            'text': "1-й звонок с линии + WA",
            'commentary': "Звонок с линии по Softfone + сразу же отправляем клиенту шаблон в WA о недозвоне. (Первая попытка)",
            'day_offset': 0,
            'time_offset_minutes': 10
        },
        {
            'text': "2-й звонок с iPhone (через час)",
            'commentary': "2 звонок с другого номера iPhone через час. (Вторая попытка)",
            'day_offset': 0,
            'time_hour': 0,  # Временно 0, будет рассчитано как 1-я задача + 1 час
            'is_relative': True
        },
        {
            'text': "3-й звонок с HUAWEI (конец дня)",
            'commentary': "3 звонок с третьего номера HUAWEI в конце рабочего дня. (Третья попытка)",
            'day_offset': 0,
            'time_hour': 17,
            'time_minute': 0
        },

        # ДЕНЬ 2 (Завтра)
        {
            'text': "4-й звонок (Утро + WA Check)",
            'commentary': "Проверяем, прочитал ли клиент сообщение в WA, и если нет, звоним еще раз с утра (около 11:00). (Четвертая попытка)",
            'day_offset': 1,
            'time_hour': 11,
            'time_minute': 0
        },
        {
            'text': "5-й звонок с iPhone",
            'commentary': "2 звонок в середине дня с iPhone (около 14:00). (Пятая попытка)",
            'day_offset': 1,
            'time_hour': 14,
            'time_minute': 0
        },
        {
            'text': "6-й звонок с HUAWEI",
            'commentary': "3 звонок с третьего номера HUAWEI (около 17:00). (Шестая попытка)",
            'day_offset': 1,
            'time_hour': 17,
            'time_minute': 0
        },

        # ДЕНЬ 3 (Послезавтра)
        {
            'text': "7-й звонок (Утро + WA Check)",
            'commentary': "Проверяем, прочитал ли клиент сообщение в WA, и если нет, звоним еще раз с утра (около 11:00). (Седьмая попытка)",
            'day_offset': 2,
            'time_hour': 11,
            'time_minute': 0
        },
        {
            'text': "8-й звонок с iPhone",
            'commentary': "2 звонок в середине дня с iPhone (около 14:00). (Восьмая попытка)",
            'day_offset': 2,
            'time_hour': 14,
            'time_minute': 0
        },
        {
            'text': "9-й звонок с HUAWEI + WA Cancel",
            'commentary': "3 звонок с третьего номера HUAWEI (около 17:00) и если снова не дозвон, то отправляем в WhatsApp шаблон первого сообщения “НДЗ - 3-й день (отмена)”. (Последняя попытка)",
            'day_offset': 2,
            'time_hour': 17,
            'time_minute': 0
        },
    ]

    for order_data in orders_list:
        order_id = order_data.get('id')
        manager_id = order_data.get('managerId')
        operator_comment = order_data.get('managerComment', '')

        print(f"Начинаю обработку регламента для заказа ID: {order_id}")

        if not manager_id:
            print(f"  В заказе {order_id} нет менеджера. Пропускаю.")
            continue

        # Проверка на маркер, предотвращающий повторный запуск регламента
        if MISSED_CALL_TASK_MARKER in operator_comment:
            print(f"  ✅ Заказ {order_id} уже имеет маркер '{MISSED_CALL_TASK_MARKER}'. Пропускаю.")
            print("-" * 50)
            continue

        # Запуск первой задачи (Now + 10 минут)
        first_task_time = now_moscow + timedelta(minutes=10)

        # Переменная для расчета относительного времени (для 2-й задачи)
        last_task_time = first_task_time

        all_tasks_created = True

        for task_config in task_schedule:

            # --- РАСЧЕТ ВРЕМЕНИ ЗАДАЧИ ---
            task_dt = None

            if task_config.get('is_relative'):
                # Вторая задача: + 1 час от первой задачи
                task_dt = last_task_time + timedelta(hours=1)
            elif task_config['day_offset'] == 0:
                # Первая и Третья задача 1-го дня
                if task_config.get('time_offset_minutes'):
                    # 1-я задача
                    task_dt = first_task_time
                elif task_config.get('time_hour'):
                    # 3-я задача (на 17:00)
                    target_time = now_moscow.replace(hour=task_config['time_hour'], minute=task_config['time_minute'],
                                                     second=0, microsecond=0)
                    if target_time > first_task_time:
                        # Если 17:00 еще не наступило, ставим на 17:00 сегодня
                        task_dt = target_time
                    else:
                        # Если 17:00 уже прошло, переносим на завтра на 17:00
                        task_dt = (now_moscow + timedelta(days=1)).replace(hour=task_config['time_hour'],
                                                                           minute=task_config['time_minute'], second=0,
                                                                           microsecond=0)
            else:
                # Задачи 2-го и 3-го дня (абсолютное время)
                task_dt = (now_moscow + timedelta(days=task_config['day_offset'])).replace(
                    hour=task_config['time_hour'],
                    minute=task_config['time_minute'],
                    second=0, microsecond=0
                )

            # Обновляем last_task_time для следующей относительной задачи (хотя в нашем регламенте она одна)
            if task_config['day_offset'] == 0 and not task_config.get('is_relative'):
                last_task_time = task_dt

            # Проверка, что дата не в прошлом (на всякий случай, хотя логика выше должна это предотвратить)
            if task_dt < now_moscow - timedelta(minutes=5):
                print(
                    f"    ⚠️ Задача '{task_config['text']}' имеет прошедшее время {task_dt.strftime('%Y-%m-%d %H:%M')}. Пропускаю.")
                continue  # Пропускаем задачи, если время в прошлом

            # --- СОЗДАНИЕ ЗАДАЧИ ---

            task_data = {
                'text': task_config['text'],
                'commentary': task_config['commentary'],
                'datetime': task_dt.strftime('%Y-%m-%d %H:%M'),
                'performerId': manager_id,
                'order': {'id': order_id}
            }

            response = create_task(task_data)

            if response.get('success'):
                print(
                    f"    ✅ Задача '{task_config['text']}' успешно создана на {task_dt.strftime('%Y-%m-%d %H:%M')}. ID: {response.get('id')}")
            else:
                print(f"    ❌ Ошибка при создании задачи '{task_config['text']}': {response}")
                all_tasks_created = False  # Если одна задача не создалась, ставим флаг

        # --- ДОБАВЛЕНИЕ МАРКЕРА ---
        if all_tasks_created:
            # Добавляем маркер в комментарий, чтобы не запускать регламент повторно
            marker_with_timestamp = f"\n[{now_moscow.strftime('%Y-%m-%d %H:%M')}] {MISSED_CALL_TASK_MARKER}"
            new_comment = f"{operator_comment}{marker_with_timestamp}"

            update_response = update_order_comment(order_id, new_comment)
            if update_response.get('success'):
                print(f"  ✅ Комментарий к заказу ID {order_id} обновлен маркером '{MISSED_CALL_TASK_MARKER}'.")
            else:
                print(f"  ❌ Ошибка при обновлении комментария маркером: {update_response}")
        else:
            print(
                f"  ❌ Не все задачи для заказа ID {order_id} были созданы. Маркер '{MISSED_CALL_TASK_MARKER}' НЕ добавлен.")

        print("-" * 50)


def main():
    """Главная функция для запуска периодической обработки."""
    print("Запускаю периодическую проверку новых заказов...")

    # Шаг 0: Определяем текущее время для условного запуска новой логики
    now_moscow = datetime.now(MOSCOW_TZ)
    current_time_str = now_moscow.strftime('%H:%M')
    current_hour = now_moscow.hour
    is_evening_run = current_hour == 21  # Проверяем, что сейчас 21:xx

    # --- БЛОК 1: Проверка на "зависшие" статусы ---
    process_status_trackers(now_moscow)

    # --- БЛОК 2: НОВЫЙ РЕГЛАМЕНТ ДЛЯ ПРОПУЩЕННЫХ ЗВОНКОВ (12:00 и 16:00) ---
    if current_hour == 12 or current_hour == 16:
        print(f"\n--- Запускаю регламент для пропущенных звонков (Время: {current_time_str}) ---")

        date_from = None
        date_to = now_moscow.strftime('%Y-%m-%d %H:%M:%S')

        if current_hour == 12:
            # Запуск в 12:00: с 16:01 предыдущего дня до 12:00 текущего дня
            yesterday_1601 = (now_moscow - timedelta(days=1)).replace(hour=16, minute=1, second=0, microsecond=0)
            date_from = yesterday_1601.strftime('%Y-%m-%d %H:%M:%S')

        elif current_hour == 16:
            # Запуск в 16:00: с 12:01 до 16:00 текущего дня
            today_1201 = now_moscow.replace(hour=12, minute=1, second=0, microsecond=0)
            date_from = today_1201.strftime('%Y-%m-%d %H:%M:%S')

        print(f"  Ищем заказы ({MISSED_CALL_METHOD}) в диапазоне: {date_from} — {date_to}")

        # Получаем заказы из CRM
        missed_call_orders_data = get_orders_by_method_and_date_range(MISSED_CALL_METHOD, date_from, date_to)

        if missed_call_orders_data:
            missed_call_orders = missed_call_orders_data.get('orders', [])
            print(f"  Найдено {len(missed_call_orders)} новых заказов по методу '{MISSED_CALL_METHOD}'.")
            process_missed_call_reglament(missed_call_orders, now_moscow)
        else:
            print(f"  Новые заказы по методу '{MISSED_CALL_METHOD}' в заданном диапазоне не найдены.")
            print("-" * 50)

    # --- БЛОК 3: Проверка не доставленных сегодня заказов (только в 21:00) ---
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

    # --- БЛОК 4: Обработка последних 50 заказов для анализа комментариев (ОСТАВЛЕНО) ---
    # Переименован БЛОК 3 в БЛОК 4
    print("\n--- Запускаю обработку последних 50 заказов для анализа комментариев ---")

    # Шаг 1: Получаем последние 50 заказов
    orders_data = get_recent_orders(limit=50)

    if not orders_data:
        print("Ошибка при получении списка последних заказов. Завершение работы блока.")
    else:
        orders = orders_data.get('orders', [])

        if not orders:
            print("Нет новых заказов для обработки. Завершение работы блока.")
        else:
            print(f"Найдено {len(orders)} последних заказов.")

            # Шаг 2: Обрабатываем каждый заказ из полученного списка
            for order_data in orders:
                process_order(order_data)

    print("\nОбработка завершена.")


if __name__ == "__main__":
    main()
