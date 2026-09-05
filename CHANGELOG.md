# Changelog

## [2.5.0] - 2026-08-30

### Changed
- **Entity Names Now Translated:** Only two entities carried a translation key, so a German installation showed a mix: "Haustür Fehlerstatus" next to "Haustür Lock Count". Every entity now takes its name from the translation files, and the German set has been completed. Entity IDs are unaffected - they follow the serial number since 2.4.2 and do not depend on the display name. For the counter sensors the payload key doubles as the translation key, so each one is named in exactly one place.
- **Mode Options Translated:** The day/night selector listed its options as the raw values `day` and `night`. They are now translated as well. The stored values are unchanged, so automations and scripts referring to them keep working.

### Added
- **Reconfigure Flow:** Connection settings of an existing entry can now be changed from the integration's menu. Zeroconf keeps the IP up to date on its own, but mDNS does not cross subnet boundaries - precisely the setup a lock in a separate IoT VLAN lives in. Until now a changed address meant deleting and re-adding the integration, which discarded entity IDs, history and every automation built on them. The new address is verified before it is stored, an empty password field keeps the existing one, and the serial number stays fixed because it identifies the entry.
- **Repair Issue for an Unreachable Device:** The notification shown after three failed updates has become a repair issue. It appears in the Repairs dashboard, survives a restart, names the number of failed attempts and points at the reconfigure flow for the case where the address has changed. It clears itself as soon as the lock responds again, and is removed when the entry is unloaded.
- **Diagnostics Download:** The device page now offers a diagnostics export covering the connection state, session and reconnect counters, both coordinator intervals, the internal protocol counters and the lock's own firmware and operation counts. Serial number, IP addresses, user name and password are redacted, so the file can be attached to a public issue as it is.

### Fixed
- **Untranslated State on Multiple Faults:** When the lock reported more than one fault at a time, the error sensor joined them into a single state such as `blocked, overcurrent`. No translation exists for a combined value, so the dashboard fell back to the raw English string. The state now carries the first fault, which always has a translation, and the complete list is available through the new `all_errors` and `error_count` attributes - so nothing is lost and automations can react to either.
- **Entity Name Not Translated:** The error sensor set both an explicit name and a translation key. The explicit name won, so the entity stayed "Error State" in every language while only its states were translated. The name now comes from the translations as intended.

## [2.4.2] - 2026-08-16

### Fixed
- **Non-Deterministic Entity IDs:** Since 2.3.0 the device carries the name configured on the lock, and Home Assistant derives entity ids from the device name. That made the ids depend on a value the user can change - and one that is not even known yet if the configuration request fails during the very first setup, in which case the placeholder name was used instead. Two identical installations could therefore end up with different entity ids. Entity ids are now built from the serial number, which is stable and already the basis of every unique id. The lock's name remains in use for the friendly name, where it belongs. Existing entities keep their current ids; Home Assistant only assigns an id once, so nothing breaks in automations.

## [2.4.1] - 2026-08-15

### Fixed
- **Setup Aborted Instead of Retrying:** When the lock was not reachable during the initial refresh - typically while Home Assistant was still booting and the network had not settled - the integration gave up permanently and had to be reloaded by hand. `async_config_entry_first_refresh()` raises `ConfigEntryNotReady` in that situation, which is Home Assistant's signal to retry with a growing backoff, but a broad `except Exception` caught it and turned it into a plain setup failure. The signal is now passed through, alongside `ConfigEntryAuthFailed` which was already handled correctly, so a device that is briefly unavailable at boot no longer needs manual intervention.
- **Unhelpful Error Text:** Startup and update failures were logged with the exception class name only. Since the API layer wraps every network problem in a plain `Exception`, the log read `: Exception` and revealed nothing about the cause. The message is logged instead, so a timeout, a refused connection and a name resolution failure can be told apart.

## [2.4.0] - 2026-08-14

### Added
- **Fragment Reassembly:** The lock splits payloads larger than roughly 1 KB into chunks, marking only the last one with a FIN bit, and encrypts each chunk separately. The integration now buffers the decrypted plaintext until the final chunk arrives instead of treating every chunk as a complete message. Previously a split response was lost entirely: the first chunk failed JSON parsing and the remainder was discarded silently. Decoding is deferred until the message is complete, since a chunk boundary can fall inside a multi-byte UTF-8 character.
- **Rejection Attribution:** Warnings about rejected requests now name the request they belong to, e.g. `Lock rejected /api/v1/control {'command': 'night'} (sent 0.4s ago): blocked`. The device does not echo request identifiers, so attribution is by recency - accurate while a single request is in flight, and flagged as uncertain when the last request went out more than ten seconds earlier.
- **Replay Protection:** Incoming messages must carry a strictly increasing counter. The counter only advances after a successful decrypt, so a corrupted frame cannot lock out the messages that follow it, and it resets on every handshake because the device restarts its own sequence.

