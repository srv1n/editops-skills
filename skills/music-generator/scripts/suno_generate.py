#!/usr/bin/env python3
"""
Suno API integration for music generation.

Usage:
    python3 suno_generate.py --prompt "Epic trailer music" --duration 30 --output music.wav

Environment:
    SUNO_API_KEY - Required API key for Suno
"""

import argparse
import os
import sys
import time
import json
from pathlib import Path


# Try to load from .env file
def load_env():
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    if key not in os.environ:
                        os.environ[key] = value.strip('"').strip("'")


load_env()


def generate_with_suno(prompt: str, duration: int, output_path: str):
    """
    Generate music using Suno API.

    Note: This is a placeholder implementation. The actual Suno API
    integration will depend on their API structure.
    """
    api_key = os.environ.get("SUNO_API_KEY")
    if not api_key:
        print("Error: SUNO_API_KEY environment variable not set", file=sys.stderr)
        print("Get your API key from https://suno.ai and set it:", file=sys.stderr)
        print("  export SUNO_API_KEY=your_key_here", file=sys.stderr)
        sys.exit(1)

    try:
        import requests
    except ImportError:
        print("Error: requests library required. Install with: pip install requests", file=sys.stderr)
        sys.exit(1)

    # Suno API endpoint (placeholder - update with actual API)
    # The actual Suno API might be different - this is a template
    api_base = os.environ.get("SUNO_API_BASE", "https://api.suno.ai/v1")

    print(f"Generating music with Suno...")
    print(f"  Prompt: {prompt}")
    print(f"  Duration: {duration}s")

    # Step 1: Create generation request
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "prompt": prompt,
        "duration": duration,
        "make_instrumental": True,  # For trailer music, usually instrumental
    }

    try:
        # This is a placeholder - actual API structure may differ
        response = requests.post(
            f"{api_base}/generate",
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 401:
            print("Error: Invalid API key", file=sys.stderr)
            sys.exit(1)

        response.raise_for_status()
        result = response.json()

        # Poll for completion (placeholder logic)
        task_id = result.get("id") or result.get("task_id")
        if task_id:
            print(f"  Task ID: {task_id}")
            print("  Waiting for generation...")

            for attempt in range(60):  # Max 5 minutes
                time.sleep(5)
                status_response = requests.get(
                    f"{api_base}/status/{task_id}",
                    headers=headers,
                    timeout=30
                )
                status = status_response.json()

                if status.get("status") == "completed":
                    audio_url = status.get("audio_url")
                    if audio_url:
                        # Download the audio
                        audio_response = requests.get(audio_url, timeout=60)
                        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, "wb") as f:
                            f.write(audio_response.content)
                        print(f"  Downloaded to: {output_path}")
                        return
                    break
                elif status.get("status") == "failed":
                    print(f"Error: Generation failed: {status.get('error')}", file=sys.stderr)
                    sys.exit(1)

                print(f"  Status: {status.get('status', 'processing')}...")

        # If we get here without downloading, show what we got
        print("Warning: Could not download generated audio", file=sys.stderr)
        print(f"API response: {json.dumps(result, indent=2)}", file=sys.stderr)

    except requests.exceptions.RequestException as e:
        print(f"Error calling Suno API: {e}", file=sys.stderr)
        print("\nNote: The Suno API integration is a placeholder.", file=sys.stderr)
        print("You may need to update this script for the actual Suno API.", file=sys.stderr)
        print("\nAlternative: Generate music manually at https://suno.ai", file=sys.stderr)
        print(f"and save to: {output_path}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Generate music using Suno API")
    parser.add_argument("--prompt", "-p", required=True, help="Text prompt for music generation")
    parser.add_argument("--duration", "-d", type=int, default=30, help="Duration in seconds (default: 30)")
    parser.add_argument("--output", "-o", required=True, help="Output audio file path")
    parser.add_argument("--style", "-s", help="Optional style hint to append to prompt")

    args = parser.parse_args()

    prompt = args.prompt
    if args.style:
        prompt = f"{prompt}, {args.style} style"

    generate_with_suno(prompt, args.duration, args.output)
    print("Done!")


if __name__ == "__main__":
    main()
