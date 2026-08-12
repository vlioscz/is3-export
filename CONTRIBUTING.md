# Contributing

## Test environment

On Linux (and in CI) this is all it takes:

```bash
pip install -r requirements-test.txt
python -m pytest -q
```

### Windows

Windows needs three precautions; skipping them wastes an afternoon.

1. **Python 3.13** — current Home Assistant needs it. `winget install --id
   Python.Python.3.13 --scope user`. A bare `python` on PATH may be the
   Microsoft Store stub, which silently does nothing useful — call the full
   path (`%LOCALAPPDATA%\Programs\Python\Python313\python.exe`) or reopen the
   shell after installing.

2. **Put the venv on a short path**, e.g. `C:\Users\<you>\hav`. Installing
   Home Assistant pulls in packages with very long file names, and a deep
   venv path overflows Windows' 260-character `MAX_PATH` limit mid-install
   (`OSError [Errno 2]` from pip).

3. **The `lru-dict` pin.** Home Assistant pins `lru-dict==1.3.0`, which has
   no Python 3.13 wheel, so pip tries to compile it and fails without MSVC
   Build Tools. Workaround that needs no compiler: install the
   API-compatible 1.4.1 and relabel it so the pin is satisfied.

   ```bash
   pip install lru-dict==1.4.1
   ```

   Then in `<venv>\Lib\site-packages`: edit
   `lru_dict-1.4.1.dist-info\METADATA` changing `Version: 1.4.1` to
   `Version: 1.3.0`, rename the folder to `lru_dict-1.3.0.dist-info`, and
   only then `pip install -r requirements-test.txt`. Verify with
   `python -c "from lru import LRU"`.

Run the tests from the repository root: `python -m pytest -q`.

The tests are deliberately harness-free — `pytest-homeassistant-custom-component`
is POSIX-only (it imports `fcntl`), so the suite builds objects directly
instead and runs on any OS. One consequence: shadowing a Home Assistant
base-class attribute is a bug the suite can only catch by asserting identity
against the base class (see `tests/test_stale_refresh.py`), so never name a
coordinator method after anything `DataUpdateCoordinator` already has.

## Testing against a real unit

`tools/ha_smoke.py` boots a genuine Home Assistant into a temporary config
directory and sets the integration up against a unit you name — covering
config-entry setup, the migration, entity creation, the service layer and
unload, none of which the suite above can reach:

```bash
python tools/ha_smoke.py 192.168.1.10 --read-only
```

Without `--read-only` it also drives a dimmer and a heating zone, reading each
value first and putting it back afterwards; a zone is only ever asked for a
temperature below the room it is in, so nothing calls for heat. Use it before
releasing anything that touches setup.

`tools/ha_reconfigure.py <host>` covers the other half of the dialog: it
republishes the unit's own export under a new installation id, with one entry
removed and one added, uploads it through the reconfigure form exactly as the
front end would, and checks that the entry adopts the new identity and comes
back with the devices rearranged. It only ever reads from the unit.

```bash
python tools/ha_reconfigure.py 192.168.1.10
```

On a unit whose firmware serves no export over HTTP, pass `--export <file>` to
give it the starting point it cannot fetch.

`tools/compat_check.py <host>` is the other half: it fingerprints every
assumption the protocol code makes and can diff two fingerprints, which is how
a firmware change gets caught deliberately rather than by a bug report. Take one
from any unit you have access to, and keep it.

`tools/probe_is3.py <host>` answers the "does this unit talk to us at all"
question from outside Home Assistant.

## Before committing

**Never commit real installation data** — no real device names, hardware
serials, addresses or IPs. Test fixtures use anonymised exports
(`tests/fixtures/*.is3`, serials like `0C0001`). CI runs `tools/check_pii.py`
(a hash-based guard) on every push; run it locally too, or install the
pre-commit hook:

```bash
python tools/check_pii.py
pre-commit install
```

## Releases

Every user-visible change bumps `version` in
`custom_components/is3_export/manifest.json` and ships as a new git tag plus
GitHub Release — HACS installs whatever the release tag points at, so tags
are never moved.
