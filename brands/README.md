# Brand assets

The source design for the integration's icon and logo.

| File | Size |
| --- | --- |
| `icon.png` | 256×256 |
| `icon@2x.png` | 512×512 |
| `logo.png` | 461×256 |
| `logo@2x.png` | 922×512 |

`icon.svg` is the source; `make_icons.py` renders the PNGs from the same design
(run it from the repository root with Pillow installed). These files are kept
here as the design source and for the README banner.

## Showing them in Home Assistant

Since Home Assistant 2026.3 a custom integration ships its own brand images: the
same PNGs are copied into `custom_components/is3_export/brand/`, and Home
Assistant serves them locally in preference to the brands CDN. No submission to
[home-assistant/brands](https://github.com/home-assistant/brands) is needed --
that repository no longer accepts custom-integration icons.

After updating the integration, restart Home Assistant (2026.3 or newer) for the
icon to appear. On older versions the default icon is shown, which changes
nothing about how the integration works.
