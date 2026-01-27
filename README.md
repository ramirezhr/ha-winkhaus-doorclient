# Winkhaus Doorclient for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/releases)
[![License](https://img.shields.io/github/license/ramirezhr/ha-winkhaus-doorclient?style=for-the-badge)](https://github.com/ramirezhr/ha-winkhaus-doorclient/blob/main/LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Default-orange?style=for-the-badge)](https://github.com/hacs/integration)
[![Maintainer](https://img.shields.io/badge/maintainer-ramirezhr-blue?style=for-the-badge)](https://github.com/ramirezhr)

Custom integration to control and monitor **Winkhaus Door Systems** (blueMotion+) via local API (HTTPS).

This integration communicates directly with your door controller over the local network. **No cloud connection required.**

![Logo](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/winkhaus_doorclient/logo.png)

## ✨ Features

* **🔒 Lock Control:** Lock (Night Mode), Unlock (Day Mode), and Open (Pull Latch) the door.
* **🚪 Door Status:** Binary sensor to see if the door is physically open or closed.
* **🌗 Day/Night Mode:** Dedicated `select` entity to switch between Day (Trap) and Night (Locked) modes.
* **🔎 Auto-Discovery:** Automatically finds your Winkhaus door in the network (Zeroconf/mDNS).
* **🔐 Secure Local Connection:** Uses HTTPS with handled legacy SSL compatibility.

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

### Services
You can use these services in your automations:

* `winkhaus_doorclient.set_day_mode` - Switches the door to day mode (unlocked/trap).
* `winkhaus_doorclient.set_night_mode` - Switches the door to night mode (locked).

## 🔧 Troubleshooting

**"Translation Error" during setup:**
Ensure you are running at least version **v1.2.4** or newer.

**Connection Failed:**
Check if the door is reachable via ping. The integration uses port 443 (HTTPS) by default.

**Debug Logging:**
To enable debug logging, add this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.winkhaus_doorclient: debug
```

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---
*Disclaimer: This is a custom integration and not an official product of Winkhaus.*
