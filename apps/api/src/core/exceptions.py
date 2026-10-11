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


class ValidationError(AppError):
    def __init__(self, message: str = "Validation failed", code: str = "validation_error"):
        super().__init__(message, code=code, status_code=422)


class UpstreamError(AppError):
    def __init__(self, message: str = "Upstream service error", code: str = "upstream_error"):
        super().__init__(message, code=code, status_code=status.HTTP_502_BAD_GATEWAY)


def to_http(exc: AppError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


_LOCATIONS = {"body", "query", "path", "header", "cookie"}
_TYPE_MESSAGES = {
    "missing": "is required",
    "uuid_parsing": "must be a valid ID",
    "uuid_type": "must be a valid ID",
    "string_pattern_mismatch": "has an invalid value",
    "date_parsing": "must be a valid date (YYYY-MM-DD)",
    "date_from_datetime_parsing": "must be a valid date (YYYY-MM-DD)",
    "date_from_datetime_inexact": "must be a valid date (YYYY-MM-DD)",
    "date_type": "must be a valid date (YYYY-MM-DD)",
    "datetime_parsing": "must be a valid date and time",
    "datetime_from_date_parsing": "must be a valid date and time",
    "datetime_type": "must be a valid date and time",
    "int_parsing": "must be a whole number",
    "int_type": "must be a whole number",
    "int_from_float": "must be a whole number",
    "float_parsing": "must be a number",
    "float_type": "must be a number",
    "decimal_parsing": "must be a number",
    "decimal_type": "must be a number",
    "bool_parsing": "must be true or false",
    "bool_type": "must be true or false",
    "string_type": "must be text",
    "dict_type": "must be an object",
    "model_attributes_type": "must be an object",
    "model_type": "must be an object",
    "list_type": "must be a list",
    "json_invalid": "is not valid JSON",
    "extra_forbidden": "is not allowed",
}


def _field_label(loc: tuple | list) -> str:
    parts = [str(p) for p in loc if p not in _LOCATIONS and not isinstance(p, int)]
    return parts[-1].replace("_", " ").capitalize() if parts else "Request body"


def _field_path(loc: tuple | list) -> str:
    return ".".join(str(p) for p in loc if p not in _LOCATIONS) or "body"


def _validation_message(error: dict) -> str:
    """A user-facing reason that never echoes the submitted value or internal patterns."""
    kind, ctx = error.get("type", ""), error.get("ctx") or {}
    if kind in _TYPE_MESSAGES:
        return _TYPE_MESSAGES[kind]
    if kind in {"literal_error", "enum"} and ctx.get("expected"):
        return f"must be one of {ctx['expected']}"
    if kind == "string_too_long":
        return f"must be at most {ctx.get('max_length')} characters"
    if kind == "string_too_short":
        n = ctx.get("min_length")
        return "must not be empty" if n == 1 else f"must be at least {n} characters"
    if kind in {"decimal_max_digits", "decimal_whole_digits"}:
        return "is too large"
    if kind == "decimal_max_places":
        return f"must have at most {ctx.get('decimal_places')} decimal places"
    if kind in {"too_long", "too_short"}:
        return error.get("msg", "has an invalid number of items").replace("List should", "must")
    msg = str(error.get("msg", "is invalid"))
    if "valid email address" in msg:
        return "must be a valid email address"
    if msg.startswith("Value error, "):
        return msg.removeprefix("Value error, ")
    return msg.replace("Input should", "must")


_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 409: "conflict"}


def http_error_body(status_code: int, detail) -> dict:
    """Standard error shape for HTTPException; `detail` is kept for callers that still read it."""
    if isinstance(detail, dict) and detail.get("message"):
        code, message = detail.get("code") or _HTTP_CODES.get(status_code, "http_error"), detail["message"]
    else:
        code, message = _HTTP_CODES.get(status_code, "http_error"), str(detail)
    return {"error": {"code": code, "message": message}, "detail": detail}


def validation_error_body(errors: list[dict]) -> dict:
    fields = []
    for error in errors:
        loc = error.get("loc", ())
        reason = _validation_message(error)
        is_model_rule = error.get("type") == "value_error" and len([p for p in loc if p not in _LOCATIONS]) == 0
        fields.append(
            {
                "field": _field_path(loc),
                "message": reason if is_model_rule else f"{_field_label(loc)} {reason}",
            }
        )
    message = fields[0]["message"] if fields else "Invalid request"
    if message:
        message = message[0].upper() + message[1:]
    return {"error": {"code": "validation_error", "message": message, "fields": fields}}

