"""Topic metadata and per-device-type topic maps.

The API describes some of this itself, in each widget's ``precepts``: units and
multipliers appear there for sensor-ish widget types. It is not complete —
ventilation and heating widgets ship empty precepts — so this module supplies
the gaps, keyed by widget ``type`` rather than by any particular flat's IDs.
That is what lets a different flat work without configuration.

Where the API and this table disagree, see ``OVERRIDE_MULTIPLIERS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TopicMeta:
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    multiplier: float = 1.0
    precision: int | None = None
    diagnostic: bool = False
    binary: bool = False
    # For binary entities: the raw value that means "on"/"open"/"active".
    on_value: int = 1


# Topic -> how it should surface in Home Assistant.
TOPICS: dict[str, TopicMeta] = {
    # Environment sensor
    "htc_tmp": TopicMeta("Temperature", "°C", "temperature", multiplier=0.001, precision=2),
    "htc_hum": TopicMeta("Humidity", "%", "humidity", multiplier=0.001, precision=1),
    "htc_co2": TopicMeta("CO2", "ppm", "carbon_dioxide"),
    # Electricity meter
    "p1_ene": TopicMeta("Energy", "kWh", "energy", "total_increasing", 0.001, 3),
    "p1_pwr": TopicMeta("Power", "W", "power"),
    "p1_cur": TopicMeta("Current", "A", "current", multiplier=0.001, precision=2),
    # Water meter
    "wm_tfl": TopicMeta("Water total", "m³", "water", "total_increasing", 0.001, 3),
    "wm_rat": TopicMeta("Water flow", "m³/h", None),
    "wm_tmp": TopicMeta("Water temperature", "°C", "temperature", multiplier=0.001, precision=2),
    # Boiler. Note the deliberately different multipliers.
    "bo_tmpc": TopicMeta("Temperature", "°C", "temperature", multiplier=0.001, precision=2),
    "bo_tmpt": TopicMeta("Temperature top", "°C", "temperature", multiplier=0.001, precision=2),
    "bo_tmpb": TopicMeta("Temperature bottom", "°C", "temperature", multiplier=0.001, precision=2),
    "bo_stp": TopicMeta("Target temperature", "°C", "temperature", multiplier=0.01, precision=1),
    "bo_mod": TopicMeta("Mode", None, None, None, diagnostic=True),
    "bo_hst": TopicMeta("Heating", None, "heat", None, diagnostic=True, binary=True),
    # Air preheater
    "hr_stp": TopicMeta("Target temperature", "°C", "temperature", multiplier=0.001, precision=1),
    "hr_ena": TopicMeta("Enabled", None, "running", None, diagnostic=True, binary=True),
    "hr_hst": TopicMeta("Heating", None, "heat", None, diagnostic=True, binary=True),
    # Heat recovery ventilation
    "av_stp": TopicMeta("Airflow setpoint", "m³/h", None),
    "av_rat": TopicMeta("Airflow", "m³/h", None),
    # Precepts give the unit but no multiplier, and the raw value is ~1157 for
    # what the dampers report as roughly 12 %. Inferred, not confirmed.
    "av_blp": TopicMeta("Blade position", "%", None, multiplier=0.01, precision=1),
    # Raw on purpose: only the "running" value (0) is confirmed, and guessing
    # the polarity of a switch that silently stops ventilation is not worth it.
    "av_mod": TopicMeta("Mode (raw)", None, None, None, diagnostic=True),
    # Blinds. Timing-driven hardware: these report movement, never position.
    "ag_ost": TopicMeta("Opening", None, None, None, diagnostic=True, binary=True),
    "ag_cst": TopicMeta("Closing", None, None, None, diagnostic=True, binary=True),
    # Switch
    "sw_sst": TopicMeta("State", None, "power", None, binary=True),
    # Doors
    "dr_dst": TopicMeta("Door", None, "door", None, binary=True),
}

# The API advertises multiplication 0.001 for p1_pwr with unit "W", which turns a
# real ~200 W draw into 0.2 W. The vendor's own dashboard shows "0 W" as a
# result. Treat the raw value as watts and ignore what precepts claim.
OVERRIDE_MULTIPLIERS: dict[str, float] = {"p1_pwr": 1.0}

# Fallback topic sets for widget types whose precepts are empty.
TYPE_TOPICS: dict[str, list[str]] = {
    "HTC_SENSOR_SENSIT": ["htc_tmp", "htc_hum", "htc_co2"],
    "HT_SENSOR_SENSIT": ["htc_tmp", "htc_hum"],
    "PRO_1_MOD": ["p1_ene", "p1_pwr", "p1_cur"],
    "WATER_METER_SHOMETERS": ["wm_tfl", "wm_rat", "wm_tmp"],
    "BOILER": ["bo_tmpc", "bo_tmpt", "bo_tmpb", "bo_stp", "bo_mod", "bo_hst"],
    "RECUPERATION_HEAT": ["hr_stp", "hr_ena", "hr_hst"],
    "AIR_VALVE_TROX": ["av_stp", "av_rat", "av_blp", "av_mod"],
    "BLINDS": ["ag_ost", "ag_cst"],
    "SWITCH": ["sw_sst"],
    "DOOR": ["dr_dst"],
    "TENANT_DOOR": ["dr_dst"],
    "MAIN_DOOR": ["dr_dst"],
    "SPECIAL_DOOR": ["dr_dst"],
    "GARAGE": ["dr_dst"],
}

# Communal hardware a tenant account can see but does not own.
SHARED_TYPES = {
    "DOOR",
    "MAIN_DOOR",
    "SPECIAL_DOOR",
    "GARAGE",
    "BICYCLE_LOCK",
    "RFID_OFFLINE",
}


@dataclass
class Device:
    """One pollable component, flattened out of a widget."""

    widget_id: str
    component_id: str
    name: str
    type: str
    description: str | None
    topics: list[str]
    units: dict[str, str] = field(default_factory=dict)
    multipliers: dict[str, float] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        return f"nexuri_{self.component_id}"

    def meta_for(self, topic: str) -> TopicMeta:
        """Merge the curated table with whatever the API said about this topic."""
        base = TOPICS.get(topic)
        if base is None:
            # Unknown topic: still publish it, but as a raw diagnostic value.
            base = TopicMeta(topic, self.units.get(topic), None, None, diagnostic=True)

        multiplier = base.multiplier
        if topic in self.multipliers:
            multiplier = self.multipliers[topic]
        if topic in OVERRIDE_MULTIPLIERS:
            multiplier = OVERRIDE_MULTIPLIERS[topic]

        unit = base.unit or self.units.get(topic)
        if multiplier == base.multiplier and unit == base.unit:
            return base
        return TopicMeta(
            base.name,
            unit,
            base.device_class,
            base.state_class,
            multiplier,
            base.precision,
            base.diagnostic,
            base.binary,
            base.on_value,
        )


def _precept_topics(precepts: dict) -> tuple[list[str], dict[str, str], dict[str, float]]:
    """Pull topic names, units and multipliers out of a widget's precepts."""
    topics: list[str] = []
    units: dict[str, str] = {}
    multipliers: dict[str, float] = {}

    for key, value in (precepts or {}).items():
        for prefix, sink in (("unit_", units), ("multiplication_", multipliers)):
            if key.startswith(prefix):
                topic = key[len(prefix) :]
                if topic not in topics:
                    topics.append(topic)
                if value is not None:
                    sink[topic] = value
                break

    return topics, units, multipliers


