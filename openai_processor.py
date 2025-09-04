import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Dict, Any
from datetime import datetime

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
client = OpenAI(api_key=OPENAI_API_KEY)

PROMPT_TEMPLATE = """
Текущая дата: {current_date}
Проанализируй следующий текст из комментария оператора в RetailCRM. Извлеки из него все задачи, которые нужно создать. Для каждой задачи определи:
1. Тип задачи (выбери один из следующих: 'Звонок', 'WhatsApp', 'Email', 'Шоурум').
2. Дату и время выполнения (в формате 'YYYY-MM-DD HH:MM'). Если время не указано, используй '10:00'. Если дата не указана, используй текущую дату.
3. Короткий и понятный текст задачи.
4. Дополнительный комментарий к задаче.

Важные правила:
- Верни результат в виде массива JSON-объектов.
- Верни ТОЛЬКО JSON-код, без каких-либо пояснений, заголовков или дополнительного текста.
- Если в тексте нет явных задач, верни пустой JSON-массив: [].
- Если в тексте указана только дата без года, используй текущий год.

Пример ответа:
[
  {{
    "type": "WhatsApp",
    "date_time": "2025-09-05 10:00",
    "task": "Написать клиенту",
    "commentary": "Напомнить о предложении, связанном с акцией на доставку"
  }}
]

Текст для анализа: "{comment_text}"
"""


def analyze_comment_with_openai(comment_text: str) -> List[Dict[str, Any]]:
    print("Анализ комментария с помощью OpenAI...")
    try:
        current_date_str = datetime.now().strftime('%Y-%m-%d')
        formatted_prompt = PROMPT_TEMPLATE.format(
            current_date=current_date_str,
            comment_text=comment_text
        )

        completion = client.chat.completions.create(
            model="gpt-4o",  # Рекомендую использовать более новую модель для лучшей производительности
            messages=[{"role": "user", "content": formatted_prompt}],
            temperature=0.1
        )

        raw_response = completion.choices[0].message.content
        print(f"Сырой ответ от OpenAI: {raw_response}")

        # Надежное извлечение и очистка JSON
        json_string = raw_response.strip()
        if json_string.startswith("```json"):
            json_string = json_string[7:].strip()
        if json_string.endswith("```"):
            json_string = json_string[:-3].strip()

        # Проверка на пустые строки и невалидные символы перед парсингом
        if not json_string.startswith("[") or not json_string.endswith("]"):
            print("Не удалось найти валидный JSON-массив в ответе от OpenAI.")
            return []

        parsed_tasks = json.loads(json_string)

        if not isinstance(parsed_tasks, list):
            print("Ошибка: Ответ OpenAI не является списком.")
            return []
        if not all(isinstance(task, dict) for task in parsed_tasks):
            print("Ошибка: Один из элементов в ответе OpenAI не является словарем.")
            return []

        return parsed_tasks

    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON ответа от OpenAI: {e}. Сырой ответ: {raw_response}")
        return []
    except Exception as e:
        print(f"Ошибка при обращении к OpenAI API: {e}")
        return []