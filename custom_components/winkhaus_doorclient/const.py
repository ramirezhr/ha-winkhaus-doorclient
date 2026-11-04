# in custom_components/winkhaus_doorclient/const.py

from homeassistant.util import slugify

DOMAIN = "winkhaus_doorclient"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 60
CONF_UPDATE_MODE = "update_mode"
MODE_HYBRID = "hybrid"
MODE_POLLING = "polling"

# The serial number is not just a label: it goes into the PBKDF2 salt
# together with the user name, so it has to match the lock character for
# character. The model prefix is part of it, and that is the part people
# leave off when they copy the number from the sticker.
# hassfest rejects a URL inside a translated string: it has to arrive as a
# description placeholder, so that a translator cannot be handed a link to
# mistype and every step supplies the same one.
DOCS_URL = "https://github.com/ramirezhr/ha-winkhaus-doorclient#-prerequisites"

CONF_MODEL = "model"
MODEL_BLUEMOTION = "bluemotion_plus"
MODEL_EAV4 = "eav4_plus"
MODEL_PREFIXES = {
    MODEL_BLUEMOTION: "WH_02",
    MODEL_EAV4: "WH_01",
}
SERIAL_PREFIX = "WH_"


def normalise_serial(value: str, model: str | None = None) -> str:
    """Turn what someone typed into what the lock expects.

    Whitespace goes, because it survives copy and paste. A missing model
    prefix is filled in from the selected model, and a lower case one is
    corrected - it is a known literal. The rest is left exactly as entered:
    it is device data, and guessing at its case would trade one silent
    mismatch for another.
    """
    cleaned = "".join(value.split())
    if cleaned[: len(SERIAL_PREFIX)].upper() == SERIAL_PREFIX:
        return SERIAL_PREFIX + cleaned[len(SERIAL_PREFIX) :]
    return MODEL_PREFIXES.get(model or "", "") + cleaned


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
