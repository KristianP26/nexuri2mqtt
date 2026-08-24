# nexuri2mqtt

Bridge between the **Nexuri** smart-flat system (`app.nexuri.cz`) and **MQTT**,
with Home Assistant auto-discovery.

Point it at your Nexuri account and it finds the devices itself. No IDs to look
up, no YAML per sensor. A device added to your flat shows up on the next
discovery pass.

> **Unofficial.** Nexuri publishes no API documentation. This client was built by
> observing the official web app's own network traffic. It can break whenever the
> vendor changes something. Not affiliated with or endorsed by Nexuri.

## Why this exists

A Nexuri flat is already fully metered. Electricity, hot and cold water, indoor
temperature, humidity and CO2, boiler and ventilation state: all of it is measured by
hardware you pay for through the rent, and all of it is visible in the vendor's web
dashboard.

What the app does not do is automate. It shows values and lets you change a setpoint
by hand. There are no automations, no schedules, no conditions, and no integration
with anything else. If the flat is going to react to its own measurements, you are the
one reacting, and you need a phone in your hand to do it.

History exists, but only in coarse form. The app reports consumption per month, which
answers a billing question and little else. Nothing keeps the fine detail, so you
cannot ask what the CO2 did at 3 a.m. last Tuesday, or whether raising the ventilation
setpoint changed anything that week.

So the bridge does the plumbing: log in, discover what the account can see, poll on a
sane interval, publish to MQTT with Home Assistant discovery. After that the data
behaves like any other sensor. It is recorded at full resolution, energy meters land in
the Energy dashboard, and CO2 or boiler state can drive automations, including ones
that act on hardware Nexuri knows nothing about.

Mixing sources is the real payoff. In my flat three BLE thermometers report per-room
temperature to the same Home Assistant, while Nexuri contributes the environmental
sensor, CO2, and the boiler and ventilation state. Separately each half is a partial
picture. On one broker they are one system, so a decision can use all of it: ventilate
on CO2 while watching what that does to the coldest room, instead of trusting one
sensor in the hallway.

Discovery keys off device types rather than the IDs in one particular apartment, so a
neighbour with the same hardware runs the same container with their own credentials
and nothing else to configure.

## Status

**Read-only.** This release polls sensors and publishes them, and never sends a
command. Writing to a building-management system moves real hardware: blinds,
boilers, ventilation. Control waits until the read path has proven itself.

## How discovery works

On start, and again every `DISCOVERY_INTERVAL`:

1. `PUT /login` with your portal credentials -> auth token
2. `PUT /component_widget/get_list` -> every widget your account can see,
   paginated, including `customer_alias` (the human name), `type` (device class)
   and `hardware_components[].component_id`
3. Topics per device come from the widget's `precepts` where the API provides
   them, otherwise from a fallback table keyed by device `type`
4. `PUT /measured_data/get_last` per device -> current values
5. MQTT discovery config published once per entity, then state updates on a loop

Step 3 keys off `type`, not off your flat's IDs, so a different flat with the same
kinds of devices needs no configuration.

### Self-healing topic probing

If a device rejects a topic, the API answers `state: "error"`, `rc: -1` and names
the offending topic in `rsn`, all under **HTTP 200**. The bridge drops that topic,
retries, and caches the working topic set per component, so the probe runs once
rather than every poll.

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
| `DOOR`, `TENANT_DOOR`, `MAIN_DOOR`, `SPECIAL_DOOR`, `GARAGE` | Doors | lock released/held |

Anything else is still discovered. Its topics are read if the API advertises them,
and published as raw diagnostic entities.

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
| `NEXURI_EMAIL` | (none) | Portal login, required |
| `NEXURI_PASSWORD` | (none) | Portal password, required |
| `NEXURI_BASE_URL` | `https://backend.nexuri.cz` | API base |
| `MQTT_HOST` | (none) | Broker host, required |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | (none) | Broker auth, if any |
| `MQTT_PREFIX` | `nexuri` | State topic prefix |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | HA discovery prefix |
| `POLL_INTERVAL` | `60` | Seconds between polls |
| `DISCOVERY_INTERVAL` | `3600` | Seconds between device-list refreshes |
| `INCLUDE_SHARED` | `false` | Also publish shared building devices (see below) |
| `LOG_LEVEL` | `INFO` | Python log level |

### Shared building devices

A tenant account usually sees communal hardware too: main entrance, garage, lifts,
bike racks. Those are **not your flat**, so they are excluded by default. Set
`INCLUDE_SHARED=true` to publish them as read-only sensors.

## Polling

Every read reaches the hardware controller in your flat, so the bridge probes only
what the API advertised, once, then caches it. It never brute-forces topic names.
At the default 60 seconds it is lighter traffic than the vendor's own dashboard,
which polls every few seconds while the tab is open.

## MQTT topics

```
nexuri/<component_id>/state        JSON, all topics for that device
nexuri/bridge/availability         online | offline (LWT)
homeassistant/<platform>/<uid>/config    retained discovery
```

Entities go `unavailable` when the bridge loses the API, instead of showing a stale
reading forever.

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
