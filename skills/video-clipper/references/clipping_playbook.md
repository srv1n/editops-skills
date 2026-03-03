# Viral Clipping Playbook System

## Architecture Overview

```
Source Content → Transcript + Audio Analysis → Pattern Detection → Playbook Match → Treatment Application → Output
                         ↓
              Cross-Index Database (topic tags, audience tags, emotional markers)
```

---

## Part 1: Detection Signals

These are the signals you extract from transcripts and audio to identify clip-worthy moments and match them to playbooks.

### 1.1 Transcript-Level Signals

**Linguistic Markers (detect via NLP)**
- Superlatives: "the most", "never", "always", "worst", "best", "only"
- Contrarian openers: "Actually...", "Here's what people don't understand...", "Everyone thinks X but..."
- Confession signals: "I've never told anyone this", "The truth is", "I made a huge mistake"
- Revelation markers: "What I realized was", "That's when it hit me", "The secret is"
- Controversy triggers: "I don't care if people hate me for this", "This might offend some people"
- Authority claims: "As someone who has...", "In my 20 years of..."
- Emotional intensifiers: "I was terrified", "I couldn't believe", "It destroyed me"
- Rhetorical questions: Any "?" followed by immediate answer
- List openers: "There are three things...", "The first thing you need to know..."
- Story beats: "So there I was", "Picture this", "Let me tell you what happened"

**Structural Markers**
- Short sentences after long ones (emphasis pattern)
- Repeated phrases (speaker drilling a point)
- Name drops (celebrities, companies, known figures)
- Number specificity ("$47,000", "3 years", "127 people")
- Time markers indicating story climax ("And then", "Finally", "At that moment")

### 1.2 Audio-Level Signals

**Prosodic Features (extract from audio waveform)**
- Volume spikes (>20% above baseline = emphasis)
- Pitch elevation (excitement, surprise)
- Speech rate changes:
  - Acceleration = building tension
  - Deceleration = emphasis on key point
- Pause patterns:
  - Long pause before statement = dramatic setup
  - Pause after statement = letting it land
- Laughter (speaker or audience) = potential humor clip
- Voice breaks/cracks = emotional moment
- Whisper/quiet speech followed by normal = intimacy/secret sharing

**Multi-Speaker Signals (with diarization)**
- Interruptions = conflict/excitement
- Overlapping speech = agreement enthusiasm or disagreement
- Long silence from one speaker while other talks = absorption (potential wisdom moment)
- Quick back-and-forth = dynamic energy
- "Wait, what?" or "Say that again" = rewind moment (the other person flagging importance)

### 1.3 Scoring Formula

For each potential clip moment, calculate:

```
Viral Score = (Linguistic Signals × 2) + (Audio Spikes × 3) + (Topic Relevance × 1.5) + (Controversy Potential × 2.5)

Where:
- Linguistic Signals: Count of markers in 30-second window (0-10)
- Audio Spikes: Count of prosodic events (0-10)
- Topic Relevance: Match to trending topics/evergreen topics (0-10)
- Controversy Potential: Likelihood of comment debate (0-10)
```

Clips scoring >25 = High priority
Clips scoring 15-25 = Medium priority
Clips scoring <15 = Low priority (but may work in compilations)

---

## Part 2: The Playbooks

### Playbook 01: THE HOT TAKE

**Detection Triggers:**
- Contrarian language markers
- Confident tone (no hedging words like "maybe", "I think")
- Statement that contradicts common belief
- Often preceded by setup like "Here's what nobody talks about"

**Ideal Length:** 15-25 seconds

**Structure:**
```
[0-2s]   Hook: Start mid-statement at the controversial claim
[2-15s]  Core: The full hot take with reasoning
[15-25s] Exit: Cut before they soften or qualify (leave it raw)
```

**Visual Treatment:**
- Subtitle style: BOLD (Hormozi style) - large text, center screen
- Zoom: Slow push-in throughout (builds intensity)
- Color grade: Slightly higher contrast
- SAM3 effect: `desaturate_bg` or `spotlight`

