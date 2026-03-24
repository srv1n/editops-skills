---
name: texture-studio
description: "Visual color and texture preset editor for ClipOps, App Store creatives, and Remotion overlays, with export and converter workflows for downstream tooling. Use when creating/updating Texture Studio presets or converting them to brand kits/style packs/themes."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Editor requires a local browser to open the HTML file. Converters require python3."
metadata:
  author: Clipper
  version: "0.1.0"
  category: design
  tags: [textures, color, presets, appstore, clipops]
---

# Texture Studio Skill

Visual color and texture preset editor for ClipOps, App Store creatives, and Remotion overlays.

## Overview

Texture Studio provides a visual interface (`color-texture-studio-full.html`) for configuring:
- **Color gradients**: Background gradient with start/end colors
- **Background textures**: Screen, multiply, overlay blend modes with selectable texture kinds, scale, and seeds
- **Typography**: Font family, weight, letter spacing
- **Text effects**: Ink bleed, grain patterns (seeded + scaled), knockout/erosion (seeded + scaled), letterpress emboss, optional text blur
- **Background image**: Upload an image layer, link or embed it, and blend/mask with gradient + textures

Presets can be exported as JSON and converted to formats consumed by:
- ClipOps brand kits
- App Store creative style packs
- Remotion overlay themes
- Web token bundles (CSS variables + JSON)

## When to Use (Triggers)

- You need a consistent “look” (colors + textures + typography + effects) that can be exported and reused across tools.
- You want to generate ClipOps brand kits / App Store style packs / Remotion themes from one preset source.
- You want deterministic variants (seeded textures/ink/grain) for A/B testing.

## Inputs

Required:
- A Texture Studio preset JSON (single preset or bundle).

Optional:
- A target format to convert into (ClipOps brand kit, App Store style pack, Remotion theme, Web tokens).

## Outputs

- Converted artifacts such as:
  - ClipOps brand kit: `bundle/brand/kit.json`
  - App Store style pack: `style_pack.json`
  - Remotion theme: `src/themes/MyTheme.ts`
  - Web tokens: `web_tokens.json` + `web_tokens.css`

## Safety / Security

- Confirm output paths before overwriting existing brand kits/style packs/themes; converters write deterministic outputs and may replace files.
- Treat preset JSON as untrusted input; validate against the schema before conversion when unsure.
- Keep presets and generated assets free of secrets and private URLs; prefer embedding assets only when intended for distribution.

## Canonical Workflow / Commands

1) Open the editor:

```bash
open assets/color-texture-studio-full.html
```

2) Export a preset JSON (copy/download/save).

3) Convert the preset to your target format (examples below).

## Workflow

### 1. Open the Studio

```bash
open assets/color-texture-studio-full.html
```

### 2. Configure Your Style

- **Colors**: Click swatches to select gradient start/end colors
- **Typography**: Choose font family, weight (400-900), letter spacing
- **Text Effects**: Toggle and adjust ink bleed, grain, knockout, letterpress
- **Background Textures**: Enable/adjust screen, multiply, overlay, blur layers
- **Background Image**: Upload an image, choose embed vs link, set opacity/blend/fit, mask with gradient, pan/zoom, and optionally disable the gradient behind the image
- **Quick Variants**: Use the Quick Variants panel to explore randomized options (add/remove variants as needed)

### 3. Export Preset

- Enter a preset name in the "Preset Export" panel
- Click "Export JSON" to download, or "Copy JSON" for clipboard
- If you have multiple variants, export/copy/save emits a **bundle**:
  - `clipper.texture_studio.bundle.v0.1` with `variants: [...]`
  - Single-variant exports still follow `clipper.texture_studio.preset.v0.2`

### 3b. Project Folder (File System Access)

For easier library management in Chromium browsers:
- Open a folder in the **Project Folder** panel
- Click **Save Preset** to write JSON files directly to that folder
- Click a listed preset to load it
- Use **Refresh** to rescan the folder

Safari/Firefox require manual Import/Export instead.

### 4. Convert to Target Format

#### ClipOps Brand Kit

```bash
python3 scripts/convert_to_brand_kit.py \
  --preset preset.json \
  --output bundle/brand/kit.json
```

Bundle-aware options:
- `--variant-id my_variant`
- `--variant-index 2` (0-based)
- `--all-variants` (writes `__<variant>` suffixed outputs)

#### App Store Style Pack

```bash
python3 scripts/convert_to_style_pack.py \
  --preset preset.json \
  --output style_pack.json
```

