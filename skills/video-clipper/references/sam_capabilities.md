# SAM 3 & SAM 3D Capabilities

Quick reference for available segmentation effects. Use `scripts/sam_effects.py`.

## SAM 3 Effects (Video Segmentation)

### Focus Effects
| Effect | Use Case | Command |
|--------|----------|---------|
| `desaturate_bg` | Podcasts, interviews | `--effect desaturate_bg --prompt "person"` |
| `spotlight` | Drama, product focus | `--effect spotlight --prompt "person"` |
| `face_zoom` | Vertical reformat (16:9→9:16) | `--effect face_zoom --prompt "face" --target-aspect 9:16` |
| `subject_track` | Track subject for smart crop | `--effect subject_track --prompt "person" --target-aspect 9:16` |

### Highlight Effects
| Effect | Use Case | Command |
|--------|----------|---------|
| `contour` | TikTok/gaming aesthetic | `--effect contour --prompt "person"` |
| `bounding_box` | Tech/analytical | `--effect bounding_box --prompt "person"` |
| `motion_trail` | Sports/action | `--effect motion_trail --prompt "person"` |

### Creative Effects
| Effect | Use Case | Command |
|--------|----------|---------|
| `clone_squad` | Comedy/memes | `--effect clone_squad --prompt "person" --clone-count 3` |
| `green_screen` | Custom backgrounds | `--effect green_screen --prompt "person" --bg-image bg.jpg` |
| `blur_face` | Privacy | `--effect blur_face --prompt "face"` |

## SAM 3D Effects (3D Reconstruction)

### Object Effects
| Effect | Use Case | Command |
|--------|----------|---------|
| `object_3d_glow` | Product reveals | `--effect object_3d_glow --prompt "handbag"` |
| `object_3d_isolate` | Hero shots | `--effect object_3d_isolate --prompt "watch" --object-3d-scale 1.5` |

### Body Effects
| Effect | Use Case | Command |
|--------|----------|---------|
| `body_pose_overlay` | Fitness form check | `--effect body_pose_overlay --prompt "person"` |
| `body_silhouette` | Dance/movement | `--effect body_silhouette --prompt "person"` |

## Customization Options

```bash
# Contour colors
--contour-color 0,255,255      # Cyan (tech)
--contour-color 255,0,255      # Magenta (gaming)
--contour-color 255,215,0      # Gold (luxury)

# Spotlight intensity
--spotlight-intensity 0.3      # Subtle
--spotlight-intensity 0.1      # Dramatic

# Face zoom padding
--zoom-padding 0.3             # Tight
--zoom-padding 0.5             # Breathing room

# Target aspect ratios (for face_zoom and subject_track)
--target-aspect 9:16           # TikTok, Reels, Shorts
--target-aspect 1:1            # Instagram feed square
--target-aspect 4:5            # Instagram feed portrait

# Subject detection method (for smart cropping)
--detect-method mediapipe      # Fast, good for single face (default)
--detect-method yolo           # Better for multiple people
--detect-method sam            # Most accurate, slower
```

## Common Object Prompts

For product content: `"handbag"`, `"watch"`, `"shoes"`, `"phone"`, `"laptop"`, `"jewelry"`, `"dress"`

## Full Command Example

```bash
python3 scripts/sam_effects.py input.mp4 \
  --effect desaturate_bg \
  --prompt "person" \
  -o output.mp4
```
