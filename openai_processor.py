import os
import openai
import json
import re
from dotenv import load_dotenv
from typing import List, Dict, Any

load_dotenv()

# Устанавливаем ключ API из переменных окружения
openai.api_key = os.getenv('OPENAI_API_KEY')


def analyze_comment_with_openai(comment: str) -> List[Dict[str, Any]]:
    """
    Отправляет комментарий на анализ в OpenAI и возвращает список найденных задач
    в виде JSON-объектов.
    """
    if not openai.api_key:
        print("Ошибка: Ключ OpenAI API не установлен.")
        return []

    system_prompt = """
    Ты — продвинутый ассистент, который анализирует комментарии менеджеров к заказам в CRM-системе.
    Твоя задача — находить в тексте будущие задачи (такие как "позвонить", "отправить", "связаться") и извлекать по ним ключевую информацию.

    При анализе учитывай следующие правила:
    - Игнорируй любые записи, которые не являются будущими задачами, например, "дубль заказа", "обратной связи нет", "бросила трубку".
    - Игнорируй записи с прошедшими датами или те, которые описывают уже произошедшие события.
    - Извлекай только явные задачи, которые нужно выполнить в будущем.
    - Если в комментарии нет явных задач, которые нужно поставить, верни пустой список [].

    Твой ответ должен быть в формате JSON-массива, где каждый элемент — это объект с тремя полями:
    - "task": краткое описание задачи, например "Написать на WhatsApp", "Позвонить".
    - "date_time": дата и время выполнения задачи в формате ГГГГ-ММ-ДД ЧЧ:ММ. Используй текущий год и разумное время, если оно не указано (например, 10:00 утра). Если указан только день недели, используй ближайшую дату.
    - "marked_line": точная строка из исходного текста, которая содержит эту задачу. Эта строка будет использоваться для проставления маркера ✅.

    Пример входящего комментария:
    "21,08 - обратной связи нет, сказал сама напишет
    написать на вотс ап 6ого сентября
    дубль заказа 50619"

    Пример ожидаемого JSON-ответа:
    [
      {
        "task": "Написать на WhatsApp",
        "date_time": "2025-09-06 10:00",
        "marked_line": "написать на вотс ап 6ого сентября"
      }
    ]
    """

    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": comment}
            ]
        )

        raw_content = response.choices[0].message.content
        print(f"Сырой ответ от OpenAI: ```json\n{raw_content}\n```")

        # Удаляем лишние символы из ответа, если они есть
        clean_content = re.sub(r'```json\n|```', '', raw_content).strip()

        # Загружаем JSON-данные
        parsed_data = json.loads(clean_content)

        # Проверяем, что ответ является списком
        if isinstance(parsed_data, list):
            return parsed_data

        print("Ошибка: Ответ OpenAI не является списком.")
        return []

    except json.JSONDecodeError as e:
        print(f"Ошибка декодирования JSON: {e}")
        return []
    except openai.APIError as e:
        print(f"Ошибка при запросе к OpenAI API: {e}")
        return []