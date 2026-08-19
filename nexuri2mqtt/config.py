"""Configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer, got {raw!r}")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required (see .env.example)")
    return value


@dataclass(frozen=True)
class Config:
    nexuri_email: str
    nexuri_password: str
    nexuri_base_url: str

    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_prefix: str
    discovery_prefix: str

    poll_interval: int
    discovery_interval: int
    include_shared: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        poll = _int("POLL_INTERVAL", 60)
        if poll < 15:
            # Every read reaches the hardware controller in the flat.
            raise SystemExit("POLL_INTERVAL below 15s is unreasonable; refusing")

        return cls(
            nexuri_email=_required("NEXURI_EMAIL"),
            nexuri_password=_required("NEXURI_PASSWORD"),
            nexuri_base_url=os.getenv("NEXURI_BASE_URL", "https://backend.nexuri.cz"),
            mqtt_host=_required("MQTT_HOST"),
            mqtt_port=_int("MQTT_PORT", 1883),
            mqtt_username=os.getenv("MQTT_USERNAME") or None,
            mqtt_password=os.getenv("MQTT_PASSWORD") or None,
            mqtt_prefix=os.getenv("MQTT_PREFIX", "nexuri").strip("/"),
            discovery_prefix=os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant").strip("/"),
            poll_interval=poll,
            discovery_interval=_int("DISCOVERY_INTERVAL", 3600),
            include_shared=_bool("INCLUDE_SHARED", False),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
