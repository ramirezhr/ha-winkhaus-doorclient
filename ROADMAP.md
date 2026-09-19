# Roadmap

Where this integration is heading, and what it deliberately will not do.

Current release: **2.6.1**

---

## Done

Everything that was on this list has been built. The integration meets all
forty-three rules of the Home Assistant quality scale or records an
exception with its reasoning; `quality_scale.yaml` has the detail.

**Structure.** Runtime data lives on the config entry, the coordinators are
classes in `coordinator.py`, and a shared base in `entity.py` handles the
identity wiring every platform used to repeat.

**Tests.** 321 of them, 97% coverage, `api.py` at 100%. The listener tests
build genuine AES-CCM frames and the handshake tests perform a real X25519
exchange against a simulated lock, so they check agreement with a
counterpart rather than with a mock. `mypy --strict` is clean.

**Async throughout.** The HTTP layer uses `aiohttp` with Home Assistant's
shared session; `websockets` was already async. Nothing runs on a worker
thread except building the SSL context, which reads from disk and has to.

## Open

Nothing planned. Ideas are welcome as issues - particularly from anyone
running an EAV4+ or a battery-powered model, since neither has been tested
by the author.

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
