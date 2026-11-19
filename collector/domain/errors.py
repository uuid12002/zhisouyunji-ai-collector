from enum import Enum


class ErrorKind(Enum):
    UNAUTHORIZED = 'unauthorized'
    FORBIDDEN = 'forbidden'
    NOT_FOUND = 'not_found'
    CONFLICT = 'conflict'
    INVALID_REQUEST = 'invalid_request'


class ApplicationError(Exception):
    def __init__(self, kind: ErrorKind, message: str = ''):
        super().__init__(message)
        self.kind = kind
        self.message = message


class CollectionError(Exception):
    def __init__(self, code, message, human=True):
        super().__init__(message)
        self.code, self.human = code, human


class TransportError(Exception):
    """Remote rejection independent of urllib or an HTTP framework."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
