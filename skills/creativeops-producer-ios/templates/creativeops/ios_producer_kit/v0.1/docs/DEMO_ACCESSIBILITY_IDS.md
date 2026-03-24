# Demo Automation Accessibility IDs (Template)

This file is meant to live in a **product iOS repo** (the “producer”) at:

- `docs/DEMO_ACCESSIBILITY_IDS.md`

It’s the single source of truth for **stable** `accessibilityIdentifier` values used by:

- your UI test demo runner (emits `signals/ios_ui_events.json`)
- your CreativeOps/Director plan allowlists (hero taps)

Treat these IDs as a **public contract**. Once an ID is used in a capture plan, changing it will break automation.

## Conventions

- **Namespace:** `area.screen.element` (lowerCamelCase segments).
- **Stable & explicit:** put IDs on the actual tappable element (Button, row, segmented control segment).
- **Do not reuse:** an ID should refer to one conceptual element across the app.
- **Dynamic IDs:** allowed when the dynamic part is stable (e.g. a template id, enum raw value).

## Required fields per entry

For each ID, document:

- **ID string**
- **Screen/state** it exists in (and how to get there)
- Whether it’s a **hero tap** candidate (arrow/label-worthy) vs a background tap

## Starter IDs (replace with your app’s IDs)

### Recording / core action

- `note.recordButton`
  - Main record/stop button.
- `note.primaryCta`
  - Main “do the thing” button (rewrite/summarize/submit/etc.).

### Tabs / navigation inside a screen

- `note.contentToggle`
  - Container for content tabs.
- `note.tab.primary`
  - Primary content tab.
- `note.tab.secondary`
  - Secondary content tab.

### Template suggestions / inline CTAs (optional)

- `note.suggestion.apply`
  - Apply suggested format/template.
- `note.suggestion.notNow`
  - Dismiss suggestion.

## Adding new IDs

When adding a new demo flow step that requires a tap/callout:

1. Add a stable `accessibilityIdentifier` in app code.
2. Add the ID here with required fields.
3. Update your producer video plan (or storyboard) and re-run the capture pipeline.

