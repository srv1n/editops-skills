# Aspect Ratio Guide for Short-Form Content

## Platform Requirements (Quick Reference)

| Platform | Aspect | Resolution | Use Case |
|----------|--------|------------|----------|
| TikTok | 9:16 | 1080x1920 | FYP, required |
| Instagram Reels | 9:16 | 1080x1920 | Reels tab, required |
| YouTube Shorts | 9:16 | 1080x1920 | Shorts shelf, required |
| Instagram Feed | 1:1 / 4:5 | 1080x1080 / 1080x1350 | Feed posts |
| LinkedIn | 16:9 / 1:1 | 1920x1080 / 1080x1080 | Professional |

## The 16:9 → 9:16 Problem

Most source content (podcasts, interviews, YouTube) is **16:9 horizontal**.

Converting to **9:16 vertical** crops ~75% of frame width:
- 1920px wide → ~540px wide crop window
- Center cropping WILL cut off subjects not centered in frame
- Podcasts like Huberman often have subject offset from center

**Never blindly center-crop. Always detect subject position first.**

## Smart Cropping Workflow

### Option A: Fast Detection (Recommended for Podcasts)

Uses MediaPipe face detection on a single frame. Very fast, works well for:
- Single speaker podcasts (Huberman, Lex Fridman solo)
- Interviews with static camera
- Talking head content

```bash
python3 scripts/clip_extractor.py video.mp4 \
  --start 100.0 --end 130.0 \
  --vertical --smart-crop \
  -o clip_vertical.mp4
```

### Option B: Full Tracking (For Movement)

Uses SAM to track subject throughout clip. Slower, use when:
- Subject moves significantly within frame
- Camera pans during clip
- Multiple speakers taking turns

```bash
python3 scripts/sam_effects.py clip.mp4 \
  --effect face_zoom --prompt "face" \
  --target-aspect 9:16 \
  -o clip_vertical.mp4
```

### Option C: Manual Position

When auto-detection fails or you want specific framing:

```bash
# --crop-x: 0.0 = far left, 0.5 = center, 1.0 = far right
python3 scripts/clip_extractor.py video.mp4 \
  --start 100.0 --end 130.0 \
  --vertical --crop-x 0.35 \
  -o clip_vertical.mp4
```

## Detection Methods

| Method | Command | Speed | Best For |
|--------|---------|-------|----------|
| MediaPipe Face | `--detect-method mediapipe` | ~50ms | Single face, podcasts |
| MediaPipe Pose | `--detect-method mediapipe-pose` | ~80ms | Full body, fitness |
| YOLO | `--detect-method yolo` | ~100ms | Multiple people |
| SAM | `--detect-method sam` | ~500ms+ | Complex scenes |

**Default:** MediaPipe Face (fast and accurate for most podcast content)

## Common Scenarios

### Single Speaker Podcast (Huberman, Lex solo)
```bash
--vertical --smart-crop
# MediaPipe detects face, crops 9:16 window centered on face
```

### Two-Person Interview
```bash
--vertical --smart-crop --detect-method yolo
# Detects both faces, crops to include primary speaker
# Or use --crop-x to manually select which side
```

### Speaker Not Centered (Common in Podcasts)
The whole point of smart crop—auto-detects offset position:
```bash
# Speaker at 30% from left edge
--vertical --smart-crop
# → Automatically crops with face centered in 9:16 output
```

### Split-Screen Alternative
For interviews where cropping loses too much context:
```bash
python3 scripts/effects.py clip.mp4 \
  --split-screen left=0.0-0.5 right=0.5-1.0 \
  --target-aspect 9:16 \
  -o clip_split.mp4
```

## Output Verification

After creating vertical clips, verify:
1. Subject's face is fully visible
2. No awkward cropping at chin/forehead
3. Sufficient "breathing room" around face
4. Key gestures/movements not cut off

## Quick Decision Tree

```
Is source 16:9 and target vertical (9:16)?
├─ Yes → Is subject moving during clip?
│        ├─ Yes → Use SAM face_zoom
│        └─ No → Use --smart-crop (MediaPipe)
└─ No → Use direct extraction (no conversion needed)
```
