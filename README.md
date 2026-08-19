# nexuri2mqtt

Bridge between the **Nexuri** smart-flat system (`app.nexuri.cz`) and **MQTT**,
with Home Assistant auto-discovery.

Point it at your Nexuri account and it finds your devices by itself. No device
IDs to look up, no YAML per sensor. New device added to your flat? It shows up
on the next discovery pass.

> **Unofficial.** Nexuri publishes no API documentation. This client was built by
> observing the official web app's own network traffic. It can break whenever the
> vendor changes something. Not affiliated with or endorsed by Nexuri.

## Status

**Read-only.** This release polls sensors and publishes them. It never sends a
command. Writing to a building-management system moves real hardware — blinds,
boilers, ventilation — so control is deliberately deferred until the read path
has proven itself.

## How discovery works

Most integrations for undocumented APIs make you paste device IDs out of your
browser's DevTools. This one doesn't:

1. `PUT /login` with your portal credentials -> auth token
2. `PUT /component_widget/get_list` -> every widget your account can see,
   paginated, including `customer_alias` (the human name), `type` (device class)
   and `hardware_components[].component_id`
3. Topics per device come from the widget's `precepts` where the API provides
   them, otherwise from a fallback table keyed by device `type`
4. `PUT /measured_data/get_last` per device -> current values
5. MQTT discovery config published once per entity, then state updates on a loop

Because step 3 keys off `type` and not off your flat's IDs, a different flat with
the same kinds of devices works with no configuration at all.

### Self-healing topic probing

If a device rejects a topic, the API answers `state: "error"`, `rc: -1` and puts
the offending topic name in `rsn` — with **HTTP 200**. The bridge uses that: it
drops the topic named in `rsn`, retries, and caches the working topic set per
component so the probe runs once, not every poll.

## Supported device types

Discovered automatically. Types with a curated topic map:

| `type` | Device | Entities |
|---|---|---|
| `HTC_SENSOR_SENSIT` | Environment sensor | temperature, humidity, CO2 |
| `HT_SENSOR_SENSIT` | Environment sensor | temperature, humidity |
| `PRO_1_MOD` | Electricity meter | energy, power, current |
| `WATER_METER_SHOMETERS` | Water meter | total, flow rate, temperature |
| `BOILER` | Hot water boiler | current/top/bottom temp, setpoint, mode, heating |
| `RECUPERATION_HEAT` | Air preheater | target temp, enabled, heating |
| `AIR_VALVE_TROX` | Heat recovery ventilation | airflow setpoint, actual rate, on/off |
| `BLINDS` | Blinds | open/close state |
| `SWITCH` | Switch / direct heater | on/off |
| `DOOR`, `TENANT_DOOR`, `MAIN_DOOR`, `SPECIAL_DOOR`, `GARAGE` | Doors | open/closed |

Anything else is still discovered — its topics are read if the API advertises
them, and published as raw diagnostic entities.

## Install

```bash
cp .env.example .env
$EDITOR .env          # portal credentials + MQTT broker
docker compose up -d
docker compose logs -f
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NEXURI_EMAIL` | — | Portal login (required) |
| `NEXURI_PASSWORD` | — | Portal password (required) |
| `NEXURI_BASE_URL` | `https://backend.nexuri.cz` | API base |
| `MQTT_HOST` | — | Broker host (required) |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | — | Broker auth, if any |
| `MQTT_PREFIX` | `nexuri` | State topic prefix |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | HA discovery prefix |
| `POLL_INTERVAL` | `60` | Seconds between polls |
| `DISCOVERY_INTERVAL` | `3600` | Seconds between device-list refreshes |
| `INCLUDE_SHARED` | `false` | Also publish shared building devices (see below) |
| `LOG_LEVEL` | `INFO` | Python log level |

### Shared building devices

A tenant account usually sees communal hardware too — main entrance, garage,
lifts, bike racks. Those are **not your flat**. They are excluded by default;
set `INCLUDE_SHARED=true` if you want them as read-only sensors.

## Polling and being a good citizen

The vendor's own web dashboard polls every few seconds. The default here is 60
seconds, so the bridge is considerably lighter than simply leaving the official
app open in a tab.

Each read reaches the hardware controller in your flat, so the bridge never
brute-forces topic names — it probes only what the API advertised, once, then
caches.

## MQTT topics

```
nexuri/<component_id>/state        JSON, all topics for that device
nexuri/bridge/availability         online | offline (LWT)
homeassistant/<platform>/<uid>/config    retained discovery
```

Entities go `unavailable` when the bridge loses the API, rather than showing a
stale reading forever. That distinction matters if you automate on this data.

## Known vendor quirks

- **`p1_pwr` multiplier is wrong.** The API advertises `multiplication: 0.001`
  with unit `W`, which turns a real ~200 W draw into `0.2 W`. The official
  dashboard consequently displays `0 W`. This bridge overrides it to raw watts.
- **Mixed multipliers on one device.** The boiler reports `bo_tmpc` at `0.001`
  and `bo_stp` at `0.01`. Multipliers are per topic, never per device.
- **"Automatika" on ventilation is not a mode.** Despite the label, it switches
  the whole unit off. Airflow is regulated through the setpoint instead.
- **Errors arrive with HTTP 200.** Always check `state` / `ok` in the body.

## License

MIT
