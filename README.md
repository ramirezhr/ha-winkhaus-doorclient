# Winkhaus Doorclient for Home Assistant

[![GitHub Release](https://img.shields.io/github/release/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/releases)
[![License](https://img.shields.io/github/license/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/blob/main/LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Default-orange?style=for-the-badge)](https://github.com/hacs/integration)
[![Maintainer](https://img.shields.io/badge/maintainer-ramirezhr-blue?style=for-the-badge)](https://github.com/ramirezhr)

Custom integration to control and monitor **Winkhaus Door Systems** (blueMotion+ and EAV4+) via local API.

This integration communicates directly with your door controller over the local network. **No cloud connection required.**

![Logo](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/winkhaus_doorclient/logo.png)

---

## ✨ Features

### 🚀 Version 2.0+ (Hybrid Mode)

* **⚡ WebSocket Real-time Updates:** Instant status changes with <0.5s latency
* **🔄 Hybrid Communication:** WebSocket primary, HTTP fallback for maximum reliability
* **📊 Half the HTTP Requests:** ~720/day vs ~1,440/day in pure polling mode
* **🛡️ Triple Safety Net:** Protocol pings (20s) + Watchdog (75s) + HTTP polling (120s)
* **📈 Connection Statistics:** Uptime, reconnect count and link status as lock attributes

### 🔓 Core Features

* **🔓 Lock Control:** Lock (Night Mode), Unlock (Day Mode), and Open (Pull Latch)
* **🚪 Door Status:** Binary sensor showing if door is physically open or closed
* **🌗 Day/Night Mode:** Dedicated select entity to switch between modes
* **⚠️ Error State Sensor:** Reports hardware faults (motor blocked, overcurrent, low battery)
* **🔁 Clear Errors Button:** Resets a fault state on the lock without a power cycle
* **🔍 Auto-Discovery:** Automatically finds devices via Zeroconf/mDNS
* **🔐 Secure Local Connection:** HTTPS with legacy SSL compatibility
* **🛡️ Network Resilience:** Maintains last known state during temporary outages
* **🔄 Smart Reauth:** Automatic password update prompts via Repairs dashboard
* **🏠 Multi-Lock Support:** Unlimited locks, each with independent WebSocket connection

---

## 🚀 Installation

### Option 1: HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Go to **Integrations**
3. Click **+ Explore & Download Repositories**
4. Search for **"Winkhaus Doorclient"**
5. Click on the integration
6. Click **Download**
7. Restart Home Assistant

> **Note:** If the integration is not listed, you may need to add it as a custom repository:
> 1. HACS → Integrations → Top right menu (⋮) → **Custom repositories**
> 2. Add URL: `https://github.com/ramirezhr/ha-winkhaus-doorclient`
> 3. Category: **Integration**

### Option 2: Manual Installation

1. Download `custom_components/winkhaus_doorclient` from the [latest release](https://github.com/ramirezhr/ha-winkhaus-doorclient/releases)
2. Copy the folder to `config/custom_components/` directory
3. Restart Home Assistant

---

## ⚙️ Configuration

### Auto-Discovery (Recommended)

1. Ensure Winkhaus door is on the same network as Home Assistant
2. Go to **Settings** → **Devices & Services**
3. Discovered device should appear automatically
4. Click **Configure**
5. Enter password (default username: `admin`)

### Manual Setup

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for **Winkhaus Doorclient**
4. Choose setup method:
   * **Search via Network:** Auto-scan for devices
   * **Manual Input:** Enter Serial Number and IP Address

### Changing Connection Settings

If the lock's IP address, user name or password changes, there is no need to
remove and re-add the integration - which would lose entity IDs, history and
every automation referring to them.

**Settings → Devices & Services → Winkhaus Door → ⋮ → Reconfigure**

Leave the password field empty to keep the current one. The serial number
cannot be changed, since a different lock needs its own entry.

> Discovered devices usually update their IP on their own via Zeroconf. That
> only works within a subnet, so a lock in a separate IoT VLAN needs this step.

---

## 🔧 Connection Modes

The integration supports two operation modes:

### 1️⃣ Hybrid Mode (Default - Recommended)

**Best for:** Real-time control and instant status updates

* ⚡ WebSocket primary: Commands in <0.5s
* 🛡️ HTTP safety net: Polls every 120s as backup
* 📡 Protocol pings: Keeps connection alive every 20s
* 🔄 Auto-recovery: Reconnects automatically

**Performance:**
- HTTP Requests/Day: ~720 (-50%)
- Command Latency: <0.5s
- Status Latency: <1s - the lock pushes changes as they happen

### 2️⃣ Classic Polling Mode

**Best for:** Maximum compatibility

* 🔌 HTTP-only: No WebSocket
* ⚙️ Configurable: 30-300s interval (default: 60s)
* 📶 Simple: Works on any network

**Performance:**
- HTTP Requests/Day: ~1,440 (at 60s)
- Command Latency: 2-3s
- Status Latency: 0-60s - a change is only noticed at the next poll

**To switch modes:**
Settings → Devices & Services → Winkhaus Door → Configure → Select mode

---

## 🧩 Entities Created

Entity IDs are derived from the serial number, so they are identical on every
installation. The examples below use the serial `SERIAL123`.

| Entity ID | Type | Description |
|-----------|------|-------------|
| `lock.winkhaus_door_serial123_lock` | Lock | Main control (Lock/Unlock/Open) |
| `binary_sensor.winkhaus_door_serial123_door` | Binary Sensor | Door contact (Open/Closed) |
| `select.winkhaus_door_serial123_mode` | Select | Day/Night mode selector |
| `button.winkhaus_door_serial123_clear_errors` | Button | Clears a fault state on the lock |
| `sensor.winkhaus_door_serial123_lock_cnt` | Sensor | Total lock operations |
| `sensor.winkhaus_door_serial123_unlock_cnt` | Sensor | Total unlock operations |
| `sensor.winkhaus_door_serial123_error_cnt` | Sensor | Error counter |
| `sensor.winkhaus_door_serial123_error_state` | Sensor | Current fault, with `all_errors` and `error_count` attributes (diagnostic) |
| `sensor.winkhaus_door_serial123_connection_mode` | Sensor | Hybrid or Polling (diagnostic) |

**Display names** follow the name configured on the lock itself. A door named
"Front Door" shows up as *Front Door Lock*, *Front Door Mode* and so on, while
the entity IDs stay tied to the serial number.

### Lock Attributes

The lock entity carries the current status plus connection statistics:

| Attribute | Description |
|-----------|-------------|
| `state` | `open` or `closed` |
| `locked` | `true` or `false` |
| `mode` | `day` or `night` |
| `last_update_from_device` | Timestamp of the last status the lock reported |
| `websocket_connected` | Whether the WebSocket link is currently up |
| `connection_count` | WebSocket connections since Home Assistant started |
| `current_uptime` | Length of the current session as `HH:MM:SS` |
| `current_uptime_seconds` | The same value as a number, for templates and graphs |

Hours keep counting past 24, so a two-day session reads `48:30:23` rather than
switching format.

---

## 🎮 Services

### Custom Services

* `winkhaus_doorclient.set_day_mode` - Switch to day mode (unlocked/trap)
* `winkhaus_doorclient.set_night_mode` - Switch to night mode (locked)
* `winkhaus_doorclient.get_system_state` - Write the full system state to the Home Assistant log (for troubleshooting)

---

## 📝 Example Automations

### Auto-lock at Night

```yaml
automation:
  - alias: "Lock door at bedtime"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: winkhaus_doorclient.set_night_mode
        target:
          entity_id: lock.winkhaus_door_serial123_lock
```

### Door Opened Notification

```yaml
automation:
  - alias: "Door opened notification"
    trigger:
      - platform: state
        entity_id: binary_sensor.winkhaus_door_serial123_door
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          message: "Front door has been opened!"
```

### Morning Auto-unlock (Workdays Only)

```yaml
automation:
  - alias: "Unlock door in the morning"
    trigger:
      - platform: time
        at: "07:00:00"
    condition:
      - condition: state
        entity_id: binary_sensor.workday_sensor
        state: "on"
    action:
      - service: winkhaus_doorclient.set_day_mode
        target:
          entity_id: lock.winkhaus_door_serial123_lock
```

### Fault Notification

```yaml
automation:
  - alias: "Winkhaus Fault Alert"
    trigger:
      - platform: state
        entity_id: sensor.winkhaus_door_serial123_error_state
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.state != 'none' }}"
    action:
      - service: notify.mobile_app
        data:
          title: "🔐 Door Fault"
          message: "Winkhaus door reports: {{ trigger.to_state.state }}"
```

### WebSocket Disconnected for a While

```yaml
automation:
  - alias: "Winkhaus Connection Alert"
    trigger:
      - platform: state
        entity_id: lock.winkhaus_door_serial123_lock
        attribute: websocket_connected
        to: false
        for:
          minutes: 10
    action:
      - service: notify.mobile_app
        data:
          title: "🔐 Door Connection Issue"
          message: "WebSocket down for 10 minutes. HTTP fallback is still active."
```

---

## 🏠 Multi-Lock Support

The integration fully supports multiple Winkhaus locks:

### How it Works

Each lock operates independently with:
* ✅ Dedicated WebSocket connection
* ✅ Independent coordinators
* ✅ Separate HTTP sessions
* ✅ Individual offline handling

### Setup Multiple Locks

Simply add each lock as a separate integration:

1. **Settings** → **Devices & Services**
2. **+ Add Integration** → **Winkhaus Doorclient**
3. Configure each lock with its unique serial number

### Example: Lock All Doors

```yaml
automation:
  - alias: "Lock All Doors at Night"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: lock.lock
        target:
          entity_id:
            - lock.winkhaus_door_serial123_lock  # Front door
            - lock.winkhaus_door_serial456_lock  # Back door
            - lock.winkhaus_door_serial789_lock  # Garage
```

---

## 🔒 Security

### Encryption & Authentication

* **Transport:** HTTPS (TLS 1.2) for HTTP, AES-CCM for WebSocket
* **Key Exchange:** X25519 Elliptic Curve Diffie-Hellman
* **Authentication:** PBKDF2 with HMAC-SHA1 challenge-response
* **Local Only:** No cloud servers, all communication stays on your network

### Important Notes

* ✅ Connection is **encrypted** via TLS/AES-CCM
* ✅ Communication **only within local network**
* ✅ **No data** sent to external servers
* ⚠️ Uses self-signed certificates (device limitation)

### Best Practices

1. 🔐 Change default admin password
2. 🌐 Use separate IoT VLAN for locks
3. 🚫 Restrict network access to HA server only
4. 🔄 Keep integration updated

### Why Legacy SSL?

The integration uses `SECLEVEL=1` to maintain compatibility with the door controller's embedded firmware (common requirement for IoT devices).

---

## 🔧 Troubleshooting

### Connection Failed

**Symptoms:** Integration won't connect, shows errors during setup

**Solutions:**
- Verify device is reachable: `ping <door_ip>`
- Check port 443 (HTTPS) and port 80 (WebSocket) are open
- Verify username (default: `admin`) and password
- Try rebooting the door controller (power cycle)

### Entities Show "Unavailable"

**Symptoms:** All entities gray/unavailable

**Solutions:**
- Check Home Assistant logs for errors
- The integration keeps the last known state across three failed polls before raising an alert (about six minutes in Hybrid mode, three in Polling mode at the default interval)
- Connection auto-recovers when the device comes back online
- If persistent, try reloading the integration

### WebSocket Not Connecting

**Symptoms:** Logs show "WS Connection failed" or "WS Auth Failed"

**Solutions:**
1. **Switch to Polling Mode** (temporary workaround)
   - Settings → Devices → Winkhaus Door → Configure → Classic Polling
2. **Check Firewall:** Ensure port 80 is not blocked
3. **Verify Firmware:** Update door controller if available
4. **Enable Debug Logging:**
   ```yaml
   logger:
     logs:
       custom_components.winkhaus_doorclient: debug
       websockets.client: debug
   ```

### Frequent Reconnects

**Symptoms:** The `connection_count` attribute climbs steadily, `current_uptime` rarely
reaches more than a few hours

**Solutions:**
- Improve WiFi signal strength to the door controller
- Reduce network congestion and check for interference
- Look for `WS session ended after HH:MM:SS` in the log to see how long sessions last
- Occasional reconnects are normal and handled automatically; commands keep working
  through the HTTP fallback while the link is down

### Commands Not Working

**Symptoms:** Lock/unlock commands don't execute

**Solutions:**
- Verify lock is powered (24V supply)
- Check device logs for error messages
- Test manual command via Developer Tools
- Switch to Polling mode to bypass WebSocket

---

## 📋 Diagnostics

When reporting a problem, attach a diagnostics export - it saves a round of
questions:

**Settings → Devices & Services → Winkhaus Door → ⋮ → Download Diagnostics**

The file contains the connection state, session and reconnect counters, both
coordinator intervals, the protocol counters and the lock's firmware and
counters. Serial number, IP addresses, user name and password are removed
automatically, so the file is safe to attach to a public issue.

---

## 🐛 Debug Logging

Enable detailed logging:

```yaml
logger:
  default: info
  logs:
    custom_components.winkhaus_doorclient: debug
    custom_components.winkhaus_doorclient.api: debug
    websockets.client: debug
```

Then check: **Settings** → **System** → **Logs**

Look for:
- `WS Auth OK` - WebSocket connected successfully
- `WS Status Update` - Status pushes received
- `Command sent via WS to` - Commands sent via WebSocket
- `WS session ended after` - How long a session lasted before reconnecting
- `Lock rejected` - The lock refused a request, including which one
- `% sending keepalive ping` - Protocol pings active

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

---

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

## 📚 Additional Resources

- **Roadmap:** [What's planned and what isn't](ROADMAP.md)
- **Documentation:** [GitHub Wiki](https://github.com/ramirezhr/ha-winkhaus-doorclient/wiki)
- **Issues:** [GitHub Issues](https://github.com/ramirezhr/ha-winkhaus-doorclient/issues)
- **Discussions:** [GitHub Discussions](https://github.com/ramirezhr/ha-winkhaus-doorclient/discussions)
- **Community:** [Home Assistant Forum](https://community.home-assistant.io/)

---

## 🙏 Credits

- **Integration Author:** [@ramirezhr](https://github.com/ramirezhr)
- **Protocol:** Worked out by observing the device's local API, plus a good deal of trial and error
- **Hardware:** [Winkhaus blueMotion+ / EAV4+](https://www.winkhaus.de/)

---

*Disclaimer: This is a custom integration and not an official product of Winkhaus.*

**Made with ❤️ for the Home Assistant Community**
