"""AWS Cognito helpers — password auth + AdminCreateUser for school provisioning."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse
import urllib.request
from base64 import b64encode, urlsafe_b64decode
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from src.core.config import get_settings
from src.core.exceptions import ConflictError, UnauthorizedError

logger = logging.getLogger(__name__)


@dataclass
class CognitoAuthResult:
    access_token: str
    id_token: str
    refresh_token: str
    sub: str
    email: str


@dataclass
class CognitoNewPasswordChallenge:
    session: str
    email: str
    challenge: str = "NEW_PASSWORD_REQUIRED"


def cognito_configured() -> bool:
    s = get_settings()
    return bool(s.cognito_user_pool_id and s.cognito_app_client_id and s.cognito_region)


def _client():
    s = get_settings()
    return boto3.client("cognito-idp", region_name=s.cognito_region)


def _secret_hash(username: str) -> str | None:
    s = get_settings()
    if not s.cognito_app_client_secret:
        return None
    dig = hmac.new(
        s.cognito_app_client_secret.encode("utf-8"),
        f"{username}{s.cognito_app_client_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return b64encode(dig).decode()


def _decode_id_token(id_token: str, fallback_email: str) -> tuple[str, str]:
    def _b64pad(part: str) -> bytes:
        return urlsafe_b64decode(part + "=" * (-len(part) % 4))

    payload = json.loads(_b64pad(id_token.split(".")[1]).decode())
    return str(payload.get("sub") or ""), str(payload.get("email") or fallback_email).lower()


def _decode_id_token_payload(id_token: str) -> dict:
    def _b64pad(part: str) -> bytes:
        return urlsafe_b64decode(part + "=" * (-len(part) % 4))

    return json.loads(_b64pad(id_token.split(".")[1]).decode())


def _auth_result_from_tokens(result: dict, fallback_email: str) -> CognitoAuthResult:
    id_token = result.get("IdToken")
    access_token = result.get("AccessToken")
    refresh_token = result.get("RefreshToken")
    if not id_token or not access_token:
        raise UnauthorizedError("Cognito did not return tokens")
    sub, email = _decode_id_token(id_token, fallback_email)
    return CognitoAuthResult(
        access_token=access_token,
        id_token=id_token,
        refresh_token=refresh_token or "",
        sub=sub,
        email=email,
    )


def _resolve_username(email: str) -> str:
    """Resolve Cognito Username for an email (email-as-username or UUID username pools)."""
    s = get_settings()
    email = email.strip().lower()
    client = _client()
    try:
        user = client.admin_get_user(UserPoolId=s.cognito_user_pool_id, Username=email)
        return str(user.get("Username") or email)
    except ClientError:
        pass
    try:
        resp = client.list_users(
            UserPoolId=s.cognito_user_pool_id,
            Filter=f'email = "{email}"',
            Limit=1,
        )
        users = resp.get("Users") or []
        if users:
            return str(users[0].get("Username") or email)
    except ClientError as exc:
        logger.warning("Cognito list_users by email failed: %s", exc)
    return email


def authenticate(email: str, password: str) -> CognitoAuthResult | CognitoNewPasswordChallenge:
    """USER_PASSWORD_AUTH. May return NEW_PASSWORD_REQUIRED for temp passwords."""
    if not cognito_configured():
        raise UnauthorizedError("Cognito is not configured")

    s = get_settings()
    username = email.strip().lower()
    candidates = [username]
    resolved = _resolve_username(username)
    if resolved not in candidates:
        candidates.append(resolved)

    last_exc: Exception | None = None
    resp = None
    for candidate in candidates:
        auth_params: dict[str, str] = {"USERNAME": candidate, "PASSWORD": password}
        secret = _secret_hash(candidate)
        if secret:
            auth_params["SECRET_HASH"] = secret
        try:
            resp = _client().initiate_auth(
                ClientId=s.cognito_app_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters=auth_params,
            )
            username = candidate
            break
        except ClientError as exc:
            last_exc = exc
            code = exc.response.get("Error", {}).get("Code", "")
            msg = exc.response.get("Error", {}).get("Message") or code
            logger.warning("Cognito auth failed for %s: %s %s", candidate, code, msg)
            resp = None

    if resp is None:
        raise UnauthorizedError("Invalid email or password") from last_exc

    if resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        session = resp.get("Session") or ""
        if not session:
            raise UnauthorizedError("Password change required but session missing")
        return CognitoNewPasswordChallenge(session=session, email=email.strip().lower())

    if "ChallengeName" in resp:
        raise UnauthorizedError(f"Additional Cognito challenge required: {resp['ChallengeName']}")

    return _auth_result_from_tokens(resp.get("AuthenticationResult") or {}, username)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "School Admin").strip().split(None, 1)
    given = parts[0] if parts else "School"
    family = parts[1] if len(parts) > 1 else "Admin"
    return given[:50], family[:50]


def complete_new_password(
    *,
    email: str,
    session: str,
    new_password: str,
    full_name: str | None = None,
) -> CognitoAuthResult:
    """Finish NEW_PASSWORD_REQUIRED challenge after first login with temporary password."""
    if not cognito_configured():
        raise UnauthorizedError("Cognito is not configured")

    s = get_settings()
    username = _resolve_username(email)
    challenge_responses: dict[str, str] = {
        "USERNAME": username,
        "NEW_PASSWORD": new_password,
    }
    secret = _secret_hash(username)
    if secret:
        challenge_responses["SECRET_HASH"] = secret

    try:
        resp = _client().respond_to_auth_challenge(
            ClientId=s.cognito_app_client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=session,
            ChallengeResponses=challenge_responses,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        msg = exc.response.get("Error", {}).get("Message") or "Could not set new password"
        # If pool still requires missing profile attrs, supply placeholders once.
        if code == "InvalidParameterException" and "missing" in msg.lower():
            given, family = _split_name(full_name or username.split("@")[0])
            challenge_responses.update(
                {
                    "userAttributes.given_name": given,
                    "userAttributes.family_name": family,
                    "userAttributes.phone_number": "+15555550100",
                    "userAttributes.birthdate": "1990-01-01",
                }
            )
            try:
                resp = _client().respond_to_auth_challenge(
                    ClientId=s.cognito_app_client_id,
                    ChallengeName="NEW_PASSWORD_REQUIRED",
                    Session=session,
                    ChallengeResponses=challenge_responses,
                )
            except ClientError as inner:
                logger.warning("Cognito new-password retry failed: %s", inner)
                raise UnauthorizedError(
                    inner.response.get("Error", {}).get("Message") or msg
                ) from inner
        else:
            logger.warning("Cognito new-password failed: %s", code)
            raise UnauthorizedError(msg) from exc

    if "ChallengeName" in resp:
        raise UnauthorizedError(f"Additional Cognito challenge required: {resp['ChallengeName']}")

    return _auth_result_from_tokens(resp.get("AuthenticationResult") or {}, username)


def admin_create_user(
    *,
    email: str,
    temporary_password: str,
    full_name: str,
    make_permanent: bool = False,
) -> str:
    """Create Cognito user with temporary password (FORCE_CHANGE_PASSWORD → first-login reset)."""
    if not cognito_configured():
        raise ConflictError("Cognito is not configured")

    s = get_settings()
    username = email.strip().lower()
    given, family = _split_name(full_name)
    client = _client()
    attrs = [
        {"Name": "email", "Value": username},
        {"Name": "email_verified", "Value": "true"},
        {"Name": "name", "Value": full_name},
        {"Name": "given_name", "Value": given},
        {"Name": "family_name", "Value": family},
        {"Name": "phone_number", "Value": "+15555550100"},
        {"Name": "birthdate", "Value": "1990-01-01"},
    ]
    try:
        resp = client.admin_create_user(
            UserPoolId=s.cognito_user_pool_id,
            Username=username,
            UserAttributes=attrs,
            TemporaryPassword=temporary_password,
            MessageAction="SUPPRESS",
            DesiredDeliveryMediums=["EMAIL"],
        )
        sub = next(
            (a["Value"] for a in resp["User"]["Attributes"] if a["Name"] == "sub"),
            resp["User"]["Username"],
        )
        if make_permanent:
            client.admin_set_user_password(
                UserPoolId=s.cognito_user_pool_id,
                Username=username,
                Password=temporary_password,
                Permanent=True,
            )
        return str(sub)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "UsernameExistsException":
            try:
                resolved = _resolve_username(email)
                client.admin_update_user_attributes(
                    UserPoolId=s.cognito_user_pool_id,
                    Username=resolved,
                    UserAttributes=attrs,
                )
                client.admin_set_user_password(
                    UserPoolId=s.cognito_user_pool_id,
                    Username=resolved,
                    Password=temporary_password,
                    Permanent=make_permanent,
                )
                user = client.admin_get_user(UserPoolId=s.cognito_user_pool_id, Username=resolved)
                sub = next(
                    (a["Value"] for a in user.get("UserAttributes", []) if a["Name"] == "sub"),
                    resolved,
                )
                return str(sub)
            except ClientError as inner:
                raise ConflictError(f"Cognito user exists but password update failed: {inner}") from inner
        raise ConflictError(f"Cognito create user failed: {code}") from exc


def admin_set_enabled(*, email: str, enabled: bool) -> None:
    if not cognito_configured():
        return
    s = get_settings()
    username = _resolve_username(email)
    client = _client()
    try:
        if enabled:
            client.admin_enable_user(UserPoolId=s.cognito_user_pool_id, Username=username)
        else:
            client.admin_disable_user(UserPoolId=s.cognito_user_pool_id, Username=username)
    except ClientError as exc:
        logger.warning("Cognito enable/disable failed for %s: %s", username, exc)


def admin_set_temporary_password(*, email: str, temporary_password: str) -> None:
    """Force a new temporary password so next login requires NEW_PASSWORD_REQUIRED."""
    if not cognito_configured():
        raise ConflictError("Cognito is not configured")
    s = get_settings()
    username = _resolve_username(email)
    try:
        _client().admin_set_user_password(
            UserPoolId=s.cognito_user_pool_id,
            Username=username,
            Password=temporary_password,
            Permanent=False,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        msg = exc.response.get("Error", {}).get("Message") or "Could not reset password"
        raise ConflictError(f"{msg} ({code})") from exc


def admin_clear_mfa(*, email: str) -> None:
    """Disable MFA preferences and forget devices for the user (MFA recovery)."""
    if not cognito_configured():
        raise ConflictError("Cognito is not configured")
    s = get_settings()
    username = _resolve_username(email)
    client = _client()
    try:
        client.admin_set_user_mfa_preference(
            UserPoolId=s.cognito_user_pool_id,
            Username=username,
            SMSMfaSettings={"Enabled": False, "PreferredMfa": False},
            SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
        )
    except ClientError as exc:
        logger.warning("Cognito clear MFA preference failed for %s: %s", username, exc)

    # Forget remembered devices (common source of MFA lock-in)
    try:
        token = None
        while True:
            kwargs = {"UserPoolId": s.cognito_user_pool_id, "Username": username, "Limit": 60}
            if token:
                kwargs["PaginationToken"] = token
            page = client.admin_list_devices(**kwargs)
            for device in page.get("Devices", []):
                device_key = device.get("DeviceKey")
                if not device_key:
                    continue
                try:
                    client.admin_forget_device(
                        UserPoolId=s.cognito_user_pool_id, Username=username, DeviceKey=device_key
                    )
                except ClientError:
                    continue
            token = page.get("PaginationToken")
            if not token:
                break
    except Exception:
        # Device listing/forgetting is best-effort; do not block recovery.
        return


def hosted_ui_google_authorize_url(*, state: str) -> str:
    """Build a Cognito Hosted UI authorize URL for Google sign-in."""
    s = get_settings()
    if not s.cognito_domain:
        raise ConflictError("Cognito domain is not configured")
    domain = s.cognito_domain.rstrip("/")
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    params = {
        "response_type": "code",
        "client_id": s.cognito_app_client_id,
        "redirect_uri": s.cognito_oauth_redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "identity_provider": "Google",
    }
    return f"{domain}/oauth2/authorize?{urllib.parse.urlencode(params)}"


def exchange_oauth_code_for_tokens(*, code: str) -> CognitoAuthResult:
    """Exchange OAuth2 auth code from Hosted UI for Cognito tokens."""
    s = get_settings()
    if not s.cognito_domain:
        raise ConflictError("Cognito domain is not configured")
    domain = s.cognito_domain.rstrip("/")
    if not domain.startswith("http"):
        domain = f"https://{domain}"

    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": s.cognito_app_client_id,
            "code": code,
            "redirect_uri": s.cognito_oauth_redirect_uri,
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if s.cognito_app_client_secret:
        basic = b64encode(f"{s.cognito_app_client_id}:{s.cognito_app_client_secret}".encode("utf-8")).decode()
        headers["Authorization"] = f"Basic {basic}"

    req = urllib.request.Request(f"{domain}/oauth2/token", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except Exception as exc:
        raise UnauthorizedError("Could not exchange Google login code") from exc

    payload = json.loads(body)
    # OAuth2 token endpoint returns snake_case keys
    mapped = {
        "AccessToken": payload.get("access_token"),
        "IdToken": payload.get("id_token"),
        "RefreshToken": payload.get("refresh_token"),
    }
    return _auth_result_from_tokens(mapped, fallback_email="")


def generate_temporary_password(length: int = 12) -> str:
    """Cognito-compatible temp password: upper + lower + digit + symbol."""
    import secrets
    import string

    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    symbols = "!@#$%"
    chars = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(symbols),
    ]
    pool = lower + upper + digits + symbols
    chars.extend(secrets.choice(pool) for _ in range(max(0, length - 4)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def admin_link_google_to_user_by_email(*, email: str, id_token: str) -> None:
    """Best-effort: link Google federated identity to the existing email-based user in the pool."""
    if not cognito_configured():
        return
    s = get_settings()
    username = email.strip().lower()
    client = _client()
    try:
        dest = client.admin_get_user(UserPoolId=s.cognito_user_pool_id, Username=username)
        dest_sub = next(
            (a["Value"] for a in dest.get("UserAttributes", []) if a.get("Name") == "sub"),
            None,
        )
        if not dest_sub:
            return
        payload = _decode_id_token_payload(id_token)
        identities = payload.get("identities") or []
        google = None
        for ident in identities:
            if isinstance(ident, dict) and (
                ident.get("providerName") == "Google" or ident.get("providerType") == "Google"
            ):
                google = ident
                break
        if not google:
            return
        source_user_id = google.get("userId")
        if not source_user_id:
            return
        client.admin_link_provider_for_user(
            UserPoolId=s.cognito_user_pool_id,
            DestinationUser={
                "ProviderName": "Cognito",
                "ProviderAttributeName": "Cognito_Subject",
                "ProviderAttributeValue": dest_sub,
            },
            SourceUser={
                "ProviderName": "Google",
                "ProviderAttributeName": "Cognito_Subject",
                "ProviderAttributeValue": source_user_id,
            },
        )
    except Exception:
        return


def admin_forgot_password(*, email: str) -> None:
    """Start Cognito ForgotPassword (sends verification code to email)."""
    if not cognito_configured():
        raise ConflictError("Cognito is not configured")
    s = get_settings()
    username = _resolve_username(email)
    kwargs: dict = {"ClientId": s.cognito_app_client_id, "Username": username}
    secret = _secret_hash(username)
    if secret:
        kwargs["SecretHash"] = secret
    try:
        _client().forgot_password(**kwargs)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"UserNotFoundException", "InvalidParameterException"}:
            logger.info("Forgot password for unknown/invalid user %s: %s", email, code)
            return
        msg = exc.response.get("Error", {}).get("Message") or "Could not start password reset"
        logger.warning("Cognito forgot_password failed: %s %s", code, msg)
        raise ConflictError(msg) from exc


def admin_confirm_forgot_password(*, email: str, confirmation_code: str, new_password: str) -> None:
    """Complete Cognito ForgotPassword with code + new password."""
    if not cognito_configured():
        raise ConflictError("Cognito is not configured")
    s = get_settings()
    username = _resolve_username(email)
    kwargs: dict = {
        "ClientId": s.cognito_app_client_id,
        "Username": username,
        "ConfirmationCode": confirmation_code.strip(),
        "Password": new_password,
    }
    secret = _secret_hash(username)
    if secret:
        kwargs["SecretHash"] = secret
    try:
        _client().confirm_forgot_password(**kwargs)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        msg = exc.response.get("Error", {}).get("Message") or "Could not reset password"
        raise UnauthorizedError(f"{msg} ({code})") from exc
