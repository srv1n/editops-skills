#!/usr/bin/env python3
"""
SAM3 (Segment Anything Model 3) video effects.

Uses Meta's SAM3 via HuggingFace Transformers for video segmentation
with 1fps mask sampling and interpolation for efficiency.

Effects:
- contour: Glowing outline around subjects
- bounding_box: Track subjects with boxes
- desaturate_bg: Color subject, grayscale background

Requires: transformers, torch, opencv-python
Install: pip install transformers torch opencv-python

NOTE: SAM3 requires HuggingFace authentication.
Set HF_TOKEN environment variable or login with `huggingface-cli login`
"""

import argparse
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any
from enum import Enum
import subprocess

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("OpenCV required. Install: pip install opencv-python")

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    print("PyTorch required. Install: pip install torch")

# Check for SAM3 availability
SAM3_AVAILABLE = False
try:
    from transformers import Sam3Processor, Sam3Model
    SAM3_AVAILABLE = True
except ImportError:
    pass


class EffectType(Enum):
    """Available SAM2 effects."""
    DESATURATE_BG = "desaturate_bg"
    CONTOUR = "contour"
    BOUNDING_BOX = "bounding_box"
    SPOTLIGHT = "spotlight"


@dataclass
class EffectConfig:
    """Configuration for video effects."""
    effect_type: EffectType

    # Contour settings
    contour_color: Tuple[int, int, int] = (0, 255, 255)  # Cyan
    contour_thickness: int = 3
    contour_glow: bool = True
    glow_intensity: int = 15

    # Bounding box settings
    box_color: Tuple[int, int, int] = (0, 255, 0)  # Green
    box_thickness: int = 2
    show_label: bool = True

    # Spotlight settings
    spotlight_intensity: float = 0.3  # Background darkness
    spotlight_feather: int = 50  # Edge softness


