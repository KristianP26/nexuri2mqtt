"""Poll Nexuri, publish to MQTT, let Home Assistant discover the result."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time

import paho.mqtt.client as mqtt

from .client import NexuriAuthError, NexuriClient, NexuriError
from .config import Config
from .registry import Device, devices_from_widget

log = logging.getLogger(__name__)

AVAILABILITY_ONLINE = "online"
AVAILABILITY_OFFLINE = "offline"


class Bridge:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = NexuriClient(
            config.nexuri_email, config.nexuri_password, config.nexuri_base_url
        )
        self._mqtt = self._build_mqtt()
        self._stop = threading.Event()

        self._devices: list[Device] = []
        # component_id -> topics the device actually accepted, so the probe runs
        # once rather than on every poll.
        self._accepted: dict[str, list[str]] = {}
        self._announced: set[str] = set()

    # ------------------------------------------------------------ topics

    @property
    def _availability_topic(self) -> str:
        return f"{self._config.mqtt_prefix}/bridge/availability"

    def _state_topic(self, device: Device) -> str:
        return f"{self._config.mqtt_prefix}/{device.component_id}/state"

    # -------------------------------------------------------------- mqtt

    def _build_mqtt(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="nexuri2mqtt"
        )
        if self._config.mqtt_username:
            client.username_pw_set(
                self._config.mqtt_username, self._config.mqtt_password
            )
        client.will_set(
            self._availability_topic, AVAILABILITY_OFFLINE, qos=1, retain=True
        )
        return client

    def _connect_mqtt(self) -> None:
        log.info("connecting to MQTT %s:%s", self._config.mqtt_host, self._config.mqtt_port)
        self._mqtt.connect(self._config.mqtt_host, self._config.mqtt_port, keepalive=60)
        self._mqtt.loop_start()
        self._publish_availability(AVAILABILITY_ONLINE)

    def _publish_availability(self, state: str) -> None:
        self._mqtt.publish(self._availability_topic, state, qos=1, retain=True)

    # --------------------------------------------------------- discovery

    def _device_block(self, device: Device) -> dict:
        return {
            "identifiers": [device.unique_id],
            "name": device.name,
            "manufacturer": "Nexuri",
            "model": device.type,
            "via_device": "nexuri2mqtt",
        }

    def _announce(self, device: Device, topics: list[str]) -> None:
        """Publish retained HA discovery configs for one device's entities."""
        for topic in topics:
            meta = device.meta_for(topic)
            platform = "binary_sensor" if meta.binary else "sensor"
            unique_id = f"{device.unique_id}_{topic}"

            payload: dict = {
                "name": meta.name,
                "unique_id": unique_id,
                "object_id": unique_id,
                "state_topic": self._state_topic(device),
                "availability_topic": self._availability_topic,
                "payload_available": AVAILABILITY_ONLINE,
                "payload_not_available": AVAILABILITY_OFFLINE,
                "device": self._device_block(device),
            }

            if meta.binary:
                payload["value_template"] = (
                    "{{ 'ON' if value_json.%s | int(-1) == %d else 'OFF' }}"
                    % (topic, meta.on_value)
                )
                payload["payload_on"] = "ON"
                payload["payload_off"] = "OFF"
            else:
                payload["value_template"] = "{{ value_json.%s }}" % topic
                if meta.unit:
                    payload["unit_of_measurement"] = meta.unit
                if meta.state_class:
                    payload["state_class"] = meta.state_class
                if meta.precision is not None:
                    payload["suggested_display_precision"] = meta.precision

            if meta.device_class:
                payload["device_class"] = meta.device_class
            if meta.diagnostic:
                payload["entity_category"] = "diagnostic"

            config_topic = (
                f"{self._config.discovery_prefix}/{platform}/{unique_id}/config"
            )
            self._mqtt.publish(config_topic, json.dumps(payload), qos=1, retain=True)

        self._announced.add(device.component_id)
        log.info("announced %s (%s) with %d entities", device.name, device.type, len(topics))

    # --------------------------------------------------------- discovery

    def refresh_devices(self) -> None:
        widgets = list(self._client.iter_widgets())
        devices: list[Device] = []
        for widget in widgets:
            devices.extend(
                devices_from_widget(widget, include_shared=self._config.include_shared)
            )

        known = {d.component_id for d in self._devices}
        found = {d.component_id for d in devices}
        if found - known:
            log.info("discovered %d new device(s)", len(found - known))
        if known - found:
            log.info("%d device(s) disappeared from the account", len(known - found))

        self._devices = devices
        log.info("tracking %d device(s) from %d widget(s)", len(devices), len(widgets))

    # -------------------------------------------------------------- poll

    def poll_once(self) -> None:
        for device in self._devices:
            if self._stop.is_set():
                return

            topics = self._accepted.get(device.component_id)
            try:
                if topics is None:
                    values, topics = self._client.read_probing(
                        device.widget_id, device.component_id, device.topics
                    )
                    self._accepted[device.component_id] = topics
                    if not topics:
                        log.warning(
                            "%s accepted none of its topics; skipping", device.name
                        )
                        continue
                else:
                    values = self._client.read(
                        device.widget_id, device.component_id, topics
                    )
            except NexuriAuthError:
                raise
            except NexuriError as exc:
                # One dead device must not stop the others. The boiler-room bin
                # sensor being offline is not a reason to lose the whole flat.
                log.warning("read failed for %s: %s", device.name, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("unexpected error reading %s: %s", device.name, exc)
                continue

            if not values:
                continue

            if device.component_id not in self._announced:
                self._announce(device, topics)

            scaled = {}
            for topic, raw in values.items():
                meta = device.meta_for(topic)
                value = raw * meta.multiplier if meta.multiplier != 1.0 else raw
                if meta.precision is not None and isinstance(value, float):
                    value = round(value, meta.precision)
                scaled[topic] = value

            self._mqtt.publish(
                self._state_topic(device), json.dumps(scaled), qos=0, retain=False
            )

    # -------------------------------------------------------------- main

    def run(self) -> None:
        self._install_signal_handlers()
        self._connect_mqtt()

        last_discovery = 0.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if started - last_discovery >= self._config.discovery_interval:
                    self.refresh_devices()
                    last_discovery = started
                self.poll_once()
                self._publish_availability(AVAILABILITY_ONLINE)
            except NexuriAuthError as exc:
                log.error("authentication problem: %s", exc)
                self._publish_availability(AVAILABILITY_OFFLINE)
            except Exception as exc:  # noqa: BLE001 - a bridge that dies is useless
                log.exception("poll cycle failed: %s", exc)
                self._publish_availability(AVAILABILITY_OFFLINE)

            elapsed = time.monotonic() - started
            self._stop.wait(max(1.0, self._config.poll_interval - elapsed))

        self._shutdown()

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            log.info("signal %s received, shutting down", signum)
            self._stop.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, handler)

    def _shutdown(self) -> None:
        self._publish_availability(AVAILABILITY_OFFLINE)
        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        log.info("stopped")
