---
name: beads-planner
description: >
  Use Beads (bd) as the persistent planning/backlog system: create epics + stories
  from design specs, resume work by querying bd (ready/in_progress/blocked),
  and capture discovered follow-ups with dependencies. Use whenever a request
  mentions planning, backlog, epics/stories, “design spec”, “PRD”, “roadmap”,
  or Beads/bd/issue tracking.
---

# Beads Planner

## Overview

This skill makes Codex treat **bd (beads)** as the system of record for multi-session work:
it creates/updates issues, maintains epic→story structure, and uses bd queries to resume work.

If you’re starting a new session, run `bd prime` first.

## When to Use (Triggers)

- The user mentions planning, backlog, epics/stories, roadmap, or a design spec/PRD.
- Work spans multiple sessions and you don’t want to lose follow-ups/dependencies.
- You want a deterministic way to resume (“what’s in progress / what’s ready?”).

## Inputs

Required:
- `bd` CLI available on PATH (beads)

Optional:
- Existing issue IDs (e.g. `clipper-a3f2dd`)
- A markdown design spec to import into an epic + child stories

## Outputs

- Persistent issues in the bd database (epics/stories/tasks/bugs)
- Status transitions (`open → in_progress → blocked/closed`)
- Exportable JSONL via `bd sync --flush-only` (safe even without git)

## Canonical Workflow / Commands

Start/resume:

```bash
bd prime
bd status
bd list --status=in_progress
bd ready
```

Create work from a markdown spec:

```bash
python3 skills/public/beads-planner/scripts/import_markdown_spec.py <spec.md> --labels spec,creativeops
```

## Smoke Test

```bash
bd prime
bd status
```

Expected artifacts:
- A status summary printed to stdout (and a bd db initialized/loaded)

## References / Contracts

- Repo workflow notes: `docs/CREATIVEOPS_EPICS_AND_STORIES_V0.1.md`
- Skill importer: `skills/public/beads-planner/scripts/import_markdown_spec.py`

## Session Start (Codex)

Codex has no auto-hook integration for bd in this repo, so do this at the top of a session:

```bash
bd prime
bd status
bd list --status=in_progress
bd ready
```

Then:
- If the user names an issue ID (e.g. `clipper-a3f2dd`), `bd show <id>` and proceed.
- Otherwise, pick from `bd ready`, claim it (`bd update <id> --status=in_progress`), and work it.

## Conventions (this repo)

- **When to use bd vs TODOs**
  - Use `bd` for anything multi-session, dependency-heavy, or “discovered work” you might forget.
  - Use a simple in-session TODO list only for small, single-session execution.

- **Statuses**
  - Use `open`, `in_progress`, `blocked`, `closed`.

- **Types**
  - Use `epic` for big deliverables; children are `feature` or `task`; use `bug` for defects.

- **Labels**
  - Prefer a small set: `creativeops`, `clipops`, `video-clipper`, `spec`, `docs`, `infra`.

## Create Epics + Stories From a Design Spec

Preferred workflow:

1) Create an **epic** for the spec (or identify the existing epic).
2) Create child stories under it (features/tasks) with acceptance criteria.
3) Add explicit dependencies (`bd dep add`) for sequencing.

### Option A: Manual (fast for small specs)

```bash
bd create "<Epic title>" --type epic --priority 2 --json   # copy the returned "id"
bd create "<Story 1>" --type feature --priority 2 --parent <EPIC_ID> --acceptance "..."
bd create "<Story 2>" --type task --priority 2 --parent <EPIC_ID> --acceptance "..."
```

### Option B: From Markdown Spec (recommended for “just tag and move on”)

Use the bundled importer script:

```bash
python3 skills/public/beads-planner/scripts/import_markdown_spec.py docs/specs/my_spec.md --labels spec,creativeops
```

Markdown input format (minimal):

```md
# <Epic title>

## <Story 1 title>
Short description (optional; becomes bd description if no sections exist)

### Type
feature

### Acceptance Criteria
- ...

## <Story 2 title>
### Type
task
### Acceptance Criteria
- ...
```

Notes:
- H1 becomes the epic.
- Each H2 becomes a child issue under that epic.
- H3 sections supported: `Priority`, `Type`, `Description`, `Design`, `Acceptance Criteria`, `Assignee`, `Labels`, `Dependencies`.

## Resume Work (multi-session)

When the user says “resume”, “continue”, “pick up where we left off”, or references ongoing work:

- Run `bd prime`.
- Check `bd list --status=in_progress` and `bd ready`.
- If multiple plausible issues exist, ask the user to pick one (or pick the highest priority / most recently updated and state which ID you’re proceeding with).
- Always update status when you start/finish:
  - start: `bd update <id> --status=in_progress`
  - finish: `bd close <id> --reason "..."`

## Discovered Work (capture and keep moving)

When you find follow-ups, file them immediately so they’re not lost:

```bash
bd create \"<follow-up>\" --type task --priority 3 --deps discovered-from:<current-issue-id>
```