### Fixed
- **Reconnect Loop After a Dropped Session:** `_listen` handles `ConnectionClosed` itself and returns normally, so the backoff in the enclosing exception handler never applied and the reconnect loop restarted instantly. A device that repeatedly drops the connection would have been hammered with handshakes - the same failure mode as the authentication loop fixed in 2.2.0, reached by a different path. Sessions now pause five seconds before reconnecting, and the log records how long the session lasted.
- **Attributes Vanishing After a Push:** A state-change push carries only the fields that changed, but it replaced the entire data set, so fields present only in a full poll - notably `last_update_from_device` - disappeared until the next watchdog cycle. Pushes are now merged into the existing data. Fields the lock omits rather than reporting as empty are exempt from the merge, so a cleared fault does not linger on the error sensor.

## [2.3.0] - 2026-08-09

### Added
- **Device Naming from Lock Configuration:** The integration now reads the user-defined name from the device via the new `/api/v1/getConfiguration` endpoint. Both the device registry entry and the config entry title follow the name configured on the lock itself (e.g. "Front Door") instead of the generic serial-number placeholder. If the endpoint is unavailable or no name is set, the previous naming scheme is used as a fallback.
- **Configuration Endpoint:** Added `get_configuration()` to the API client. It is fetched alongside the system state on the existing 12-hour coordinator, so no additional polling load is introduced.

### Changed
- **Uptime Format:** The `current_uptime` attribute now always reports `HH:MM:SS` with hours accumulating beyond 24. Previously the underlying `timedelta` switched its structure once a session passed the 24 hour mark (`1 day, 1:01:01`), which broke templates parsing the string - and it broke them only after a connection had been stable for a full day, making the failure hard to reproduce.
- **Standardised Logs:** Converted the remaining German log messages and code comments in `__init__.py`, `sensor.py`, `select.py` and `config_flow.py` to English. The entire integration is now pure ASCII.
- **Notification Wording:** Removed a misleading minute estimate from the offline notification. The failure count does not translate to minutes, since the update interval differs between Hybrid (120s) and Polling mode.

### Fixed
- **Timestamp Time Zone:** The `last_update_from_device` attribute displayed a time shifted by the UTC offset - two hours early during daylight saving time in Central Europe. The lock reports a standard UTC timestamp, but the conversion produced a naive string with no offset, so the frontend rendered the UTC value as if it were local time. The attribute now carries an explicit UTC offset and is converted independently of the host system's time zone, which differs between HA OS and container installations. Implausible values from an unset device clock no longer produce a bogus date.
- **Deprecated Notification API:** Replaced the `hass.components.persistent_notification` helper, which was deprecated in Home Assistant 2024.6 and has since been removed. The offline notification previously raised an `AttributeError` on current Home Assistant versions - precisely when the device was unreachable and the warning was needed most.
- **Duplicate EntityCategory Import:** Removed a second `EntityCategory` import in `sensor.py` that shadowed the correct one with the deprecated `homeassistant.helpers.entity` path.
- **Button Error Handling:** The "Clear Errors" button no longer swallows failures silently. Errors from the unblock call are now logged and re-raised so Home Assistant reports the failed action in the UI.
- **Defensive Configuration Parsing:** Hardened the device name lookup against unexpected payload shapes. Older firmware that omits the `system` object or returns it in a different structure no longer breaks integration setup.

## [2.2.0] - 2026-08-08

### Added
- **Button Platform:** Introduced a new "Clear Errors" button entity (`button.py`) allowing users to actively reset lock error states (`blocked`, `overcurrent`) directly via the `/api/v1/unblock` API endpoint.
- **Diagnostic Sensor:** Error state sensor now fully maps all known lock error states (`blocked`, `batterylow`, `overcurrent`) directly from the JSON payload.
- **Translations:** Added comprehensive English and German (`strings.json`, `de.json`) localization for the new button and all error states for a seamless Home Assistant dashboard experience.

### Changed
- **Hardware Model Detection:** Refactored the device model identification logic. The integration now reliably distinguishes between models (e.g., `EAV4+` vs. `blueMotion+`) based on the device's serial number prefix (`WH_01`) instead of the API version string.
- **Command Resolution:** Replaced the conditional command chain in `async_execute_command` with an explicit `COMMAND_MAP`, centralising the device's command vocabulary in a single place. Unknown commands and invalid mode values are now rejected with a descriptive log entry instead of being forwarded to the lock as an invalid payload.
- **Watchdog Timing:** Reduced the watchdog trigger from 100 to 75 seconds and the HTTP fallback threshold from 110 to 85 seconds. With protocol-level keep-alive pings running every 20 seconds, the previous thresholds delayed recovery unnecessarily.
- **Standardised Logs:** Converted all remaining German log messages and code comments in `api.py` to English. The module is now pure ASCII, avoiding encoding issues on systems with unusual locale settings.

