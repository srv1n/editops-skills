#!/usr/bin/env python3
"""
Video effects for social media clips.
Adds subtitles, hook text, zoom effects, and other enhancements.
"""

import argparse
import subprocess
import sys
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class SubtitleStyle:
    """Subtitle styling configuration."""
    font: str = 'Arial'
    fontsize: int = 48
    fontcolor: str = 'white'
    borderw: int = 3
    bordercolor: str = 'black'
    shadowx: int = 2
    shadowy: int = 2
    position: str = 'bottom'  # bottom, center, top
    box: bool = False
    boxcolor: str = 'black@0.5'
    bold: bool = False
    
    def to_drawtext_style(self) -> str:
        """Convert to FFmpeg drawtext filter style."""
        parts = [
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            f"fontsize={self.fontsize}",
            f"fontcolor={self.fontcolor}",
            f"borderw={self.borderw}",
            f"bordercolor={self.bordercolor}",
            f"shadowx={self.shadowx}",
            f"shadowy={self.shadowy}",
        ]
        if self.box:
            parts.append(f"box=1")
            parts.append(f"boxcolor={self.boxcolor}")
            parts.append(f"boxborderw=10")
        return ':'.join(parts)


# Predefined subtitle styles
SUBTITLE_STYLES = {
    'classic': SubtitleStyle(),
    'bold': SubtitleStyle(fontsize=72, borderw=4, bold=True),
    'boxed': SubtitleStyle(box=True, borderw=0),
    'karaoke': SubtitleStyle(fontsize=56, fontcolor='yellow', borderw=4),
    'minimal': SubtitleStyle(fontsize=36, borderw=2, shadowx=0, shadowy=0),
}


