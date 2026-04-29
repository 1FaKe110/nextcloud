```markdown
# Nextcloud Talk Bot

Python library for creating bots in Nextcloud Talk with an API similar to python-telegram-bot.

## Features

- Simple API inspired by python-telegram-bot
- Send text messages with Markdown support
- Send files (documents, photos, any attachments)
- Automatic membership management in rooms
- Message threading and reply support
- Automatic API version detection (v1-v4)
- Automatic re-authentication on errors
- Polling mode for receiving messages
- Both sync and async versions available
- Multi-room support (async version)

## Requirements

- Python 3.10+
- Nextcloud 33.x or higher (with Talk app installed)
- Bot account in Nextcloud (app token recommended)

## Installation

### From source

```bash
git clone https://github.com/your-username/nextcloud-talk-bot.git
cd nextcloud-talk-bot
pip install -r requirements.txt
```

### As a library

Copy the `nextcloud` folder to your project or install as module:

```python
from nextcloud import Bot  # sync version
# or
from nextcloud import AsyncBot  # async version
```

## Quick Start

### Minimal example (sync)

```python
from nextcloud import Bot

# Create bot
bot = Bot(
    host="https://nextcloud.example.com",
    user="bot_user",
    password="your-app-token",
    default_room="room_token_here"
)

# Send message
bot.send_message("Hello, World!")

# Start polling
bot.run_polling()
```

### Minimal example (async)

```python
from nextcloud import AsyncBot
import asyncio

bot = AsyncBot(
    host="https://nextcloud.example.com",
    user="bot_user",
    password="your-app-token",
    default_room="room_token_here"
)

@bot.command("start")
async def start_command(update, context):
    await update.message.reply_text("Hello from async bot!")

asyncio.run(bot.run_polling())
```

## Sending Messages

### Text messages

```python
# Simple text
bot.send_message("Hello, World!")

# Markdown formatting
bot.send_message("**Bold text** and *italic*")

# Reply to a message
bot.send_message(
    chat_id="room_token",
    text="This is a reply",
    reply_to_message_id=12345
)
```

### Sending files

```python
# Send file with caption
bot.send_message(
    chat_id="room_token",
    text="Check this document",
    file_path="/path/to/document.pdf"
)

# Send photo
bot.send_photo(
    "room_token",
    "/path/to/photo.jpg",
    caption="Beautiful sunset"
)

# Send document
bot.send_document(
    "room_token",
    "/path/to/report.pdf",
    caption="Monthly report"
)

# Send file from memory
file_content = b"Hello, this is a text file!"
bot.send_message(
    chat_id="room_token",
    text="Generated file",
    file_content=file_content,
    file_name="hello.txt",
    mime_type="text/plain"
)

# Send file from BytesIO
from io import BytesIO

buffer = BytesIO()
buffer.write(b"Some data")
buffer.seek(0)

bot.send_document(
    "room_token",
    buffer,
    caption="Data from buffer"
)
```

## Receiving Messages

### Polling mode

```python
# Start polling (sync version)
bot.run_polling(chat_id="room_token", poll_interval=2)

# Parameters:
# - chat_id: room token (uses default_room if not specified)
# - poll_interval: polling interval in seconds (default 2)
```

### Async polling with multi-room support

```python
from nextcloud import AsyncBot
import asyncio

# Multi-room mode - listens to all rooms where bot is member
bot = AsyncBot(
    host="https://nextcloud.example.com",
    user="bot_user",
    password="app-token",
    listen_all_rooms=True,
    max_concurrent_rooms=50
)

@bot.message_handler
async def handle_all_messages(update, context):
    print(f"Message from {update.message.chat.id}: {update.message.text}")

asyncio.run(bot.run_polling())  # No room token needed
```

### Message object

When receiving a message, you get a `Message` object with these attributes:

```python
@bot.message_handler
def handle_message(update, context):
    message = update.message
    
    # Basic attributes
    print(f"ID: {message.message_id}")
    print(f"Text: {message.text}")
    print(f"From: {message.from_user.full_name}")
    print(f"Time: {message.date}")
    
    # Reply to message
    message.reply_text("Got your message!")
    
    # Or use reply alias
    message.reply("Same thing")
```

## Command Handling

### Sync version

```python
from nextcloud import Bot

bot = Bot(
    host="https://nextcloud.example.com",
    user="my_bot",
    password="app-token",
    default_room="room_token"
)

@bot.command("start")
def start_command(update, context):
    update.message.reply_text("Welcome! I am a Nextcloud Talk bot!")

