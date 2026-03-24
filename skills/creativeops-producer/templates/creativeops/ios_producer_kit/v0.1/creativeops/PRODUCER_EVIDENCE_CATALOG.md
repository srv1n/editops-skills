# Producer Evidence Catalog (v0.1)

This document describes the minimal producer-side contract needed for the `clipper` App Store creatives system:

- screenshot rendering (App Store PNG/JPG) driven by a Creative Manifest
- video demo compilation/rendering via Director + ClipOps v0.4

## What the catalog is

The **Producer Evidence Catalog** is an app-owned JSON file that lists stable IDs for:

- screenshot routes (where to navigate before capturing)
- video flows (which recorded demo sequences exist / can be captured)
- evidence IDs referenced by manifests (e.g. `screenshot.slide_01`, `video.onboarding_flow`)

Clipper schema:
- `schemas/appstore_creatives/v0.1/producer_evidence_catalog.schema.json`

Expected location (convention):
- `creativeops/producer_evidence_catalog.json`

## How to generate

This template includes a generator that derives the catalog from two producer-owned plans:

- screenshot plan (defines stable route IDs + slide IDs)
- video plan (defines stable flow IDs)

```bash
python3 scripts/appstore_screenshots/export_producer_evidence_catalog.py \
  --app-id com.your.app \
  --screenshot-plan scripts/appstore_screenshots/plan.json \
  --video-plan scripts/appstore_screenshots/video_plan.json \
  --out creativeops/producer_evidence_catalog.json
```

## Key conventions

- Keep `routeId` and `flowId` stable over time (treat them like an API).
- Prefer a small, curated set of `captureElements` / callout IDs:
  - they directly influence tap guides, focus outlines, and camera pulses downstream.