Bundle-aware options:
- `--variant-id my_variant`
- `--variant-index 2`
- `--all-variants`

#### Remotion Theme

```bash
python3 scripts/convert_to_remotion_theme.py \
  --preset preset.json \
  --output src/themes/MyTheme.ts
```

Bundle-aware options:
- `--variant-id my_variant`
- `--variant-index 2`
- `--all-variants`

#### Web Tokens (CSS + JSON)

```bash
python3 scripts/convert_to_web_tokens.py \
  --preset preset.json \
  --output-json web_tokens.json \
  --output-css web_tokens.css
```

Bundle-aware options:
- `--variant-id my_variant`
- `--variant-index 2`
- `--all-variants`

## Preset Schema

Presets follow `schemas/texture_studio/v0.2/texture_preset.schema.json`:

```json
{
  "schema": "clipper.texture_studio.preset.v0.2",
  "id": "bold_gradient_v02",
  "background": {
    "gradient": { "start": "#92400e", "end": "#d97706", "angle_deg": 135 },
    "textures": {
      "layers": [
        { "id": "screen", "enabled": true, "opacity": 0.35, "blend_mode": "screen", "kind": "dust", "seed": 1207, "tile_px": 160 },
        { "id": "multiply", "enabled": true, "opacity": 0.25, "blend_mode": "multiply", "kind": "paper", "seed": 881, "tile_px": 210 },
        { "id": "overlay", "enabled": true, "opacity": 0.4, "blend_mode": "overlay", "kind": "grain", "seed": 5221, "tile_px": 120 }
      ]
    },
    "blur_px": 0.5
  },
  "typography": {
    "font_family": "Space Grotesk",
    "font_weight": 900,
    "letter_spacing_em": -0.04
  },
  "text_effects": {
    "ink_bleed": { "enabled": true, "amount": 2 },
    "grain": { "enabled": true, "intensity": 0.6, "pattern": "fine", "seed": 913, "tile_px": 140 },
    "knockout": { "enabled": false, "amount": 0.7, "pattern": "worn", "seed": 4021, "tile_px": 150 },
    "letterpress": { "enabled": false, "depth": 1 },
    "blur": { "enabled": false, "amount": 0 }
  },
  "color_palette": {
    "primary": "#92400e",
    "secondary": "#d97706"
  }
}
```

## Font Registry

Font mappings are in `schemas/texture_studio/v0.1/font_registry.json`:
- `google_fonts`: Remotion import path
- `bundle_path`: ClipOps bundle font path
- `css_family`: CSS font-family declaration
- `system_path_macos`: macOS system font path (if applicable)

## Example Presets

See `templates/texture_studio/presets/` for example presets:
- `bold_gradient.json` - High contrast gradient with grain (v0.1)
- `minimal_paper.json` - Subtle paper texture (v0.1)
- `distressed_vintage.json` - Heavy knockout + letterpress (v0.1)
- `bold_gradient_v02.json` - Seeded texture layers + grain scale (v0.2)
- `distressed_vintage_v02.json` - Fibers + stains with seeded textures (v0.2)

## Integration

### ClipOps

The converted brand kit integrates with ClipOps timeline rendering:
- Text styles with fill, stroke, shadow from texture effects
- Color palette from gradient colors
- Font paths resolved from registry

### App Store Creatives

The converted style pack works with the App Store creatives orchestrator:
- Background gradient for screenshot backgrounds
- Typography settings for title/subtitle fonts
- Color tokens for highlights

### Remotion Overlays

The generated TypeScript theme file can be imported by:
- `LowerThird.tsx`
- `IntroTitle.tsx`

Pass the theme object as a prop to customize rendering.

## Smoke Test

Convert an example preset into a ClipOps brand kit:

```bash
rm -rf /tmp/clipper_texture_studio && mkdir -p /tmp/clipper_texture_studio && \
  python3 scripts/convert_to_brand_kit.py \
    --preset templates/texture_studio/presets/bold_gradient_v02.json \
    --output /tmp/clipper_texture_studio/kit.json
```

Expected artifacts:
- `/tmp/clipper_texture_studio/kit.json`

## References / Contracts

- Trigger tests: `references/TRIGGER_TESTS.md`
- Texture preset schema: `schemas/texture_studio/v0.2/texture_preset.schema.json`
- Font registry: `schemas/texture_studio/v0.1/font_registry.json`
- Converters:
  - `scripts/convert_to_brand_kit.py`
  - `scripts/convert_to_style_pack.py`
  - `scripts/convert_to_remotion_theme.py`
  - `scripts/convert_to_web_tokens.py`