def get_video_info(path: str) -> dict:
    """Get video dimensions and duration."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,duration',
        '-show_entries', 'format=duration',
        '-of', 'json', path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    stream = data.get('streams', [{}])[0]
    fmt = data.get('format', {})
    
    return {
        'width': stream.get('width', 1920),
        'height': stream.get('height', 1080),
        'duration': float(fmt.get('duration', stream.get('duration', 0)))
    }


def generate_subtitle_filter(
    transcript_path: str,
    start_offset: float,
    style: SubtitleStyle,
    video_height: int,
    mode: str = 'segment'  # 'segment' or 'word' (karaoke)
) -> str:
    """
    Generate FFmpeg drawtext filters for subtitles.
    
    Args:
        transcript_path: Path to transcript JSON
        start_offset: Original video timestamp where clip starts
        style: Subtitle styling
        video_height: Video height for positioning
        mode: 'segment' for full sentences, 'word' for karaoke-style
    
    Returns:
        FFmpeg filter string
    """
    with open(transcript_path) as f:
        transcript = json.load(f)
    
    filters = []
    
    # Calculate Y position based on style
    if style.position == 'bottom':
        y_pos = f"h-{style.fontsize + 60}"
    elif style.position == 'center':
        y_pos = "(h-text_h)/2"
    else:  # top
        y_pos = "50"
    
    if mode == 'word' and transcript.get('segments'):
        # Karaoke mode - show each word individually
        for segment in transcript['segments']:
            for word in segment.get('words', []):
                word_start = word['start'] - start_offset
                word_end = word['end'] - start_offset
                
                if word_start < 0:
                    continue
                
                text = word['word'].strip().replace("'", "'\\''")
                
                filter_str = (
                    f"drawtext=text='{text}':"
                    f"{style.to_drawtext_style()}:"
                    f"x=(w-text_w)/2:y={y_pos}:"
                    f"enable='between(t,{word_start:.3f},{word_end:.3f})'"
                )
                filters.append(filter_str)
    else:
        # Segment mode - show full sentences
        for segment in transcript['segments']:
            seg_start = segment['start'] - start_offset
            seg_end = segment['end'] - start_offset
            
            if seg_end < 0:
                continue
            if seg_start < 0:
                seg_start = 0
            
            text = segment['text'].strip().replace("'", "'\\''").replace(":", "\\:")
            
            # Wrap long text
            max_chars = 40
            if len(text) > max_chars:
                words = text.split()
                lines = []
                current_line = []
                for word in words:
                    if len(' '.join(current_line + [word])) > max_chars:
                        lines.append(' '.join(current_line))
                        current_line = [word]
                    else:
                        current_line.append(word)
                if current_line:
                    lines.append(' '.join(current_line))
                text = '\\n'.join(lines)
            
            filter_str = (
                f"drawtext=text='{text}':"
                f"{style.to_drawtext_style()}:"
                f"x=(w-text_w)/2:y={y_pos}:"
                f"enable='between(t,{seg_start:.3f},{seg_end:.3f})'"
            )
            filters.append(filter_str)
    
    return ','.join(filters)


def generate_hook_filter(
    text: str,
    position: str,
    video_width: int,
    video_height: int,
    duration: float = 0,  # 0 = entire video
    fontsize: int = 56
) -> str:
    """Generate FFmpeg filter for hook/title text."""
    text = text.replace("'", "'\\''").replace(":", "\\:")
    
    if position == 'top':
        y_pos = "80"
    elif position == 'center':
        y_pos = "(h-text_h)/2"
    else:  # bottom (above subtitles)
        y_pos = f"h-{fontsize + 150}"
    
    filter_str = (
        f"drawtext=text='{text}':"
        f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"fontsize={fontsize}:"
        f"fontcolor=white:"
        f"borderw=4:"
        f"bordercolor=black:"
        f"x=(w-text_w)/2:y={y_pos}"
    )
    
    if duration > 0:
        filter_str += f":enable='between(t,0,{duration})'"
    
    return filter_str


def generate_zoom_filter(
    mode: str,
    duration: float,
    video_width: int,
    video_height: int
) -> str:
    """
    Generate zoom effect filter.
    
    Modes:
        - 'slow': Gradual zoom in
        - 'pulse': Subtle zoom pulses
        - 'out': Zoom out from close
    """
    if mode == 'slow':
        # Gradual zoom from 1.0x to 1.15x over duration
        return (
            f"scale=iw*1.15:ih*1.15,"
            f"zoompan=z='1+0.15*on/{duration}/25':d={int(duration*25)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={video_width}x{video_height}"
        )
    elif mode == 'pulse':
        # Subtle zoom pulse every 2 seconds
        return (
            f"scale=iw*1.1:ih*1.1,"
            f"zoompan=z='1+0.05*sin(on/25*3.14159)':d={int(duration*25)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={video_width}x{video_height}"
        )
    elif mode == 'out':
        # Zoom out from 1.2x to 1.0x
        return (
            f"scale=iw*1.2:ih*1.2,"
            f"zoompan=z='1.2-0.2*on/{duration}/25':d={int(duration*25)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={video_width}x{video_height}"
        )
    return ""


def apply_effects(
    input_path: str,
    output_path: str,
    transcript_path: Optional[str] = None,
    start_offset: float = 0,
    subtitle_style: str = 'classic',
    subtitle_mode: str = 'segment',
    hook_text: Optional[str] = None,
    hook_position: str = 'top',
    hook_duration: float = 0,
    zoom: Optional[str] = None,
    brightness: float = 1.0,
    contrast: float = 1.0,
    saturation: float = 1.0
) -> bool:
    """
    Apply visual effects to a video clip.
    """
    video_info = get_video_info(input_path)
    width = video_info['width']
    height = video_info['height']
    duration = video_info['duration']
    
    filters = []
    
    # Color adjustments
    if brightness != 1.0 or contrast != 1.0 or saturation != 1.0:
        filters.append(f"eq=brightness={brightness-1}:contrast={contrast}:saturation={saturation}")
    
    # Zoom effect (must come before text overlays)
    if zoom and zoom != 'none':
        zoom_filter = generate_zoom_filter(zoom, duration, width, height)
        if zoom_filter:
            filters.append(zoom_filter)
    
    # Hook text
    if hook_text:
        hook_filter = generate_hook_filter(
            hook_text, hook_position, width, height, hook_duration
        )
        filters.append(hook_filter)
    
    # Subtitles
    if transcript_path:
        style = SUBTITLE_STYLES.get(subtitle_style, SUBTITLE_STYLES['classic'])
        subtitle_filter = generate_subtitle_filter(
            transcript_path, start_offset, style, height, subtitle_mode
        )
        if subtitle_filter:
            filters.append(subtitle_filter)
    
    # Build FFmpeg command
    cmd = ['ffmpeg', '-y', '-i', input_path]
    
    if filters:
        cmd.extend(['-vf', ','.join(filters)])
    
    cmd.extend([
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-c:a', 'copy',
        output_path
    ])
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"FFmpeg error: {result.stderr}", file=sys.stderr)
        return False
    
    return True


def main():
    parser = argparse.ArgumentParser(description='Add effects to video clips')
    parser.add_argument('input', help='Input video path')
    parser.add_argument('--output', '-o', help='Output path (default: input_effects.mp4)')
    
    # Subtitle options
    parser.add_argument('--subtitles', '-s', help='Transcript JSON for subtitles')
    parser.add_argument('--start-offset', type=float, default=0,
                        help='Original video timestamp where clip starts')
    parser.add_argument('--subtitle-style', choices=list(SUBTITLE_STYLES.keys()),
                        default='classic', help='Subtitle style preset')
    parser.add_argument('--subtitle-mode', choices=['segment', 'word'],
                        default='segment', help='Subtitle display mode')
    
    # Hook text options
    parser.add_argument('--hook-text', help='Hook/title text overlay')
    parser.add_argument('--hook-position', choices=['top', 'center', 'bottom'],
                        default='top', help='Hook text position')
    parser.add_argument('--hook-duration', type=float, default=0,
                        help='How long to show hook (0 = entire video)')
    
    # Zoom options
    parser.add_argument('--zoom', choices=['none', 'slow', 'pulse', 'out'],
                        help='Zoom effect type')
    
    # Color adjustments
    parser.add_argument('--brightness', type=float, default=1.0,
                        help='Brightness adjustment (1.0 = normal)')
    parser.add_argument('--contrast', type=float, default=1.0,
                        help='Contrast adjustment (1.0 = normal)')
    parser.add_argument('--saturation', type=float, default=1.0,
                        help='Saturation adjustment (1.0 = normal)')
    
    args = parser.parse_args()
    
    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    
    # Default output path
    input_path = Path(args.input)
    output_path = args.output or str(input_path.parent / f"{input_path.stem}_effects{input_path.suffix}")
    
    success = apply_effects(
        args.input,
        output_path,
        transcript_path=args.subtitles,
        start_offset=args.start_offset,
        subtitle_style=args.subtitle_style,
        subtitle_mode=args.subtitle_mode,
        hook_text=args.hook_text,
        hook_position=args.hook_position,
        hook_duration=args.hook_duration,
        zoom=args.zoom,
        brightness=args.brightness,
        contrast=args.contrast,
        saturation=args.saturation
    )
    
    if success:
        print(f"✓ Effects applied: {output_path}")
    else:
        print("✗ Failed to apply effects", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