### Fixed
- **WebSocket Authentication Loop:** Fixed a serious issue where a failed WebSocket handshake (e.g. after a password change) caused the reconnect loop to retry immediately and without limit, flooding the lock with handshake attempts and spamming the Home Assistant log. Failed authentication now backs off for 30 seconds and logs an actionable message.
- **Stale Connection State:** Connection state (`ws_connected`, `_active_ws`) was only reset when the listener exited via a `ConnectionClosed` exception. If the message loop ended normally, the integration continued to believe the socket was alive and kept routing commands into a dead connection. State is now reset in a `finally` block covering every exit path.
- **Rejected Command Reporting:** `XC_ERR` responses arriving over the WebSocket were misclassified as watchdog heartbeats and silently discarded at debug level. Commands rejected by the lock are now reported as warnings including the error text from the payload.
- **Watchdog Keep-Alive Accuracy:** The keep-alive timestamp was refreshed by every incoming frame, including frames too short to carry a payload. Only genuine messages are counted now, so the watchdog no longer misreads noise as a sign of life.
- **Watchdog Error Visibility:** A bare exception handler in the watchdog loop swallowed all ping failures without a trace. Failures are now logged at debug level.

## [2.1.0] - 2026-06-26

### Added
- **Error State Sensor:** Introduced a new diagnostic sensor (`sensor.error_state`) to monitor hardware error states directly from the lock's JSON payload.
- **Translations:** Added English UI translations for lock error states (e.g., "overcurrent") for a cleaner Home Assistant dashboard experience.

### Changed
- **Robust Payload Parsing:** Enhanced the internal JSON parsing logic to gracefully handle missing `error` arrays, accurately reporting a healthy "No Error" state when the lock does not broadcast faults.

## [2.0.0] - 2026-03-14

### Added
- **Event-Driven Architecture (Hybrid Mode):** Introduced real-time WebSocket integration. The integration now listens for instant state pushes from the lock (0ms latency on door movements) instead of relying solely on HTTP polling.
- **Dynamic Mode Switching:** Added the ability to seamlessly toggle between `HYBRID` (WebSocket + Watchdog) and `POLLING` (HTTP) modes directly via the Home Assistant integration options.
- **Smart Keep-Alive Watchdog:** Implemented a lightweight 100-second WS ping interval. This triggers the lock's status broadcast to prevent the router from dropping the inactive TCP connection during standby periods.

### Changed
- **Network Optimization:** Drastically reduced network traffic and device load. The system now only utilizes HTTP fallback if the WebSocket connection is completely unresponsive for over 110 seconds.
- **Log Accuracy:** Updated internal logging and comments to accurately reflect the lock's true broadcast behavior (lock only pushes on physical movement or active requests, not on an internal timer).

### Fixed
- **Hardware Socket Leaks:** Fixed an issue where rapid reloading or mode switching caused HTTP timeouts (`Failed to communicate with device`). Introduced a clean `stop()` method to kill background tasks and a 2-second hardware cooldown in `async_unload_entry` to allow the lock to gracefully release TCP sockets.
- **WebSocket Log Spam:** Added a filter to properly catch and silence benign `{"XC_SUC": {}}` command acknowledgments from the WebSocket stream, preventing "Unknown Message" warnings in the Home Assistant logs.

## [1.5.2] - 2026-03-XX

### Added
- **System Metrics:** Lock/Unlock/Error counter sensors from device statistics (12h polling interval)
- **Device Information:** Automatic firmware version and model detection from device
- **Persistent Notifications:** Users are notified when device is offline for 3+ minutes
- **Centralized Device Info:** All entities now share a single device_info object

### Changed
- **System State Polling:** Reduced from 60s to 12h interval (saves ~1,438 API calls/day)
- **Binary Sensor:** More robust state comparison (handles different API response types)

### Fixed
- **Code Cleanup:** Removed redundant XC_SUC unpacking (already handled by API layer)

## [1.5.1] - 2026-02-XX

### Changed
- **Refined Firmware Display:** The firmware string is now neatly split and formatted as Version (Timestamp) in the Home Assistant device info (e.g., 1.5.6 (2512151512)).
- **Model Name Correction:** The raw "BM+" string from the lock is now correctly expanded and displayed as "blueMotion+" for better readability.
- **Standardized Logs:** System error messages have been switched to English for better consistency and easier troubleshooting.
- **v2.0 Groundwork:** Internal preparation for the upcoming hybrid architecture, keeping the current polling method as the fallback path.

## [1.5.0] - 2026-02-28

