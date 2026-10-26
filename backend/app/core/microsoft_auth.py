"""Validate Entra v2 ID tokens against a fixed, administrator-configured tenant."""
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWTError

from app.core.config import settings


def microsoft_configuration() -> tuple[str, str] | None:
    try:
        return str(UUID(settings.MICROSOFT_CLIENT_ID)), str(UUID(settings.MICROSOFT_TENANT_ID))
    except (ValueError, TypeError, AttributeError):
        return None


@lru_cache(maxsize=4)
def _jwks_client(tenant_id: str) -> PyJWKClient:
    # Never use token-provided URLs or tenants for key retrieval.
    return PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        timeout=5, lifespan=3600,
    )


def verify_microsoft_token(token: str) -> dict:
    config = microsoft_configuration()
    if config is None:
        raise HTTPException(status_code=503, detail="Đăng nhập Microsoft chưa được cấu hình.")
    client_id, tenant_id = config
    try:
        # Reject malformed/unsupported tokens before any network call.
        if jwt.get_unverified_header(token).get("alg") != "RS256":
            raise jwt.InvalidTokenError("Unsupported signing algorithm")
        key = _jwks_client(tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tid", "oid"]},
        )
        if claims["tid"] != tenant_id or claims.get("ver") != "2.0":
            raise jwt.InvalidTokenError("Unexpected tenant or token version")
        claims["oid"] = str(UUID(claims["oid"]))
        return claims
    except PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="Không thể kết nối Microsoft để xác thực. Vui lòng thử lại sau.") from None
    except (PyJWTError, ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Phiên đăng nhập Microsoft không hợp lệ hoặc đã hết hạn.", headers={"WWW-Authenticate": "Bearer"}) from None
