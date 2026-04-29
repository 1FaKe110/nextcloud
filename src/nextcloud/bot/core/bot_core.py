"""
Core business logic for Nextcloud Talk Bot.
Independent of sync/async HTTP implementation.
"""

import time
import os
import mimetypes
import threading
import traceback
from datetime import datetime
from typing import Optional, Callable, Dict, Any, List, Union, Tuple
from loguru import logger

from ..http.base import BaseHTTPClient, HttpResponse
from .models import Update, User, Chat, Message, File


class BotCore:
    """
    Ядро бота для Nextcloud Talk со всей бизнес-логикой.

    Не содержит HTTP вызовов напрямую — использует HTTP клиент.
    Не содержит циклов опроса — это задача наследников.

    Содержит:
    - Регистрацию и вызов обработчиков
    - Отправку сообщений и файлов
    - Управление комнатами (join, membership)
    - Обработку входящих сообщений
    - Работу с файлами (download, upload, sharing)
    """

    def __init__(
            self,
            http_client: BaseHTTPClient,
            default_room: str = None,
            read_all_chat: bool = False,
            auto_join_room: bool = True
    ):
        """
        Инициализация ядра бота.

        Args:
            http_client: HTTP клиент (синхронный или асинхронный)
            default_room: Токен комнаты по умолчанию
            read_all_chat: Читать всю историю или только новые сообщения
            auto_join_room: Автоматически присоединяться к комнатам
        """
        self.http = http_client
        self.default_room = default_room
        self.read_all_chat = read_all_chat
        self.auto_join_room = auto_join_room

        # Обработчики
        self.handlers: List[Dict[str, Any]] = []
        self.command_handlers: Dict[str, Callable] = {}

        # Состояние комнат (message_id для отслеживания новых сообщений)
        self.last_message_id: Dict[str, int] = {}

        # Информация о комнатах
        self.rooms_info: Dict[str, dict] = {}

        logger.info(f"BotCore инициализирован, режим истории: {'читать всё' if read_all_chat else 'только новые'}")

    # ========== Вспомогательные методы для работы с API ==========

    def _extract_file_from_message(self, msg_data: Dict, chat_id: str) -> List[File]:
        """
        Извлечь информацию о файлах из сообщения.

        Args:
            msg_data: Данные сообщения из API
            chat_id: ID чата

        Returns:
            Список File объектов
        """
        files = []

        # Способ 1: Проверяем поле messageParameters на наличие файлов
        message_params = msg_data.get('messageParameters', {})

        # Важно: messageParameters может быть либо словарём, либо списком
        if isinstance(message_params, dict):
            for key, param in message_params.items():
                if param.get('type') == 'file':
                    file_info = {
                        'file_id': param.get('id', ''),
                        'file_name': param.get('name', 'unknown'),
                        'file_size': param.get('size', 0),
                        'mime_type': param.get('mimetype', 'application/octet-stream'),
                        'file_path': param.get('path', ''),
                        'download_url': param.get('link', ''),
                        'direct_link': param.get('directLink', '')
                    }
                    files.append(File(**file_info))
        elif isinstance(message_params, list):
            # Если это список - итерируем напрямую
            for param in message_params:
                if isinstance(param, dict) and param.get('type') == 'file':
                    file_info = {
                        'file_id': param.get('id', ''),
                        'file_name': param.get('name', 'unknown'),
                        'file_size': param.get('size', 0),
                        'mime_type': param.get('mimetype', 'application/octet-stream'),
                        'file_path': param.get('path', ''),
                        'download_url': param.get('link', ''),
                        'direct_link': param.get('directLink', '')
                    }
                    files.append(File(**file_info))

        # Способ 2: Проверяем системные сообщения о расшаренных файлах
        system_message = msg_data.get('systemMessage', '')
        if system_message == 'file_shared':
            # message_params может быть словарём или списком
            if isinstance(message_params, dict):
                file_param = message_params.get('file', {})
            else:
                # Если список, ищем элемент с type='file'
                file_param = next((p for p in message_params if isinstance(p, dict) and p.get('type') == 'file'), {})

            if file_param:
                file_info = {
                    'file_id': file_param.get('id', ''),
                    'file_name': file_param.get('name', 'unknown'),
                    'file_size': file_param.get('size', 0),
                    'mime_type': file_param.get('mimetype', 'application/octet-stream'),
                    'file_path': file_param.get('path', ''),
                    'download_url': file_param.get('link', ''),
                }
                if not any(f.file_id == file_info['file_id'] for f in files):
                    files.append(File(**file_info))

        return files

    def _get_forward_info(self, msg_data: Dict) -> Tuple[bool, Optional[str]]:
        """
        Получить информацию о пересылке сообщения.

        Returns:
            (is_forwarded, forward_origin)
        """
        is_forwarded = False
        forward_origin = None

        # Проверяем наличие информации о пересылке
        if msg_data.get('isForwarded'):
            is_forwarded = True
            forward_origin = msg_data.get('forwardedFrom', {}).get('actorDisplayName', 'unknown')

        # Альтернативный способ через messageParameters
        message_params = msg_data.get('messageParameters', {})

        # messageParameters может быть списком или словарём
        if isinstance(message_params, dict):
            forward_param = message_params.get('forward')
            is_forwarded = is_forwarded or (forward_param is not None)
            if forward_param:
                forward_origin = forward_param.get('name', 'unknown')
        elif isinstance(message_params, list):
            forward_param = next((p for p in message_params if isinstance(p, dict) and p.get('type') == 'forward'),
                                 None)
            if forward_param:
                is_forwarded = True
                forward_origin = forward_param.get('name', 'unknown')

        return is_forwarded, forward_origin

    def _create_message_object(self, msg_data: Dict, chat_id: str) -> Message:
        """
        Создать объект Message из данных API.

        Args:
            msg_data: Данные сообщения из API
            chat_id: ID чата

        Returns:
            Message объект
        """
        msg_id = msg_data.get('id', 0)
        text = msg_data.get('message', '')
        actor_id = msg_data.get('actorId', '')
        actor_name = msg_data.get('actorDisplayName', actor_id)
        timestamp = msg_data.get('timestamp', 0)

        user = User(id=actor_id, first_name=actor_name, username=actor_id)
        chat = Chat(id=chat_id, title=f"Room {chat_id}", type="group")

        # Извлекаем файлы из сообщения
        files = self._extract_file_from_message(msg_data, chat_id)

        # Получаем информацию о пересылке
        is_forwarded, forward_origin = self._get_forward_info(msg_data)

        # Создаём сообщение
        message = Message(
            message_id=msg_id,
            text=text,
            from_user=user,
            chat=chat,
            date=datetime.fromtimestamp(timestamp),
            files=files,
            is_forwarded=is_forwarded,
            forward_origin=forward_origin,
            _bot=self  # Бот будет передан, но нужно определить свойство
        )

        # Добавляем ссылку на бота в сообщение
        message.bot = self

        # Обрабатываем ответ на сообщение
        if msg_data.get('parent'):
            parent_data = msg_data.get('parent', {})
            if parent_data:
                parent_message = Message(
                    message_id=parent_data.get('id', 0),
                    text=parent_data.get('message', ''),
                    from_user=user,
                    chat=chat,
                    date=datetime.fromtimestamp(parent_data.get('timestamp', 0)),
                    _bot=self
                )
                parent_message.bot = self
                message.reply_to_message = parent_message

        return message

    def _process_update(self, msg_data: Dict, chat_id: str):
        """
        Обработать одно обновление (вызов обработчиков).
        Этот метод вызывается наследниками при получении нового сообщения.

        Args:
            msg_data: Данные сообщения из API
            chat_id: ID чата
        """
        message = self._create_message_object(msg_data, chat_id)

        # Игнорируем свои сообщения
        if message.from_user.id == self.http.user:
            logger.trace("Игнорируем своё сообщение")
            return

        logger.info(f"[{chat_id}] {message.from_user.full_name}: {message.text[:50] if message.text else '(файл)'}")
        update = Update(message, message.message_id)

        # Обработка команд
        if message.text and message.text.startswith('/'):
            parts = message.text.split()
            command = parts[0][1:].lower()
            args = parts[1:] if len(parts) > 1 else []

            if command in self.command_handlers:
                try:
                    handler = self.command_handlers[command]
                    context = self._create_context()
                    handler(update, context)  # Здесь может быть sync или async
                except Exception as e:
                    logger.error(f"Ошибка в обработчике команды /{command}: {e}")
                    logger.error(traceback.format_exc())
                return

        # Общие обработчики сообщений
        for handler in self.handlers:
            if handler['type'] == 'message':
                try:
                    handler['callback'](update, self._create_context())
                except Exception as e:
                    logger.error(f"Ошибка в обработчике: {e}")
                    logger.error(traceback.format_exc())

    def _create_context(self):
        """Создать контекст для обработчиков."""

        class Context:
            def __init__(self, bot):
                self.bot = bot

        return Context(self)

    # ========== Регистрация обработчиков ==========

    def add_handler(self, handler: Callable, handler_type: str = 'message'):
        """Добавить обработчик сообщений."""
        self.handlers.append({'type': handler_type, 'callback': handler})

    def message_handler(self, func: Callable = None):
        """Декоратор для обработки всех сообщений."""

        def decorator(f):
            self.add_handler(f, 'message')
            return f

        if func:
            return decorator(func)
        return decorator

    def command(self, command: str):
        """Декоратор для обработки команд."""

        def decorator(func):
            self.command_handlers[command] = func
            return func

        return decorator

    # ========== Работа с комнатами ==========

    def _get_current_message_id(self, chat_id: str) -> int:
        """
        Получить ID последнего сообщения в чате.

        Args:
            chat_id: Токен комнаты

        Returns:
            ID последнего сообщения или 0
        """
        response: HttpResponse = self.http.get(
            f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}",
            params={'limit': 1, 'lookIntoFuture': 0}
        )

        if not response.data:
            return 0

        messages = response.data if isinstance(response.data, list) else response.data.get('data', [])
        if messages:
            return messages[0].get('id', 0)

        return 0

    def get_new_messages(self, chat_id: str, limit: int = 100) -> List[Dict]:
        """
        Получить только новые сообщения из комнаты.

        Args:
            chat_id: Токен комнаты
            limit: Максимальное количество сообщений

        Returns:
            Список новых сообщений
        """
        response: HttpResponse = self.http.get(
            f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}",
            params={'limit': limit, 'lookIntoFuture': 0}
        )

        if not response.data:
            return []

        messages = response.data if isinstance(response.data, list) else response.data.get('data', [])
        if not messages:
            return []

        last_id = self.last_message_id.get(chat_id, 0)

        # При первом запуске и read_all_chat=False - начинаем с текущего момента
        if last_id == 0 and not self.read_all_chat:
            last_id = self._get_current_message_id(chat_id)
            if last_id > 0:
                self.last_message_id[chat_id] = last_id
                logger.info(f"Комната {chat_id}: пропускаем историю, начинаем с ID: {last_id}")
                return []

        # Фильтруем новые сообщения
        new_messages = [msg for msg in messages if msg.get('id', 0) > last_id]

        if new_messages:
            max_id = max(msg.get('id', 0) for msg in new_messages)
            self.last_message_id[chat_id] = max_id
            logger.debug(f"Комната {chat_id}: найдено {len(new_messages)} новых сообщений (последний ID: {max_id})")

        return new_messages

    def join_room(self, chat_id: str, password: str = None) -> bool:
        """
        Присоединиться к комнате (стать активным участником).

        Args:
            chat_id: Токен комнаты
            password: Пароль комнаты (если требуется)

        Returns:
            True если успешно присоединился
        """
        try:
            endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants/active"

            data = {}
            if password:
                data['password'] = password

            response: HttpResponse = self.http.post(endpoint, data=data)

            if response.data is not None:
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
            endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants"
            response: HttpResponse = self.http.get(endpoint)

            if response.data:
                participants = response.data if isinstance(response.data, list) else response.data.get('data', [])

                for participant in participants:
                    actor_id = participant.get('actorId', '')
                    user_id = participant.get('userId', '')
                    session_id = participant.get('sessionId', '')

                    if (actor_id == self.http.user or user_id == self.http.user) and session_id != '0':
                        logger.trace(f"Бот уже участник комнаты {chat_id}")
                        return True

                    if actor_id == self.http.user or user_id == self.http.user:
                        logger.info(f"Бот в комнате {chat_id} без активной сессии, присоединяемся...")
                        return self.join_room(chat_id)

                logger.info(f"Бот не участник комнаты {chat_id}, присоединяемся...")
                return self.join_room(chat_id)
            else:
                logger.warning(f"Не удалось получить участников комнаты {chat_id}, пробуем присоединиться...")
                return self.join_room(chat_id)

        except Exception as e:
            logger.error(f"Ошибка проверки членства в комнате {chat_id}: {e}")
            return False

    def get_rooms(self) -> List[Dict]:
        """Получить список всех комнат, доступных боту."""
        response: HttpResponse = self.http.get("/ocs/v2.php/apps/spreed/api/v4/room")
        if response.data:
            return response.data if isinstance(response.data, list) else response.data.get('data', [])
        return []

    def get_room_info(self, chat_id: str) -> dict:
        """Получить информацию о комнате."""
        response: HttpResponse = self.http.get(f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}")
        return response.data or {}

    # ========== Отправка сообщений ==========

    def send_message(
            self,
            chat_id: str = None,
            text: str = None,
            reply_to_message_id: int = None,
            parse_mode: str = None,
            ensure_membership: bool = True,
            file_path: str = None,
            file_content: bytes = None,
            file_name: str = None,
            file_url: str = None,
            mime_type: str = None,
            **kwargs
    ) -> bool:
        """
        Отправить сообщение с возможностью прикрепления файла.

        Returns:
            True если успешно отправлено
        """
        # Используем default_room если chat_id не указан
        if chat_id is None:
            chat_id = self.default_room
            if chat_id is None:
                logger.error("Не указан chat_id и не задан default_room")
                return False

        # Проверяем членство если нужно
        if ensure_membership:
            if not self.ensure_room_membership(chat_id):
                logger.error(f"Не удалось обеспечить членство в комнате {chat_id}")
                return False

        # Подготовка файла для отправки
        file_to_send = None

        try:
            if file_path and os.path.exists(file_path):
                file_name = file_name or os.path.basename(file_path)
                mime_type = mime_type or mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                file_to_send = (file_name, file_content, mime_type)

            elif file_content:
                if not file_name:
                    file_name = f"file_{int(time.time())}"
                mime_type = mime_type or 'application/octet-stream'
                file_to_send = (file_name, file_content, mime_type)

            # Если есть файл - отправляем с файлом
            if file_to_send:
                return self._send_message_with_file(
                    chat_id=chat_id,
                    text=text,
                    file=file_to_send,
                    reply_to_message_id=reply_to_message_id
                )

            # Если есть URL - отправляем ссылку
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

    def _send_text_message(self, chat_id: str, text: str, reply_to_message_id: int = None) -> bool:
        """Отправить только текстовое сообщение."""
        if not text:
            logger.warning("Нет текста для отправки")
            return False

        endpoint = f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}"
        data = {'message': text}
        if reply_to_message_id:
            data['replyTo'] = reply_to_message_id

        params = {'lookIntoFuture': 0, 'setReadMarker': 0}

        response: HttpResponse = self.http.post(endpoint, data=data, params=params)
        if response.data is not None and response.data != {}:
            logger.success(f"Отправка текстового сообщения в {chat_id}: {text[:50]}...")
            return True

        # Fallback: пробуем без параметров
        logger.trace("Отправка с параметрами не удалась, пробуем без них...")
        response = self.http.post(endpoint, data=data)
        if response.data is not None and response.data != {}:
            logger.success("Сообщение успешно отправлено (без параметров)")
            return True

        logger.error("Не удалось отправить текстовое сообщение")
        return False

    def _send_message_with_file(
            self,
            chat_id: str,
            text: str,
            file: Tuple[str, bytes, str],
            reply_to_message_id: int = None
    ) -> bool:
        """
        Отправить сообщение с файлом-вложением.

        Args:
            file: Кортеж (filename, content, mime_type)
        """
        file_name, file_content, mime_type = file
        logger.info(f"📤 Отправка файла {file_name} в комнату {chat_id}")

        try:
            # ШАГ 1: Создаём директорию для файлов бота
            webdav_dir = f"/remote.php/dav/files/{self.http.user}/Talk"
            self.http.mkcol(webdav_dir)  # Игнорируем ошибку если существует

            # ШАГ 2: Загружаем файл через WebDAV
            webdav_url = f"{webdav_dir}/{file_name}"
            headers = {'Content-Type': mime_type}

            response = self.http.put(webdav_url, data=file_content, headers=headers)
            if response.status_code not in [200, 201, 204]:
                logger.error(f"Ошибка загрузки файла через WebDAV: {response.status_code}")
                return False

            logger.info(f"Файл {file_name} загружен на сервер")

            # ШАГ 3: Получаем ID файла
            file_id = self._get_file_id(file_name)

            if file_id and file_id != "unknown":
                share_url = self._create_public_share(file_id, file_name)
                if share_url:
                    file_link = share_url
                    logger.info(f"Создана публичная ссылка: {share_url}")
                else:
                    file_link = f"{self.http.host}/index.php/f/{file_id}"
                    logger.warning(f"Используем прямую ссылку: {file_link}")
            else:
                file_link = f"{self.http.host}{webdav_url}"
                logger.warning(f"Не удалось получить ID файла, используем WebDAV ссылку")

            # ШАГ 4: Отправляем сообщение со ссылкой
            if text:
                message_text = f"{text}\n\n📎 [{file_name}]({file_link})"
            else:
                message_text = f"📎 [{file_name}]({file_link})"

            return self._send_text_message(chat_id, message_text, reply_to_message_id)

        except Exception as e:
            logger.error(f"Ошибка при отправке файла: {e}")
            return False

    def _get_file_id(self, file_name: str) -> str:
        """
        Получить ID загруженного файла через WebDAV PROPFIND.

        Returns:
            ID файла или "unknown"
        """
        webdav_url = f"/remote.php/dav/files/{self.http.user}/Talk/{file_name}"

        propfind_body = '''<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <oc:fileid />
    <oc:size />
    <d:getlastmodified />
  </d:prop>
</d:propfind>'''

        try:
            response = self.http.propfind(webdav_url, propfind_body)

            if response.status_code == 207:  # Multi-status
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.raw_text)

                namespaces = {
                    'd': 'DAV:',
                    'oc': 'http://owncloud.org/ns',
                    'nc': 'http://nextcloud.org/ns'
                }

                fileid_elem = root.find('.//oc:fileid', namespaces)
                if fileid_elem is not None and fileid_elem.text:
                    return fileid_elem.text

                fileid_elem = root.find('.//nc:fileid', namespaces)
                if fileid_elem is not None and fileid_elem.text:
                    return fileid_elem.text

                return "unknown"
            else:
                return "unknown"

        except Exception as e:
            logger.error(f"Ошибка при получении ID файла {file_name}: {e}")
            return "unknown"

    def _create_public_share(self, file_id: str, file_name: str, password: str = None) -> Optional[str]:
        """
        Создать публичную ссылку на файл через Sharing API.

        Returns:
            URL публичной ссылки или None
        """
        if not file_id or file_id == "unknown":
            return None

        endpoint = "/ocs/v2.php/apps/files_sharing/api/v1/shares"

        data = {
            'shareType': 3,
            'path': f"/Talk/{file_name}",
            'permissions': 1,
            'name': f"Shared: {file_name}"
        }

        if password:
            data['password'] = password

        try:
            response = self.http.post(endpoint, data=data)

            if response.data and 'url' in response.data:
                return response.data['url']
            else:
                logger.warning(f"Не удалось создать публичную ссылку для {file_name}")
                return None

        except Exception as e:
            logger.error(f"Ошибка при создании публичной ссылки для {file_name}: {e}")
            return None

    # ========== Работа с файлами ==========

    def download_file(self, file_obj: File, save_path: Optional[str] = None) -> Union[bytes, str, None]:
        """
        Скачать файл с сервера.

        Args:
            file_obj: File объект из сообщения
            save_path: Путь для сохранения. Если None - вернуть байты

        Returns:
            Байты или путь к файлу
        """
        try:
            # Пробуем через WebDAV
            file_url = f"/remote.php/dav/files/{self.http.user}/{file_obj.file_path}"
            response = self.http.get(file_url)

            if response.status_code == 200:
                content = response.raw_text.encode() if isinstance(response.raw_text, str) else response.raw_text
                if save_path:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, 'wb') as f:
                        f.write(content if isinstance(content, bytes) else content.encode())
                    return save_path
                return content if isinstance(content, bytes) else content.encode()

            # Пробуем прямую ссылку
            if file_obj.download_url:
                response = self.http.get(file_obj.download_url)
                if response.status_code == 200:
                    content = response.raw_text.encode() if isinstance(response.raw_text, str) else response.raw_text
                    if save_path:
                        with open(save_path, 'wb') as f:
                            f.write(content if isinstance(content, bytes) else content.encode())
                        return save_path
                    return content if isinstance(content, bytes) else content.encode()

            logger.error(f"Не удалось скачать файл {file_obj.file_name}")
            return None

        except Exception as e:
            logger.error(f"Ошибка при скачивании файла {file_obj.file_name}: {e}")
            return None

    # ========== Утилиты ==========

    def check_session_status(self) -> dict:
        """Проверка состояния сессии."""
        response = self.http.get("/ocs/v2.php/cloud/user")
        if response.status_code == 200:
            return {
                'authenticated': True,
                'user': response.data.get('display-name', self.http.user)
            }
        else:
            return {
                'authenticated': False,
                'status_code': response.status_code
            }

    def diagnose_room_access(self, chat_id: str, room_name: str = None) -> dict:
        """Диагностика доступа к комнате."""
        results = {
            'room_token': chat_id,
            'bot_user': self.http.user,
            'session': self.check_session_status()
        }

        # Получаем список комнат
        try:
            rooms = self.get_rooms()
            results['available_rooms_count'] = len(rooms)
            results['available_rooms'] = [
                {'name': r.get('name'), 'token': r.get('token'), 'type': r.get('type')}
                for r in rooms[:10]
            ]

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

        # Проверяем информацию о комнате
        try:
            room_info = self.get_room_info(chat_id)
            if room_info:
                results['room_info'] = {
                    'name': room_info.get('name'),
                    'type': room_info.get('type'),
                    'participant_count': room_info.get('participantCount', 0)
                }
        except Exception as e:
            results['room_info_error'] = str(e)

        # Проверяем членство
        try:
            is_member = self.ensure_room_membership(chat_id)
            results['is_member'] = is_member
        except Exception as e:
            results['membership_check_error'] = str(e)

        return results