# Nextcloud Talk Bot

Python библиотека для создания ботов в Nextcloud Talk с API, похожим на python-telegram-bot.

## Возможности

- 🚀 **Простой API** — интерфейс, похожий на python-telegram-bot
- 📝 **Отправка сообщений** с поддержкой Markdown
- 📎 **Отправка файлов** (документы, фото, любые вложения)
- 🔄 **Автоматическое управление членством** в комнатах
- 💬 **Поддержка threading и ответов на сообщения**
- 🔌 **Автоматическое определение версии API** (v1-v4)
- 🛡️ **Автоматическая переавторизация** при ошибках
- 📡 **Polling режим** для получения сообщений

## Требования

- Python 3.7+
- Nextcloud 33.x и выше (с установленным приложением Talk)
- Аккаунт бота в Nextcloud (рекомендуется использовать токен приложения)

## Установка

### Из исходного кода

```bash
git clone https://github.com/your-username/nextcloud-talk-bot.git
cd nextcloud-talk-bot
pip install -r requirements.txt
```

### Использование как библиотеки

Скопируйте папку `core/` в ваш проект или установите как модуль:

```python
# Просто скопируйте файлы в ваш проект
from nextcloudbot import Bot
```

## Быстрый старт

### Минимальный пример

```python
from nextcloudbot import Bot

# Создаем бота
bot = Bot(
    host="https://nextcloud.example.com",
    user="bot_user",
    password="your-app-token",  # или пароль
    default_room="room_token_here"
)

# Отправляем сообщение
bot.send_message("Hello, World!")

# Запускаем получение сообщений
bot.run_polling()
```

### Бот с обработкой команд

```python
from nextcloudbot import Bot

bot = Bot(
    host="https://nextcloud.example.com",
    user="my_bot",
    password="app-xxxxx",
    default_room="room_token"
)

# Обработчик команды /start
@bot.command("start")
def start_command(update, context):
    update.message.reply_text("👋 Привет! Я бот для Nextcloud Talk!")

# Обработчик команды /help
@bot.command("help")
def help_command(update, context):
    help_text = """
🤖 **Доступные команды:**
/start - Приветствие
/help - Эта справка
/echo <текст> - Повторить сообщение
    """
    update.message.reply_text(help_text)

# Обработчик команды /echo
@bot.command("echo")
def echo_command(update, context):
    # Получаем текст после команды
    text = update.message.text.replace("/echo", "").strip()
    if text:
        update.message.reply_text(f"🔊 {text}")
    else:
        update.message.reply_text("❌ Напишите что-нибудь после /echo")

# Обработчик всех текстовых сообщений
@bot.message_handler
def handle_message(update, context):
    message = update.message
    user = message.from_user
    print(f"Получено сообщение от {user.full_name}: {message.text}")
    message.reply_text(f"Получил: {message.text}")

# Запускаем бота
if __name__ == "__main__":
    bot.run_polling()
```

## Отправка сообщений

### Текстовые сообщения

```python
# Простой текст
bot.send_message("Hello, World!")

# Markdown форматирование
bot.send_message("**Жирный текст** и *курсив*")

# Ответ на сообщение
bot.send_message(
    chat_id="room_token", # optional
    text="Это ответ на ваше сообщение",
    reply_to_message_id=12345
)
```

### Отправка файлов

```python
# Отправить файл с подписью
bot.send_message(
    chat_id="room_token", # optional
    text="Смотрите документ",
    file_path="/path/to/document.pdf"
)

# Отправить фото
bot.send_file("/path/to/photo.jpg", caption="Красивый закат")

# Или через метод send_photo
bot.send_photo(
    "room_token", # optional
    "/path/to/photo.jpg",
    caption="Красивый закат"
)

# Отправить документ
bot.send_document(
    "room_token", # optional
    "/path/to/report.pdf",
    caption="Ежемесячный отчет"
)

# Отправить файл из памяти (например, сгенерированный)
import io

# Создаем файл в памяти
file_content = b"Hello, this is a text file!"
bot.send_message(
    chat_id="room_token", # optional
    text="Сгенерированный файл",
    file_content=file_content,
    file_name="hello.txt",
    mime_type="text/plain"
)

# Отправить файл из BytesIO
from io import BytesIO

buffer = BytesIO()
buffer.write(b"Some data")
buffer.seek(0)

bot.send_document(
    "room_token", # optional
    buffer,
    caption="Данные из буфера"
)
```

## Работа с сообщениями

