# Clipper Skills

AI agent skills for video clipping, creative operations, and app store content workflows.

## Installation

### Claude Code

```bash
# Install all skills
claude plugin add clipper-skills

# Or copy individual skill folders to .claude/skills/
cp -r skills/video-clipper ~/.claude/skills/
```

### Manual

Each skill folder is self-contained. Copy any folder from `skills/` into your agent's skill directory.

## Skills

### Core

| Skill | Description |
|-------|-------------|
| [video-clipper](skills/video-clipper/) | Extract viral clips from YouTube videos |
| [clipops-runner](skills/clipops-runner/) | Run ClipOps render pipeline |
| [clipper-orchestrator](skills/clipper-orchestrator/) | Route requests to the right clipper skill |

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
