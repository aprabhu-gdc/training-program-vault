"""Who is calling: identity and roles from the caller's Entra ID access token.

The connector never trusts a user name or id passed as a tool argument. The
identity written to the analytics lists comes only from the validated token
that Claude obtained when the person connected and signed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class NotSignedInError(PermissionError):
    """Raised when a tool runs without a validated, attributable token."""


@dataclass(frozen=True)
class Caller:
    user_id: str
    user_name: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_role(self, role: str) -> bool:
        return role in self.roles


def roles_from_claims(claims: Mapping[str, Any]) -> frozenset[str]:
    raw = claims.get("roles")
    if isinstance(raw, str):
        return frozenset({raw})
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(str(role) for role in raw if role)
    return frozenset()


def caller_from_claims(claims: Mapping[str, Any] | None, *, tenant_id: str = "") -> Caller:
    """Build the caller from Entra v2 access-token claims.

    ``oid`` (the user's Entra object id) is the stable id. The display name
    falls back to the sign-in name, then to the id. A token from another tenant
    is rejected even though the auth provider already pins the issuer.
    """

    if not claims:
        raise NotSignedInError("No signed-in user on this request.")
    if tenant_id and str(claims.get("tid") or "") not in ("", tenant_id):
        raise NotSignedInError("Token is from a different tenant.")
    user_id = str(claims.get("oid") or "").strip()
    if not user_id:
        raise NotSignedInError("Token has no user object id (oid); app-only tokens are not accepted.")
    user_name = (
        str(claims.get("name") or "").strip()
        or str(claims.get("preferred_username") or "").strip()
        or user_id
    )
    return Caller(user_id=user_id, user_name=user_name, roles=roles_from_claims(claims))
