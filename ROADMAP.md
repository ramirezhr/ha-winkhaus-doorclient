# Roadmap

Where this integration is heading, and what it deliberately will not do.

Current release: **2.7.0**
In development: **3.0.0** - the protocol work described below. There will be
no 2.7.1; what was staged for it is part of the 3.0.0 line.

---

## Done

Everything that was on this list has been built. The integration meets
forty-six of the forty-seven rules of the Home Assistant quality scale and
records the one exception with its reasoning; `quality_scale.yaml` has the
detail.

**Structure.** Runtime data lives on the config entry, the coordinators are
classes in `coordinator.py`, and a shared base in `entity.py` handles the
identity wiring every platform used to repeat.

**Tests.** 444 of them, 98% coverage, `api.py` and `coordinator.py` at 100%.
The listener tests
build genuine AES-CCM frames and the handshake tests perform a real X25519
exchange against a simulated lock, so they check agreement with a
counterpart rather than with a mock. `mypy --strict` is clean.

**Async throughout.** The HTTP layer uses `aiohttp` with Home Assistant's
shared session; `websockets` was already async. Nothing runs on a worker
thread except building the SSL context, which reads from disk and has to.

## Open

**3.0.0: the WebSocket becomes the primary transport.** Reads go over the
socket as well, with HTTPS kept as a genuine failsafe rather than a vestige.
Not WSS: the lock refuses a WebSocket upgrade on port 443, measured on
19 November 2026, so the socket is plain `ws://` on port 80 and the X25519
and AES-CCM layer inside it is the only protection there can be. The
protocol groundwork is measured rather than assumed: frame header,
fragmentation in both directions, counters, ordering, timing, and which
endpoints answer over the socket.

Two findings decide the shape of it. The lock answers strictly in order, so a
response can be matched to its request without an identifier - measured with
four requests open at once and answers between 94 and 1893 bytes, without a
single overtake. And it interleaves unsolicited state pushes with the
fragments of a reply, which is why reassembly keeps one buffer per packet
type since 2.7.1.

The design that follows: one request in flight at a time rather than
pipelining, which costs nothing measurable because the lock serialises its
own work anyway; a per-request timeout generous enough for `scan`, which
takes over five seconds; and a reconnect when that timeout fires, since a
late answer would otherwise be attributed to the next request and nothing but
a fresh handshake can resynchronise the order.

Beyond that, ideas are welcome as issues - particularly from anyone running an
EAV4+ or a battery-powered model, since neither has been tested by the
author.

## Not planned

Recorded here so the reasoning is not lost.

**Battery sensor.** `getSystemState` returns a `battery` field, but its value
is constantly `0` and there is no documentation for the protocol - so it is
unclear what the field represents, what unit it uses, or whether it is
populated on this hardware at all. A sensor would display a number nobody
can interpret. Left out until there is more insight. `batterylow` stays in
the fault vocabulary, where it costs nothing.

**Connection quality rating.** Was planned as a score from success rate, ping
latency and reconnect ratio. The original reasoning was that two of those three
cannot be measured. One half of it no longer holds: the lock still does not
echo request identifiers, but it answers strictly in order, so a response can
be matched to its request after all, measured over 24 answers with up to four
requests outstanding. A success
rate therefore becomes measurable once 3.0.0 carries that correlation. Ping
latency remains out of reach, since protocol pings are handled inside the
`websockets` library where their round-trip time is not visible. Still not
planned for now, but worth revisiting rather than settled.

**Raw TCP instead of the `websockets` library.** The lock's WebSocket
implementation does not follow the specification closely: frame headers
occasionally arrive in a shape a strict parser rejects, which closes the
connection with a protocol error. Handling that would mean framing the
stream by hand instead of relying on a maintained library - trading a few
log entries for a new class of bugs to debug alone. Reconnect and HTTP
fallback cover the situation; sessions of more than 48 hours are normal.

**Configurable timings.** Watchdog thresholds, ping interval, HTTP fallback
interval and reconnect backoff are tuned against each other - the 600-second
fetch only makes sense because the watchdog triggers at 300, and would become
the traffic it is meant to back up if it were set any lower. Exposing them
individually invites
silent misconfiguration. The mode switch between Push and Polling covers
the one decision that genuinely depends on the user's network.

**Transitional lock states.** A `locking` or `unlocking` state between the
acknowledgement and the finished movement. Measured on the blueMotion+, that
window is half a second: the lock confirms a command as soon as it has handed
it to the motor controller, and the two state pushes follow about 500 ms
later. A transitional state would therefore be visible for half a second in
the normal case - and would need a timeout for the case where the second push
never arrives, because a lock stuck on "locking" forever is a worse misreading
than the brief moment of showing the old state. The push pair already tells
the truth faster than anyone can look.

**Diagnostic sensor entities.** Uptime, reconnect count and link status are
available as lock attributes, and the diagnostics download covers the rest.
Dedicated entities would add clutter for everyone to serve a handful of
dashboards. A template sensor does the job for anyone who wants long-term
statistics.