**Hook Text Formula:**
"[Speaker] just said [controversial topic] and people are LOSING IT"
OR
"This [profession/identity] just destroyed [common belief]"

**Thumbnail:**
- Face: Intense expression, direct eye contact
- Text: 2-3 words of the hot take
- Visual element: X over the thing they're contradicting

**Cross-Index Tags:**
`#hottake` `#controversial` `#opinion` `[topic]`

---

### Playbook 02: THE VULNERABILITY MOMENT

**Detection Triggers:**
- Confession language ("I've never told anyone", "The truth is")
- Voice quality changes (softer, slower, possible breaks)
- Past tense personal stories with emotional weight
- Audience/interviewer goes quiet (long gaps in diarization)

**Ideal Length:** 30-45 seconds

**Structure:**
```
[0-3s]   Hook: The admission/confession moment
[3-35s]  Core: The story with emotional buildup
[35-45s] Exit: End on the insight or the weight of it (not the resolution)
```

**Visual Treatment:**
- Subtitle style: Classic (clean, readable, not distracting)
- Zoom: Face zoom, slow, intimate
- Color grade: Slightly desaturated, warmer tones
- SAM3 effect: `spotlight` with soft feathering
- Optional: Subtle vignette

**Hook Text Formula:**
"[Speaker] finally opened up about [topic]"
OR
"This broke [him/her]"

**Cross-Index Tags:**
`#vulnerable` `#emotional` `#story` `#real` `[topic]`

---

### Playbook 03: THE WISDOM DROP

**Detection Triggers:**
- Authority language ("In my experience", "What I've learned")
- Instructional structure (problem → insight → action)
- Slow, deliberate speech patterns
- Often ends with memorable one-liner
- Spiritual/philosophical vocabulary

**Ideal Length:** 20-35 seconds

**Structure:**
```
[0-2s]   Hook: The insight or lesson (end of the thought delivered first)
[2-25s]  Core: The explanation/context
[25-35s] Exit: Restate or land on the quotable moment
```

**Visual Treatment:**
- Subtitle style: Karaoke (word-by-word highlight for emphasis)
- Zoom: Minimal, stable frame OR very slow drift
- Color grade: Clean, natural
- SAM3 effect: `desaturate_bg` - professional, focused

**Hook Text Formula:**
"[Speaker] on [life topic] 🔥"
OR
"This advice is worth more than [expensive thing]"

**Cross-Index Tags:**
`#wisdom` `#advice` `#motivation` `#mindset` `[specific topic]`

**Cross-Source Potential:** HIGH - ideal for combining wisdom from multiple speakers

---

### Playbook 04: THE REVELATION

**Detection Triggers:**
- "That's when I realized", "What I discovered"
- Narrative structure: Setup → Turning point → Insight
- Often includes specific numbers or facts that surprise
- Interviewer reaction sounds ("Wow", "Wait really?")

**Ideal Length:** 25-40 seconds

**Structure:**
```
[0-3s]   Hook: The revelation itself (spoil the "ending")
[3-30s]  Core: How they got there / the context
[30-40s] Exit: Return to the revelation with weight
```

**Visual Treatment:**
- Subtitle style: Karaoke with color emphasis on key words
- Zoom: Pulse zoom on the revelation moment
- SAM3 effect: `spotlight` with intensity pulse
- Sound effect: Subtle bass hit on key reveal (optional)

**Hook Text Formula:**
"[Speaker] discovered something that changes everything about [topic]"
OR
"This [fact/number] will blow your mind"

**Cross-Index Tags:**
`#revelation` `#facts` `#mindblown` `[topic]`

---

### Playbook 05: THE CONFRONTATION

**Detection Triggers:**
- Rapid speaker switching in diarization
- Interruptions
- Disagreement language ("No, that's not right", "I disagree")
- Rising pitch/volume from both speakers
- Tension markers ("Let me finish", "Hold on")

