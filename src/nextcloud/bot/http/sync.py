"""
Синхронный HTTP клиент для Nextcloud API на базе requests.Session.
"""

import requests
import json
from typing import Optional, Dict, Any, Union
from urllib.parse import urljoin

from loguru import logger

from .base import BaseHTTPClient, HttpResponse


class SyncHTTPClient(BaseHTTPClient):
    """
    Синхронная реализация HTTP клиента с автоматической переавторизацией.
    """

    def __init__(self, host: str, user: str, password: str):
        super().__init__(host, user, password)
        self._session: Optional[requests.Session] = None
        self._init_session()

    def _init_session(self):
        """Создать новую сессию с базовой аутентификацией"""
        self._session = requests.Session()
        self._session.auth = (self.user, self.password)
        # Убираем глобальный Content-Type
        self._session.headers.update({
            'OCS-APIRequest': 'true',
            'Accept': 'application/json'
        })

    def _reinit_session(self):
        """Пересоздать сессию (при 401 ошибке)"""
        logger.warning("Пересоздание сессии из-за 401")
        if self._session:
            self._session.close()
        self._init_session()

    def _make_request(
            self,
            method: str,
            url: str,
            retry: bool = True,
            **kwargs
    ) -> HttpResponse:
        """
        Внутренний метод выполнения запроса с обработкой 401.
        """
        # Добавляем OCS параметр format=json если его нет
        if 'params' in kwargs:
            if 'format' not in kwargs['params']:
                kwargs['params']['format'] = 'json'
        else:
            kwargs['params'] = {'format': 'json'}

        # Таймаут по умолчанию
        if 'timeout' not in kwargs:
            kwargs['timeout'] = 30

        try:
            response = self._session.request(method, url, **kwargs)

            # При 401 пробуем переавторизоваться
            if response.status_code == 401 and retry:
                self._reinit_session()
                return self._make_request(method, url, retry=False, **kwargs)

            # Парсим ответ
            data = {}
            raw_text = response.text

            if response.status_code in [200, 201, 207]:
                try:
                    content_type = response.headers.get('content-type', '').lower()

                    # Для PROPFIND или XML не парсим JSON
                    if 'xml' in content_type:
                        data = {}
                    elif 'json' in content_type:
                        json_response = response.json()
                        data = json_response.get('ocs', {}).get('data', {})
                    else:
                        # Не JSON и не XML - оставляем как есть
                        data = {}
                except json.JSONDecodeError:
                    # Если не JSON, но и не ошибка - логируем только trace
                    if not raw_text.startswith('<?xml') and response.status_code != 500:
                        logger.trace(f"Не JSON ответ (status={response.status_code}): {raw_text[:100]}")

            return HttpResponse(
                status_code=response.status_code,
                data=data,
                raw_text=raw_text,
                headers=dict(response.headers)
            )

        except requests.exceptions.Timeout:
            logger.error(f"Timeout при запросе к {url}")
            return HttpResponse(status_code=408, data={}, raw_text="Timeout")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Ошибка подключения: {e}")
            return HttpResponse(status_code=0, data={}, raw_text=str(e))
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return HttpResponse(status_code=0, data={}, raw_text=str(e))

    def request(
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
            # Не устанавливаем Content-Type - requests сам поставит
            # application/x-www-form-urlencoded для data
        elif json_data:
            kwargs['json'] = json_data
        elif files:
            kwargs['files'] = files
            # Для multipart убираем Content-Type, requests сам установит
            if 'Content-Type' in kwargs['headers']:
                del kwargs['headers']['Content-Type']

        return self._make_request(method, url, **kwargs)

    def get(self, endpoint: str, params: Optional[Dict] = None) -> HttpResponse:
        """GET запрос"""
        return self.request('GET', endpoint, params=params)

    def post(
            self,
            endpoint: str,
            data: Optional[Dict] = None,
            json_data: Optional[Dict] = None,
            files: Optional[Dict] = None,
            params: Optional[Dict] = None
    ) -> HttpResponse:
        """POST запрос"""
        # Определяем, какой эндпоинт
        is_chat_endpoint = '/chat/' in endpoint

        if is_chat_endpoint and data and not files:
            # Для чата используем form-urlencoded
            return self.request('POST', endpoint, data=data, params=params)
        else:
            # Для остальных - JSON
            return self.request('POST', endpoint, json_data=json_data or data, params=params)

    def put(self, endpoint: str, data: Any, headers: Optional[Dict] = None) -> HttpResponse:
        """PUT запрос для WebDAV загрузки файлов."""
        url = urljoin(self.host, endpoint)
        kwargs = {
            'data': data,
            'headers': headers or {},
            'timeout': 60
        }
        return self._make_request('PUT', url, **kwargs)

    def delete(self, endpoint: str) -> HttpResponse:
        """DELETE запрос"""
        return self.request('DELETE', endpoint)

    def propfind(self, url: str, body: str) -> HttpResponse:
        """PROPFIND запрос для WebDAV."""
        full_url = urljoin(self.host, url) if not url.startswith('http') else url

        headers = {
            'Content-Type': 'application/xml; charset=utf-8',
            'Depth': '0'
        }

        kwargs = {
            'data': body,
            'headers': headers,
            'timeout': 30
        }

        return self._make_request('PROPFIND', full_url, **kwargs)

    def mkcol(self, url: str) -> HttpResponse:
        """MKCOL запрос для создания директории в WebDAV."""
        full_url = urljoin(self.host, url) if not url.startswith('http') else url
        return self._make_request('MKCOL', full_url)

    def check_connection(self) -> bool:
        """Проверить подключение и аутентификацию"""
        response = self.get('/ocs/v2.php/cloud/user')
        return response.status_code == 200 and response.data.get('id')

    def close(self):
        """Закрыть HTTP сессию"""
        if self._session:
            self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()