# EditOps Skills

Deterministic agent skills for video editing pipelines: clipping, app demos, promos, grading, motion graphics, and App Store creative workflows.

## Quick Start (macOS)

For a local macOS install with Python, Bun, FFmpeg, yt-dlp, and a repo-scoped virtualenv:

```bash
./install.sh
source .venv/bin/activate
```

This bootstrap:
- installs Homebrew packages from `install/macos/Brewfile`
- creates `.venv/` with `uv`
- installs Python requirements for the common editing flows
- installs Bun deps for the bundled MapLibre renderers
- runs `tools/editops_doctor.py`

Current bootstrap support:

| Platform | Status |
|----------|--------|
| macOS (Apple Silicon / Intel) | supported |
| Linux / Windows | planned |

## Installation

### Claude Code

```bash
# Install all skills
claude plugin add editops-skills

# Or copy individual skill folders to .claude/skills/
cp -r skills/video-clipper ~/.claude/skills/
```

### Manual

Each skill folder is self-contained. Copy any folder from `skills/` into your agent's skill directory.

## Releases

GitHub Releases (tags like `v0.1.0`) publish:
- Per-skill zip archives (`<skill>-<tag>.zip`)
- Bundle zips (`editops-core`, `creativeops`, `all-skills`)
- `SHA256SUMS.txt` for integrity verification

Build locally:

```bash
python3 tools/skills_release.py lint
python3 tools/skills_release.py build-zips --out dist --tag v0.1.0
```

## Skills

### Core

| Skill | Description |
|-------|-------------|
| [video-clipper](skills/video-clipper/) | Extract viral clips from YouTube videos |
| [clipops-runner](skills/clipops-runner/) | Run ClipOps render pipeline |
| [editops-orchestrator](skills/editops-orchestrator/) | Route requests to the right EditOps skill |

### Creative Operations

| Skill | Description |
|-------|-------------|
| [creativeops-director](skills/creativeops-director/) | Convert producer artifacts into ClipOps plans |
| [creativeops-producer](skills/creativeops-producer/) | Produce run directories from storyboards |
| [creativeops-producer-ios](skills/creativeops-producer-ios/) | iOS simulator demo recording |
| [creativeops-grade](skills/creativeops-grade/) | Color grading and LUT application |
| [promo-director](skills/promo-director/) | Promo video editing and compilation |

### Templates & Design

| Skill | Description |
|-------|-------------|
| [motion-templates](skills/motion-templates/) | Motion graphics template catalog |
| [texture-studio](skills/texture-studio/) | Texture and color preset generation |
| [theme-library](skills/theme-library/) | Theme configuration management |

### App Store

| Skill | Description |
|-------|-------------|
| [appstore-creatives-orchestrator](skills/appstore-creatives-orchestrator/) | Orchestrate App Store creative workflows |
| [appstore-swiss-grid](skills/appstore-swiss-grid/) | Swiss grid layout for App Store screenshots |

### Utilities

| Skill | Description |
|-------|-------------|
| [beads-planner](skills/beads-planner/) | Epic/story planning from design specs |
| [remotion-best-practices](skills/remotion-best-practices/) | Remotion video framework best practices |
| [beat-analyzer](skills/beat-analyzer/) | Audio beat detection and analysis |
| [music-generator](skills/music-generator/) | AI music generation workflows |
| [unified-director](skills/unified-director/) | Unified directing across all pipelines |

## License

MIT License. See [LICENSE.txt](LICENSE.txt).