**Ideal Length:** 20-35 seconds

**Structure:**
```
[0-2s]   Hook: The clash moment (the disagreement statement)
[2-30s]  Core: The back-and-forth
[30-35s] Exit: Cut at tension peak (NOT at resolution)
```

**Visual Treatment:**
- Subtitle style: Bold, possibly different colors for each speaker
- Zoom: Quick cuts between speakers OR split screen
- SAM3 effect: `contour` with different colors per person
- Pacing: Faster cuts on beat with exchanges

**Hook Text Formula:**
"[Speaker A] and [Speaker B] go AT IT over [topic]"
OR
"This got HEATED 🔥"

**Cross-Index Tags:**
`#debate` `#confrontation` `#beef` `#disagreement` `[topic]`

---

### Playbook 06: THE HUMBLE BRAG / FLEX

**Detection Triggers:**
- Numbers (money, followers, achievements)
- Casual delivery of impressive facts
- "I was just..." followed by extraordinary thing
- Name drops of impressive people/places

**Ideal Length:** 15-25 seconds

**Structure:**
```
[0-2s]   Hook: The flex moment itself
[2-20s]  Core: Context that makes it land
[20-25s] Exit: Cut before they downplay it
```

**Visual Treatment:**
- Subtitle style: Bold with number/achievement emphasized
- Zoom: Quick zoom-in on face when flex lands
- SAM3 effect: `spotlight` or `contour` (gold)
- Optional: B-roll of the thing they're flexing

**Hook Text Formula:**
"[Speaker] casually drops that they [impressive thing]"
OR
"POV: [Speaker] tells you how they [achievement]"

**Cross-Index Tags:**
`#success` `#money` `#achievement` `#flex` `[industry]`

---

### Playbook 07: THE STORY CLIMAX

**Detection Triggers:**
- Narrative structure leading to peak
- "And then..." followed by dramatic statement
- Voice intensity peak in audio
- Audience gasps or reactions
- Resolution language ("That's when everything changed")

**Ideal Length:** 30-60 seconds

**Structure:**
```
[0-3s]   Hook: Tease the climax or start IN the action
[3-50s]  Core: The story building to climax
[50-60s] Exit: The moment it lands (or cut just before resolution)
```

**Visual Treatment:**
- Subtitle style: Classic or Karaoke
- Zoom: Build zoom through story, release at climax
- SAM3 effect: `spotlight` building intensity
- Pacing: Match cuts to story beats

**Hook Text Formula:**
"[Speaker] tells the story of [dramatic event]"
OR
"Wait for it... 😳"

**Cross-Index Tags:**
`#story` `#storytime` `#dramatic` `[topic/theme]`

---

### Playbook 08: THE INSTRUCTIONAL GEM

**Detection Triggers:**
- Instructional language ("Here's how", "Step one", "The key is")
- Specific, actionable advice
- Numbered lists in speech
- "Write this down" or "Remember this"

**Ideal Length:** 25-40 seconds

**Structure:**
```
[0-2s]   Hook: The promise of what they'll learn
[2-35s]  Core: The actual instruction
[35-40s] Exit: Summary or call-back to benefit
```

**Visual Treatment:**
- Subtitle style: Karaoke with key terms highlighted
- Zoom: Stable with slight push-in
- SAM3 effect: `bounding_box` for demos, `desaturate_bg` for talking head
- Text overlays: Numbered points appearing on screen

**Hook Text Formula:**
"How to [desirable outcome] in [timeframe/steps]"
OR
"[Expert/Speaker] reveals their [process/secret]"

**Cross-Index Tags:**
`#howto` `#tutorial` `#advice` `#tips` `[specific skill/topic]`

---

### Playbook 09: THE LAUGH MOMENT

**Detection Triggers:**
- Laughter in audio (speaker and/or audience)
- Punchline patterns (setup → pause → delivery)
- Absurdist statements
- Self-deprecating language

