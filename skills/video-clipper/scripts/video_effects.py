#!/usr/bin/env python3
"""
Fast video effects using MediaPipe.

Uses MediaPipe Selfie Segmentation for real-time person segmentation
to create effects like:
- desaturate_bg: Color subject, grayscale background
- blur_bg: Blur background, sharp subject
- spotlight: Darken background, highlight subject

Much faster than SAM3 - runs at ~30fps on CPU.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional, Tuple
import os

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("OpenCV required. Install: pip install opencv-python")

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


def get_segmenter_model_path() -> str:
    """Get or download the selfie segmentation model."""
    import urllib.request

    models_dir = Path(__file__).parent.parent / 'models'
    models_dir.mkdir(exist_ok=True)

    model_path = models_dir / 'selfie_segmenter.tflite'

    if not model_path.exists():
        url = "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite"
        print(f"Downloading selfie_segmenter.tflite...")
        urllib.request.urlretrieve(url, model_path)

    return str(model_path)


class VideoEffectsProcessor:
    """Process video with MediaPipe-based effects."""

    def __init__(self):
        if not MEDIAPIPE_AVAILABLE:
            raise ImportError("MediaPipe required. Install: pip install mediapipe")

        self.segmenter = None
        self._init_segmenter()

    def _init_segmenter(self):
        """Initialize the selfie segmenter."""
        model_path = get_segmenter_model_path()

        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = vision.ImageSegmenterOptions(
            base_options=base_options,
            output_category_mask=True
        )
        self.segmenter = vision.ImageSegmenter.create_from_options(options)

    def get_person_mask(self, frame: np.ndarray) -> np.ndarray:
        """Get binary mask of person in frame."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        result = self.segmenter.segment(mp_image)

        if result.category_mask:
            mask = result.category_mask.numpy_view()
            # Category 1 is person
            person_mask = (mask > 0).astype(np.float32)
            return person_mask

        return np.zeros(frame.shape[:2], dtype=np.float32)

    def apply_desaturate_bg(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Keep subject in color, convert background to grayscale."""
        # Smooth mask edges
        mask_smooth = cv2.GaussianBlur(mask, (21, 21), 0)
        mask_3ch = np.stack([mask_smooth] * 3, axis=-1)

        # Create grayscale background
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        # Blend
        result = (frame * mask_3ch + gray_bgr * (1 - mask_3ch)).astype(np.uint8)
        return result

    def apply_blur_bg(self, frame: np.ndarray, mask: np.ndarray, blur_strength: int = 51) -> np.ndarray:
        """Blur background, keep subject sharp."""
        mask_smooth = cv2.GaussianBlur(mask, (21, 21), 0)
        mask_3ch = np.stack([mask_smooth] * 3, axis=-1)

        # Blur the whole frame
        blurred = cv2.GaussianBlur(frame, (blur_strength, blur_strength), 0)

        # Blend: sharp subject + blurred background
        result = (frame * mask_3ch + blurred * (1 - mask_3ch)).astype(np.uint8)
        return result

    def apply_spotlight(self, frame: np.ndarray, mask: np.ndarray, darkness: float = 0.3) -> np.ndarray:
        """Darken background, spotlight on subject."""
        mask_smooth = cv2.GaussianBlur(mask, (51, 51), 0)
        mask_3ch = np.stack([mask_smooth] * 3, axis=-1)

        # Darken background
        darkened = (frame * darkness).astype(np.uint8)

        # Blend
        result = (frame * mask_3ch + darkened * (1 - mask_3ch)).astype(np.uint8)
        return result

    def apply_contour(self, frame: np.ndarray, mask: np.ndarray,
                      color: Tuple[int, int, int] = (0, 255, 255),
                      thickness: int = 3,
                      glow: bool = True) -> np.ndarray:
        """Draw glowing contour around subject."""
        result = frame.copy()

        # Get binary mask
        binary_mask = (mask > 0.5).astype(np.uint8) * 255

        # Find contours
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if glow:
            # Draw glow layers
            for i in range(15, 0, -3):
                alpha = (15 - i) / 15
                glow_color = tuple(int(c * alpha) for c in color)
                cv2.drawContours(result, contours, -1, glow_color, thickness + i)

        # Draw main contour
        cv2.drawContours(result, contours, -1, color, thickness)

        return result

    def apply_bounding_box(self, frame: np.ndarray, mask: np.ndarray,
                           color: Tuple[int, int, int] = (0, 255, 0),
                           thickness: int = 2,
                           label: str = "person") -> np.ndarray:
        """Draw bounding box around subject."""
        result = frame.copy()

        # Get binary mask
        binary_mask = (mask > 0.5).astype(np.uint8) * 255

        # Find bounding box from mask
        coords = np.where(binary_mask > 0)
        if len(coords[0]) == 0:
            return result

        y_min, y_max = coords[0].min(), coords[0].max()
        x_min, x_max = coords[1].min(), coords[1].max()

        # Draw box
        cv2.rectangle(result, (x_min, y_min), (x_max, y_max), color, thickness)

        # Draw label
        if label:
            label_text = label
            (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(result, (x_min, y_min - th - 10), (x_min + tw + 10, y_min), color, -1)
            cv2.putText(
                result, label_text, (x_min + 5, y_min - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2
            )

        return result


def process_video(
    input_path: str,
    output_path: str,
    effect: str,
    sample_rate: int = 1,  # Process every Nth frame for mask, interpolate others
    **effect_kwargs
) -> bool:
    """
    Process video with the specified effect.

    Args:
        input_path: Input video path
        output_path: Output video path
        effect: Effect name (desaturate_bg, blur_bg, spotlight, contour)
        sample_rate: Process mask every N frames (1=every frame, 30=1fps)
    """
    if not CV2_AVAILABLE:
        print("OpenCV required")
        return False

    processor = VideoEffectsProcessor()

    # Open video
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        print(f"Error: Cannot open {input_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Create writer
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    # Process with audio later via ffmpeg
    temp_video = output_path + '.temp.mp4'
    out = cv2.VideoWriter(temp_video, fourcc, fps, (width, height))

    print(f"Processing {total_frames} frames with {effect} effect...")
    print(f"  Mask sample rate: every {sample_rate} frames")

    frame_count = 0
    last_mask = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Get mask (sample or reuse)
        if frame_count % sample_rate == 0 or last_mask is None:
            mask = processor.get_person_mask(frame)
            last_mask = mask
        else:
            mask = last_mask

        # Apply effect
        if effect == 'desaturate_bg':
            result = processor.apply_desaturate_bg(frame, mask)
        elif effect == 'blur_bg':
            result = processor.apply_blur_bg(frame, mask, effect_kwargs.get('blur_strength', 51))
        elif effect == 'spotlight':
            result = processor.apply_spotlight(frame, mask, effect_kwargs.get('darkness', 0.3))
        elif effect == 'contour':
            result = processor.apply_contour(
                frame, mask,
                color=effect_kwargs.get('color', (0, 255, 255)),
                thickness=effect_kwargs.get('thickness', 3),
                glow=effect_kwargs.get('glow', True)
            )
        elif effect == 'bounding_box':
            result = processor.apply_bounding_box(
                frame, mask,
                color=effect_kwargs.get('box_color', (0, 255, 0)),
                thickness=effect_kwargs.get('box_thickness', 2),
                label=effect_kwargs.get('label', 'person')
            )
        else:
            result = frame

        out.write(result)
        frame_count += 1

        if frame_count % 100 == 0:
            pct = 100 * frame_count / total_frames
            print(f"  Progress: {frame_count}/{total_frames} ({pct:.1f}%)")

    cap.release()
    out.release()

    # Mux audio from original
    print("Adding audio...")
    import subprocess
    cmd = [
        'ffmpeg', '-y',
        '-i', temp_video,
        '-i', input_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0?',
        '-shortest',
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Clean up temp file
    try:
        os.remove(temp_video)
    except:
        pass

    if result.returncode != 0:
        print(f"Warning: Audio mux failed, video-only output at {temp_video}")
        os.rename(temp_video, output_path)

    print(f"Done! Output: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Apply video effects using MediaPipe segmentation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Effects:
  desaturate_bg  - Color subject, grayscale background (podcast style)
  blur_bg        - Blur background, sharp subject
  spotlight      - Darken background, highlight subject
  contour        - Glowing outline around subject

Examples:
  # Desaturate background (fast, every frame)
  python video_effects.py clip.mp4 --effect desaturate_bg -o output.mp4

  # Spotlight with 1fps mask sampling (faster for long videos)
  python video_effects.py clip.mp4 --effect spotlight --sample-rate 30 -o output.mp4

  # Cyan contour glow
  python video_effects.py clip.mp4 --effect contour --color 0,255,255 -o output.mp4
        """
    )

    parser.add_argument('input', help='Input video path')
    parser.add_argument('--effect', '-e', required=True,
                        choices=['desaturate_bg', 'blur_bg', 'spotlight', 'contour', 'bounding_box'],
                        help='Effect to apply')
    parser.add_argument('--output', '-o', help='Output video path')
    parser.add_argument('--sample-rate', type=int, default=1,
                        help='Process mask every N frames (default: 1, use 30 for 1fps)')

    # Effect-specific options
    parser.add_argument('--blur-strength', type=int, default=51,
                        help='Blur strength for blur_bg (default: 51)')
    parser.add_argument('--darkness', type=float, default=0.3,
                        help='Background darkness for spotlight (0-1, default: 0.3)')
    parser.add_argument('--color', default='0,255,255',
                        help='Contour color as R,G,B (default: 0,255,255 cyan)')
    parser.add_argument('--thickness', type=int, default=3,
                        help='Contour thickness (default: 3)')
    parser.add_argument('--no-glow', action='store_true',
                        help='Disable glow effect on contour')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input not found: {args.input}")
        sys.exit(1)

    output_path = args.output or str(
        Path(args.input).parent / f"{Path(args.input).stem}_{args.effect}.mp4"
    )

    # Parse color
    color = tuple(int(x) for x in args.color.split(','))

    success = process_video(
        args.input,
        output_path,
        args.effect,
        sample_rate=args.sample_rate,
        blur_strength=args.blur_strength,
        darkness=args.darkness,
        color=color,
        thickness=args.thickness,
        glow=not args.no_glow
    )

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