@bot.command("echo")
def echo_command(update, context):
    text = update.message.text.replace("/echo", "").strip()
    if text:
        update.message.reply_text(f"Echo: {text}")
    else:
        update.message.reply_text("Write something after /echo")

@bot.message_handler
def handle_all(update, context):
    message = update.message
    if message.text:
        print(f"Received: {message.text}")

if __name__ == "__main__":
    bot.run_polling()
```

### Async version with sync/async handlers

```python
from nextcloud import AsyncBot
import asyncio

bot = AsyncBot(
    host="https://nextcloud.example.com",
    user="my_bot",
    password="app-token",
    default_room="room_token"
)

# Async handler
@bot.command("time")
async def time_command(update, context):
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"Current time: {now}")

# Sync handler (runs in thread pool)
@bot.command("ping")
def ping_command(update, context):
    update.message.reply_text("pong!")

asyncio.run(bot.run_polling())
```

## Room Management

### Joining rooms

```python
# Automatic joining on startup (default)
bot = Bot(
    host="https://nextcloud.example.com",
    user="bot_user",
    password="password",
    default_room="room_token",
    auto_join_room=True
)

# Manual join
bot.join_room("room_token", password="room_password")
```

### Getting room information

```python
# List all available rooms
rooms = bot.get_rooms()
for room in rooms:
    print(f"Room: {room.get('name')} ({room.get('token')})")
    print(f"Participants: {room.get('participantCount')}")

# Get specific room info
room_info = bot.get_room_info("room_token")
print(f"Name: {room_info.get('name')}")
print(f"Type: {room_info.get('type')}")

# Get participants list
participants = bot.get_room_participants("room_token")
for p in participants:
    print(f"Participant: {p.get('actorDisplayName')}")
```

## File Download

```python
@bot.message_handler
def handle_file(update, context):
    message = update.message
    
    if message.files:
        for file_obj in message.files:
            # Download to memory
            content = bot.download_file(file_obj)
            print(f"Downloaded {file_obj.file_name}: {len(content)} bytes")
            
            # Download to disk
            path = bot.download_file(file_obj, f"/tmp/{file_obj.file_name}")
            print(f"Saved to {path}")
```

## Diagnostic Tools

### Connection check

```python
# Check session status
status = bot.check_session_status()
if status['authenticated']:
    print(f"Connected as: {status['user']}")
else:
    print("Authentication failed")

# Room access diagnosis
diagnostic = bot.diagnose_room_access("room_token")
import json
print(json.dumps(diagnostic, indent=2))
```

### Bot info

```python
# Get bot information
info = bot.get_bot_info()
print(f"User: {info['user']}")
print(f"Host: {info['host']}")
print(f"Authenticated: {info['authenticated']}")
print(f"Rooms count: {info['rooms_count']}")
```

## Configuration

### Bot parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| host | str | - | Nextcloud server URL |
| user | str | - | Bot username |
| password | str | - | Password or app token |
| default_room | str | None | Default room token |
| read_all_chat | bool | False | Read full history (old to new) or only new messages |
| auto_join_room | bool | True | Automatically join rooms |
| listen_all_rooms | bool | False | (Async only) Listen to all rooms where bot is member |
| max_concurrent_rooms | int | 50 | (Async multi-room only) Max concurrent rooms to poll |

### Logging setup

The library uses `loguru` for logging. You can configure log level:

```python
from loguru import logger
import sys

# Set log level
logger.remove()  # Remove default handler
logger.add(sys.stderr, level="INFO")  # Only INFO and above
# or
logger.add(sys.stderr, level="DEBUG")  # Detailed logging
```

## Examples

### Echo bot

```python
from nextcloud import Bot

bot = Bot(
    host="https://nextcloud.example.com",
    user="echo_bot",
    password="app-token",
    default_room="room_token"
)

@bot.message_handler
def echo(update, context):
    message = update.message
    if message.text:
        message.reply_text(f"Echo: {message.text}")
    else:
        message.reply_text("Write some text and I'll echo it!")

bot.run_polling()
```

### Notification bot

```python
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
        emoji = {
            "info": "[i]",
            "success": "[OK]",
            "warning": "[!]",
            "error": "[X]"
        }.get(priority, "[*]")
        
        text = f"{emoji} {title}\n{message}"
        self.bot.send_message(self.room, text)
    
    def send_file_notification(self, title, file_path, message=None):
        full_text = f"[FILE] {title}"
        if message:
            full_text += f"\n{message}"
        
        self.bot.send_message(
            chat_id=self.room,
            text=full_text,
            file_path=file_path
        )