**Ideal Length:** 15-30 seconds

**Structure:**
```
[0-2s]   Hook: Start just before the punchline setup
[2-25s]  Core: Setup and delivery
[25-30s] Exit: Natural laugh beat (don't cut too fast)
```

**Visual Treatment:**
- Subtitle style: Classic or Boxed
- Zoom: Pulse on punchline
- SAM3 effect: `clone_squad` for visual gags, minimal for pure comedy
- Keep it clean—don't over-edit comedy

**Hook Text Formula:**
"[Speaker] has NO filter 😂"
OR
"I can't believe [he/she] said this"

**Cross-Index Tags:**
`#funny` `#comedy` `#lol` `#nofilter`

---

### Playbook 10: THE DELIBERATE ERROR BAIT

**Detection Triggers:**
- Factual claims that are slightly wrong
- Mispronunciations of known terms
- Obvious mistakes that experts would catch

**Note:** Select clips where the speaker makes minor errors. Comments = engagement = reach.

**Ideal Length:** 15-25 seconds

**Structure:**
```
[0-2s]   Hook: The error itself
[2-20s]  Core: The context where error lives
[20-25s] Exit: Don't correct it—let comments do the work
```

**Visual Treatment:**
- Subtitle style: Bold (emphasize the wrong thing)
- SAM3 effect: Standard `desaturate_bg`

**Hook Text Formula:**
"Wait... did [Speaker] really just say [wrong thing]?"

**Cross-Index Tags:**
`#fail` `#wrong` `#cringe` `[topic]`

---

### Playbook 11: THE OBJECT FEATURE (SAM3 + SAM3D Enhanced)

**Detection Triggers:**
- Object mentions in transcript ("this watch", "my car", "this bag")
- Product placement moments
- Show-and-tell patterns
- Tech/fashion/lifestyle reveals

**Ideal Length:** 20-35 seconds

**Structure:**
```
[0-2s]   Hook: Object reveal or claim about it
[2-30s]  Core: Discussion/feature explanation
[30-35s] Exit: Final glamour shot or reaction
```

**Visual Treatment (SAM3 + SAM3D):**
- Use SAM3 to segment the object from background
- Use SAM3D Objects for 3D reconstruction
- Apply 3D rotation/zoom on isolated object
- Picture-in-picture: Speaker in corner, object featured
- Glow/highlight effect on object edges

**SAM3D Enhancement Pipeline:**
```
1. Detect object in frame
2. SAM3 segmentation → mask
3. SAM3D Objects → 3D mesh reconstruction
4. Render 3D rotation/spin
5. Composite back with glow effect
```

**Hook Text Formula:**
"The [price/status] [object] everyone's talking about"
OR
"[Speaker] shows off their [object]"

**Cross-Index Tags:**
`#luxury` `#tech` `#fashion` `#unboxing` `[brand]` `[object type]`

---

### Playbook 12: THE COMPILATION FORMAT

**For Cross-Indexed Content**

This is a meta-playbook for combining clips from multiple sources.

**Structure Options:**

**A) Agreement Compilation**
```
[0-5s]   Hook: "4 experts agree on [topic]"
[5-45s]  Clips from each expert (8-12s each)
[45-50s] Exit: Return to first speaker or summary overlay
```

**B) Disagreement Compilation**
```
[0-5s]   Hook: "They can't agree on [topic]"
[5-50s]  Alternating clips showing different views
[50-55s] Exit: "What do YOU think?" CTA
```

**Visual Treatment:**
- Name/title lower thirds for each speaker
- Consistent subtitle style across all clips
- SAM3 effect: Same treatment per clip for consistency

**Cross-Index Query Example:**
```sql
topic = "morning routine" AND
audience_fit includes "entrepreneurs" AND
playbook_match in ["03_wisdom", "08_instructional"]
```

---

## Part 3: Audience Targeting Matrix

