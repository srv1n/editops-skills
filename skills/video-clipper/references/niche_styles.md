# Niche-Specific Style Guide

When clipping videos, choose effects based on the content vertical. Each niche has distinct audience expectations and visual language.

## Content Verticals

### 1. Educational / Podcast (Huberman, Lex Fridman, etc.)

**Audience**: Knowledge seekers, professionals, self-improvement focused
**Watch context**: Often multitasking, need clear audio/subtitles

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `desaturate_bg` | Default choice - clean, professional, focuses on speaker |
| `spotlight` | For dramatic moments, key insights |
| `face_zoom` | Converting long-form horizontal to vertical Shorts |

**Subtitle Style**: `bold` or `karaoke` - large, readable
**Hook Position**: `top` - "The ONE thing that..." / "Why experts say..."
**Contour Color**: White or subtle blue (professional, not flashy)

**What Works**:
- Thought-provoking insights that challenge conventional wisdom
- "Did you know..." moments
- Specific data points ("8 hours of sleep is a myth")
- Contrarian takes from experts

**Clip Structure**:
```
[0-2s]  Hook text overlay
[2-25s] Core insight with karaoke subtitles
[25-30s] End on tension - cut before resolution
```

---

### 2. Fashion / Luxury / Product (Kim K, Influencers, Brand content)

**Audience**: Aspirational shoppers, trend followers
**Watch context**: Leisure browsing, discovery mode

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `contour` (gold/white) | Highlight product being discussed |
| `spotlight` | Isolate product from busy background |
| `green_screen` | Place product in clean/luxury environment |

**SAM 3D Use Case**: Isolate handbag/product → 3D reconstruction → Rotate/showcase
**Object Prompts**: `"handbag"`, `"watch"`, `"shoes"`, `"jewelry"`, `"dress"`

**Subtitle Style**: `minimal` or `classic` - don't distract from visuals
**Hook Position**: `bottom` - let product dominate frame
**Contour Color**: Gold (`255,215,0`) or white - premium feel

**What Works**:
- Quick outfit transitions (pattern interrupts)
- Product reveals with dramatic lighting
- "Get ready with me" clips
- Price reveals / "Is it worth it?"

**Editing Techniques**:
- Fast cuts synced to beat
- Slow-mo for fabric/texture details
- Walking shots at 2x speed
- Above-angle shots for outfit details

---

### 3. Business / Entrepreneurship (Hormozi, GaryVee, Founders)

**Audience**: Aspiring entrepreneurs, business owners
**Watch context**: Motivation seeking, lunch breaks, commute

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `desaturate_bg` | Default - professional authority look |
| `spotlight` | For "drop the mic" moments |
| `bounding_box` | When referencing data/charts shown |

**Subtitle Style**: `bold` - Hormozi-style large text
**Hook Position**: `top` - "$0 to $10M in 18 months"
**Hook Style**: Specific numbers beat vague claims

**What Works**:
- Specific revenue/growth numbers ("$2M ARR in 3 months")
- Unconventional strategies that worked
- Failure stories with lessons
- Predictions about markets
- Criticism of popular advice

**Clip Structure**:
```
[0s]    Specific number/claim as hook
[1-40s] Story with tension
[40-45s] Lesson/takeaway
```

---

### 4. Parenting / Mom Content (Pregnancy, Kids, Family)

**Audience**: Parents, expecting parents, family-focused
**Watch context**: Quick breaks, late night feeds, relatable scrolling

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `spotlight` | Intimate moments, confessions |
| `blur_face` | Protecting kids' privacy in crowd scenes |
| `contour` (soft pink/blue) | Gentle highlight for baby content |

**Subtitle Style**: `classic` or `boxed` - readable but warm
**Hook Position**: `top` - "No one told me about..."
**Contour Color**: Soft pastels - pink (`255,182,193`), blue (`173,216,230`)

**What Works**:
- "Real talk" confessions about parenting
- Unexpected moments (kids saying funny things)
- Before/after (pregnancy journey, nursery setup)
- Product recommendations with real results
- Relatable struggles ("3am thoughts")

**Privacy Consideration**: Always offer `blur_face` for children in background

---

### 5. Fitness / Health / Transformation

**Audience**: Goal-oriented, visual results focused
**Watch context**: Pre/post workout, motivation

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `motion_trail` | Exercise demonstrations, form checks |
| `bounding_box` | Highlighting muscle groups, form |
| `spotlight` | Before/after reveals |
| `face_zoom` | Talking head motivation clips |

**Subtitle Style**: `bold` - high energy
**Hook Position**: `center` for transformations, `top` for tips
**Contour Color**: Energetic - cyan, green, orange

**What Works**:
- Transformation reveals
- "One exercise that changed everything"
- Form corrections with visual guides
- Day-in-the-life routines
- Myth-busting

---

### 6. Comedy / Entertainment / Reactions

**Audience**: Entertainment seekers, sharers
**Watch context**: Pure leisure, social sharing

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `clone_squad` | Visual gags, "me vs my thoughts" |
| `contour` (neon colors) | Gaming/meme aesthetic |
| `motion_trail` | Exaggerated movements for comedy |

