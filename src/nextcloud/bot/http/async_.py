"""
Асинхронный HTTP клиент для Nextcloud API на базе aiohttp.ClientSession.
"""

import aiohttp
import asyncio
import json
from typing import Optional, Dict, Any, Union
from urllib.parse import urljoin

from loguru import logger

from .base import BaseHTTPClient, HttpResponse


class AsyncHTTPClient(BaseHTTPClient):
    """
    Асинхронная реализация HTTP клиента с автоматической переавторизацией.

    Особенности:
    - Использует aiohttp.ClientSession для постоянного соединения
    - Автоматически добавляет OCS-APIRequest хедеры
    - Обрабатывает 401 и пересоздаёт сессию
    - Все ответы приводятся к единому HttpResponse формату
    - Поддерживает async context manager
    """

    def __init__(self, host: str, user: str, password: str):
        super().__init__(host, user, password)
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None

    async def _init_session(self):
        """Создать новую сессию с базовой аутентификацией"""
        # Создаём коннектор с ограничениями
        self._connector = aiohttp.TCPConnector(
            limit=100,  # Максимум одновременных соединений
            limit_per_host=30,  # На один хост
            ttl_dns_cache=300,  # DNS кэш на 5 минут
            enable_cleanup_closed=True
        )

        auth = aiohttp.BasicAuth(self.user, self.password)
        self._session = aiohttp.ClientSession(
            auth=auth,
            connector=self._connector,
            headers={
                'OCS-APIRequest': 'true',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
        )

    async def _reinit_session(self):
        """Пересоздать сессию (при 401 ошибке)"""
        logger.warning("Пересоздание сессии из-за 401")
        if self._session:
            await self._session.close()
        if self._connector:
            await self._connector.close()
        await self._init_session()

    async def _ensure_session(self):
        """Убедиться, что сессия создана"""
        if self._session is None:
            await self._init_session()

    async def _make_request(
            self,
            method: str,
            url: str,
            retry: bool = True,
            **kwargs
    ) -> HttpResponse:
        """Внутренний метод выполнения запроса с обработкой 401."""
        await self._ensure_session()

        # Добавляем OCS параметр format=json если его нет
        if 'params' in kwargs:
            if 'format' not in kwargs['params']:
                kwargs['params']['format'] = 'json'
        else:
            kwargs['params'] = {'format': 'json'}

        # Таймаут по умолчанию
        if 'timeout' not in kwargs:
            kwargs['timeout'] = aiohttp.ClientTimeout(total=30)

        try:
            async with self._session.request(method, url, **kwargs) as response:
                status_code = response.status
                raw_text = await response.text()

                # При 401 пробуем переавторизоваться
                if status_code == 401 and retry:
                    logger.warning(f"Получен 401 для {url}, пересоздаём сессию...")
                    await self._reinit_session()
                    return await self._make_request(method, url, retry=False, **kwargs)

                # Парсим ответ
                data = {}
                if status_code in [200, 201, 207]:  # 207 - Multi-status для PROPFIND
                    try:
                        # Для PROPFIND не парсим JSON
                        if 'xml' in response.headers.get('content-type', ''):
                            data = {}  # XML обрабатывается отдельно
                        else:
                            json_response = json.loads(raw_text) if raw_text else {}
                            data = json_response.get('ocs', {}).get('data', {})
                    except json.JSONDecodeError:
                        if raw_text and not raw_text.startswith('<?xml'):
                            logger.error(f"Ошибка парсинга JSON: {raw_text[:200]}")

                logger.debug(f"Request {method} {url} -> {status_code}")
                return HttpResponse(
                    status_code=status_code,
                    data=data,
                    raw_text=raw_text,
                    headers=dict(response.headers)
                )

        except aiohttp.ClientResponseError as e:
            logger.error(f"ClientResponseError: {e.status} {e.message}")
            return HttpResponse(status_code=e.status, data={}, raw_text=str(e))
        except asyncio.TimeoutError:
            logger.error(f"Timeout при запросе к {url}")
            return HttpResponse(status_code=408, data={}, raw_text="Timeout")
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка подключения: {e}")
            return HttpResponse(status_code=0, data={}, raw_text=str(e))
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return HttpResponse(status_code=0, data={}, raw_text=str(e))

    async def request(
            self,
            method: str,
            endpoint: str,
            data: Optional[Dict] = None,
            params: Optional[Dict] = None,
            json_data: Optional[Dict] = None,
            files: Optional[Dict] = None,
            headers: Optional[Dict] = None,
    ) -> HttpResponse:
        """
        Выполнить HTTP запрос к API.
        """
        url = urljoin(self.host, endpoint)

        kwargs = {
            'params': params or {},
            'headers': headers or {}
        }

        if data:
            kwargs['data'] = data
        if json_data:
            kwargs['json'] = json_data
        if files:
            # aiohttp обрабатывает files через форму
            # files должен быть dict {field: (filename, content, content_type)}
            kwargs['data'] = aiohttp.FormData()
            for field, file_tuple in files.items():
                kwargs['data'].add_field(
                    field,
                    file_tuple[1],
                    filename=file_tuple[0],
                    content_type=file_tuple[2] if len(file_tuple) > 2 else 'application/octet-stream'
                )

        return await self._make_request(method, url, **kwargs)

    async def get(self, endpoint: str, params: Optional[Dict] = None) -> HttpResponse:
        """GET запрос"""
        return await self.request('GET', endpoint, params=params)

    async def post(
            self,
            endpoint: str,
            data: Optional[Dict] = None,
            json_data: Optional[Dict] = None,
            files: Optional[Dict] = None,
            params: Optional[Dict] = None  # Добавить этот параметр
    ) -> HttpResponse:
        """POST запрос"""
        return await self.request('POST', endpoint, data=data, json_data=json_data, files=files, params=params)


    async def put(self, endpoint: str, data: Any, headers: Optional[Dict] = None) -> HttpResponse:
        """
        PUT запрос для WebDAV загрузки файлов.
        """
        url = urljoin(self.host, endpoint)
        kwargs = {
            'data': data,
            'headers': headers or {},
            'timeout': aiohttp.ClientTimeout(total=60)  # Файлы грузятся дольше
        }
        return await self._make_request('PUT', url, **kwargs)

    async def delete(self, endpoint: str) -> HttpResponse:
        """DELETE запрос"""
        return await self.request('DELETE', endpoint)

    async def propfind(self, url: str, body: str) -> HttpResponse:
        """
        PROPFIND запрос для WebDAV.
        """
        full_url = urljoin(self.host, url) if not url.startswith('http') else url

        headers = {
            'Content-Type': 'application/xml; charset=utf-8',
            'Depth': '0'
        }

        kwargs = {
            'data': body,
            'headers': headers,
            'timeout': aiohttp.ClientTimeout(total=30)
        }

        return await self._make_request('PROPFIND', full_url, **kwargs)

    async def mkcol(self, url: str) -> HttpResponse:
        """
        MKCOL запрос для создания директории в WebDAV.
        """
        full_url = urljoin(self.host, url) if not url.startswith('http') else url
        return await self._make_request('MKCOL', full_url)

    async def check_connection(self) -> bool:
        """Проверить подключение и аутентификацию"""
        response = await self.get('/ocs/v2.php/cloud/user')
        return response.status_code == 200 and response.data.get('id')

    async def close(self):
        """Закрыть HTTP сессию и коннектор"""
        if self._session:
            await self._session.close()
        if self._connector:
            await self._connector.close()

    async def __aenter__(self):
        """Асинхронный контекстный менеджер"""
        await self._init_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Закрыть сессию при выходе"""
        await self.close()