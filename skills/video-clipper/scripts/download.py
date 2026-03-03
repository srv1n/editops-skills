#!/usr/bin/env python3
"""
YouTube video/channel downloader using yt-dlp.
Downloads audio (and optionally video) with metadata.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
import re


def sanitize_filename(name: str) -> str:
    """Create safe filename from title."""
    return re.sub(r'[^\w\s-]', '', name)[:50].strip()


def get_video_info(url: str) -> dict:
    """Get video/playlist metadata without downloading."""
    cmd = [
        'yt-dlp',
        '--dump-json',
        '--flat-playlist',
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Failed to get info: {result.stderr}")
    
    # Handle playlist vs single video
    lines = result.stdout.strip().split('\n')
    if len(lines) > 1:
        return {'type': 'playlist', 'entries': [json.loads(l) for l in lines]}
    return {'type': 'video', **json.loads(lines[0])}


def download_video(
    url: str,
    output_dir: Path,
    audio_only: bool = False,
    quality: str = '720',
    verbose: bool = False
) -> dict:
    """
    Download a single video.
    
    Returns:
        dict with paths to downloaded files and metadata
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Build yt-dlp command
    if audio_only:
        cmd = [
            'yt-dlp',
            '-f', 'bestaudio[ext=m4a]/bestaudio',
            '-o', str(output_dir / 'audio.%(ext)s'),
            '--write-info-json',
            '--write-thumbnail',
            url
        ]
    else:
        cmd = [
            'yt-dlp',
            '-f', f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
            '-o', str(output_dir / 'video.%(ext)s'),
            '--write-info-json',
            '--write-thumbnail',
            '--merge-output-format', 'mp4',
            url
        ]
    
    if verbose:
        print(f"Running: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=not verbose, text=True)
    if result.returncode != 0:
        raise Exception(f"Download failed: {result.stderr if not verbose else 'See output above'}")
    
    # Find downloaded files
    files = {
        'audio': None,
        'video': None,
        'info': None,
        'thumbnail': None
    }
    
    for f in output_dir.iterdir():
        if f.suffix in ['.m4a', '.mp3', '.wav', '.opus', '.webm'] and 'audio' in f.stem:
            files['audio'] = str(f)
        elif f.suffix in ['.mp4', '.mkv', '.webm'] and 'video' in f.stem:
            files['video'] = str(f)
        elif f.suffix == '.json':
            files['info'] = str(f)
        elif f.suffix in ['.jpg', '.png', '.webp']:
            files['thumbnail'] = str(f)
    
    # If we downloaded video, extract audio for transcription
    if files['video'] and not files['audio']:
        audio_path = output_dir / 'audio.m4a'
        subprocess.run([
            'ffmpeg', '-i', files['video'],
            '-vn', '-acodec', 'aac', '-y',
            str(audio_path)
        ], capture_output=True)
        files['audio'] = str(audio_path)
    
    return files


def download_channel(
    url: str,
    output_base: Path,
    limit: int = 10,
    audio_only: bool = False,
    quality: str = '720',
    verbose: bool = False
) -> list:
    """Download multiple videos from a channel/playlist."""
    
    # Get list of videos
    info = get_video_info(url)
    if info['type'] != 'playlist':
        # Single video
        video_id = info.get('id', 'unknown')
        output_dir = output_base / video_id
        return [download_video(url, output_dir, audio_only, quality, verbose)]
    
    # Download each video
    results = []
    entries = info['entries'][:limit]
    
    for i, entry in enumerate(entries):
        video_id = entry.get('id', f'video_{i}')
        video_url = entry.get('url') or f"https://youtube.com/watch?v={video_id}"
        output_dir = output_base / video_id
        
        print(f"[{i+1}/{len(entries)}] Downloading {entry.get('title', video_id)[:50]}...")
        
        try:
            result = download_video(video_url, output_dir, audio_only, quality, verbose)
            result['video_id'] = video_id
            result['title'] = entry.get('title', '')
            results.append(result)
        except Exception as e:
            print(f"  Error: {e}")
            continue
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Download YouTube videos for clipping')
    parser.add_argument('url', help='YouTube video, playlist, or channel URL')
    parser.add_argument('--output', '-o', default='downloads', help='Output directory')
    parser.add_argument('--audio-only', '-a', action='store_true', 
                        help='Download audio only (faster, sufficient for transcription)')
    parser.add_argument('--quality', '-q', default='720',
                        help='Video quality (360, 480, 720, 1080). Default: 720')
    parser.add_argument('--limit', '-l', type=int, default=10,
                        help='Max videos to download from channel/playlist. Default: 10')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show yt-dlp output')
    
    args = parser.parse_args()
    output_base = Path(args.output)
    
    try:
        results = download_channel(
            args.url,
            output_base,
            limit=args.limit,
            audio_only=args.audio_only,
            quality=args.quality,
            verbose=args.verbose
        )
        
        print(f"\n✓ Downloaded {len(results)} video(s)")
        for r in results:
            print(f"  - {r.get('video_id', 'unknown')}: audio={r.get('audio')}, video={r.get('video')}")
        
        # Save manifest
        manifest_path = output_base / 'manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nManifest saved to: {manifest_path}")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
