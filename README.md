# RetailCRM Task Manager 🤖

## Описание проекта
Этот проект представляет собой автоматизированный скрипт, который анализирует комментарии менеджеров в **RetailCRM** с помощью искусственного интеллекта (**OpenAI**) и на их основе автоматически создаёт задачи.  
Это помогает оптимизировать рабочий процесс, гарантируя, что ни одна важная задача не будет упущена.

---

## Ключевые особенности
- **Интеллектуальный анализ**: скрипт использует API OpenAI для распознавания задач в свободных текстовых комментариях менеджеров.  
- **Автоматизация**: задачи в RetailCRM создаются автоматически с указанием даты, времени и ответственного менеджера.  
- **Периодический запуск**: скрипт запускается по расписанию (в 12:00 и 20:00 по МСК) для обработки недавних изменений в заказах.  
- **Защита от повторной обработки**: каждая обработанная строка комментария помечается маркером ✅ для исключения дублирования задач.  
- **Простое развертывание**: проект упакован в Docker-контейнер для лёгкой установки на любой сервер с Docker и Cron.  

---

## Архитектура
Проект работает по следующей схеме:
1. Планировщик **Cron** запускает Docker-контейнер в заданное время.  
2. Скрипт `main.py` обращается к **RetailCRM API** для получения истории изменений заказов.  
3. Извлекаются последние необработанные строки из комментариев и отправляются в **OpenAI API**.  
4. OpenAI анализирует текст, возвращая задачи в структурированном виде.  
5. Скрипт создаёт задачи в **RetailCRM** и обновляет комментарий, добавляя ✅ к обработанной строке.  

---

## Настройка и запуск

### Локальная настройка
Клонируйте репозиторий:
```bash
git clone https://github.com/Lavr-18/TaskManager_RetailCRM.git
cd TaskManager_RetailCRM
```

Создайте файл **.env** в корне проекта:
```ini
RETAILCRM_BASE_URL=https://<ваш_аккаунт>.retailcrm.ru
RETAILCRM_API_KEY=<ваш_API_ключ>
RETAILCRM_SITE_CODE=<код_сайта>
OPENAI_API_KEY=<ваш_ключ_OpenAI>
```

Установите зависимости:
```bash
pip install -r requirements.txt
```

Запустите тест:
```bash
python test_script.py
```

---

### Развертывание на сервере
Используется связка **Docker + Cron**.

1. Подготовка сервера:
```bash
sudo apt update
sudo apt install git docker.io -y
sudo usermod -aG docker $USER
newgrp docker
```

2. Развертывание проекта:
```bash
mkdir ~/task_manager && cd ~/task_manager
git clone https://github.com/Lavr-18/TaskManager_RetailCRM.git ..
nano .env   # создайте .env вручную
docker build -t task_manager_cron ..
```

3. Настройка Cron:
```cron
# Запуск в 12:00 по московскому времени
0 12 * * * CRON_TZ=Europe/Moscow docker run --rm -v ~/task_manager/.env:/app/.env task_manager_cron python main.py >> ~/task_manager/cron.log 2>&1

# Запуск в 20:00 по московскому времени
0 20 * * * CRON_TZ=Europe/Moscow docker run --rm -v ~/task_manager/.env:/app/.env task_manager_cron python main.py >> ~/task_manager/cron.log 2>&1
```

---

## Структура проекта
```
.
├── .env                  # Конфиденциальные данные (не в Git)
├── .gitignore            # Файлы для исключения из репозитория
├── Dockerfile            # Инструкции для сборки Docker-образа
├── main.py               # Основная логика скрипта
├── openai_processor.py   # Взаимодействие с OpenAI API
├── requirements.txt      # Зависимости Python
├── retailcrm_api.py      # Взаимодействие с RetailCRM API
└── test_script.py        # Скрипт для ручного тестирования
```
