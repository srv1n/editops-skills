---
name: remotion-best-practices
description: "Best practices for Remotion (React-based video rendering). Use when editing Remotion projects/compositions or debugging timing, assets, and render performance."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Applies to Remotion projects (Node tooling). No special runtime is required for reading rules; applying changes requires the project’s normal Node toolchain."
metadata:
  author: Clipper
  version: "0.1.0"
  category: remotion
  tags: [remotion, video, react, animation, composition]
---

# Remotion Best Practices

## Overview

Domain knowledge for working on Remotion projects (React-based video rendering): composition structure, asset handling, timing, animations, and performance-safe patterns.

## When to Use (Triggers)

Use this skills whenever you are dealing with Remotion code to obtain the domain-specific knowledge.

## Inputs

Required:
- A Remotion project (or Remotion-based codebase) to edit.

## Outputs

- Updated Remotion code following best practices (compositions, sequences, timing, assets).
- Fewer render failures/perf regressions (by using safe patterns).

## Safety / Security

- Treat third-party Remotion deps and code snippets as untrusted; prefer well-maintained libraries and pinned versions.
- Avoid embedding secrets into rendered outputs or public assets; keep tokens in env vars.
- When changing render code, prioritize deterministic behavior and bounded resource usage (memory, CPU) to avoid CI failures.

## Canonical Workflow / Commands

Pick the relevant rule file(s) below and apply the patterns to the target code.

## Smoke Test

```bash
ls rules | head
```

Expected artifacts:
- A list of rule markdown files printed to stdout.

## References / Contracts

Rule index (open these for detailed explanations and examples):

Read individual rule files for detailed explanations and code examples:

- Trigger tests: `references/TRIGGER_TESTS.md`
- [rules/3d.md](rules/3d.md) - 3D content in Remotion using Three.js and React Three Fiber
- [rules/animations.md](rules/animations.md) - Fundamental animation skills for Remotion
- [rules/assets.md](rules/assets.md) - Importing images, videos, audio, and fonts into Remotion
- [rules/audio.md](rules/audio.md) - Using audio and sound in Remotion - importing, trimming, volume, speed, pitch
- [rules/calculate-metadata.md](rules/calculate-metadata.md) - Dynamically set composition duration, dimensions, and props
- [rules/can-decode.md](rules/can-decode.md) - Check if a video can be decoded by the browser using Mediabunny
- [rules/charts.md](rules/charts.md) - Chart and data visualization patterns for Remotion
- [rules/compositions.md](rules/compositions.md) - Defining compositions, stills, folders, default props and dynamic metadata
- [rules/display-captions.md](rules/display-captions.md) - Displaying captions in Remotion with TikTok-style pages and word highlighting
- [rules/extract-frames.md](rules/extract-frames.md) - Extract frames from videos at specific timestamps using Mediabunny
- [rules/fonts.md](rules/fonts.md) - Loading Google Fonts and local fonts in Remotion
- [rules/get-audio-duration.md](rules/get-audio-duration.md) - Getting the duration of an audio file in seconds with Mediabunny
- [rules/get-video-dimensions.md](rules/get-video-dimensions.md) - Getting the width and height of a video file with Mediabunny
- [rules/get-video-duration.md](rules/get-video-duration.md) - Getting the duration of a video file in seconds with Mediabunny
- [rules/gifs.md](rules/gifs.md) - Displaying GIFs synchronized with Remotion's timeline
- [rules/images.md](rules/images.md) - Embedding images in Remotion using the Img component
- [rules/import-srt-captions.md](rules/import-srt-captions.md) - Importing .srt subtitle files into Remotion using @remotion/captions
- [rules/lottie.md](rules/lottie.md) - Embedding Lottie animations in Remotion
- [rules/measuring-dom-nodes.md](rules/measuring-dom-nodes.md) - Measuring DOM element dimensions in Remotion
- [rules/measuring-text.md](rules/measuring-text.md) - Measuring text dimensions, fitting text to containers, and checking overflow
- [rules/sequencing.md](rules/sequencing.md) - Sequencing patterns for Remotion - delay, trim, limit duration of items
- [rules/tailwind.md](rules/tailwind.md) - Using TailwindCSS in Remotion
- [rules/text-animations.md](rules/text-animations.md) - Typography and text animation patterns for Remotion
- [rules/timing.md](rules/timing.md) - Interpolation curves in Remotion - linear, easing, spring animations
- [rules/transcribe-captions.md](rules/transcribe-captions.md) - Transcribing audio to generate captions in Remotion
- [rules/transitions.md](rules/transitions.md) - Scene transition patterns for Remotion
- [rules/trimming.md](rules/trimming.md) - Trimming patterns for Remotion - cut the beginning or end of animations
- [rules/videos.md](rules/videos.md) - Embedding videos in Remotion - trimming, volume, speed, looping, pitch
