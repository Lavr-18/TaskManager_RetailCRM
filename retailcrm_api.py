# retailcrm_api.py

import os
import requests
import json
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List

load_dotenv()

RETAILCRM_BASE_URL = os.getenv('RETAILCRM_BASE_URL')
RETAILCRM_API_KEY = os.getenv('RETAILCRM_API_KEY')
RETAILCRM_SITE_CODE = os.getenv('RETAILCRM_SITE_CODE')

REQUEST_TIMEOUT = 120  # seconds


def fetch_data_from_retailcrm(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Универсальная функция для GET-запросов к RetailCRM API."""
    url = f"{RETAILCRM_BASE_URL}/api/v5/{endpoint}"
    if params is None:
        params = {}

    # ВОЗВРАЩАЕМСЯ К ИСХОДНОМУ РЕШЕНИЮ: Передаем apiKey и site как параметры URL
    params["apiKey"] = RETAILCRM_API_KEY
    params["site"] = RETAILCRM_SITE_CODE

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при запросе к RetailCRM API (endpoint: {endpoint}): {e}")
        return {}


def post_data_to_retailcrm(endpoint: str, data: Dict[str, Any], use_json: bool = False) -> Dict[str, Any]:
    """
    Универсальная функция для POST-запросов к RetailCRM API.
    Обрабатывает ошибки и выводит детали.
    """
    url = f"{RETAILCRM_BASE_URL}/api/v5/{endpoint}"

    # API-ключ и сайт передаются в POST-параметрах
    params = {
        "apiKey": RETAILCRM_API_KEY,
        "site": RETAILCRM_SITE_CODE
    }

    try:
        if use_json:
            print(f"Отправляемый JSON-payload: {json.dumps(data, indent=2)}")
            response = requests.post(url, params=params, json=data, timeout=REQUEST_TIMEOUT)
        else:
            print(f"Отправляемые form-data: {data}")
            response = requests.post(url, params=params, data=data, timeout=REQUEST_TIMEOUT)

        response.raise_for_status()  # Вызовет исключение для ошибок 4xx/5xx
        return response.json()
    except requests.exceptions.RequestException as e:
        # Детальный вывод ошибок
        error_info = f"Ошибка при POST-запросе к RetailCRM API (endpoint: {endpoint}): {e}"
        if e.response is not None:
            try:
                error_details = e.response.json()
                error_info += f". Детали: {error_details}"
            except json.JSONDecodeError:
                error_info += f". Текст ответа: {e.response.text}"
        print(error_info)
        return {"success": False, "error": error_info}


def get_order_history(since_id: Optional[int] = None) -> Dict[str, Any]:
    """Получает историю изменений заказов по ID. Устаревший метод для нашей новой логики."""
    params = {'filter[sinceId]': since_id} if since_id else {}
    return fetch_data_from_retailcrm("orders/history", params=params)


def get_order_history_by_dates(start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Получает историю изменений заказов в заданном диапазоне дат.
    Формат дат: Y-m-d H:i:s.
    """
    print(f"Запрос истории изменений с {start_date} до {end_date}...")
    params = {
        'filter[startDate]': start_date,
        'filter[endDate]': end_date
    }
    return fetch_data_from_retailcrm("orders/history", params=params)


def get_recent_orders(limit: int = 50) -> Optional[Dict[str, Any]]:
    """
    Получает последние заказы из RetailCRM.
    """
    print(f"Запрос последних {limit} заказов...")
    params = {'limit': limit}
    data = fetch_data_from_retailcrm("orders", params=params)
    if data.get('success') and data.get('orders'):
        return data
    return None


def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    """Получает полные данные заказа по его внутреннему ID."""
    print(f"Запрос полных данных заказа {order_id}...")
    params = {'filter[ids][]': order_id}
    data = fetch_data_from_retailcrm("orders", params=params)
    if data.get('success') and data.get('orders'):
        return data['orders'][0]
    return None


def create_task(task_data: dict) -> dict:
    """
    Создает задачу в RetailCRM, сериализуя данные в JSON-строку.
    """
    print("Попытка создать задачу в RetailCRM...")

    # Сериализуем словарь задачи в JSON-строку
    task_json_string = json.dumps(task_data)

    # Формируем итоговый payload для отправки в виде form-data
    payload = {
        'task': task_json_string
    }

    # Отправляем form-data (use_json=False)
    return post_data_to_retailcrm('tasks/create', data=payload, use_json=False)


def update_order_comment(order_id: int, new_comment: str) -> Dict[str, Any]:
    """
    Обновляет комментарий менеджера в заказе.
    """
    print(f"Попытка обновить комментарий для заказа ID: {order_id}...")

    # Формируем словарь заказа с обновленным комментарием
    order_payload = {
        'id': order_id,
        'managerComment': new_comment
    }

    # Сериализуем словарь заказа в JSON-строку
    order_json_string = json.dumps(order_payload)

    # Формируем итоговый POST-запрос для обновления
    payload = {
        'order': order_json_string,
        'by': 'id'  # Добавляем параметр для указания, что используется внутренний ID
    }

    # Используем универсальную POST-функцию
    return post_data_to_retailcrm(f'orders/{order_id}/edit', data=payload)


def get_orders_by_delivery_date(date_str: str) -> Optional[Dict[str, Any]]:
    """
    Получает заказы из RetailCRM, у которых дата доставки совпадает с указанной.
    Формат даты: YYYY-MM-DD.
    Устанавливает лимит 100 для обработки всех заказов с доставкой на сегодня.
    """
    print(f"Запрос заказов с датой доставки: {date_str}...")
    params = {
        'filter[deliveryDateFrom]': date_str,
        'filter[deliveryDateTo]': date_str,
        'limit': 100
    }
    data = fetch_data_from_retailcrm("orders", params=params)
    if data.get('success') and data.get('orders'):
        return data
    return None


def get_orders_by_statuses(statuses: List[str]) -> Optional[Dict[str, Any]]:
    """
    Получает заказы из RetailCRM, находящиеся в одном из указанных статусов.
    Использует filter[extendedStatus][] для множественного запроса.
    Устанавливает лимит 100 для обработки (только первая страница).
    """
    # Убедитесь, что List импортирован: from typing import Dict, Any, Optional, List
    print(f"Запрос заказов со статусами: {', '.join(statuses)}...")

    # requests автоматически преобразует список в повторяющиеся параметры типа filter[extendedStatus][]=status1&...
    params = {
        'filter[extendedStatus][]': statuses,
        'limit': 100
    }

    data = fetch_data_from_retailcrm("orders", params=params)

    if data.get('success') and data.get('orders'):
        return data
    return None
