# Changelog
Alle Änderungen am Winkhaus Doorclient werden in dieser Datei dokumentiert.

## [1.2.3] - 2026-01-23
### Neu
- **Zeroconf / Auto-Discovery:** Der Doorclient findet Winkhaus-Schlösser nun automatisch im Netzwerk.
- **Discovery Flow:** Neuer Einrichtungsdialog.

### Geändert
- **Service Registrierung:** Refactoring auf `platform.async_register_entity_service`.
- **Manifest:** Version auf 1.2.3 und `zeroconf` Eintrag hinzugefügt.

## [1.2.0] - 2026-01-20
### Neu
- **Select Plattform:** Auswahl Day/Night.
- **Binary Sensor Plattform:** Türstatus (Offen/Geschlossen).
- **Architektur:** Einführung `DataUpdateCoordinator`.

## [1.1.29] - 2026-01-12
### Behoben
- **HACS Konformität:** Diverse Anpassungen und Fixes, um die Anforderungen für die Aufnahme in den Default HACS Store zu erfüllen (Struktur, Linting).

## [1.1.25] - 2025-12-28
### Initial Release
- Manuelle Konfiguration und Lock-Plattform.