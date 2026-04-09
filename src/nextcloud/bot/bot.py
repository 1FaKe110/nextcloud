# bot.py
"""
Nextcloud Talk Bot API wrapper with full message and file support.
Compatible with Nextcloud 33.x and later versions.

Features:
- Auto-join rooms and membership management
- Send text messages with Markdown support
- Send files as attachments (direct upload via API v4)
- Fallback to WebDAV if direct upload fails
- Message threading and replies
- Automatic session management
- Background membership maintenance
"""

import traceback
import requests
import time
import os
import json
import mimetypes
import hashlib
from datetime import datetime
from typing import Optional, Callable, Dict, Any, List, Union, Tuple, BinaryIO
import threading
from loguru import logger
from .models import Update, User, Chat, Message


class Bot:
    """
    Бот для Nextcloud Talk с API, похожим на python-telegram-bot.

    Поддерживает отправку текстовых сообщений, файлов, фото и документов
    с автоматическим управлением членством в комнатах.

    Пример использования:
        bot = Bot(
            host="https://nextcloud.example.com",
            user="bot_user",
            password="app-token",
            default_room="room_token",
            auto_join_room=True
        )

        # Отправить текстовое сообщение
        bot.send_message("room_token", "Hello, World!")

        # Отправить файл с подписью
        bot.send_message(
            "room_token",
            text="Check this file",
            file_path="/path/to/document.pdf"
        )

        # Запустить polling для получения сообщений
        bot.run_polling()
    """

    def __init__(self, host: str, user: str, password: str, default_room: str = None,
                 read_all_chat: bool = False, auto_join_room: bool = True):
        """
        Инициализация бота

        Args:
            host: URL Nextcloud сервера (например, https://nextcloud.example.com)
            user: Имя пользователя для авторизации
            password: Пароль пользователя или токен приложения (app-xxxxx)
            default_room: Токен комнаты по умолчанию
            read_all_chat: Если False - не читать историю чата при старте,
                          начинать получать сообщения только с момента запуска.
                          Если True - прочитать всю историю чата.
            auto_join_room: Автоматически присоединяться к комнате при старте
                           и периодически проверять членство
        """
        self.host = host.rstrip('/')
        self.user = user
        self.password = password
        self.default_room = default_room
        self.read_all_chat = read_all_chat
        self.auto_join_room = auto_join_room

        # Инициализация HTTP сессии
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({
            'OCS-APIRequest': 'true',
            'Accept': 'application/json'
        })

        self.handlers: List[Dict[str, Any]] = []
        self.command_handlers: Dict[str, Callable] = {}
        self.last_message_id: Dict[str, int] = {}
        self.running = False
        self.membership_thread = None

        self.api_version = self._detect_api_version()
        logger.info(f"Используется API версии: {self.api_version}")
        logger.info(f"Режим чтения истории: {'читать всё' if self.read_all_chat else 'только новые сообщения'}")
        self._check_connection()

    def _detect_api_version(self) -> str:
        """
        Определить какая версия API работает на сервере.

        Returns:
            Версия API (v1-v4) или 'v1' по умолчанию
        """
        for version in ['v4', 'v3', 'v2', 'v1']:
            try:
                url = f"{self.host}/ocs/v2.php/apps/spreed/api/{version}/room"
                response = self.session.get(url, params={'format': 'json'}, timeout=10)
                if response.status_code == 200:
                    logger.info(f"Найдена работающая версия API: {version}")
                    return version
            except:
                continue
        return 'v1'

    def _check_connection(self) -> bool:
        """
        Проверить подключение к Nextcloud и аутентификацию.

        Returns:
            True если подключение успешно
        """
        try:
            url = f"{self.host}/ocs/v2.php/cloud/user"
            response = self.session.get(url, params={'format': 'json'}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Подключен как: {data['ocs']['data']['display-name']}")
                return True
            else:
                logger.error(f"Ошибка подключения: статус {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            return False

    def _make_request(self, method: str, endpoint: str, data: dict = None,
                      params: dict = None, files: dict = None, retry: bool = True) -> dict:
        """
        Выполнить запрос к Nextcloud API с автоматической переавторизацией.

        Args:
            method: HTTP метод (GET, POST)
            endpoint: API эндпоинт (начинается с /)
            data: Данные для POST запроса
            params: Query параметры
            files: Файлы для multipart/form-data
            retry: Флаг повторной попытки при ошибке

        Returns:
            Словарь с данными ответа или пустой словарь при ошибке
        """
        url = f"{self.host}{endpoint}"
        params = params or {}
        params['format'] = 'json'

        try:
            if method.upper() == 'GET':
                response = self.session.get(url, params=params, timeout=30)
            elif method.upper() == 'POST':
                if files:
                    response = self.session.post(url, files=files, data=data,
                                                 params=params, timeout=60)
                else:
                    response = self.session.post(url, data=data, params=params, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            logger.trace(f"Request: {method} {url}")
            logger.trace(f"Status: {response.status_code}")
            logger.trace(f"response: {json.dumps(response.json(), indent=2, ensure_ascii=False, default=str)}")

            # 401 Unauthorized - пробуем переавторизоваться
            if response.status_code == 401 and retry:
                logger.warning(f"Получен статус 401, пробуем переавторизацию...")
                self._reinit_session()
                return self._make_request(method, endpoint, data, params, files, retry=False)

            if response.status_code in [200, 201]:
                try:
                    json_response = response.json()
                    return json_response.get('ocs', {}).get('data', {})
                except json.JSONDecodeError:
                    logger.error(f"Ошибка парсинга JSON: {response.text[:200]}")
                    return {}
            else:
                if response.status_code not in [404, 400]:
                    logger.warning(f"Request failed: {response.status_code}")
                    logger.debug(f"Ответ: {response.text[:500]}")
                return {}

        except requests.exceptions.Timeout:
            logger.error(f"Timeout при запросе к {url}")
            return {}
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Ошибка подключения: {e}")
            return {}
        except Exception as e:
            logger.error(f"Request error: {e}")
            return {}

    def _reinit_session(self):
        """Пересоздание сессии с авторизацией."""
        self.session = requests.Session()
        self.session.auth = (self.user, self.password)
        self.session.headers.update({
            'OCS-APIRequest': 'true',
            'Accept': 'application/json'
        })

    def _get_current_message_id(self, chat_id: str) -> int:
        """
        Получить ID последнего сообщения в чате.
        Используется при read_all_chat=False, чтобы начать с текущего момента.

        Args:
            chat_id: Токен комнаты

        Returns:
            ID последнего сообщения или 0
        """
        endpoint = f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}"

        result = self._make_request('GET', endpoint, params={'limit': 1, 'lookIntoFuture': 0})

        if not result:
            return 0

        messages = result if isinstance(result, list) else result.get('data', [])
        if messages:
            latest_id = messages[0].get('id', 0)
            logger.info(f"Текущий последний ID сообщения в чате: {latest_id}")
            return latest_id

        return 0

    def _get_new_messages(self, chat_id: str, limit: int = 100) -> List[Dict]:
        """
        Получить только новые сообщения из комнаты.
        API возвращает сообщения от новых к старым.

        Args:
            chat_id: Токен комнаты
            limit: Максимальное количество сообщений

        Returns:
            Список новых сообщений
        """
        endpoint = f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}"

        result = self._make_request('GET', endpoint, params={'limit': limit, 'lookIntoFuture': 0})

        if not result:
            return []

        messages = result if isinstance(result, list) else result.get('data', [])
        if not messages:
            return []

        last_id = self.last_message_id.get(chat_id, 0)

        # При первом запуске и read_all_chat=False - начинаем с текущего момента
        if last_id == 0 and not self.read_all_chat:
            last_id = self._get_current_message_id(chat_id)
            if last_id > 0:
                self.last_message_id[chat_id] = last_id
                logger.info(f"Пропускаем историю чата, начинаем с сообщения ID: {last_id}")
                return []

        # Фильтруем новые сообщения
        new_messages = [msg for msg in messages if msg.get('id', 0) > last_id]

        if new_messages:
            max_id = max(msg.get('id', 0) for msg in new_messages)
            self.last_message_id[chat_id] = max_id
            logger.debug(f"Найдено {len(new_messages)} новых сообщений (последний ID: {max_id})")

        return new_messages

    def join_room(self, chat_id: str, password: str = None) -> bool:
        """
        Присоединиться к комнате (стать активным участником).
        Правильный эндпоинт для Nextcloud 22+.

        Args:
            chat_id: Токен комнаты
            password: Пароль комнаты (если требуется)

        Returns:
            True если успешно присоединился
        """
        try:
            # Правильный эндпоинт для присоединения к комнате
            endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants/active"

            data = {}
            if password:
                data['password'] = password

            result = self._make_request('POST', endpoint, data=data)

            if result is not None:
                logger.info(f"Бот присоединился к комнате {chat_id}")
                return True
            else:
                logger.debug(f"Не удалось присоединиться к комнате {chat_id}")
                return False

        except Exception as e:
            logger.error(f"Ошибка при присоединении к комнате {chat_id}: {e}")
            return False

    def ensure_room_membership(self, chat_id: str) -> bool:
        """
        Проверить и обеспечить членство бота в комнате.

        Args:
            chat_id: Токен комнаты

        Returns:
            True если бот является участником
        """
        try:
            # 1. Проверяем, есть ли у бота активная сессия в комнате
            endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants"
            result = self._make_request('GET', endpoint)

            if result:
                participants = result if isinstance(result, list) else result.get('data', [])

                # Проверяем, есть ли бот в активных участниках
                for participant in participants:
                    actor_id = participant.get('actorId', '')
                    user_id = participant.get('userId', '')
                    session_id = participant.get('sessionId', '')

                    # Если есть активная сессия (sessionId не '0')
                    if (actor_id == self.user or user_id == self.user) and session_id != '0':
                        logger.debug(f"Бот уже участник комнаты {chat_id} (активная сессия)")
                        return True

                    # Если бот есть в списке, но без активной сессии
                    if actor_id == self.user or user_id == self.user:
                        logger.info(f"Бот найден в комнате {chat_id}, но без активной сессии, присоединяемся...")
                        return self.join_room(chat_id)

                # Если бота нет в списке участников - присоединяемся
                logger.info(f"Бот не участник комнаты {chat_id}, присоединяемся...")
                return self.join_room(chat_id)

            else:
                # Не удалось получить список участников, пробуем присоединиться
                logger.warning(f"Не удалось получить участников, пробуем присоединиться...")
                return self.join_room(chat_id)

        except Exception as e:
            logger.error(f"Ошибка проверки членства: {e}")
            return False

    def ensure_bot_in_room(self, chat_id: str, room_name: str = None) -> bool:
        """
        Полная проверка доступа бота к комнате.

        Args:
            chat_id: Токен комнаты
            room_name: Имя комнаты (для поиска если токен не работает)

        Returns:
            True если бот может отправлять сообщения
        """
        logger.info(f"Проверка доступа к комнате {chat_id}...")

        # 1. Проверяем, существует ли комната
        room_info = self._get_room_info(chat_id)
        if not room_info:
            logger.error(f"Комната {chat_id} не найдена")

            # Пробуем найти по имени
            if room_name:
                logger.info(f"Пробуем найти комнату по имени: {room_name}")
                rooms = self.get_rooms()
                for room in rooms:
                    if room.get('name') == room_name:
                        actual_chat_id = room.get('token')
                        logger.info(f"Найдена комната {room_name} с токеном {actual_chat_id}")
                        chat_id = actual_chat_id
                        room_info = room
                        break

            if not room_info:
                return False

        # 2. Проверяем тип комнаты
        room_type = room_info.get('type', 0)
        # Типы: 1=один на один, 2=группа, 3=публичная, 4=публичная

        logger.info(f"Тип комнаты: {room_type}, название: {room_info.get('name')}")

        # 3. Убеждаемся, что бот участник
        if not self.ensure_room_membership(chat_id):
            logger.error(f"Не удалось добавить бота в комнату {chat_id}")
            return False

        logger.success(f"Бот успешно добавлен в комнату и может отправлять сообщения")
        return True

    def get_rooms(self) -> List[Dict]:
        """
        Получить список всех комнат, доступных боту.

        Returns:
            Список комнат с их данными
        """
        endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room"
        result = self._make_request('GET', endpoint)

        if result:
            return result if isinstance(result, list) else result.get('data', [])
        return []

    def get_room_participants(self, chat_id: str) -> List[Dict]:
        """
        Получить список активных участников комнаты.

        Args:
            chat_id: Токен комнаты

        Returns:
            Список участников
        """
        endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants"
        result = self._make_request('GET', endpoint)

        if result:
            return result if isinstance(result, list) else result.get('data', [])
        return []

    def get_room_info(self, chat_id: str) -> dict:
        """
        Получить информацию о комнате.

        Args:
            chat_id: Токен комнаты

        Returns:
            Словарь с информацией о комнате
        """
        endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}"
        return self._make_request('GET', endpoint)

    def _get_room_info(self, chat_id: str) -> dict:
        """Внутренний метод для получения информации о комнате."""
        endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}"
        return self._make_request('GET', endpoint)

    @logger.catch
    def send_message(self, chat_id: str = None, text: str = None,
                     reply_to_message_id: int = None,
                     parse_mode: str = None,
                     ensure_membership: bool = True,
                     file_path: str = None,
                     file_content: bytes = None,
                     file_name: str = None,
                     file_url: str = None,
                     mime_type: str = None,
                     **kwargs) -> bool:
        """
        Отправить сообщение с возможностью прикрепления файла.

        Args:
            chat_id: ID комнаты (room_token). Если не указан, используется default_room
            text: Текст сообщения
            reply_to_message_id: ID сообщения, на которое отвечаем
            parse_mode: Режим форматирования ('Markdown' или None)
            ensure_membership: Проверить и восстановить членство перед отправкой
            file_path: Путь к файлу для отправки
            file_content: Содержимое файла в байтах
            file_name: Имя файла (обязательно если передается file_content)
            file_url: URL файла (для вставки ссылки)
            mime_type: MIME тип файла (определяется автоматически если не указан)

        Returns:
            True если успешно отправлено

        Примеры:
            # Текстовое сообщение
            bot.send_message(text="Hello, World!")

            # Сообщение с файлом
            bot.send_message(
                text="Check this file",
                file_path="/path/to/document.pdf"
            )

            # Отправка файла из памяти
            with open("image.jpg", "rb") as f:
                bot.send_message(
                    text="Photo from server",
                    file_content=f.read(),
                    file_name="photo.jpg",
                    mime_type="image/jpeg"
                )

            # Отправка ссылки на файл
            bot.send_message(
                chat_id,
                text="Documentation",
                file_url="https://example.com/doc.pdf",
                file_name="documentation.pdf"
            )
        """

        # Используем default_room если chat_id не указан
        if chat_id is None:
            chat_id = self.default_room
            if chat_id is None:
                logger.error("Не указан chat_id и не задан default_room")
                return False

        # Проверяем членство, если нужно
        if ensure_membership:
            if not self.ensure_room_membership(chat_id):
                logger.error(f"Не удалось обеспечить членство в комнате {chat_id}")
                return False

        # Подготовка файла для отправки
        file_to_send = None

        try:
            # Если передан путь к файлу
            if file_path and os.path.exists(file_path):
                file_name = file_name or os.path.basename(file_path)
                mime_type = mime_type or mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                file_to_send = (file_name, file_content, mime_type)

            # Если передано содержимое файла
            elif file_content:
                if not file_name:
                    file_name = f"file_{int(time.time())}"
                mime_type = mime_type or 'application/octet-stream'
                file_to_send = (file_name, file_content, mime_type)

            # Если есть файл - используем API v4 для отправки с вложением
            if file_to_send:
                return self._send_message_with_file(
                    chat_id=chat_id,
                    text=text,
                    file=file_to_send,
                    reply_to_message_id=reply_to_message_id
                )

            # Если есть URL файла - отправляем текстовое сообщение со ссылкой
            elif file_url:
                file_display_name = file_name or 'файл'
                if text:
                    message_text = f"{text}\n\n📎 [{file_display_name}]({file_url})"
                else:
                    message_text = f"📎 [{file_display_name}]({file_url})"
                return self._send_text_message(chat_id, message_text, reply_to_message_id)

            # Иначе просто текст
            else:
                if not text:
                    logger.warning("Нет текста и файла для отправки")
                    return False
                return self._send_text_message(chat_id, text, reply_to_message_id)

        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            return False

    def _send_text_message(self, chat_id: str, text: str,
                           reply_to_message_id: int = None) -> bool:
        """
        Отправить только текстовое сообщение (внутренний метод).
        Использует рабочий код из backup.
        """
        if not text:
            logger.warning("Нет текста для отправки")
            return False

        # Используем API v1 для сообщений (стабильный)
        endpoint = f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}"

        data = {'message': text}
        if reply_to_message_id:
            data['replyTo'] = reply_to_message_id

        # Добавляем необходимые параметры
        params = {
            'lookIntoFuture': 0,
            'setReadMarker': 0
        }

        result = self._make_request('POST', endpoint, data=data, params=params)
        if result is not None and result != {}:
            logger.success(f"Отправка текстового сообщения в {chat_id}: {text[:50]}...")
            return True

        # Fallback: пробуем без параметров
        logger.trace("Отправка с параметрами не удалась, пробуем без них...")
        result = self._make_request('POST', endpoint, data=data)
        if result is not None and result != {}:
            logger.success("Сообщение успешно отправлено (без параметров)")
            return True

        logger.error("Не удалось отправить текстовое сообщение")
        return False

    def _send_message_with_file(self, chat_id: str, text: str, file: tuple,
                                reply_to_message_id: int = None) -> bool:
        """
        Отправить сообщение с файлом-вложением.

        Рабочий способ для Nextcloud:
        1. Загружаем файл через WebDAV
        2. Создаем публичную ссылку через Sharing API
        3. Отправляем сообщение со ссылкой

        Args:
            chat_id: ID комнаты
            text: Текст сообщения
            file: Кортеж (filename, content, mime_type)
            reply_to_message_id: ID сообщения для ответа

        Returns:
            True если успешно отправлено
        """
        file_name, file_content, mime_type = file

        logger.info(f"📤 Отправка файла {file_name} в комнату {chat_id}")

        try:
            # ШАГ 1: Создаем директорию для файлов бота если её нет
            webdav_dir = f"{self.host}/remote.php/dav/files/{self.user}/Talk"
            try:
                self.session.request('MKCOL', webdav_dir, timeout=30)
            except:
                pass  # Директория уже существует

            # ШАГ 2: Загружаем файл через WebDAV
            webdav_url = f"{webdav_dir}/{file_name}"

            headers = {
                'Content-Type': mime_type,
            }

            response = self.session.put(webdav_url, data=file_content, headers=headers, timeout=60)

            if response.status_code not in [200, 201, 204]:
                logger.error(f"Ошибка загрузки файла через WebDAV: {response.status_code}")
                logger.debug(f"Ответ: {response.text[:500]}")
                return False

            logger.info(f"Файл {file_name} загружен на сервер")

            # ШАГ 3: Получаем ID файла через PROPFIND
            file_id = self._get_file_id(file_name)

            if file_id and file_id != "unknown":
                # ШАГ 4: Создаем публичную ссылку через Sharing API
                share_url = self._create_public_share(file_id, file_name)

                if share_url:
                    file_link = share_url
                    logger.info(f"Создана публичная ссылка: {share_url}")
                else:
                    # Если не удалось создать публичную ссылку, используем прямую ссылку
                    file_link = f"{self.host}/index.php/f/{file_id}"
                    logger.warning(f"Используем прямую ссылку: {file_link}")
            else:
                # Fallback: используем WebDAV ссылку
                file_link = webdav_url
                logger.warning(f"Не удалось получить ID файла, используем WebDAV ссылку")

            # ШАГ 5: Отправляем сообщение со ссылкой
            message_text = text or ""
            if text:
                message_text = f"{text}\n\n📎 [{file_name}]({file_link})"
            else:
                message_text = f"📎 [{file_name}]({file_link})"

            return self._send_text_message(chat_id, message_text, reply_to_message_id)

        except Exception as e:
            logger.error(f"Ошибка при отправке файла: {e}")
            return False

    def _send_file_via_webdav(self, file_content: bytes, file_name: str,
                              chat_id: str = None, caption: str = None, reply_to_message_id: int = None) -> bool:
        """
        Отправить файл через WebDAV с созданием ссылки (fallback).

        Args:
            chat_id: Токен комнаты
            file_content: Содержимое файла в байтах
            file_name: Имя файла
            caption: Подпись к файлу
            reply_to_message_id: ID сообщения для ответа

        Returns:
            True если успешно отправлено
        """
        try:
            logger.info(f"Загрузка файла {file_name} через WebDAV")

            # Создаем директорию Talk если её нет
            webdav_dir_url = f"{self.host}/remote.php/dav/files/{self.user}/Talk/"
            try:
                self.session.request('MKCOL', webdav_dir_url, timeout=30)
            except:
                pass  # Директория уже существует

            # Загружаем файл через WebDAV
            webdav_url = f"{self.host}/remote.php/dav/files/{self.user}/Talk/{file_name}"

            headers = {
                'OCS-APIRequest': 'true',
                'Content-Type': 'application/octet-stream',
            }

            response = self.session.put(webdav_url, data=file_content, headers=headers, timeout=60)

            if response.status_code not in [200, 201, 204]:
                logger.error(f"Ошибка загрузки файла: {response.status_code}")
                return False

            logger.info(f"Файл {file_name} загружен на сервер")

            # Получаем ID файла для прямой ссылки
            file_id = self._get_file_id(file_name)

            # Формируем прямую ссылку на файл
            if file_id and file_id != "unknown":
                file_url = f"{self.host}/index.php/f/{file_id}"
            else:
                file_url = f"{self.host}/remote.php/dav/files/{self.user}/Talk/{file_name}"

            # Создаем сообщение с красивой ссылкой
            if caption:
                message_text = f"{caption}\n\n📎 [{file_name}]({file_url})"
            else:
                message_text = f"📎 [{file_name}]({file_url})"

            # Отправляем сообщение со ссылкой
            chat_id = chat_id if chat_id else self.default_room
            return self._send_text_message(chat_id, message_text, reply_to_message_id)

        except Exception as e:
            logger.error(f"Ошибка отправки через WebDAV: {e}")
            return False

    def _get_file_id(self, file_name: str) -> str:
        """
        Получить ID загруженного файла через WebDAV PROPFIND.

        Args:
            file_name: Имя файла

        Returns:
            ID файла или "unknown"
        """
        # Путь к файлу в WebDAV
        webdav_url = f"{self.host}/remote.php/dav/files/{self.user}/Talk/{file_name}"

        headers = {
            'Content-Type': 'application/xml; charset=utf-8',
            'Depth': '0'  # Только конкретный файл
        }

        # PROPFIND запрос для получения свойств файла
        propfind_body = '''<?xml version="1.0"?>
    <d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
      <d:prop>
        <oc:fileid />
        <oc:size />
        <d:getlastmodified />
      </d:prop>
    </d:propfind>'''

        try:
            response = self.session.request(
                'PROPFIND',
                webdav_url,
                data=propfind_body,
                headers=headers,
                timeout=30
            )

            if response.status_code == 207:  # Multi-status
                import xml.etree.ElementTree as ET

                # Парсим XML ответ
                root = ET.fromstring(response.text)

                # Namespace для парсинга XML
                namespaces = {
                    'd': 'DAV:',
                    'oc': 'http://owncloud.org/ns',
                    'nc': 'http://nextcloud.org/ns'
                }

                # Ищем fileid в ответе
                fileid_elem = root.find('.//oc:fileid', namespaces)
                if fileid_elem is not None and fileid_elem.text:
                    file_id = fileid_elem.text
                    logger.trace(f"Найден fileid для {file_name}: {file_id}")
                    return file_id

                # Пробуем nc:fileid (альтернативный namespace)
                fileid_elem = root.find('.//nc:fileid', namespaces)
                if fileid_elem is not None and fileid_elem.text:
                    logger.trace(f"Найден fileid (nc) для {file_name}: {fileid_elem.text}")
                    return fileid_elem.text

                logger.warning(f"Не удалось найти fileid для {file_name}")
                return "unknown"
            else:
                logger.error(f"PROPFIND запрос не удался: {response.status_code}")
                return "unknown"

        except Exception as e:
            logger.error(f"Ошибка при получении ID файла {file_name}: {e}")
            return "unknown"

    # --- Совместимость со старым API ---

    def send_file(self, chat_id: str, file_path: str, caption: str = None,
                  reply_to_message_id: int = None) -> bool:
        """
        Отправить файл (сохранение обратной совместимости).

        Args:
            chat_id: Токен комнаты
            file_path: Путь к файлу
            caption: Подпись к файлу
            reply_to_message_id: ID сообщения для ответа

        Returns:
            True если успешно отправлено
        """
        return self.send_message(
            chat_id=chat_id,
            text=caption,
            file_path=file_path,
            reply_to_message_id=reply_to_message_id
        )

    def send_photo(self, photo: Union[str, bytes], chat_id: Optional[str] = None, caption: str = None,
                   reply_to_message_id: int = None, **kwargs) -> bool:
        """
        Отправить фото.

        Args:
            chat_id: Токен комнаты
            photo: Путь к файлу или байты изображения
            caption: Подпись к фото
            reply_to_message_id: ID сообщения для ответа

        Returns:
            True если успешно отправлено
        """
        chat_id = chat_id if chat_id else self.default_room

        if isinstance(photo, str) and os.path.exists(photo):
            return self.send_message(
                chat_id=chat_id,
                text=caption,
                file_path=photo,
                reply_to_message_id=reply_to_message_id
            )
        elif isinstance(photo, bytes):
            return self.send_message(
                chat_id=chat_id,
                text=caption,
                file_content=photo,
                file_name=f"photo_{int(time.time())}.jpg",
                mime_type='image/jpeg',
                reply_to_message_id=reply_to_message_id
            )
        return False

    def send_document(self, document: Union[str, bytes, object],
                      chat_id: Optional[str] = None, caption: str = None, reply_to_message_id: int = None, **kwargs) -> bool:
        """
        Отправить документ.

        Args:
            chat_id: Токен комнаты
            document: Путь к файлу, байты или file-like объект
            caption: Подпись к документу
            reply_to_message_id: ID сообщения для ответа

        Returns:
            True если успешно отправлено
        """
        chat_id = chat_id if chat_id else self.default_room

        if isinstance(document, str) and os.path.exists(document):
            return self.send_message(
                chat_id=chat_id,
                text=caption,
                file_path=document,
                reply_to_message_id=reply_to_message_id
            )
        elif hasattr(document, 'read'):
            try:
                content = document.read()
                filename = getattr(document, 'name', f"document_{int(time.time())}.txt")
                if hasattr(filename, 'split'):
                    filename = filename.split('/')[-1]

                return self.send_message(
                    chat_id=chat_id,
                    text=caption,
                    file_content=content,
                    file_name=filename,
                    reply_to_message_id=reply_to_message_id
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке документа: {e}")
                return False
        return False

    # --- Обработка сообщений ---

    def _create_message_object(self, msg_data: Dict, chat_id: str) -> Message:
        """Создать объект Message из данных API"""
        msg_id = msg_data.get('id', 0)
        text = msg_data.get('message', '')
        actor_id = msg_data.get('actorId', '')
        actor_name = msg_data.get('actorDisplayName', actor_id)
        timestamp = msg_data.get('timestamp', 0)

        user = User(id=actor_id, first_name=actor_name, username=actor_id)
        chat = Chat(id=chat_id, title=f"Room {chat_id}", type="group")

        message = Message(
            message_id=msg_id, text=text, from_user=user, chat=chat,
            date=datetime.fromtimestamp(timestamp), _bot=self
        )

        if msg_data.get('parent'):
            parent_data = msg_data.get('parent', {})
            if parent_data:
                message.reply_to_message = Message(
                    message_id=parent_data.get('id', 0),
                    text=parent_data.get('message', ''),
                    from_user=user, chat=chat,
                    date=datetime.fromtimestamp(parent_data.get('timestamp', 0)), _bot=self
                )
        return message

    def _process_update(self, msg_data: Dict, chat_id: str):
        """Обработать одно обновление"""
        message = self._create_message_object(msg_data, chat_id)

        if message.from_user.id == self.user:
            logger.trace(f"Игнорируем свое сообщение")
            return

        logger.info(f"{message.from_user.full_name}: {message.text}")
        update = Update(message, message.message_id)

        if message.text and message.text.startswith('/'):
            cmd_parts = message.text.split()
            command = cmd_parts[0][1:].lower()

            logger.info(f"🔍 Обнаружена команда: /{command}")

            if command in self.command_handlers:
                try:
                    self.command_handlers[command](update, self._create_context())
                except Exception as e:
                    logger.error(f"Ошибка в обработчике команды /{command}: {e}")
                    logger.error(traceback.format_exc())
                return

        for handler in self.handlers:
            if handler['type'] == 'message':
                try:
                    handler['callback'](update, self._create_context())
                except Exception as e:
                    logger.error(f"Ошибка в обработчике: {e}")

    def _create_context(self):
        """Создать контекст для обработчиков"""

        class Context:
            def __init__(self, bot):
                self.bot = bot

        return Context(self)

    def add_handler(self, handler: Callable, handler_type: str = 'message'):
        """Добавить обработчик сообщений"""
        self.handlers.append({'type': handler_type, 'callback': handler})

    def message_handler(self, func: Callable = None):
        """Декоратор для обработки всех сообщений"""

        def decorator(f):
            self.add_handler(f, 'message')
            return f

        if func:
            return decorator(func)
        return decorator

    def command(self, command: str):
        """Декоратор для обработки команд"""

        def decorator(func):
            self.command_handlers[command] = func
            return func

        return decorator

    # --- Запуск бота ---

    def run_polling(self, chat_id: str = None, poll_interval: float = 2):
        """
        Запустить бота в режиме пулинга.

        Args:
            chat_id: ID комнаты (room_token)
            poll_interval: Интервал опроса в секундах
        """
        chat_id = chat_id or self.default_room
        if not chat_id:
            raise ValueError("Chat ID (room token) is required")

        # Убеждаемся, что бот в комнате
        if self.auto_join_room:
            if not self.ensure_bot_in_room(chat_id):
                logger.error("Не удалось обеспечить доступ к комнате, бот не может работать")
                return

        logger.info(f"Бот запущен в комнате {chat_id}")

        if self.read_all_chat:
            logger.info("Режим: читаем всю историю чата (от страых к новым)")
        else:
            logger.info("Режим: читаем только новые сообщения (история пропускается)")

        if self.last_message_id.get(chat_id):
            logger.info(f"Продолжаем с сообщения ID: {self.last_message_id[chat_id]}")
        logger.info("Нажми Ctrl+C для остановки\n")

        self.running = True

        # Запускаем фоновый поток для поддержания членства
        def ensure_membership_loop():
            while self.running:
                time.sleep(300)  # Каждые 5 минут
                if self.running and self.auto_join_room:
                    self.ensure_room_membership(chat_id)

        self.membership_thread = threading.Thread(target=ensure_membership_loop, daemon=True)
        self.membership_thread.start()

        try:
            while self.running:
                new_messages = self._get_new_messages(chat_id, limit=100)
                for msg_data in new_messages:
                    self._process_update(msg_data, chat_id)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("\n👋 Бот остановлен")
            self.running = False
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            self.running = False

    def stop(self):
        """Остановка бота"""
        self.running = False
        if self.membership_thread and self.membership_thread.is_alive():
            self.membership_thread.join(timeout=2)
        logger.info("Бот остановлен")

    # --- Диагностика ---

    def check_session_status(self) -> dict:
        """
        Проверка состояния сессии.

        Returns:
            Словарь со статусом сессии
        """
        try:
            url = f"{self.host}/ocs/v2.php/cloud/user"
            response = self.session.get(url, params={'format': 'json'}, timeout=10)

            if response.status_code == 200:
                data = response.json()
                return {
                    'authenticated': True,
                    'user': data['ocs']['data']['display-name'],
                    'cookies': len(response.cookies)
                }
            else:
                return {
                    'authenticated': False,
                    'status_code': response.status_code
                }
        except Exception as e:
            return {
                'authenticated': False,
                'error': str(e)
            }

    def diagnose_room_access(self, chat_id: str, room_name: str = None) -> dict:
        """
        Диагностика доступа к комнате.

        Args:
            chat_id: Токен комнаты
            room_name: Имя комнаты (опционально)

        Returns:
            Словарь с результатами диагностики
        """
        results = {
            'room_token': chat_id,
            'bot_user': self.user
        }

        # 1. Проверяем сессию
        results['session'] = self.check_session_status()

        # 2. Получаем список всех комнат
        try:
            rooms = self.get_rooms()
            results['available_rooms_count'] = len(rooms)
            results['available_rooms'] = [
                {'name': r.get('name'), 'token': r.get('token'), 'type': r.get('type')}
                for r in rooms[:10]  # Показываем только первые 10
            ]

            # Ищем нашу комнату
            found = False
            for room in rooms:
                if room.get('token') == chat_id:
                    results['found_room'] = {
                        'name': room.get('name'),
                        'token': room.get('token'),
                        'type': room.get('type'),
                        'participant_count': room.get('participantCount', 0)
                    }
                    found = True
                    break
                elif room_name and room.get('name') == room_name:
                    results['found_room_by_name'] = {
                        'name': room.get('name'),
                        'token': room.get('token'),
                        'type': room.get('type')
                    }

            if not found:
                results['room_not_found'] = True

        except Exception as e:
            results['get_rooms_error'] = str(e)

        # 3. Проверяем информацию о комнате
        try:
            room_info = self._get_room_info(chat_id)
            if room_info:
                results['room_info'] = {
                    'name': room_info.get('name'),
                    'type': room_info.get('type'),
                    'participant_count': room_info.get('participantCount', 0)
                }
        except Exception as e:
            results['room_info_error'] = str(e)

        # 4. Проверяем членство
        try:
            is_member = self.ensure_room_membership(chat_id)
            results['is_member'] = is_member
        except Exception as e:
            results['membership_check_error'] = str(e)

        return results

    def _create_public_share(self, file_id: str, file_name: str, password: str = None) -> str:
        """
        Создать публичную ссылку на файл через Sharing API.

        Args:
            file_id: ID файла
            file_name: Имя файла
            password: Пароль для защиты ссылки (опционально)

        Returns:
            URL публичной ссылки или None
        """
        if not file_id or file_id == "unknown":
            logger.warning(f"Некорректный file_id: {file_id}")
            return None

        # Создаем публичную ссылку через Sharing API
        endpoint = "/ocs/v2.php/apps/files_sharing/api/v1/shares"

        data = {
            'shareType': 3,  # 3 = публичная ссылка
            'path': f"/Talk/{file_name}",
            'permissions': 1,  # 1 = только чтение
            'name': f"Log Error: {file_name}"  # Название ссылки
        }

        if password:
            data['password'] = password

        try:
            result = self._make_request('POST', endpoint, data=data)

            if result and 'url' in result:
                share_url = result['url']
                logger.info(f"Создана публичная ссылка: {share_url}")
                return share_url
            else:
                logger.warning(f"Не удалось создать публичную ссылку для {file_name}")
                logger.trace(f"Ответ API: {result}")
                return None

        except Exception as e:
            logger.error(f"Ошибка при создании публичной ссылки для {file_name}: {e}")
            return None
