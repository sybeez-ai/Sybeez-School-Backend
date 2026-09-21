from fastapi import HTTPException, status


class AppError(Exception):
    def __init__(self, message: str, code: str = "app_error", status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found", code: str = "not_found"):
        super().__init__(message, code=code, status_code=status.HTTP_404_NOT_FOUND)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Forbidden", code: str = "forbidden"):
        super().__init__(message, code=code, status_code=status.HTTP_403_FORBIDDEN)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Unauthorized", code: str = "unauthorized"):
        super().__init__(message, code=code, status_code=status.HTTP_401_UNAUTHORIZED)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict", code: str = "conflict"):
        super().__init__(message, code=code, status_code=status.HTTP_409_CONFLICT)


class UpstreamError(AppError):
    def __init__(self, message: str = "Upstream service error", code: str = "upstream_error"):
        super().__init__(message, code=code, status_code=status.HTTP_502_BAD_GATEWAY)


def to_http(exc: AppError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
