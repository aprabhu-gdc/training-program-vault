"""Caller identity models shared across apps and services."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallerIdentity:
    user_id: str | None
    user_name: str | None
    tenant_id: str | None
    client_app: str | None
    channel_id: str | None = None
    conversation_id: str | None = None
    locale: str | None = None
