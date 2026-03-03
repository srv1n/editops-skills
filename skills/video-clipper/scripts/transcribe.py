#!/usr/bin/env python3
"""
Transcription with word-level timestamps.
Backends: Groq API (fast, cloud), MLX Whisper (Apple Silicon), faster-whisper (CPU/GPU).
"""

import argparse
import json
import os
import sys
import platform
import tempfile
import subprocess
from pathlib import Path


def load_env():
    """Load .env file from script directory."""
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())


def check_groq():
    """Check if Groq API is available."""
    load_env()
    return bool(os.environ.get('GROQ_API_KEY'))


def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())
    except:
        return 0


def split_audio_for_groq(audio_path: str, max_size_mb: int = 24) -> list:
    """Split audio into chunks under max_size_mb for Groq API (25MB limit, use 24 for safety)."""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

    if file_size_mb <= max_size_mb:
        return [(audio_path, 0.0)]  # (path, time_offset)

    duration = get_audio_duration(audio_path)
    if duration == 0:
        return [(audio_path, 0.0)]

    # Calculate chunk duration based on file size ratio
    num_chunks = int(file_size_mb / max_size_mb) + 1
    chunk_duration = duration / num_chunks

    chunks = []
    temp_dir = tempfile.mkdtemp(prefix='groq_chunks_')

    print(f"Splitting {file_size_mb:.1f}MB audio into {num_chunks} chunks...")

    for i in range(num_chunks):
        start_time = i * chunk_duration
        chunk_path = os.path.join(temp_dir, f'chunk_{i:03d}.mp3')

        subprocess.run([
            'ffmpeg', '-y', '-i', audio_path,
            '-ss', str(start_time), '-t', str(chunk_duration),
            '-acodec', 'libmp3lame', '-b:a', '64k',  # Compress to reduce size
            chunk_path
        ], capture_output=True)

        if os.path.exists(chunk_path):
            chunks.append((chunk_path, start_time))

    return chunks


def transcribe_with_groq(
    audio_path: str,
    language: str = None,
    model: str = 'whisper-large-v3-turbo',
) -> dict:
    """Transcribe using Groq API (fast cloud inference)."""
    from groq import Groq

    client = Groq(api_key=os.environ.get('GROQ_API_KEY'))

    # Check if we need to split the file
    chunks = split_audio_for_groq(audio_path)

    all_segments = []
    detected_language = language or 'en'

    for i, (chunk_path, time_offset) in enumerate(chunks):
        if len(chunks) > 1:
            print(f"Transcribing chunk {i+1}/{len(chunks)}...")
        else:
            print(f"Transcribing {audio_path}...")

        with open(chunk_path, 'rb') as f:
            params = {
                'file': (os.path.basename(chunk_path), f.read()),
                'model': model,
                'response_format': 'verbose_json',
                'timestamp_granularities': ['word', 'segment'],
            }
            if language:
                params['language'] = language

            result = client.audio.transcriptions.create(**params)

        # Clean up temp chunk
        if chunk_path != audio_path:
            os.remove(chunk_path)

        if hasattr(result, 'language'):
            detected_language = result.language

        # Process segments with time offset
        for seg in result.segments:
            segment = {
                'start': float(seg.get('start', 0)) + time_offset,
                'end': float(seg.get('end', 0)) + time_offset,
                'text': seg.get('text', ''),
                'words': []
            }

            # Add word-level timestamps if available
            if hasattr(result, 'words') and result.words:
                # Filter words for this segment's time range
                seg_start = seg.get('start', 0)
                seg_end = seg.get('end', 0)
                for w in result.words:
                    w_start = w.get('start', 0)
                    w_end = w.get('end', 0)
                    if w_start >= seg_start and w_end <= seg_end + 0.1:
                        segment['words'].append({
                            'word': w.get('word', ''),
                            'start': float(w_start) + time_offset,
                            'end': float(w_end) + time_offset,
                            'score': 1.0  # Groq doesn't provide confidence scores
                        })

            all_segments.append(segment)

    # Clean up temp directory
    if len(chunks) > 1:
        temp_dir = os.path.dirname(chunks[0][0])
        if temp_dir.startswith(tempfile.gettempdir()):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    return {'language': detected_language, 'segments': all_segments}


def check_mlx_whisper():
    """Check if mlx-whisper is available (Mac only)."""
    if platform.system() != 'Darwin':
        return False
    try:
        import mlx_whisper
        return True
    except ImportError:
        return False


def check_faster_whisper():
    """Check if faster-whisper is available."""
    try:
        from faster_whisper import WhisperModel
        return True
    except ImportError:
        return False


def transcribe_with_mlx(
    audio_path: str,
    language: str = None,
    model: str = 'mlx-community/whisper-large-v3-turbo',
) -> dict:
    """Transcribe using MLX Whisper (Apple Silicon optimized)."""
    import mlx_whisper

    print(f"Loading MLX Whisper model ({model})...")
    print(f"Transcribing {audio_path}...")

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model,
        word_timestamps=True,
        language=language,
    )

    segments = []
    for seg in result['segments']:
        segment = {
            'start': float(seg['start']),
            'end': float(seg['end']),
            'text': seg['text'],
            'words': [
                {
                    'word': w['word'],
                    'start': float(w['start']),
                    'end': float(w['end']),
                    'score': float(w.get('probability', 1.0))
                }
                for w in seg.get('words', [])
            ]
        }
        segments.append(segment)

    return {'language': result.get('language', language or 'en'), 'segments': segments}


