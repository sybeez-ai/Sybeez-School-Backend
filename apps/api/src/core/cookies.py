"""httpOnly session cookies. The refresh token is not readable from JavaScript."""

from fastapi import Response

from src.core.config import get_settings


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    access_max_age: int,
    refresh_max_age: int,
    secure: bool | None = None,
) -> None:
    settings = get_settings()
    use_secure = settings.use_secure_cookies if secure is None else secure
    domain = settings.cookie_domain or None
    samesite = settings.cookie_samesite.lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    shared = {
        "httponly": True,
        "secure": use_secure,
        "samesite": samesite,
        "domain": domain,
    }
    response.set_cookie(
        settings.access_cookie_name,
        access_token,
        max_age=access_max_age,
        path="/",
        **shared,
    )
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=refresh_max_age,
        path="/api/v1/auth",
        **shared,
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    domain = settings.cookie_domain or None
    response.delete_cookie(settings.access_cookie_name, path="/", domain=domain)
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth", domain=domain)
