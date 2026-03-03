"""Shared test fixtures.

Provides mock adapters for unit tests so the core domain can be tested
without any external dependencies (Marker, Azure, Ollama, etc.).
"""

from __future__ import annotations

import os

import pytest

os.environ["AT_ENV"] = "test"


@pytest.fixture
def settings():
    from app.config.settings import AppSettings

    return AppSettings(env="test")