**Subtitle Style**: `karaoke` - dramatic word reveals
**Hook Position**: `center` during punchlines
**Contour Color**: Neon magenta (`255,0,255`), cyan (`0,255,255`)

**What Works**:
- Punchlines with minimal setup
- Reaction faces (isolated moments)
- Unexpected twists
- Relatable situations exaggerated

---

### 7. Tech / Tutorial / How-To

**Audience**: Problem-solvers, learners
**Watch context**: Active learning, following along

**Recommended Effects**:
| Effect | When to Use |
|--------|-------------|
| `bounding_box` | Highlighting UI elements, products |
| `spotlight` | Focus on specific area being discussed |
| `contour` | Tech aesthetic, clean lines |

**Object Prompts**: `"phone"`, `"laptop"`, `"screen"`, `"hand"`

**Subtitle Style**: `classic` - clear, doesn't block demo
**Hook Position**: `top` - "How to fix X in 30 seconds"
**Contour Color**: Cyan (tech feel), white (clean)

**What Works**:
- Quick solutions to common problems
- "I bet you didn't know this feature"
- Comparisons (before/after using tip)
- Tool/app reveals

---

## SAM 3D: Object Isolation & 3D Reconstruction

For product-focused content, SAM 3D can isolate and reconstruct objects in 3D.

### When to Use SAM 3D

| Scenario | Workflow |
|----------|----------|
| Showcase a specific product | Isolate → 3D reconstruct → Rotate view |
| Replace cluttered background | Isolate product → Green screen → Clean bg |
| Product comparison | Isolate both → Side-by-side 3D views |
| "What's in my bag" content | Isolate each item → Animated reveal |

### Object Prompts for SAM 3

```
Fashion:     "handbag", "shoes", "watch", "sunglasses", "dress", "jewelry"
Tech:        "phone", "laptop", "headphones", "camera", "gadget"
Beauty:      "lipstick", "perfume bottle", "makeup palette"
Home:        "furniture", "lamp", "vase", "plant"
Food:        "plate", "drink", "coffee cup"
```

### SAM 3D Commands (when available)

```bash
# Isolate product for 3D reconstruction
python3 scripts/sam_effects.py video.mp4 --effect isolate_3d --prompt "handbag" -o handbag_3d.obj

# Create rotating product showcase
python3 scripts/sam_effects.py video.mp4 --effect product_spin --prompt "watch" -o watch_showcase.mp4
```

---

## Quick Reference: Effect by Goal

| Goal | Best Effect | Best Prompt |
|------|-------------|-------------|
| Professional authority | `desaturate_bg` | "person" |
| Dramatic moment | `spotlight` | "person" |
| Product showcase | `contour` + gold | product name |
| Action/movement | `motion_trail` | "person", "hand" |
| Privacy protection | `blur_face` | "face" |
| Comedy/meme | `clone_squad` | "person" |
| Horizontal→Vertical | `face_zoom` | "face" |
| Tech demo | `bounding_box` | "hand", "phone" |
| Gaming/neon aesthetic | `contour` + magenta | "person" |

---

## Platform-Specific Notes

| Platform | Max Length | Best Aspect | Notes |
|----------|------------|-------------|-------|
| TikTok | 10 min | 9:16 | Hooks in 0-2s critical |
| YouTube Shorts | 60s | 9:16 | Discovery-focused |
| Instagram Reels | 90s | 9:16 / 1:1 | Aesthetic matters more |
| LinkedIn | 10 min | 1:1 | Professional, square works |
| X/Twitter | 2:20 | 1:1 | Punchy, shareable |

---

## Combining Effects Workflow

```bash
# Example: Huberman-style educational clip
# 1. Extract clip
python3 scripts/clip_extractor.py source.mp4 --start 120 --end 150 --vertical -o clip.mp4

# 2. Apply focus effect
python3 scripts/sam_effects.py clip.mp4 --effect desaturate_bg --prompt "person" -o clip_styled.mp4

# 3. Add text overlays
python3 scripts/effects.py clip_styled.mp4 \
  --subtitles transcript.json \
  --start-offset 120 \
  --subtitle-style bold \
  --hook-text "The sleep myth doctors won't tell you" \
  --hook-position top \
  -o clip_final.mp4
```

```bash
# Example: Fashion product showcase
# 1. Isolate the product with spotlight
python3 scripts/sam_effects.py clip.mp4 --effect spotlight --prompt "handbag" -o clip_product.mp4

# 2. Add subtle contour for premium feel
python3 scripts/sam_effects.py clip_product.mp4 --effect contour --prompt "handbag" \
  --contour-color 255,215,0 --contour-thickness 2 -o clip_styled.mp4

# 3. Minimal text - let product shine
python3 scripts/effects.py clip_styled.mp4 \
  --hook-text "The $3,000 bag everyone's talking about" \
  --hook-position bottom \
  --subtitle-style minimal \
  -o clip_final.mp4
```
