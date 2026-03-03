# Clip Analysis Prompts

Use these prompts with Claude to identify high-engagement moments in transcripts.

## Main Analysis Prompt

```
Analyze this transcript and identify 5-10 moments that would make great short-form viral clips (15-60 seconds). 

Look for:
1. **Hot takes / Provocative statements** - Contrarian opinions, bold claims, controversial stances
2. **Emotional peaks** - Moments of laughter, surprise, anger, vulnerability
3. **Quotable one-liners** - Memorable phrases that stand alone
4. **Story climaxes** - The payoff moment of an anecdote
5. **"Wait, what?" moments** - Surprising revelations or unexpected turns
6. **Relatable insights** - Universal truths that resonate widely
7. **Call-outs** - Naming names, direct challenges
8. **Transformation moments** - Before/after, aha moments, mindset shifts

For each clip, provide:
- **title**: A clickbait-style hook (under 10 words)
- **start**: Start timestamp in seconds
- **end**: End timestamp in seconds  
- **hook**: The scroll-stopping text overlay (provocative but accurate)
- **reason**: Why this will perform well

Format as JSON:
{
  "clips": [
    {
      "title": "...",
      "start": 0.0,
      "end": 0.0,
      "hook": "...",
      "reason": "..."
    }
  ]
}

TRANSCRIPT:
[paste transcript here]
```

## Quick Scan Prompt

For faster analysis of long transcripts:

```
Scan this transcript quickly and identify the TOP 3 most viral-worthy moments. Focus only on the absolute bangers - moments that would stop someone mid-scroll.

Prioritize:
- Shocking statements
- Laugh-out-loud moments  
- Hard-hitting truths
- Celebrity/name drops with reactions

Be aggressive in your filtering - only surface the moments with genuine viral potential.

Format: JSON with title, start, end, hook, reason

TRANSCRIPT:
[paste transcript here]
```

## Niche-Specific Prompts

### Podcast/Interview Style (Huberman, Lex Fridman, etc.)
```
Identify moments where the guest reveals:
- Personal struggles or failures
- Counterintuitive scientific findings
- Strong disagreements with mainstream views
- Specific actionable advice
- Behind-the-scenes industry secrets
```

### Motivational/Self-Help (Sadhguru, Tony Robbins, etc.)
```
Find moments with:
- Powerful reframes of common problems
- Direct challenges to the audience
- Emotional storytelling climaxes
- Memorable metaphors
- "Drop the mic" conclusions
```

### Business/Entrepreneurship
```
Look for:
- Specific revenue/growth numbers
- Unconventional strategies that worked
- Predictions about markets/industries
- Criticism of popular business advice
- Personal failure stories with lessons
```

### Comedy/Entertainment
```
Identify:
- Punchlines with setup context
- Improv moments that land
- Roasts and burns
- Physical comedy beats (if video)
- Running jokes payoffs
```

## Hook Writing Tips

Good hooks are:
- **Specific** ("He made $2M in 3 months" > "He got rich")
- **Curiosity-inducing** ("The one thing every billionaire does at 5am")
- **Emotionally charged** ("This destroyed his 20-year friendship")
- **Pattern-interrupting** ("Why working harder is ruining your life")

Avoid:
- Generic clickbait ("You won't believe...")
- Overselling ("The GREATEST moment EVER")
- Vague promises ("This changes everything")

## Clip Length Guidelines

- **15-20s**: Perfect for pure comedy, one-liners, reactions
- **20-30s**: Ideal for single insights, quick stories
- **30-45s**: Good for complete thoughts, short anecdotes
- **45-60s**: Max length, only for compelling narratives

Rule: **Shorter is almost always better.** Cut before the thought concludes - leave them wanting more.

## Red Flags to Avoid

Skip moments that:
- Require too much context to understand
- Are offensive without redeeming value
- Could be taken badly out of context
- Are inside jokes only fans get
- Peak too early (boring ending)
