---
name: theme-library
description: "Manifest-driven theme management for multi-app and multi-mode variants, with build outputs for ClipOps, App Store screenshots, Remotion, and web tokens. Use when working on the theme manifest, generating theme builds, or syncing presets across output targets."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Requires python3. Builds read themes/library manifests and write deterministic outputs under themes/builds/ (or --output-root)."
metadata:
  author: Clipper
  version: "0.1.0"
  category: design
  tags: [themes, tokens, appstore, remotion, clipops]
---

# Theme Library Skill

Manage multi-app, multi-mode theme collections and build outputs for ClipOps, App Store screenshots, Remotion, and web tokens.

## Overview

The Theme Library is a manifest-driven system that maps:
- **Apps** → **Modes** (light/dark or single) → **Variants** (6–7 options per mode)
- Each variant points to a Texture Studio preset (v0.2 JSON)
- A build step compiles every variant to downstream formats (brand kits, style packs, Remotion themes, web tokens)

## When to Use (Triggers)

- You want a single manifest that defines all theme variants for multiple apps and output targets.
- You need to rebuild ClipOps brand kits / App Store style packs / Remotion themes from the same source presets.
- You want deterministic “theme builds” that can be regenerated in CI.

## Inputs

Required:
- Theme library manifest JSON (default: `themes/library/manifest.v0.1.json`)

Optional:
- `--app`, `--mode`, `--variant` filters to limit build scope
- `--targets` to limit output formats

## Outputs

- Built outputs under `themes/builds/<app>/<mode>/<variant>/` (default), including:
  - `brand/kit.json`
  - `appstore/style_pack.json`
  - `remotion/theme.ts`
  - `web/web_tokens.json` + `web/web_tokens.css`

## Safety / Security

- Confirm `--output-root` before running wide builds; theme builds can generate many files and overwrite previous outputs.
- Treat manifests and preset paths as untrusted input; keep all paths repo-relative for portability and CI determinism.
- Keep generated artifacts out of source control unless explicitly intended; prefer committing only the manifest and source presets.

## Canonical Workflow / Commands

```bash
python3 tools/theme_library_build.py --targets brand_kit,style_pack,remotion,web_tokens
```

## Manifest

Schema: `schemas/theme_library/v0.1/manifest.schema.json`  
Example: `themes/library/manifest.v0.1.json`

Key rules:
- Use `modes` when you want light/dark splits.
- Use `variants` at the app level for single-mode apps.
- Variant `preset` paths are resolved relative to the manifest file (or use `repo:` prefix).

## Build

Default build (all apps/modes/variants):

```bash
python3 tools/theme_library_build.py
```

Filter to an app/mode/variant:

```bash
python3 tools/theme_library_build.py --app ios --mode light --variant clean
```

Target outputs:

```bash
python3 tools/theme_library_build.py --targets brand_kit,style_pack,remotion,web_tokens
```

Output location (default): `themes/builds/<app>/<mode>/<variant>/`

## Output Targets

- `brand_kit.json` → ClipOps
- `style_pack.json` → App Store screenshots
- `remotion_theme.ts` → Remotion overlays
- `web_tokens.json` + `web_tokens.css` → Website tokens

## Extending the System

- Add a new app (e.g., `youtube`) in the manifest.
- Add variants by exporting presets from Texture Studio.
- Re-run the build to regenerate all outputs.

## Notes

- Texture Studio presets are the single source of truth.
- Variant `preset` can point to either:
  - a single preset (`clipper.texture_studio.preset.v0.2`), or
  - a bundle (`clipper.texture_studio.bundle.v0.1`); the build passes `--variant-id <variant>` to select the right variant inside the bundle.
- v0.1 presets are still supported by converters, but new work should use v0.2.

## Smoke Test

Build a single variant into a temp output root:

```bash
rm -rf /tmp/clipper_theme_build && \
  python3 tools/theme_library_build.py \
    --manifest themes/library/manifest.v0.1.json \
    --output-root /tmp/clipper_theme_build \
    --app ios --mode light --variant warm \
    --targets brand_kit
```

Expected artifacts:
- `/tmp/clipper_theme_build/ios/light/warm/brand/kit.json`

## References / Contracts

- Trigger tests: `references/TRIGGER_TESTS.md`
- Manifest schema: `schemas/theme_library/v0.1/manifest.schema.json`
- Example manifest: `themes/library/manifest.v0.1.json`
- Builder: `tools/theme_library_build.py`