# Usage
notifier = NotificationBot(
    host="https://nextcloud.example.com",
    user="notifier",
    password="app-token",
    room_token="room_token"
)

notifier.send_notification("Backup", "Backup completed", "success")
notifier.send_file_notification("Report", "/tmp/report.pdf", "Monthly report ready")
```

### Multi-room bot (async)

```python
from nextcloud import AsyncBot
import asyncio

bot = AsyncBot(
    host="https://nextcloud.example.com",
    user="helper_bot",
    password="app-token",
    listen_all_rooms=True,
    auto_join_room=True
)

# Shared storage for all rooms
user_notes = {}

@bot.command("note")
async def save_note(update, context):
    user_id = update.message.from_user.id
    room_id = update.message.chat.id
    text = update.message.text.replace("/note", "").strip()
    
    if text:
        key = f"{room_id}_{user_id}"
        user_notes[key] = text
        await update.message.reply_text("Note saved!")
    else:
        await update.message.reply_text("Write something after /note")

@bot.command("mynote")
async def get_note(update, context):
    user_id = update.message.from_user.id
    room_id = update.message.chat.id
    key = f"{room_id}_{user_id}"
    note = user_notes.get(key)
    
    if note:
        await update.message.reply_text(f"Your note:\n{note}")
    else:
        await update.message.reply_text("You have no saved notes")

@bot.command("rooms")
async def list_rooms(update, context):
    rooms = await bot.get_rooms()
    room_list = "\n".join([f"- {r.get('name')}" for r in rooms[:10]])
    await update.message.reply_text(f"Available rooms:\n{room_list}")

asyncio.run(bot.run_polling())
```

### Async bot with context manager

```python
from nextcloud import AsyncBot
import asyncio

async def main():
    async with AsyncBot(
        host="https://nextcloud.example.com",
        user="my_bot",
        password="app-token",
        default_room="room_token"
    ) as bot:
        
        @bot.command("ping")
        async def ping(update, context):
            await update.message.reply_text("pong!")
        
        await bot.run_polling()

asyncio.run(main())
```

## Troubleshooting

### Authentication error

```python
# Check connection
status = bot.check_session_status()
if not status['authenticated']:
    print(f"Error: {status.get('error')}")
    print("Check username and password/app token")
```

### Room not found

```python
# Get list of available rooms
rooms = bot.get_rooms()
print("Available rooms:")
for room in rooms:
    print(f"  - {room.get('name')} (token: {room.get('token')})")
```

### File upload issues

```python
# Use diagnostics
result = bot.diagnose_room_access("room_token")
import json
print(json.dumps(result, indent=2))

# Check WebDAV write permissions
# Ensure bot has permission to upload files
```

## API Reference

### Bot (sync) / AsyncBot (async)

#### Sending methods

| Method | Description |
|--------|-------------|
| `send_message(chat_id, text, ...)` | Universal send method (text/files) |
| `send_file(chat_id, file_path, caption, ...)` | Send file |
| `send_photo(chat_id, photo, caption, ...)` | Send photo |
| `send_document(chat_id, document, caption, ...)` | Send document |
| `reply_to(message, text, ...)` | Reply to a message |

#### Room management

| Method | Description |
|--------|-------------|
| `join_room(chat_id, password)` | Join a room |
| `get_rooms()` | Get list of all rooms |
| `get_room_info(chat_id)` | Get room information |
| `get_room_participants(chat_id)` | Get participants list |

#### Diagnostics

| Method | Description |
|--------|-------------|
| `check_session_status()` | Check session status |
| `diagnose_room_access(chat_id)` | Diagnose room access |
| `get_bot_info()` | Get bot information |

### Message object

| Attribute/Method | Description |
|------------------|-------------|
| `message_id` | Message ID |
| `text` | Message text |
| `from_user` | User object (sender) |
| `chat` | Chat object (room) |
| `date` | Send time |
| `files` | List of File objects |
| `reply_to_message` | Replied message (if any) |
| `reply_text(text)` | Reply to message |
| `reply(text)` | Alias for reply_text |

### File object

| Attribute | Description |
|-----------|-------------|
| `file_id` | File ID |
| `file_name` | File name |
| `file_size` | File size in bytes |
| `mime_type` | MIME type |
| `file_path` | Path on server |
| `download_url` | Download URL |

## License

MIT License

## Contributing

Pull requests and issues are welcome!

## Links

- Nextcloud Talk API Documentation: https://nextcloud-talk.readthedocs.io/
- Nextcloud Developer Documentation: https://docs.nextcloud.com/server/latest/developer_manual/
```