### Audience: New Mothers
**Primary Playbooks:** 02 (Vulnerability), 03 (Wisdom), 08 (Instructional)
**Topics:** Sleep deprivation, postpartum mental health, baby development, self-care
**Hook Tone:** Empathetic, validating, "you're not alone"

### Audience: Entrepreneurs
**Primary Playbooks:** 01 (Hot Take), 05 (Confrontation), 06 (Flex), 08 (Instructional)
**Topics:** Failure stories, funding, time management, tactics
**Hook Tone:** Aggressive, challenge-oriented

### Audience: Spiritual Seekers
**Primary Playbooks:** 02 (Vulnerability), 03 (Wisdom), 07 (Story)
**Topics:** Meditation, purpose, consciousness, suffering
**Hook Tone:** Contemplative, question-posing

### Audience: Health Optimizers
**Primary Playbooks:** 03 (Wisdom), 04 (Revelation), 08 (Instructional)
**Topics:** Sleep, nutrition, supplements, longevity
**Hook Tone:** Scientific, protocol-focused

---

## Part 4: Cross-Index Database Schema

```json
{
  "clip_id": "uuid",
  "source": {
    "video_id": "youtube_id",
    "channel": "channel_name",
    "title": "video_title"
  },
  "timing": {
    "start": 1234.56,
    "end": 1256.78,
    "duration": 22.22
  },
  "transcript_segment": "full text of clip",
  "analysis": {
    "viral_score": 27.5,
    "playbook_match": "01_hot_take",
    "confidence": 0.85
  },
  "tags": {
    "topics": ["parenting", "anxiety"],
    "sentiment": "vulnerable",
    "audience_fit": ["new_mothers", "parents"]
  },
  "audio_markers": {
    "volume_peaks": [1240.2, 1252.1],
    "laughter": [],
    "pauses": [1245.0]
  },
  "objects_detected": ["handbag", "watch"],
  "sam3d_available": true
}
```

---

## Appendix A: Linguistic Marker Regex Patterns

```python
CONTRARIAN = r"(actually|here's what people don't|everyone thinks|most people believe|the truth is|nobody talks about)"
CONFESSION = r"(i've never told|i haven't shared|the truth is|i made a (huge|big) mistake|i was wrong about)"
REVELATION = r"(what i realized|that's when i|it hit me|i discovered|the secret is|here's what changed)"
AUTHORITY = r"(as someone who has|in my \d+ years|having (done|built|grown|sold))"
EMOTIONAL = r"(terrified|couldn't believe|destroyed|devastated|blown away|changed my life)"
INSTRUCTIONAL = r"(here's how|step (one|1|two|2)|the key is|write this down|remember this)"
STORY_CLIMAX = r"(and then|that's when|at that moment|everything changed|finally)"
```

---

## Appendix B: Audio Analysis Thresholds

```python
VOLUME_SPIKE_THRESHOLD = 1.2  # 20% above baseline
PITCH_ELEVATION_THRESHOLD = 3  # semitones up
FAST_SPEECH_WPM = 180  # building tension
SLOW_SPEECH_WPM = 120  # emphasis
SIGNIFICANT_PAUSE = 0.8  # seconds
DRAMATIC_PAUSE = 1.5  # seconds
```

---

## Appendix C: Topic Taxonomy

```
├── Health
│   ├── Physical (Sleep, Exercise, Nutrition, Supplements)
│   └── Mental (Anxiety, Depression, Trauma, Therapy)
├── Wealth
│   ├── Business (Startups, Sales, Marketing)
│   ├── Investing
│   └── Career
├── Relationships
│   ├── Romantic
│   ├── Family (Parenting: Newborns/Toddlers/Teenagers, Marriage)
│   └── Friendship
├── Spirituality
│   ├── Meditation
│   ├── Religion (Christianity, Buddhism, Hinduism)
│   └── Philosophy
├── Lifestyle
│   ├── Productivity
│   ├── Habits
│   └── Fashion
└── Entertainment
    ├── Stories
    ├── Drama
    └── Comedy
```