### Получение сообщений через polling

```python
# Запускаем получение сообщений
bot.run_polling(chat_id="room_token", poll_interval=2)

# Параметры:
# - chat_id: токен комнаты (если не указан, используется default_room)
# - poll_interval: интервал опроса в секундах (по умолчанию 2)
```

### Объект Message

При получении сообщения вы получаете объект `Message` со следующими атрибутами:

```python
@bot.message_handler
def handle_message(update, context):
    message = update.message
    
    # Основные атрибуты
    print(f"ID: {message.message_id}")
    print(f"Текст: {message.text}")
    print(f"От: {message.from_user.full_name}")
    print(f"Время: {message.date}")
    
    # Ответить на сообщение
    message.reply_text("Получил ваше сообщение!")
    
    # Или использовать метод reply (алиас)
    message.reply("Тоже самое")
```

## Управление комнатами

### Присоединение к комнате

```python
# Автоматическое присоединение при запуске
bot = Bot(
    host="https://nextcloud.example.com",
    user="bot_user",
    password="password",
    default_room="room_token",
    auto_join_room=True  # По умолчанию True
)

# Ручное присоединение
bot.join_room("room_token", password="room_password")
```

### Получение информации о комнатах

```python
# Список всех доступных комнат
rooms = bot.get_rooms()
for room in rooms:
    print(f"Комната: {room.get('name')} ({room.get('token')})")
    print(f"Участников: {room.get('participantCount')}")

# Информация о конкретной комнате
room_info = bot.get_room_info("room_token")
print(f"Название: {room_info.get('name')}")
print(f"Тип: {room_info.get('type')}")

# Список участников
participants = bot.get_room_participants("room_token")
for p in participants:
    print(f"Участник: {p.get('actorDisplayName')}")
```

## Диагностика

### Проверка подключения

```python
# Проверка статуса сессии
status = bot.check_session_status()
if status['authenticated']:
    print(f"✅ Подключен как: {status['user']}")
else:
    print("❌ Ошибка аутентификации")

# Диагностика доступа к комнате
diagnostic = bot.diagnose_room_access("room_token")
print(json.dumps(diagnostic, indent=2, ensure_ascii=False))
```

### Логирование

Библиотека использует `loguru` для логирования. Вы можете настроить уровень логирования:

```python
from loguru import logger

# Установка уровня логирования
logger.remove()  # Удаляем стандартный обработчик
logger.add(sys.stderr, level="INFO")  # Только INFO и выше
# или
logger.add(sys.stderr, level="DEBUG")  # Подробное логирование
```

## Конфигурация

### Параметры Bot

| Параметр | Тип | По умолчанию | Описание                                                         |
|----------|-----|--------------|------------------------------------------------------------------|
| `host` | str | - | URL Nextcloud сервера                                            |
| `user` | str | - | Имя пользователя бота                                            |
| `password` | str | - | Пароль или токен приложения                                      |
| `default_room` | str | None | Токен комнаты по умолчанию                                       |
| `read_all_chat` | bool | False | Читать всю историю (c новых к старым) или только новые сообщения |
| `auto_join_room` | bool | True | Автоматически присоединяться к комнатам                          |


## Примеры

### Простой эхо-бот

```python
from nextcloud import Bot

bot = Bot(
    host="https://nextcloud.example.com",
    user="echo_bot",
    password="app-xxxxx",
    default_room="room_token"
)

@bot.message_handler
def echo(update, context):
    message = update.message
    if message.text:
        message.reply_text(f"🔊 Эхо: {message.text}")
    else:
        message.reply_text("Напишите текст, я его повторю!")

bot.run_polling()
```

### Бот для отправки уведомлений

```python
import time
from nextcloud import Bot

class NotificationBot:
    def __init__(self, host, user, password, room_token):
        self.bot = Bot(
            host=host,
            user=user,
            password=password,
            default_room=room_token
        )
        self.room = room_token
    
    def send_notification(self, title, message, priority="info"):
        """Отправить уведомление в чат"""
        emoji = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌"
        }.get(priority, "📢")
        
        text = f"{emoji} **{title}**\n{message}"
        self.bot.send_message(self.room, text)
    
    def send_file_notification(self, title, file_path, message=None):
        """Отправить уведомление с файлом"""
        full_text = f"📎 **{title}**"
        if message:
            full_text += f"\n{message}"
        
        self.bot.send_message(
            chat_id=self.room,
            text=full_text,
            file_path=file_path
        )

# Использование
notifier = NotificationBot(
    host="https://nextcloud.example.com",
    user="notifier",
    password="app-xxxxx",
    room_token="room_token"
)

notifier.send_notification("Система", "Резервное копирование завершено", "success")
notifier.send_file_notification("Отчет", "/tmp/report.pdf", "Ежемесячный отчет готов")
```

