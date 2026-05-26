# Brand assets

Icon and logo for the **Recorder Downsampler** integration.

| File                   | Size     | Purpose                   |
| ---------------------- | -------- | ------------------------- |
| `icon.png`             | 256×256  | icon                      |
| `icon@2x.png`          | 512×512  | hi-dpi icon               |
| `logo.png`             | 873×256  | logo with wordmark        |
| `logo@2x.png`          | 1746×512 | hi-dpi logo               |
| `icon.svg`, `logo.svg` | vector   | editable sources of truth |

**Design:** an app-style rounded tile with an amber gradient (`#FFB300` →
`#E65100`) and a white "equalizer" glyph — full-resolution sample bars with
every third one kept bold, i.e. a dense signal decimated down to the few values
actually recorded. The logo adds a two-line DejaVu Sans Bold wordmark in
`#3E2723`.

**Regenerate the PNGs** from the SVG sources (needs `libcairo`, already present
on most systems; pulls `cairosvg` on demand, installs nothing):

```bash
uvx cairosvg icon.svg -o icon.png    --output-width 256 --output-height 256
uvx cairosvg icon.svg -o icon@2x.png --output-width 512 --output-height 512
uvx cairosvg logo.svg -o logo.png    --output-height 256
uvx cairosvg logo.svg -o logo@2x.png --output-height 512
```

**Publishing:** HA and HACS load brand images from the
[home-assistant/brands](https://github.com/home-assistant/brands) repo, not from
here — this folder is the source copy. To make the icon appear, open a PR adding
`icon.png` / `icon@2x.png` / `logo.png` / `logo@2x.png` under
`custom_integrations/recorder_downsampler/` there (check that repo's current CI
rules: square icon, transparent, trimmed, `pngquant`-optimized, size caps).
