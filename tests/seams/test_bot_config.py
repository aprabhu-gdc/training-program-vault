"""Pilot: single-tenant bot config parsing, validation, and auth shim mapping."""

from __future__ import annotations

import logging

import pytest
from botbuilder.integration.aiohttp import ConfigurationServiceClientCredentialFactory

import app as appmod
from teams_bot.config import Settings


def _settings(**over) -> Settings:
    base = dict(
        app_id="bot-app-id",
        app_password="bot-secret",
        app_type="MultiTenant",
        app_tenant_id="",
        port=3978,
        wiki_query_callable="rag_backend.query:query_vault",
        wiki_query_http_url="",
        ingest_admin_http_url="http://localhost:8010",
        wiki_query_timeout_seconds=45.0,
        welcome_examples=("a", "b"),
    )
    base.update(over)
    return Settings(**base)


def test_from_env_reads_single_tenant(monkeypatch):
    monkeypatch.setenv("MicrosoftAppId", "aid")
    monkeypatch.setenv("MicrosoftAppPassword", "pw")
    monkeypatch.setenv("MicrosoftAppType", "SingleTenant")
    monkeypatch.setenv("MicrosoftAppTenantId", "tid-123")

    settings = Settings.from_env()
    assert settings.app_type == "SingleTenant"
    assert settings.app_tenant_id == "tid-123"


def test_from_env_defaults_to_multitenant(monkeypatch):
    monkeypatch.delenv("MicrosoftAppType", raising=False)
    monkeypatch.delenv("MICROSOFT_APP_TYPE", raising=False)
    assert Settings.from_env().app_type == "MultiTenant"


def test_validate_warns_on_single_tenant_without_tenant_id(caplog):
    settings = _settings(app_type="SingleTenant", app_tenant_id="")
    with caplog.at_level(logging.WARNING, logger="teams_bot.config"):
        settings.validate()  # must not raise
    assert any("SingleTenant" in r.message for r in caplog.records)


def test_validate_quiet_on_single_tenant_with_tenant_id(caplog):
    settings = _settings(app_type="SingleTenant", app_tenant_id="tid-123")
    with caplog.at_level(logging.WARNING, logger="teams_bot.config"):
        settings.validate()
    assert not any("SingleTenant" in r.message for r in caplog.records)


def test_validate_requires_ingest_url():
    with pytest.raises(ValueError, match="INGEST_ADMIN_HTTP_URL"):
        _settings(ingest_admin_http_url="").validate()


def test_bot_auth_config_maps_settings_fields():
    settings = _settings(app_id="aid", app_password="pw", app_type="SingleTenant", app_tenant_id="tid")
    cfg = appmod._BotAuthConfig(settings)
    assert (cfg.APP_ID, cfg.APP_PASSWORD, cfg.APP_TYPE, cfg.APP_TENANTID) == ("aid", "pw", "SingleTenant", "tid")


def test_single_tenant_credential_factory_builds():
    cfg = appmod._BotAuthConfig(
        _settings(app_id="aid", app_password="pw", app_type="SingleTenant", app_tenant_id="tid")
    )
    # Should construct without raising (validates SingleTenant required fields).
    ConfigurationServiceClientCredentialFactory(cfg)


def test_single_tenant_credential_factory_rejects_missing_tenant():
    cfg = appmod._BotAuthConfig(
        _settings(app_id="aid", app_password="pw", app_type="SingleTenant", app_tenant_id="")
    )
    with pytest.raises(Exception):
        ConfigurationServiceClientCredentialFactory(cfg)
