# Winkhaus Doorclient for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/releases)
[![License](https://img.shields.io/github/license/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/blob/main/LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Default-orange?style=for-the-badge)](https://github.com/hacs/integration)
[![Maintainer](https://img.shields.io/badge/maintainer-ramirezhr-blue?style=for-the-badge)](https://github.com/ramirezhr)

Custom integration to control and monitor **Winkhaus Door Systems** (blueMotion+) via local API (HTTPS).

This integration communicates directly with your door controller over the local network. **No cloud connection required.**

![Logo](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/winkhaus_doorclient/logo.png)

## ✨ Features

* **🔓 Lock Control:** Lock (Night Mode), Unlock (Day Mode), and Open (Pull Latch) the door.
* **🚪 Door Status:** Binary sensor to see if the door is physically open or closed.
* **🌗 Day/Night Mode:** Dedicated `select` entity to switch between Day (Trap) and Night (Locked) modes.
* **🔍 Auto-Discovery:** Automatically finds your Winkhaus door in the network (Zeroconf/mDNS).
* **🔐 Secure Local Connection:** Uses HTTPS with handled legacy SSL compatibility.
* **🛡️ Network Resilience:** Maintains last known state during temporary network issues (router restarts, WiFi hiccups). Automatically recovers when connection is restored.
* **🔄 Smart Reauth:** If the door password changes, you'll be prompted to update it via the Repairs dashboard - no need to reconfigure the entire integration.

## 🚀 Installation

### Option 1: HACS (Recommended)

1.  Open **HACS** in Home Assistant.
2.  Go to "Integrations" > Top right menu (⋮) > **Custom repositories**.
3.  Add the URL of this repository:
    `https://github.com/ramirezhr/ha-winkhaus-doorclient`
4.  Select category **Integration**.
5.  Click **Add** and search for **Winkhaus Doorclient**.
6.  Click **Download**.
7.  Restart Home Assistant.

### Option 2: Manual Installation

1.  Download the `custom_components/winkhaus_doorclient` folder from the latest release.
2.  Copy the folder into your Home Assistant `config/custom_components/` directory.
3.  Restart Home Assistant.

## ⚙️ Configuration

### Auto-Discovery (Easiest Way)
1.  Make sure your Winkhaus door is connected to the same network as Home Assistant.
2.  Go to **Settings** > **Devices & Services**.
3.  You should see a discovered **Winkhaus Doorclient** device.
4.  Click **Configure**.
5.  Enter the password for your door user (default username: `admin`).

### Add Integration Manually
1.  Go to **Settings** > **Devices & Services**.
2.  Click **+ Add Integration**.
3.  Search for **Winkhaus Doorclient**.
4.  Select one of the options:
    * **Search via Network:** Scans for devices on your local network.
    * **Manual Input:** Lets you enter Serial Number and IP Address manually.

## 🧩 Entities & Services

After setup, the following entities will be available (example for serial `123456`):

| Entity ID | Type | Description |
| :--- | :--- | :--- |
| `lock.winkhaus_door_123456_lock` | Lock | Main control (Lock/Unlock/Open). |
| `binary_sensor.winkhaus_door_123456_door` | Binary Sensor | Door contact (Open/Closed). |
| `select.winkhaus_door_123456_mode` | Select | Switch between `day` and `night` mode. |

## Configuration

### Polling Interval
You can customize the polling interval to define how often the integration updates the door status from the physical device.

- **Setting:** Adjustable via a slider in the integration options.
- **Range:** 30 to 300 seconds.
- **Default:** 60 seconds.

To change the interval:
1. Go to **Settings** > **Devices & Services**.
2. Find the **Winkhaus Door** integration.
3. Click on **Configure**.
4. Adjust the slider to your desired value and click **Submit**.

### Services
You can use these services in your automations:

* `winkhaus_doorclient.set_day_mode` - Switches the door to day mode (unlocked/trap).
* `winkhaus_doorclient.set_night_mode` - Switches the door to night mode (locked).

## 📝 Example Automations

### Auto-lock at night
```yaml
automation:
  - alias: "Lock door at bedtime"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: winkhaus_doorclient.set_night_mode
        target:
          entity_id: lock.winkhaus_door_123456_lock
```

### Notify when door is opened
```yaml
automation:
  - alias: "Door opened notification"
    trigger:
      - platform: state
        entity_id: binary_sensor.winkhaus_door_123456_door
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          message: "Front door has been opened!"
```

### Switch to day mode in the morning
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
          entity_id: lock.winkhaus_door_123456_lock
```

## 🔒 Security Note

This integration communicates over **HTTPS (TLS 1.2)** with your Winkhaus door controller. Due to the device using a self-signed/generic certificate, SSL certificate verification is disabled (`verify=False` in the code).

**Important:** 
- ✅ The connection is still **encrypted** via TLS
- ✅ Communication occurs **only within your local network**
- ✅ **No data** is transmitted to external servers or the cloud
- ⚠️ The device identity cannot be cryptographically verified

For maximum security, ensure your Winkhaus door is on a **trusted network segment** (e.g., isolated IoT VLAN).

### Why Legacy SSL Support?

The integration uses `SECLEVEL=1` and allows older cipher suites to maintain compatibility with the door controller's embedded firmware. This is a common requirement for IoT devices that cannot be easily updated.

## 🔧 Troubleshooting

**"Translation Error" during setup:**
Ensure you are running at least version **v1.2.4** or newer.

**Connection Failed:**
- Check if the door is reachable via ping from Home Assistant
- The integration uses port **443 (HTTPS)** by default
- Verify username and password (default: `admin` / your-password)

**Entities show "Unavailable":**
- Check the Home Assistant log for error messages
- If the door is temporarily offline (e.g., router restart), the integration will keep the last known state for up to 3 minutes
- Connection will automatically recover when the device is back online

**Debug Logging:**
To enable debug logging, add this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.winkhaus_doorclient: debug
```

Then check **Settings** > **System** > **Logs** for detailed information.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---
*Disclaimer: This is a custom integration and not an official product of Winkhaus.*
