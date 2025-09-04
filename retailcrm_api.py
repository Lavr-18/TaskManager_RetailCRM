import os
import requests
import json
from dotenv import load_dotenv
from typing import Dict, Any, Optional

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
    params["apiKey"] = RETAILCRM_API_KEY

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при запросе к RetailCRM API (endpoint: {endpoint}): {e}")
        return {}


def post_data_to_retailcrm(endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Универсальная функция для POST-запросов с данными в формате application/x-www-form-urlencoded."""
    url = f"{RETAILCRM_BASE_URL}/api/v5/{endpoint}"
    params = {"apiKey": RETAILCRM_API_KEY}

    try:
        response = requests.post(url, params=params, data=data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при POST-запросе к RetailCRM API (endpoint: {endpoint}): {e}")
        return {}


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


def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    """Получает полные данные заказа по его внутреннему ID."""
    print(f"Запрос полных данных заказа {order_id}...")
    params = {'filter[ids][]': order_id}
    data = fetch_data_from_retailcrm("orders", params=params)
    if data.get('success') and data.get('orders'):
        return data['orders'][0]
    return None


def create_task(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Создает новую задачу в RetailCRM."""
    print("Попытка создать задачу в RetailCRM...")

    # Формируем словарь задачи
    task_payload = {
        'text': task_data.get('text', 'Новая задача'),
        'commentary': task_data.get('commentary', ''),
        'datetime': task_data.get('datetime'),
        'performerId': task_data.get('performerId'),
        'order': {'id': task_data.get('order', {}).get('id')}
    }

    # Сериализуем словарь задачи в JSON-строку
    task_json_string = json.dumps(task_payload)

    # Формируем итоговый POST-запрос
    payload = {
        'site': RETAILCRM_SITE_CODE,
        'task': task_json_string
    }

    return post_data_to_retailcrm('tasks/create', data=payload)


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
        'site': RETAILCRM_SITE_CODE,
        'order': order_json_string,
        'by': 'id'  # Добавляем параметр для указания, что используется внутренний ID
    }

    return post_data_to_retailcrm(f'orders/{order_id}/edit', data=payload)