def transcribe_with_faster_whisper(
    audio_path: str,
    language: str = None,
    model_size: str = 'base',
    device: str = 'auto',
) -> dict:
    """Transcribe using faster-whisper (CTranslate2 backend)."""
    from faster_whisper import WhisperModel

    print(f"Loading faster-whisper model ({model_size})...")
    model = WhisperModel(model_size, device=device, compute_type='auto')

    print(f"Transcribing {audio_path}...")
    segments_gen, info = model.transcribe(audio_path, language=language, word_timestamps=True)

    segments = []
    for seg in segments_gen:
        segment = {
            'start': seg.start,
            'end': seg.end,
            'text': seg.text,
            'words': [
                {'word': w.word, 'start': w.start, 'end': w.end, 'score': w.probability}
                for w in seg.words or []
            ]
        }
        segments.append(segment)

    return {'language': info.language, 'segments': segments}


def format_time(seconds: float) -> str:
    """Format seconds as MM:SS.ms or HH:MM:SS.ms"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes}:{secs:05.2f}"


MLX_MODELS = {
    'tiny': 'mlx-community/whisper-tiny',
    'base': 'mlx-community/whisper-base',
    'small': 'mlx-community/whisper-small',
    'medium': 'mlx-community/whisper-medium',
    'large': 'mlx-community/whisper-large-v3',
    'large-v3': 'mlx-community/whisper-large-v3',
    'turbo': 'mlx-community/whisper-large-v3-turbo',
    'distil': 'mlx-community/distil-whisper-large-v3',  # Faster, good quality
}


def main():
    parser = argparse.ArgumentParser(description='Transcribe audio with word-level timestamps')
    parser.add_argument('audio', help='Path to audio file')
    parser.add_argument('--output', '-o', help='Output JSON path')
    parser.add_argument('--language', '-l', help='Language code (e.g., en, es)')
    parser.add_argument('--model', '-m', default='turbo',
                        choices=['tiny', 'base', 'small', 'medium', 'large', 'large-v3', 'turbo', 'distil'])
    parser.add_argument('--backend', '-b', default='auto', choices=['auto', 'groq', 'mlx', 'faster-whisper'])
    parser.add_argument('--text-output', '-t', action='store_true')

    args = parser.parse_args()
    audio_path = Path(args.audio)

    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else audio_path.parent / 'transcript.json'

    # Determine backend
    backend = args.backend
    if backend == 'auto':
        if check_groq():
            backend = 'groq'
        elif check_mlx_whisper():
            backend = 'mlx'
        elif check_faster_whisper():
            backend = 'faster-whisper'
        else:
            print("Error: No transcription backend available", file=sys.stderr)
            print("Install: pip install groq (+ set GROQ_API_KEY) or pip install mlx-whisper", file=sys.stderr)
            sys.exit(1)

    # Validate backend availability
    if backend == 'groq' and not check_groq():
        print("Error: Groq API key not found. Set GROQ_API_KEY in .env or environment", file=sys.stderr)
        sys.exit(1)
    if backend == 'mlx' and not check_mlx_whisper():
        print("Error: MLX Whisper not available", file=sys.stderr)
        sys.exit(1)

    try:
        if backend == 'groq':
            print("Using Groq API (cloud)")
            groq_model = 'whisper-large-v3-turbo' if args.model == 'turbo' else 'whisper-large-v3'
            transcript = transcribe_with_groq(str(audio_path), args.language, groq_model)
        elif backend == 'mlx':
            print("Using MLX Whisper (Apple Silicon optimized)")
            transcript = transcribe_with_mlx(str(audio_path), args.language, MLX_MODELS.get(args.model, MLX_MODELS['turbo']))
        else:
            print("Using faster-whisper")
            transcript = transcribe_with_faster_whisper(str(audio_path), args.language, args.model if args.model != 'turbo' else 'large-v3')

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(transcript, f, indent=2, ensure_ascii=False)
        print(f"\n✓ Transcript saved to: {output_path}")

        total_duration = transcript['segments'][-1]['end'] if transcript['segments'] else 0

        if args.text_output:
            # Create analysis-friendly transcript with segment numbers
            text_path = output_path.with_suffix('.txt')
            lines = []
            lines.append("# TRANSCRIPT FOR CLIP ANALYSIS")
            lines.append(f"# Duration: {format_time(total_duration)}")
            lines.append(f"# Segments: {len(transcript['segments'])}")
            lines.append("#")
            lines.append("# Format: [SEG_NUM] [START - END] text")
            lines.append("# Use segment timestamps for approximate clip boundaries")
            lines.append("# Use JSON file for word-level precision")
            lines.append("")

            for i, s in enumerate(transcript['segments']):
                lines.append(f"[{i:03d}] [{format_time(s['start'])} - {format_time(s['end'])}] {s['text'].strip()}")

            with open(text_path, 'w') as f:
                f.write('\n'.join(lines))
            print(f"✓ Text version saved to: {text_path}")
        word_count = sum(len(s.get('words', [])) for s in transcript['segments'])
        print(f"\nSummary: {format_time(total_duration)}, {len(transcript['segments'])} segments, {word_count} words")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
