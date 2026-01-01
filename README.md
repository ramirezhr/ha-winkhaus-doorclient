# Winkhaus Door Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/ramirezhr/ha-winkhaus-doorclient)](LICENSE)
[![Maintainer](https://img.shields.io/badge/maintainer-ramirezhr-blue)](https://github.com/ramirezhr)

Custom integration to control and monitor **Winkhaus Door Systems** (blueMotion+) via local API (HTTPS).

This integration communicates directly with your door controller over the local network. **No cloud connection required.**

![Logo](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/winkhaus_door/logo.png)
*(Note: Logo will appear once the brands PR is merged)*

## Features

* **🔒 Lock Control:** Lock (Night Mode), Unlock (Day Mode), and Open (Pull Latch) the door.
* **👁️ Door Status:** Binary sensor to see if the door is physically open or closed.
* **🌗 Day/Night Mode:** Dedicated `select` entity to switch between Day (Trap) and Night (Locked) modes.
* **🔐 Secure Local Connection:** Uses HTTPS with handled legacy SSL compatibility.

## Installation

### Option 1: HACS (Recommended)

1.  Open **HACS** in Home Assistant.
2.  Go to "Integrations" > Top right menu > **Custom repositories**.
3.  Add the URL of this repository:
    `https://github.com/ramirezhr/ha-winkhaus-doorclient`
4.  Select category **Integration**.
5.  Click **Download**.
6.  Restart Home Assistant.

### Option 2: Manual Installation

1.  Download the `custom_components/winkhaus_door` folder from this repository.
2.  Copy the folder into your Home Assistant `config/custom_components/` directory.
3.  Restart Home Assistant.

## Configuration

### Manual Configuration
1.  Go to **Settings** > **Devices & Services**.
2.  Click **+ Add Integration**.
3.  Search for **Winkhaus Doorlock**.
4.  Enter the Serial Number, IP address, Username, and Password.

## Entities

After setup, the following entities will be available (example for serial `123456`):

| Entity ID | Type | Description |
| :--- | :--- | :--- |
| `lock.winkhaus_door_123456_lock` | Lock | Main control (Lock/Unlock/Open) |
| `binary_sensor.winkhaus_door_123456_door` | Binary Sensor | Door contact (Open/Closed) |
| `select.winkhaus_door_123456_mode` | Select | Switch between `day` and `night` mode |

## Troubleshooting

* **Connection Failed:** Ensure the door gateway has a static IP or is reachable via hostname.
* **SSL Errors:** The integration uses a custom SSL adapter to support the device's legacy encryption. If you encounter issues, enable debug logging.

## Roadmap


## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---
*Disclaimer: This is a custom integration and not an official product of Winkhaus.*
