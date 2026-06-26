# Winkhaus Doorclient for Home Assistant

[![GitHub Release](https://img.shields.io/github/release/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/releases)
[![License](https://img.shields.io/github/license/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/blob/main/LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Default-orange?style=for-the-badge)](https://github.com/hacs/integration)
[![Maintainer](https://img.shields.io/badge/maintainer-ramirezhr-blue?style=for-the-badge)](https://github.com/ramirezhr)

Custom integration to control and monitor **Winkhaus Door Systems** (blueMotion+) via local API.

This integration communicates directly with your door controller over the local network. **No cloud connection required.**

![Logo](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/winkhaus_doorclient/logo.png)

---

## ✨ Features

### 🚀 Version 2.0+ (Hybrid Mode)

* **⚡ WebSocket Real-time Updates:** Instant status changes with <0.5s latency
* **🔄 Hybrid Communication:** WebSocket primary, HTTP fallback for maximum reliability
* **📊 80% Fewer API Calls:** ~290/day vs ~1,440/day in pure polling mode
* **🛡️ Triple Safety Net:** Protocol pings (20s) + Watchdog (75s) + HTTP polling (120s)
* **📈 Connection Diagnostics:** Built-in statistics, quality metrics, and diagnostics panel

### 🔓 Core Features

* **🔓 Lock Control:** Lock (Night Mode), Unlock (Day Mode), and Open (Pull Latch)
* **🚪 Door Status:** Binary sensor showing if door is physically open or closed
* **🌗 Day/Night Mode:** Dedicated select entity to switch between modes
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
- API Calls/Day: ~290 (-80%)
- Command Latency: <0.5s
- Status Latency: <1s (instant push)

### 2️⃣ Classic Polling Mode

**Best for:** Maximum compatibility

* 🔌 HTTP-only: No WebSocket
* ⚙️ Configurable: 30-300s interval (default: 60s)
* 📶 Simple: Works on any network

**Performance:**
- API Calls/Day: ~1,440 (at 60s)
- Command Latency: 2-3s
- Status Latency: 0-60s

**To switch modes:**
Settings → Devices & Services → Winkhaus Door → Configure → Select mode

---

## 🧩 Entities Created

After setup, the following entities are available (example for serial `SERIAL123`):

| Entity ID | Type | Description |
|-----------|------|-------------|
| `lock.winkhaus_door_serial123_lock` | Lock | Main control (Lock/Unlock/Open) |
| `binary_sensor.winkhaus_door_serial123_door` | Binary Sensor | Door contact (Open/Closed) |
| `select.winkhaus_door_serial123_mode` | Select | Day/Night mode selector |
| `sensor.winkhaus_door_serial123_lock_count` | Sensor | Total lock operations |
| `sensor.winkhaus_door_serial123_unlock_count` | Sensor | Total unlock operations |
| `sensor.winkhaus_door_serial123_error_count` | Sensor | Error counter |

---

## 🎮 Services

### Custom Services

* `winkhaus_doorclient.set_day_mode` - Switch to day mode (unlocked/trap)
* `winkhaus_doorclient.set_night_mode` - Switch to night mode (locked)

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

### Connection Quality Alert

```yaml
automation:
  - alias: "Winkhaus Connection Alert"
    trigger:
      - platform: state
        entity_id: sensor.winkhaus_door_serial123_connection_quality
        to: 'Poor'
        for:
          minutes: 5
    action:
      - service: notify.mobile_app
        data:
          title: "🔐 Door Connection Issue"
          message: "Winkhaus door connection quality degraded. Check device and network."
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
- Integration keeps last known state for 3 minutes during outages
- Connection auto-recovers when device comes back online
- If persistent, try reloading integration

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

### Poor Connection Quality

**Symptoms:** Sensor shows "Fair" or "Poor" quality

**Solutions:**
- Improve WiFi signal strength to door controller
- Reduce network congestion
- Check for interference from other devices
- Review diagnostics: Settings → Devices → Download Diagnostics

### Commands Not Working

**Symptoms:** Lock/unlock commands don't execute

**Solutions:**
- Verify lock is powered (24V supply)
- Check device logs for error messages
- Test manual command via Developer Tools
- Switch to Polling mode to bypass WebSocket

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
- `Befehl via WS` - Commands sent via WebSocket
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

- **Documentation:** [GitHub Wiki](https://github.com/ramirezhr/ha-winkhaus-doorclient/wiki)
- **Issues:** [GitHub Issues](https://github.com/ramirezhr/ha-winkhaus-doorclient/issues)
- **Discussions:** [GitHub Discussions](https://github.com/ramirezhr/ha-winkhaus-doorclient/discussions)
- **Community:** [Home Assistant Forum](https://community.home-assistant.io/)

---

## 🙏 Credits

- **Integration Author:** [@ramirezhr](https://github.com/ramirezhr)
- **Hardware:** [Winkhaus blueMotion+](https://www.winkhaus.de/)

---

*Disclaimer: This is a custom integration and not an official product of Winkhaus.*

**Made with ❤️ for the Home Assistant Community**
