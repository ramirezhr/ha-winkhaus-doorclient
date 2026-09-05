# Roadmap

Where this integration is heading, and what it deliberately will not do.

Current release: **2.5.0**

---

## Next up

### Reconsider the lock entity name

"Front Door Lock" reads slightly redundant in German ("Haustür Schloss").
Home Assistant's convention for the primary entity of a device is to name it
after the device itself. Worth a decision, not urgent.

---

## Towards 3.0.0 - Bronze quality scale

The [quality scale](quality_scale.yaml) tracks every rule individually. This
integration is not part of Home Assistant core, so nothing here is verified -
the goal is the engineering discipline behind the rules, not a badge.

### Structural work

**`runtime-data`** - move from `hass.data[DOMAIN][entry.entry_id]` to
`entry.runtime_data` with a typed dataclass. Mechanical, touches every
platform.

**`common-modules`** - split the coordinators out of `__init__.py` into
`coordinator.py`, and introduce a shared base class in `entity.py` so
`device_info` is not wired up in every platform separately.

### Test coverage

Nothing is tested today. Every change so far was verified by running it on
real hardware for days, which works for one maintainer and one lock but does
not scale.

Planned in three tiers:

1. **Pure logic, no Home Assistant required.** Entity ID building, uptime
   formatting, timestamp conversion, command resolution, state parsing,
   fault handling, push merging, counter monotonicity, fragment reassembly.
   This is where the real bugs have been, and it runs in milliseconds.
2. **Config flow.** Required at 100% for Bronze. Every step plus the error
   paths, with a mocked client.
3. **Coordinator and setup.** Specifically the failures already fixed once:
   `ConfigEntryNotReady` being swallowed, the repair issue lifecycle, and
   keeping cached data instead of dropping entities.

The crypto handshake and the wire protocol stay untested. They need real
hardware; a mock would only verify the mock.

### Cheap wins along the way

Picked up while the structural work happens, because each has value on its
own: `PARALLEL_UPDATES`, service actions raising proper exceptions, logging
once on unavailability instead of on every attempt, moving icons into
`icons.json`, translated exceptions, and the missing documentation sections.

### The architectural change

The HTTP layer uses `requests` through the executor. Core-grade integrations
need an async library, so this eventually becomes `aiohttp` - including the
legacy SSL context the door controller requires.

This is the riskiest change in the project: it touches the crypto path, the
protocol handling and both coordinators. It happens **after** the tests, not
before.

---

## Not planned

Recorded here so the reasoning is not lost.

**Battery sensor.** `getSystemState` returns a `battery` field, but its value
is constantly `0` and there is no documentation for the protocol - so it is
unclear what the field represents, what unit it uses, or whether it is
populated on this hardware at all. A sensor would display a number nobody
can interpret. Left out until there is more insight. `batterylow` stays in
the fault vocabulary, where it costs nothing.

**Connection quality rating.** Was planned as a score from success rate, ping
latency and reconnect ratio. Two of those three cannot be measured: the lock
does not echo request identifiers, so a response cannot be matched to its
request, and protocol pings are handled inside the `websockets` library where
their round-trip time is not visible. What remains would be a coloured badge
for the reconnect counter with invented thresholds.

**Raw TCP instead of the `websockets` library.** The lock's WebSocket
implementation does not follow the specification closely: frame headers
occasionally arrive in a shape a strict parser rejects, which closes the
connection with a protocol error. Handling that would mean framing the
stream by hand instead of relying on a maintained library - trading a few
log entries for a new class of bugs to debug alone. Reconnect and HTTP
fallback cover the situation; sessions of more than 48 hours are normal.

**Configurable timings.** Watchdog thresholds, ping interval, HTTP fallback
interval and reconnect backoff are tuned against each other - 75 seconds only
makes sense because pings run every 20. Exposing them individually invites
silent misconfiguration. The mode switch between Hybrid and Polling covers
the one decision that genuinely depends on the user's network.

**Diagnostic sensor entities.** Uptime, reconnect count and link status are
available as lock attributes, and the diagnostics download covers the rest.
Dedicated entities would add clutter for everyone to serve a handful of
dashboards. A template sensor does the job for anyone who wants long-term
statistics.
