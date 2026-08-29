# in custom_components/winkhaus_doorclient/const.py

from homeassistant.util import slugify

DOMAIN = "winkhaus_doorclient"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 60
CONF_UPDATE_MODE = "update_mode"
MODE_HYBRID = "hybrid"
MODE_POLLING = "polling"


def build_entity_id(platform: str, serial: str, suffix: str) -> str:
    """Build a deterministic entity_id from the serial number.

    With has_entity_name the entity_id would otherwise be derived from the
    device name, which comes from the lock's own configuration. That name can
    be changed by the user and is not even available yet when the
    configuration request fails during the first setup, so the resulting ids
    would differ between installations and even between attempts.

    The serial number is stable and already the basis of every unique_id, so
    the entity_id follows it as well. The device name stays purely cosmetic
    and continues to drive the friendly name.
    """
    return f"{platform}.winkhaus_door_{slugify(serial)}_{suffix}"