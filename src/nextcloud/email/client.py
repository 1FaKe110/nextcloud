"""Email client for Nextcloud"""


class EmailClient:
    """Клиент для работы с почтой Nextcloud"""

    def __init__(self, host: str, user: str, password: str):
        self.host = host
        self.user = user
        self.password = password
        raise NotImplementedError("Email module is under development")