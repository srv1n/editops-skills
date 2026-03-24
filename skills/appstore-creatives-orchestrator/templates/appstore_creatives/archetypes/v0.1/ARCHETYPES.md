# App Store Creatives Archetypes (v0.1)

These archetypes are used by `schemas/appstore_creatives/v0.1/creative_manifest.schema.json` and are intended as stable, agent-friendly “layout intent” tags.

They are not a renderer implementation; they’re a contract between:

- the human/agent brief (“what this slide is trying to do”), and
- compilers/renderers (“how we should lay it out / what evidence to emphasize”).

## Screenshot archetypes

- `BillboardHero`: Headline dominates; UI is supporting evidence.
- `Mechanism`: “How it works” in one breath; UI crop proves the mechanism.
- `OutputProof`: The output/result is the hero (artifact card or UI output view).
- `Trust`: Privacy, offline, security, “no account”, “no lock-in”.
- `WorkflowFit`: Organization, templates, search, projects, folders, widgets.
- `BeforeAfter`: Contrast: messy → clean, typing → talking, raw → structured.

## Video program modes

These map to `storyboard.videos[].mode`:

- `editorial_proof_loop`: Fast looped proof; minimal narration; strong emphasis/zoom.
- `tutorial_quickstart`: Step-by-step; chapter cards; medium pacing.
- `demo_fullscale`: Full demo sequence; multi-segment; tap pulses/guides enabled.

