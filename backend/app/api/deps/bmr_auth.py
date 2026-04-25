"""Actor identification + bearer-token gate for BMR endpoints.

v0 is intentionally minimal: every BMR endpoint must identify the actor
via a validated ``X-Actor-Id`` header, and — when ``AT_BMR__API_TOKEN``
is set — also present a matching ``Authorization: Bearer <token>``.

The real auth integration (SSO / IdP / RBAC) will replace this module
wholesale. Until then this gate removes the trivial spoof where an
anonymous client sets any actor id and writes to the audit trail.
"""

from __future__ import annotations

import hmac
import os
import re

from fastapi import Header, HTTPException, status

_ACTOR_RE = re.compile(r"^[A-Za-z0-9._@+\-]{1,128}$")
_BEARER_PREFIX = "Bearer "


def _configured_token() -> str | None:
    token = os.getenv("AT_BMR__API_TOKEN") or os.getenv("BMR_API_TOKEN")
    return token.strip() if token and token.strip() else None


def require_actor(
    x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Validate caller identity and return a normalized actor id.

    * ``X-Actor-Id`` must be present and match a conservative charset
      so it is safe to embed in audit records, filesystem paths, and
      emitted events.
    * When a shared bearer token is configured, every request must
      carry it. This is not a replacement for user auth — it is a
      deployment-level kill switch until SSO arrives.
    """

    if not x_actor_id or not _ACTOR_RE.fullmatch(x_actor_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed X-Actor-Id header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expected = _configured_token()
    if expected is not None:
        if not authorization or not authorization.startswith(_BEARER_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        presented = authorization[len(_BEARER_PREFIX):].strip()
        if not hmac.compare_digest(presented, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return x_actor_id


__all__ = ["require_actor"]
