"""Boot a genuine Home Assistant into a throwaway config folder.

Shared by the tools that need a real Home Assistant rather than the harness-free
unit tests: ``ha_smoke.py`` (setup, migration, service calls) and
``ha_reconfigure.py`` (replacing a unit's export).  Both exist for the same
reason -- the parts of this integration that only Home Assistant itself can
exercise are exactly the parts that have broken installations in the field.

Import this *before* anything from ``homeassistant``: it puts a copy of the
integration where Home Assistant will look for it.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def make_config_dir() -> Path:
    """A temporary config folder with this working copy of the integration in it."""
    config_dir = Path(tempfile.mkdtemp(prefix="ha-is3-"))
    (config_dir / "custom_components").mkdir()
    shutil.copytree(
        REPO / "custom_components" / "is3_export",
        config_dir / "custom_components" / "is3_export",
    )
    return config_dir


async def boot(config_dir: Path):
    """Bring up a minimal but genuine Home Assistant."""
    from homeassistant import auth, bootstrap, config_entries, core, loader
    from homeassistant.setup import async_setup_component
    from homeassistant.util import dt as dt_util

    hass = core.HomeAssistant(str(config_dir))
    hass.config.config_dir = str(config_dir)
    hass.config.skip_pip = True
    hass.config.latitude, hass.config.longitude = 50.0, 15.0
    hass.config.time_zone = "Europe/Prague"
    dt_util.set_default_time_zone(await dt_util.async_get_time_zone("Europe/Prague"))

    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {"config": {}})
    await bootstrap.async_load_base_functionality(hass)
    # file_upload (the integration declares it) pulls in http, which refuses to
    # start without an auth manager.
    hass.auth = await auth.auth_manager_from_config(hass, [], [])
    hass.set_state(core.CoreState.starting)

    for component in ("homeassistant", "file_upload"):
        assert await async_setup_component(hass, component, {}), component
    return hass


async def stage_upload(hass, text: str, filename: str = "export.is3") -> str:
    """Put ``text`` where the config flow's file selector would have left it.

    Returns the upload id the form would have submitted.  Home Assistant only
    creates its upload store when its HTTP view is first used, which never
    happens here, so the store is created on demand.
    """
    from homeassistant.components.file_upload import DOMAIN as FILE_UPLOAD, FileUploadData
    from homeassistant.util.ulid import ulid_hex

    if FILE_UPLOAD not in hass.data:
        hass.data[FILE_UPLOAD] = await FileUploadData.create(hass)
    store = hass.data[FILE_UPLOAD]

    file_id = ulid_hex()
    folder = store.file_dir(file_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(text, encoding="utf-8")
    store.files[file_id] = filename
    return file_id
