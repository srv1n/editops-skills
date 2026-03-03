#!/usr/bin/env python3
"""
Generic music generation wrapper - auto-selects available backend.

Usage:
    python3 generate_music.py --prompt "Epic trailer music" --duration 30 --output music.wav
"""

import argparse
import os
import sys
import subprocess
from pathlib import Path


def get_backend():
    """Determine which music generation backend to use."""
    # Check environment override
    backend = os.environ.get("MUSIC_BACKEND", "").lower()
    if backend:
        return backend

    # Check for API keys
    if os.environ.get("SUNO_API_KEY"):
        return "suno"

    # No backend available
    return None


def generate_suno(prompt: str, duration: int, output: str, style: str = None):
    """Generate music using Suno API."""
    script_dir = Path(__file__).parent
    suno_script = script_dir / "suno_generate.py"

    cmd = [
        sys.executable, str(suno_script),
        "--prompt", prompt,
        "--duration", str(duration),
        "--output", output
    ]
    if style:
        cmd.extend(["--style", style])

    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Generate music from text prompts")
    parser.add_argument("--prompt", "-p", required=True, help="Text prompt describing the music")
    parser.add_argument("--duration", "-d", type=int, default=30, help="Duration in seconds")
    parser.add_argument("--output", "-o", required=True, help="Output file path")
    parser.add_argument("--style", "-s", choices=["trailer", "ambient", "upbeat", "dramatic"],
                        help="Music style preset")
    parser.add_argument("--backend", "-b", choices=["suno", "udio", "local"],
                        help="Force specific backend")

    args = parser.parse_args()

    backend = args.backend or get_backend()

    if not backend:
        print("Error: No music generation backend available.", file=sys.stderr)
        print("Set SUNO_API_KEY environment variable or specify --backend", file=sys.stderr)
        sys.exit(1)

    print(f"Using backend: {backend}")

    # Enhance prompt with style if provided
    prompt = args.prompt
    if args.style:
        style_hints = {
            "trailer": "cinematic trailer style, building tension, epic",
            "ambient": "ambient, atmospheric, subtle",
            "upbeat": "energetic, positive, driving rhythm",
            "dramatic": "dramatic, emotional, orchestral swells"
        }
        prompt = f"{prompt}, {style_hints.get(args.style, '')}"

    if backend == "suno":
        generate_suno(prompt, args.duration, args.output, args.style)
    else:
        print(f"Backend '{backend}' not yet implemented", file=sys.stderr)
        sys.exit(1)

    print(f"Generated music: {args.output}")


if __name__ == "__main__":
    main()
