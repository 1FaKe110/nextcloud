from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union, Tuple
from dataclasses import dataclass


# Опционально: dataclass для ответа
@dataclass
class HttpResponse:
    """Единый формат ответа от HTTP клиента"""
    status_code: int
    data: Dict[str, Any]  # Распарсенный JSON (ocs.data)
    raw_text: Optional[str] = None
    headers: Optional[Dict[str, str]] = None


class BaseHTTPClient(ABC):
    """
    Абстрактный HTTP клиент для работы с Nextcloud API.

    Все методы должны быть реализованы в синхронной и асинхронной версиях.
    """

    def __init__(self, host: str, user: str, password: str):
        self.host = host.rstrip('/')
        self.user = user
        self.password = password

    @abstractmethod
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
        Выполнить HTTP запрос.

        Args:
            method: GET, POST, PUT, DELETE, PROPFIND, MKCOL
            endpoint: Путь API (начинается с /)
            data: Form data для POST
            params: Query параметры
            json_data: JSON данные
            files: Файлы для multipart
            headers: Дополнительные заголовки

        Returns:
            HttpResponse с данными ответа
        """
        pass

    @abstractmethod
    def get(self, endpoint: str, params: Optional[Dict] = None) -> HttpResponse:
        """GET запрос"""
        pass

    @abstractmethod
    def post(
            self,
            endpoint: str,
            data: Optional[Dict] = None,
            json_data: Optional[Dict] = None,
            files: Optional[Dict] = None
    ) -> HttpResponse:
        """POST запрос"""
        pass

    @abstractmethod
    def put(self, endpoint: str, data: Any, headers: Optional[Dict] = None) -> HttpResponse:
        """PUT запрос (для WebDAV загрузки файлов)"""
        pass

    @abstractmethod
    def delete(self, endpoint: str) -> HttpResponse:
        """DELETE запрос"""
        pass

    @abstractmethod
    def propfind(self, url: str, body: str) -> HttpResponse:
        """PROPFIND запрос (WebDAV для получения fileid)"""
        pass

    @abstractmethod
    def mkcol(self, url: str) -> HttpResponse:
        """MKCOL запрос (создание директории в WebDAV)"""
        pass

    @abstractmethod
    def close(self):
        """Закрыть HTTP сессию"""
        pass

    @abstractmethod
    def check_connection(self) -> bool:
        """Проверить подключение и аутентификацию"""
        pass