class SAM3Processor:
    """
    SAM3 model wrapper for video segmentation.

    Uses HuggingFace Transformers implementation of SAM3 for
    automatic person/object segmentation.
    """

    def __init__(self, model_id: str = "facebook/sam3", device: str = "auto", token: str = None):
        """Initialize SAM3 model."""
        if not TORCH_AVAILABLE:
            raise ImportError("PyTorch required. Install: pip install torch")

        if not SAM3_AVAILABLE:
            raise ImportError(
                "SAM3 not available in transformers.\n"
                "Install: pip install transformers"
            )

        self.device = self._get_device(device)
        self.model = None
        self.processor = None
        self.model_id = model_id
        self.token = token or os.environ.get('HF_TOKEN')
        self._load_model()

    def _get_device(self, device: str) -> str:
        """Determine compute device."""
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            # MPS has issues with float64, use CPU for SAM on Mac
            # elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            #     return "mps"
            return "cpu"
        return device

    def _load_model(self):
        """Load SAM3 model and processor from HuggingFace."""
        print(f"Loading SAM3 on {self.device}...")
        try:
            self.processor = Sam3Processor.from_pretrained(self.model_id, token=self.token)
            self.model = Sam3Model.from_pretrained(
                self.model_id,
                token=self.token
            ).to(self.device)
            self.model.eval()
            print("SAM3 loaded successfully")
        except Exception as e:
            raise RuntimeError(f"Failed to load SAM3: {e}")

    def segment_frame_auto(self, frame: np.ndarray, text_prompt: str = "person") -> Dict[str, Any]:
        """
        Segment subjects in the frame using text prompt.

        SAM3 uses text prompts (like "person") to identify what to segment.

        Args:
            frame: BGR image (OpenCV format)
            text_prompt: Text describing what to segment (default: "person")

        Returns:
            Dict with 'masks' (list of binary masks), 'scores'
        """
        from PIL import Image

        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        h, w = frame.shape[:2]

        # Process with SAM3 using text prompt
        inputs = self.processor(
            images=pil_img,
            text=text_prompt,
            return_tensors="pt"
        )

        # Move inputs to device
        inputs = {k: v.to(self.device) if isinstance(v, torch.Tensor) else v
                  for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model(**inputs)

        # Post-process to get instance masks
        # SAM3 outputs pred_masks and pred_scores
        target_sizes = [(h, w)]
        results = self.processor.post_process_instance_segmentation(
            outputs,
            target_sizes=target_sizes,
            threshold=0.5
        )

        # Extract masks from results
        processed_masks = []
        mask_scores = []

        if results and len(results) > 0:
            result = results[0]  # First image result

            if 'masks' in result:
                masks_tensor = result['masks']
                scores_tensor = result.get('scores', None)

                for i, mask in enumerate(masks_tensor):
                    # Convert boolean/int64 mask to uint8
                    mask_np = mask.cpu().numpy()
                    if mask_np.dtype == np.int64 or mask_np.dtype == bool:
                        mask_np = (mask_np > 0).astype(np.uint8)
                    else:
                        mask_np = mask_np.astype(np.uint8)
                    processed_masks.append(mask_np)

                    if scores_tensor is not None and i < len(scores_tensor):
                        mask_scores.append(float(scores_tensor[i]))
                    else:
                        mask_scores.append(0.5)

        # Combine all person masks into one
        if processed_masks:
            combined_mask = np.zeros((h, w), dtype=np.uint8)
            for mask in processed_masks:
                combined_mask = np.maximum(combined_mask, mask)

            return {
                'masks': [combined_mask],
                'scores': [max(mask_scores) if mask_scores else 0.5]
            }

        return {'masks': [], 'scores': []}


def interpolate_mask(mask1: np.ndarray, mask2: np.ndarray, alpha: float) -> np.ndarray:
    """
    Interpolate between two masks for smooth transitions.

    Args:
        mask1: First mask
        mask2: Second mask
        alpha: Interpolation factor (0=mask1, 1=mask2)

    Returns:
        Interpolated mask
    """
    # Simple linear interpolation
    mask1_f = mask1.astype(np.float32)
    mask2_f = mask2.astype(np.float32)
    interpolated = mask1_f * (1 - alpha) + mask2_f * alpha
    return (interpolated > 0.5).astype(np.uint8)


def get_mask_bbox(mask: np.ndarray) -> Tuple[int, int, int, int]:
    """Get bounding box from binary mask."""
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return (0, 0, 0, 0)
    y_min, y_max = coords[0].min(), coords[0].max()
    x_min, x_max = coords[1].min(), coords[1].max()
    return (x_min, y_min, x_max, y_max)


def apply_desaturate_background(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Keep subjects in color, convert background to grayscale."""
    if not masks:
        return frame

    # Create combined mask for all subjects
    combined_mask = np.zeros(frame.shape[:2], dtype=np.float32)
    for mask in masks:
        combined_mask = np.maximum(combined_mask, mask.astype(np.float32))

    # Blur mask edges for smooth transition
    combined_mask = cv2.GaussianBlur(combined_mask, (21, 21), 0)

    # Convert background to grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    # Blend: mask=1 shows color, mask=0 shows grayscale
    mask_3ch = np.stack([combined_mask] * 3, axis=-1)
    result = (frame * mask_3ch + gray_bgr * (1 - mask_3ch)).astype(np.uint8)

    return result


def apply_contour_effect(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Draw glowing contour lines around subjects."""
    if not masks:
        return frame

    result = frame.copy()

    for mask in masks:
        # Find contours
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if config.contour_glow:
            # Create glow effect with multiple passes
            for i in range(config.glow_intensity, 0, -3):
                alpha = (config.glow_intensity - i) / config.glow_intensity
                color = tuple(int(c * alpha) for c in config.contour_color)
                cv2.drawContours(
                    result, contours, -1, color,
                    config.contour_thickness + i
                )

        # Draw main contour
        cv2.drawContours(
            result, contours, -1, config.contour_color,
            config.contour_thickness
        )

    return result


def apply_bounding_box(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig,
    label: str = "person"
) -> np.ndarray:
    """Draw bounding boxes around subjects."""
    if not masks:
        return frame

    result = frame.copy()

    for i, mask in enumerate(masks):
        x1, y1, x2, y2 = get_mask_bbox(mask)
        if x2 - x1 < 10 or y2 - y1 < 10:
            continue

        # Draw box
        cv2.rectangle(result, (x1, y1), (x2, y2), config.box_color, config.box_thickness)

        # Draw label
        if config.show_label and label:
            label_text = f"{label}"
            (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(result, (x1, y1 - th - 10), (x1 + tw + 10, y1), config.box_color, -1)
            cv2.putText(
                result, label_text, (x1 + 5, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2
            )

    return result


def apply_spotlight(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Highlight subjects, darken background."""
    if not masks:
        return frame

    # Create combined mask
    combined_mask = np.zeros(frame.shape[:2], dtype=np.float32)
    for mask in masks:
        combined_mask = np.maximum(combined_mask, mask.astype(np.float32))

    # Feather edges
    if config.spotlight_feather > 0:
        k = config.spotlight_feather * 2 + 1
        combined_mask = cv2.GaussianBlur(combined_mask, (k, k), 0)

    # Darken background
    darkened = (frame * config.spotlight_intensity).astype(np.uint8)

    # Blend
    mask_3ch = np.stack([combined_mask] * 3, axis=-1)
    result = (frame * mask_3ch + darkened * (1 - mask_3ch)).astype(np.uint8)

    return result


def process_video(
    input_path: str,
    output_path: str,
    effect_config: EffectConfig,
    mask_sample_rate: int = 30,  # Process mask every N frames (30 = 1fps at 30fps video)
    sam_processor: Optional[SAM3Processor] = None
) -> bool:
    """
    Process video with SAM3 effects using 1fps mask sampling.

    Args:
        input_path: Input video path
        output_path: Output video path
        effect_config: Effect configuration
        mask_sample_rate: Process mask every N frames (e.g., 30 for 1fps)
        sam_processor: SAM2 processor (created if None)

    Returns:
        True if successful
    """
    if not CV2_AVAILABLE:
        raise ImportError("OpenCV required. Install: pip install opencv-python")

    # Initialize SAM3 if needed
    if sam_processor is None:
        sam_processor = SAM3Processor()

    # Open video
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        print(f"Error: Cannot open video {input_path}")
        return False

    # Get video properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Create temp video writer (without audio)
    temp_output = output_path + '.temp.mp4'
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(temp_output, fourcc, fps, (width, height))

    print(f"Processing {total_frames} frames with {effect_config.effect_type.value} effect...")
    print(f"  Mask sampling: every {mask_sample_rate} frames ({fps/mask_sample_rate:.1f} mask/sec)")

    frame_count = 0
    prev_mask = None
    next_mask = None
    prev_mask_frame = -1
    next_mask_frame = -1

    # Pre-compute masks at sample points
    mask_cache = {}

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Determine if we need to compute a new mask
        if frame_count % mask_sample_rate == 0 or prev_mask is None:
            # Compute mask for this frame
            seg_result = sam_processor.segment_frame_auto(frame)
            current_mask = seg_result['masks'][0] if seg_result['masks'] else np.zeros((height, width), dtype=np.uint8)

            # Update mask tracking
            prev_mask = current_mask
            prev_mask_frame = frame_count

            # Look ahead for next mask (if not at end)
            next_mask_frame = frame_count + mask_sample_rate
            masks = [current_mask]
        else:
            # Interpolate between prev and next mask
            if next_mask is not None and next_mask_frame > prev_mask_frame:
                alpha = (frame_count - prev_mask_frame) / (next_mask_frame - prev_mask_frame)
                current_mask = interpolate_mask(prev_mask, next_mask, alpha)
            else:
                current_mask = prev_mask
            masks = [current_mask]

        # Apply effect
        if effect_config.effect_type == EffectType.DESATURATE_BG:
            result = apply_desaturate_background(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.CONTOUR:
            result = apply_contour_effect(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.BOUNDING_BOX:
            result = apply_bounding_box(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.SPOTLIGHT:
            result = apply_spotlight(frame, masks, effect_config)

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
    cmd = [
        'ffmpeg', '-y',
        '-i', temp_output,
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
        os.remove(temp_output)
    except:
        pass

    if result.returncode != 0:
        print(f"Warning: Audio mux failed, video-only output")
        os.rename(temp_output, output_path)

    print(f"Done! Output: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Apply SAM3 video effects with 1fps mask sampling',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Effects available:
  desaturate_bg  - Color subject, grayscale background
  contour        - Glowing outline around subjects
  bounding_box   - Track subjects with boxes
  spotlight      - Highlight subject, darken background

Examples:
  # Desaturate background (1fps mask sampling)
  python sam2_effects.py video.mp4 --effect desaturate_bg -o output.mp4

  # Glowing contour effect
  python sam2_effects.py video.mp4 --effect contour --contour-color 0,255,255 -o output.mp4

  # Bounding box tracking
  python sam2_effects.py video.mp4 --effect bounding_box -o output.mp4

NOTE: SAM3 requires HuggingFace authentication. Set HF_TOKEN env var.
        """
    )

    parser.add_argument('input', help='Input video path')
    parser.add_argument('--output', '-o', help='Output video path')
    parser.add_argument('--effect', '-e', required=True,
                        choices=[e.value for e in EffectType],
                        help='Effect type to apply')

    # Mask sampling
    parser.add_argument('--sample-rate', type=int, default=30,
                        help='Process mask every N frames (default: 30 = 1fps at 30fps)')

    # Contour options
    parser.add_argument('--contour-color', default='0,255,255',
                        help='Contour color as R,G,B (default: cyan)')
    parser.add_argument('--contour-thickness', type=int, default=3)
    parser.add_argument('--no-glow', action='store_true', help='Disable glow effect')

    # Bounding box options
    parser.add_argument('--box-color', default='0,255,0',
                        help='Box color as R,G,B (default: green)')
    parser.add_argument('--no-label', action='store_true', help='Hide labels on boxes')

    # Spotlight options
    parser.add_argument('--spotlight-intensity', type=float, default=0.3,
                        help='Background darkness (0-1, lower=darker)')

    # Model options
    parser.add_argument('--model', default='facebook/sam3',
                        help='SAM model ID (default: facebook/sam3)')
    parser.add_argument('--device', default='auto',
                        choices=['auto', 'cpu', 'cuda', 'mps'],
                        help='Compute device')
    parser.add_argument('--token', default=None,
                        help='HuggingFace token (or set HF_TOKEN env var)')

    args = parser.parse_args()

    # Parse colors
    def parse_color(s):
        return tuple(int(x) for x in s.split(','))

    # Build config
    config = EffectConfig(
        effect_type=EffectType(args.effect),
        contour_color=parse_color(args.contour_color),
        contour_thickness=args.contour_thickness,
        contour_glow=not args.no_glow,
        box_color=parse_color(args.box_color),
        show_label=not args.no_label,
        spotlight_intensity=args.spotlight_intensity,
    )

    # Default output path
    input_path = Path(args.input)
    output_path = args.output or str(
        input_path.parent / f"{input_path.stem}_{args.effect}_sam.mp4"
    )

    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        # Initialize SAM3
        sam = SAM3Processor(model_id=args.model, device=args.device, token=args.token)

        # Process video
        success = process_video(
            args.input,
            output_path,
            config,
            mask_sample_rate=args.sample_rate,
            sam_processor=sam
        )

        if success:
            print(f"\n{config.effect_type.value} effect applied successfully!")
            print(f"Output: {output_path}")
        else:
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