def devices_from_widget(widget: dict, include_shared: bool = False) -> list[Device]:
    """Flatten one widget into the devices worth polling.

    A widget can carry several hardware components (ventilation has separate
    supply and exhaust units, for instance). Each becomes its own device.
    """
    widget_type = widget.get("type") or "UNKNOWN"
    if widget_type in SHARED_TYPES and not include_shared:
        return []

    precept_topics, units, multipliers = _precept_topics(widget.get("precepts") or {})
    # Merge, do not choose. Precepts are frequently partial: the air preheater
    # advertises a unit for hr_stp and says nothing about hr_ena or hr_hst, so
    # trusting precepts alone would silently drop two thirds of the device.
    fallback = TYPE_TOPICS.get(widget_type, [])
    topics = list(dict.fromkeys([*fallback, *precept_topics]))
    if not topics:
        return []

    name = widget.get("customer_alias") or widget.get("admin_description") or widget_type
    widget_id = widget.get("id")
    if not widget_id:
        return []

    components = widget.get("hardware_components") or []
    devices = []
    for index, component in enumerate(components):
        component_id = component.get("component_id")
        if not component_id:
            continue
        # Disambiguate the second and later components of a multi-part widget.
        suffix = "" if index == 0 else f" {index + 1}"
        devices.append(
            Device(
                widget_id=widget_id,
                component_id=component_id,
                name=f"{name}{suffix}",
                type=widget_type,
                description=widget.get("customer_description"),
                topics=list(topics),
                units=units,
                multipliers=multipliers,
            )
        )
    return devices