### Added
- **New Sensor Platform:** Introduced `sensor.py` to expose diagnostic data from the Winkhaus system dump.
- **Diagnostic Sensors:** Added dedicated sensors for `Lock Count`, `Unlock Count`, and `Error Count` to track door usage and health.
- **Device Information:** Device info (Firmware version and Hardware Model) is now officially parsed and populated across all entities in the Home Assistant device registry.

### Changed
- **Dual Coordinator Architecture:** Implemented a secondary `DataUpdateCoordinator` with a 12-hour polling interval specifically for `get_system_state`. This ensures diagnostic data stays updated without overloading the API during regular fast-polling.
- **Centralized Device Info:** Refactored `device_info` generation to be built centrally once in `__init__.py` and shared across all entities, adhering to the DRY (Don't Repeat Yourself) principle and improving maintainability.

## [1.4.1] - 2026-02-10

### Added
- **Config Flow**: Added a slider to configure the polling interval (range: 30-300 seconds), defaulting to 60 seconds.
- **Config Flow**: Added validation to ensure the interval is within the allowed range.

### Changed
- **Translations**: Updated configuration descriptions to English as requested ("Configure the polling interval...").

### Fixed
- **Stability**: General stability improvements and code cleanup.

## [1.4.0] - 2026-02-01

### Fixed
- **Translations**: Fixed a JSON syntax error (unexpected character) in `translations/de.json` that caused the integration setup to fail.
- **Stability**: Consolidated current code base as stable release v1.4.0.

## [1.3.1] - 2026-01-29

### Added
- **Graceful Error Recovery:** Coordinator now retains previous state during temporary network failures (up to 3 consecutive errors) before marking entities unavailable.

### Changed
- **Localization:** Converted all internal log messages and exceptions from German to English for better standardization.
- **Refactoring:** Renamed internal password variable to `_password` to indicate protected status.

## [1.3.0] - 2026-01-28

### Added
- **Offline Resilience:** The integration now tolerates short network interruptions (Graceful Degradation). If the lock is unreachable, it keeps the last known state instead of immediately marking entities as "Unavailable".
- **Legacy SSL Support:** Implemented `SECLEVEL=1` and `OP_LEGACY_SERVER_CONNECT` in the SSL context to support older hardware encryption on modern OS (Debian 12/HA OS).

### Changed
- **Rubber-Banding Fix:** Added `asyncio` delays (2-3s) after actions (`lock`, `unlock`, `open`, `mode`) to give the mechanical lock time to reach its target position before refreshing the state.
- **Coordinator Naming:** The DataUpdateCoordinator now uses the device serial number in its name (`winkhaus_doorclient_SERIAL`) for easier debugging.

### Fixed
- **API Hardening:** Added strict validation of API responses (HTTP Status, Content-Type, JSON structure).
- **Scanner Resource Leak:** Fixed an issue in `config_flow.py` where the `ServiceBrowser` was not properly cancelled if the scan was interrupted.

## [1.2.6] - 2026-01-25

### Added
- **Re-Authentication Flow:** If the password on the lock is changed, the integration now prompts the user to enter the new password via Home Assistant's "Repair" dashboard, instead of requiring a full re-installation.

## [1.2.5] - 2026-01-23

### Changed
- **Auto-IP-Update:** If the IP address of an already configured lock changes (e.g., via DHCP), the integration now detects this automatically via Zeroconf and updates the IP in the configuration without user intervention.
- **Zeroconf Handling:** Improved processing of discovery packets.

## [1.2.4] - 2026-01-23

### Fixed
- **Config Flow:** Fixed a translation error (`translation_key 'auth'`). The lock's serial number is now correctly displayed during the password prompt instead of showing a placeholder error.

## [1.2.3] - 2026-01-23

### Added
- **Zeroconf / Auto-Discovery:** The integration now automatically discovers Winkhaus locks in the network (`_whdc-device._tcp.local.`).
- **Discovery Flow:** New setup dialog that lists discovered devices for easy selection.

### Changed
- **Service Registration:** Services (`set_day_mode`, `set_night_mode`, `get_system_state`) now use `platform.async_register_entity_service`. This removes boilerplate code and enables native target selection in the Home Assistant UI.
- **Manifest:** Bumped version to 1.2.3 and added `zeroconf` entry.

## [1.2.0] - 2026-01-20

### Added
- **Select Platform:** Added new entity to switch between "Day" and "Night" mode.
- **Binary Sensor Platform:** Added new entity for door status (Open/Closed).
- **Architecture:** Introduced `DataUpdateCoordinator`. Status updates are now fetched centrally and distributed to all entities (Lock, Select, Sensor) to reduce load on the lock.

## [1.1.29] - 2026-01-12

### Fixed
- **HACS Compliance:** Various adjustments and fixes to meet the requirements for inclusion in the default HACS store (structure, linting).

## [1.1.25] - 2025-12-28

### Initial Release
- Basic functionality (Stable).
- Manual configuration via IP address.
- Lock Platform (Lock/Unlock/Open).