### Бот с обработкой команд

```python
from nextcloud import Bot
from datetime import datetime

bot = Bot(
    host="https://nextcloud.example.com",
    user="helper_bot",
    password="app-xxxxx",
    default_room="room_token"
)

# Хранилище данных пользователей
user_data = {}

@bot.command("start")
def cmd_start(update, context):
    user = update.message.from_user
    update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n"
        "Я бот-помощник. Доступные команды:\n"
        "/time - текущее время\n"
        "/ping - проверить работу\n"
        "/note <текст> - сохранить заметку\n"
        "/mynote - показать заметку"
    )

@bot.command("time")
def cmd_time(update, context):
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    update.message.reply_text(f"🕐 Текущее время: {now}")

@bot.command("ping")
def cmd_ping(update, context):
    update.message.reply_text("🏓 Pong!")

@bot.command("note")
def cmd_note(update, context):
    user_id = update.message.from_user.id
    text = update.message.text.replace("/note", "").strip()
    
    if text:
        user_data[user_id] = text
        update.message.reply_text("✅ Заметка сохранена!")
    else:
        update.message.reply_text("❌ Напишите текст заметки после /note")

@bot.command("mynote")
def cmd_mynote(update, context):
    user_id = update.message.from_user.id
    note = user_data.get(user_id)
    
    if note:
        update.message.reply_text(f"📝 Ваша заметка:\n{note}")
    else:
        update.message.reply_text("📭 У вас нет сохраненных заметок")

@bot.message_handler
def handle_unknown(update, context):
    text = update.message.text
    if text:
        update.message.reply_text(
            f"Неизвестная команда. Напишите /help для списка команд."
        )

if __name__ == "__main__":
    bot.run_polling()
```

## API Reference

### Bot

#### Методы отправки

| Метод | Описание |
|-------|----------|
| `send_message(chat_id, text, ...)` | Универсальный метод отправки (текст/файлы) |
| `send_file(chat_id, file_path, caption, ...)` | Отправить файл |
| `send_photo(chat_id, photo, caption, ...)` | Отправить фото |
| `send_document(chat_id, document, caption, ...)` | Отправить документ |

#### Управление комнатами

| Метод | Описание |
|-------|----------|
| `join_room(chat_id, password)` | Присоединиться к комнате |
| `get_rooms()` | Получить список всех комнат |
| `get_room_info(chat_id)` | Получить информацию о комнате |
| `get_room_participants(chat_id)` | Получить список участников |

#### Диагностика

| Метод | Описание |
|-------|----------|
| `check_session_status()` | Проверить статус сессии |
| `diagnose_room_access(chat_id)` | Диагностика доступа к комнате |

### Message

| Атрибут/Метод | Описание |
|---------------|----------|
| `message_id` | ID сообщения |
| `text` | Текст сообщения |
| `from_user` | Объект User (отправитель) |
| `chat` | Объект Chat (комната) |
| `date` | Время отправки |
| `reply_to_message` | Ответ на сообщение (если есть) |
| `reply_text(text)` | Ответить на сообщение |
| `reply(text)` | Алиас для reply_text |

## Устранение неполадок

### Ошибка аутентификации

```python
# Проверьте подключение
status = bot.check_session_status()
if not status['authenticated']:
    print(f"Ошибка: {status.get('error')}")
    print("Проверьте логин и пароль/токен")
```

### Комната не найдена

```python
# Получите список доступных комнат
rooms = bot.get_rooms()
print("Доступные комнаты:")
for room in rooms:
    print(f"  - {room.get('name')} (токен: {room.get('token')})")
```

### Проблемы с отправкой файлов

```python
# Используйте диагностику
result = bot.diagnose_room_access("room_token")
print(json.dumps(result, indent=2))

# Проверьте права на запись в WebDAV
# Убедитесь, что у бота есть права на загрузку файлов
```

## Лицензия

MIT License

## Вклад в проект

Буду рад вашим pull requests и issue!

## Ссылки

- [Nextcloud Talk API Documentation](https://nextcloud-talk.readthedocs.io/)
- [Nextcloud Developer Documentation](https://docs.nextcloud.com/server/latest/developer_manual/)