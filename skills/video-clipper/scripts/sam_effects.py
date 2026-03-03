#!/usr/bin/env python3
"""
SAM 3 (Segment Anything Model 3) video effects.

Uses Meta's SAM 3 via HuggingFace Transformers for text-prompted segmentation
to create professional video effects:
- Desaturate background (color subject, B&W background)
- Contour lines / glow effects
- Bounding boxes with tracking
- Motion trails
- Smart face/person zoom
- Face blur (privacy)
- Spotlight effect
- Clone squad (duplicate subjects)
- Green screen (background removal/replacement)

Requires: transformers (dev), torch, opencv-python
Install: pip install git+https://github.com/huggingface/transformers.git opencv-python
Setup: Set HF_TOKEN in .env file (get from https://huggingface.co/settings/tokens)
       Request access at https://huggingface.co/facebook/sam3
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any
from enum import Enum
import subprocess

try:
    import cv2
    import numpy as np
    from PIL import Image
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

# Check for SAM3 via Transformers
SAM3_AVAILABLE = False
try:
    from transformers import Sam3Processor, Sam3Model
    SAM3_AVAILABLE = True
except ImportError:
    pass


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


class EffectType(Enum):
    """Available SAM 3 and SAM 3D effects."""
    # SAM 3 Segmentation Effects
    DESATURATE_BG = "desaturate_bg"
    CONTOUR = "contour"
    BOUNDING_BOX = "bounding_box"
    MOTION_TRAIL = "motion_trail"
    FACE_ZOOM = "face_zoom"
    BLUR_FACE = "blur_face"
    SPOTLIGHT = "spotlight"
    CLONE_SQUAD = "clone_squad"
    GREEN_SCREEN = "green_screen"
    # SAM 3D Object Effects (3D reconstruction)
    OBJECT_3D_SPIN = "object_3d_spin"
    OBJECT_3D_ISOLATE = "object_3d_isolate"
    OBJECT_3D_GLOW = "object_3d_glow"
    # SAM 3D Body Effects (pose/body estimation)
    BODY_POSE_OVERLAY = "body_pose_overlay"
    BODY_SILHOUETTE = "body_silhouette"


@dataclass
class EffectConfig:
    """Configuration for video effects."""
    effect_type: EffectType
    prompt: str = "person"  # SAM 3 concept prompt

    # Contour settings
    contour_color: Tuple[int, int, int] = (0, 255, 255)  # Cyan
    contour_thickness: int = 3
    contour_glow: bool = True
    glow_intensity: int = 15

    # Bounding box settings
    box_color: Tuple[int, int, int] = (0, 255, 0)  # Green
    box_thickness: int = 2
    show_label: bool = True

    # Motion trail settings
    trail_length: int = 30  # frames
    trail_color: Tuple[int, int, int] = (255, 100, 100)  # Light red
    trail_fade: bool = True

    # Spotlight settings
    spotlight_intensity: float = 0.3  # Background darkness
    spotlight_feather: int = 50  # Edge softness

    # Blur settings
    blur_strength: int = 51  # Must be odd

    # Clone squad settings
    clone_count: int = 3
    clone_offset: int = 100  # pixels between clones

    # Green screen settings
    bg_color: Tuple[int, int, int] = (0, 255, 0)  # Green
    bg_image_path: Optional[str] = None

    # Face zoom settings
    zoom_padding: float = 0.3  # Extra padding around face
    smooth_zoom: bool = True
    zoom_speed: float = 0.1  # Interpolation factor

    # SAM 3D Object settings
    object_3d_rotation: float = 360.0  # Degrees to rotate
    object_3d_duration: float = 3.0  # Seconds for full rotation
    object_3d_scale: float = 1.2  # Scale factor for isolated object
    object_3d_glow_color: Tuple[int, int, int] = (255, 215, 0)  # Gold glow
    object_3d_glow_intensity: int = 20

    # SAM 3D Body settings
    body_pose_color: Tuple[int, int, int] = (0, 255, 255)  # Cyan skeleton
    body_pose_thickness: int = 3
    body_silhouette_color: Tuple[int, int, int] = (255, 255, 255)  # White


class SAM3Processor:
    """
    SAM 3 model wrapper for video segmentation.

    Uses HuggingFace Transformers implementation of SAM 3 for
    text-prompted segmentation of objects like "person", "face", "car", etc.

    Requires: pip install git+https://github.com/huggingface/transformers.git
    Setup: Set HF_TOKEN in .env and request access at https://huggingface.co/facebook/sam3
    """

    def __init__(self, model_size: str = "large", device: str = "auto"):
        """Initialize SAM 3 model."""
        if not TORCH_AVAILABLE:
            raise ImportError("PyTorch required. Install: pip install torch")

        if not SAM3_AVAILABLE:
            raise ImportError(
                "SAM 3 not available in transformers.\n"
                "Install: pip install git+https://github.com/huggingface/transformers.git\n"
                "Setup: Set HF_TOKEN in .env file\n"
                "       Request access at https://huggingface.co/facebook/sam3"
            )

        # Load environment variables (for HF_TOKEN)
        load_env()

        self.device = self._get_device(device)
        self.model = None
        self.processor = None
        self.model_size = model_size
        self._load_model()

    def _get_device(self, device: str) -> str:
        """Determine compute device."""
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
            return "cpu"
        return device

    def _load_model(self):
        """Load SAM 3 model and processor from HuggingFace."""
        print(f"Loading SAM 3 on {self.device}...")
        try:
            self.model = Sam3Model.from_pretrained("facebook/sam3").to(self.device)
            self.processor = Sam3Processor.from_pretrained("facebook/sam3")
            print("SAM 3 loaded successfully")
        except Exception as e:
            raise RuntimeError(
                f"Failed to load SAM 3: {e}\n"
                "Make sure you have:\n"
                "1. Set HF_TOKEN in .env file\n"
                "2. Requested access at https://huggingface.co/facebook/sam3\n"
                "3. Installed transformers from git: pip install git+https://github.com/huggingface/transformers.git"
            )

    def segment_frame(
        self,
        frame: np.ndarray,
        prompt: str,
        return_boxes: bool = False
    ) -> Dict[str, Any]:
        """
        Segment objects in a frame using text prompt.

        Args:
            frame: BGR image (OpenCV format)
            prompt: Text description of objects to segment (e.g., "person", "face")
            return_boxes: Also return bounding boxes

        Returns:
            Dict with 'masks' (list of binary masks), 'boxes' (if requested),
            'scores' (confidence scores)
        """
        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        h, w = frame.shape[:2]

        # Process with SAM 3
        inputs = self.processor(images=pil_img, text=prompt, return_tensors="pt").to(self.device)

        with torch.no_grad():
            outputs = self.model(**inputs)

        # Post-process results
        results = self.processor.post_process_instance_segmentation(
            outputs,
            threshold=0.5,
            mask_threshold=0.5,
            target_sizes=[(h, w)]
        )[0]

        # Convert masks to numpy
        processed_masks = []
        scores = []
        boxes = []

        num_masks = len(results['masks'])
        for i in range(num_masks):
            mask = results['masks'][i].cpu().numpy().astype(np.uint8)
            processed_masks.append(mask)
            scores.append(float(results['scores'][i]) if 'scores' in results else 1.0)

            if return_boxes and 'boxes' in results:
                boxes.append(results['boxes'][i].cpu().numpy().tolist())

        result = {
            'masks': processed_masks,
            'scores': scores
        }

        if return_boxes:
            result['boxes'] = boxes

        return result


class BodyPoseEstimator:
    """
    Body pose estimation using MediaPipe.
    Used for pose overlay effects.
    """

    def __init__(self):
        """Initialize MediaPipe pose estimator."""
        self.pose = None
        self._load_model()

    def _load_model(self):
        """Load MediaPipe pose model."""
        try:
            import mediapipe as mp
            self.pose = mp.solutions.pose.Pose(static_image_mode=True)
            print("MediaPipe pose estimator loaded")
        except ImportError:
            print("MediaPipe not available. Install: pip install mediapipe")
            self.pose = None

    def estimate_pose(self, image: np.ndarray) -> Dict[str, Any]:
        """
        Estimate body pose from image.

        Args:
            image: BGR image (OpenCV format)

        Returns:
            Dict with pose landmarks
        """
        if self.pose is None:
            return {'error': 'MediaPipe not installed. Run: pip install mediapipe'}

        results = self.pose.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

        if results.pose_landmarks:
            landmarks = []
            for lm in results.pose_landmarks.landmark:
                landmarks.append({
                    'x': lm.x,
                    'y': lm.y,
                    'z': lm.z,
                    'visibility': lm.visibility
                })
            return {
                'landmarks': landmarks,
                'success': True,
                'source': 'mediapipe'
            }
        return {'success': False, 'error': 'No pose detected'}


def apply_object_3d_glow(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Apply glowing 3D-style effect to isolated objects."""
    result = frame.copy()

    for mask in masks:
        # Find contours for glow
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Multi-layer glow effect
        for i in range(config.object_3d_glow_intensity, 0, -2):
            alpha = (config.object_3d_glow_intensity - i) / config.object_3d_glow_intensity
            color = tuple(int(c * alpha) for c in config.object_3d_glow_color)
            thickness = i * 2
            cv2.drawContours(result, contours, -1, color, thickness)

        # Inner highlight
        cv2.drawContours(result, contours, -1, (255, 255, 255), 2)

    return result


def apply_object_3d_isolate(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Isolate object with dramatic background removal and scaling."""
    h, w = frame.shape[:2]

    # Create dark/blurred background
    bg = cv2.GaussianBlur(frame, (51, 51), 0)
    bg = (bg * 0.2).astype(np.uint8)  # Darken

    # Combine masks
    combined_mask = np.zeros((h, w), dtype=np.float32)
    for mask in masks:
        combined_mask = np.maximum(combined_mask, mask.astype(np.float32))

    # Feather edges
    combined_mask = cv2.GaussianBlur(combined_mask, (11, 11), 0)

    # Scale up the object region
    if config.object_3d_scale != 1.0:
        # Find bounding box
        coords = np.where(combined_mask > 0.5)
        if len(coords[0]) > 0:
            y_min, y_max = coords[0].min(), coords[0].max()
            x_min, x_max = coords[1].min(), coords[1].max()

            # Extract object
            obj_h = y_max - y_min
            obj_w = x_max - x_min
            obj_region = frame[y_min:y_max, x_min:x_max]
            obj_mask = combined_mask[y_min:y_max, x_min:x_max]

            # Scale
            new_h = int(obj_h * config.object_3d_scale)
            new_w = int(obj_w * config.object_3d_scale)
            scaled_obj = cv2.resize(obj_region, (new_w, new_h))
            scaled_mask = cv2.resize(obj_mask, (new_w, new_h))

            # Center in frame
            new_y = max(0, (h - new_h) // 2)
            new_x = max(0, (w - new_w) // 2)

            # Composite
            result = bg.copy()
            end_y = min(new_y + new_h, h)
            end_x = min(new_x + new_w, w)
            crop_h = end_y - new_y
            crop_w = end_x - new_x

            mask_3ch = np.stack([scaled_mask[:crop_h, :crop_w]] * 3, axis=-1)
            result[new_y:end_y, new_x:end_x] = (
                scaled_obj[:crop_h, :crop_w] * mask_3ch +
                result[new_y:end_y, new_x:end_x] * (1 - mask_3ch)
            ).astype(np.uint8)

            return result

    # Standard composite without scaling
    mask_3ch = np.stack([combined_mask] * 3, axis=-1)
    result = (frame * mask_3ch + bg * (1 - mask_3ch)).astype(np.uint8)

    return result


def apply_body_pose_overlay(
    frame: np.ndarray,
    pose_data: Dict[str, Any],
    config: EffectConfig
) -> np.ndarray:
    """Draw body pose skeleton overlay."""
    result = frame.copy()
    h, w = frame.shape[:2]

    if not pose_data.get('success') or 'landmarks' not in pose_data:
        return result

    landmarks = pose_data['landmarks']

    # MediaPipe pose connections
    connections = [
        (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),  # Arms
        (11, 23), (12, 24), (23, 24),  # Torso
        (23, 25), (25, 27), (24, 26), (26, 28),  # Legs
        (0, 1), (1, 2), (2, 3), (3, 7),  # Face
        (0, 4), (4, 5), (5, 6), (6, 8),
    ]

    # Draw connections
    for start_idx, end_idx in connections:
        if start_idx < len(landmarks) and end_idx < len(landmarks):
            start = landmarks[start_idx]
            end = landmarks[end_idx]

            if start['visibility'] > 0.5 and end['visibility'] > 0.5:
                pt1 = (int(start['x'] * w), int(start['y'] * h))
                pt2 = (int(end['x'] * w), int(end['y'] * h))
                cv2.line(result, pt1, pt2, config.body_pose_color,
                        config.body_pose_thickness)

    # Draw keypoints
    for lm in landmarks:
        if lm['visibility'] > 0.5:
            pt = (int(lm['x'] * w), int(lm['y'] * h))
            cv2.circle(result, pt, 5, config.body_pose_color, -1)
            cv2.circle(result, pt, 7, (255, 255, 255), 2)

    return result


def apply_body_silhouette(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Create stylized body silhouette effect."""
    h, w = frame.shape[:2]

    # Create silhouette from masks
    combined_mask = np.zeros((h, w), dtype=np.float32)
    for mask in masks:
        combined_mask = np.maximum(combined_mask, mask.astype(np.float32))

    # Create gradient background
    gradient = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(h):
        ratio = i / h
        gradient[i, :] = [int(30 * ratio), int(30 * ratio), int(50 * ratio)]

    # Create white silhouette
    silhouette = np.zeros_like(frame)
    silhouette[combined_mask > 0.5] = config.body_silhouette_color

    # Add glow around silhouette
    mask_dilated = cv2.dilate(
        (combined_mask * 255).astype(np.uint8),
        np.ones((15, 15), np.uint8)
    )
    mask_dilated = cv2.GaussianBlur(mask_dilated, (21, 21), 0)

    glow = np.zeros_like(frame)
    glow[:, :] = config.body_silhouette_color
    glow_alpha = (mask_dilated / 255.0 * 0.5)[:, :, np.newaxis]

    # Composite: gradient + glow + silhouette
    result = gradient.copy()
    result = (result * (1 - glow_alpha) + glow * glow_alpha).astype(np.uint8)

    mask_3ch = np.stack([combined_mask] * 3, axis=-1)
    result = np.where(mask_3ch > 0.5, silhouette, result)

    return result


def get_mask_bbox(mask: np.ndarray) -> Tuple[int, int, int, int]:
    """Get bounding box from binary mask."""
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return (0, 0, 0, 0)
    y_min, y_max = coords[0].min(), coords[0].max()
    x_min, x_max = coords[1].min(), coords[1].max()
    return (x_min, y_min, x_max, y_max)


def get_mask_centroid(mask: np.ndarray) -> Tuple[int, int]:
    """Get centroid of mask."""
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return (0, 0)
    cy = int(coords[0].mean())
    cx = int(coords[1].mean())
    return (cx, cy)


def apply_desaturate_background(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Keep subjects in color, convert background to grayscale."""
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
    label: str = ""
) -> np.ndarray:
    """Draw bounding boxes around subjects."""
    result = frame.copy()

    for i, mask in enumerate(masks):
        x1, y1, x2, y2 = get_mask_bbox(mask)
        if x2 - x1 < 10 or y2 - y1 < 10:
            continue

        # Draw box
        cv2.rectangle(result, (x1, y1), (x2, y2), config.box_color, config.box_thickness)

        # Draw label
        if config.show_label and label:
            label_text = f"{label} #{i+1}"
            (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(result, (x1, y1 - th - 10), (x1 + tw + 10, y1), config.box_color, -1)
            cv2.putText(
                result, label_text, (x1 + 5, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2
            )

    return result


class MotionTrailTracker:
    """Tracks object positions over time for motion trails."""

    def __init__(self, max_length: int = 30):
        self.max_length = max_length
        self.trails: Dict[int, List[Tuple[int, int]]] = {}

    def update(self, masks: List[np.ndarray]) -> Dict[int, List[Tuple[int, int]]]:
        """Update trails with new mask positions."""
        # Simple tracking: match by closest centroid
        new_centroids = [get_mask_centroid(m) for m in masks]

        # For simplicity, track first N objects
        for i, centroid in enumerate(new_centroids):
            if i not in self.trails:
                self.trails[i] = []
            self.trails[i].append(centroid)
            if len(self.trails[i]) > self.max_length:
                self.trails[i].pop(0)

        return self.trails


def apply_motion_trail(
    frame: np.ndarray,
    trails: Dict[int, List[Tuple[int, int]]],
    config: EffectConfig
) -> np.ndarray:
    """Draw motion trails behind subjects."""
    result = frame.copy()

    for obj_id, trail in trails.items():
        if len(trail) < 2:
            continue

        for i in range(1, len(trail)):
            if config.trail_fade:
                # Fade trail color based on age
                alpha = i / len(trail)
                color = tuple(int(c * alpha) for c in config.trail_color)
                thickness = max(1, int(5 * alpha))
            else:
                color = config.trail_color
                thickness = 3

            cv2.line(result, trail[i-1], trail[i], color, thickness)

    return result


def apply_spotlight(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Highlight subjects, darken background."""
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


def apply_blur_face(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Blur detected faces/subjects for privacy."""
    result = frame.copy()

    for mask in masks:
        x1, y1, x2, y2 = get_mask_bbox(mask)
        if x2 - x1 < 10 or y2 - y1 < 10:
            continue

        # Extract and blur region
        roi = result[y1:y2, x1:x2]
        blurred = cv2.GaussianBlur(roi, (config.blur_strength, config.blur_strength), 0)

        # Apply blur only within mask
        mask_roi = mask[y1:y2, x1:x2]
        mask_3ch = np.stack([mask_roi] * 3, axis=-1)
        result[y1:y2, x1:x2] = np.where(mask_3ch, blurred, roi)

    return result


def apply_green_screen(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig,
    bg_frame: Optional[np.ndarray] = None
) -> np.ndarray:
    """Remove background, replace with color or image."""
    # Create combined mask
    combined_mask = np.zeros(frame.shape[:2], dtype=np.float32)
    for mask in masks:
        combined_mask = np.maximum(combined_mask, mask.astype(np.float32))

    # Feather edges
    combined_mask = cv2.GaussianBlur(combined_mask, (5, 5), 0)

    # Create background
    if bg_frame is not None:
        bg = cv2.resize(bg_frame, (frame.shape[1], frame.shape[0]))
    else:
        bg = np.full(frame.shape, config.bg_color, dtype=np.uint8)

    # Composite
    mask_3ch = np.stack([combined_mask] * 3, axis=-1)
    result = (frame * mask_3ch + bg * (1 - mask_3ch)).astype(np.uint8)

    return result


def apply_clone_squad(
    frame: np.ndarray,
    masks: List[np.ndarray],
    config: EffectConfig
) -> np.ndarray:
    """Duplicate subjects multiple times across frame."""
    result = frame.copy()
    h, w = frame.shape[:2]

    for mask in masks:
        x1, y1, x2, y2 = get_mask_bbox(mask)
        if x2 - x1 < 10:
            continue

        subject_width = x2 - x1
        subject = frame[y1:y2, x1:x2].copy()
        subject_mask = mask[y1:y2, x1:x2]

        # Create clones at different positions
        for i in range(1, config.clone_count + 1):
            # Offset position
            new_x = x1 + (i * config.clone_offset)
            if new_x + subject_width > w:
                new_x = x1 - (i * config.clone_offset)
            if new_x < 0:
                continue

            # Paste clone
            paste_region = result[y1:y2, new_x:new_x + subject_width]
            mask_3ch = np.stack([subject_mask] * 3, axis=-1)

            if paste_region.shape == subject.shape:
                result[y1:y2, new_x:new_x + subject_width] = np.where(
                    mask_3ch, subject, paste_region
                )

    return result


class FaceZoomTracker:
    """Smooth zoom tracking for face/person following."""

    def __init__(self, smooth_factor: float = 0.1):
        self.smooth_factor = smooth_factor
        self.current_bbox: Optional[Tuple[int, int, int, int]] = None

    def update(self, masks: List[np.ndarray], frame_shape: Tuple[int, int]) -> Tuple[int, int, int, int]:
        """Get smoothed bounding box for zoom."""
        h, w = frame_shape[:2]

        if not masks:
            return (0, 0, w, h)

        # Get combined bbox of all masks
        all_coords_x = []
        all_coords_y = []
        for mask in masks:
            coords = np.where(mask > 0)
            if len(coords[0]) > 0:
                all_coords_y.extend(coords[0])
                all_coords_x.extend(coords[1])

        if not all_coords_x:
            return (0, 0, w, h)

        target_bbox = (
            min(all_coords_x),
            min(all_coords_y),
            max(all_coords_x),
            max(all_coords_y)
        )

        # Smooth transition
        if self.current_bbox is None:
            self.current_bbox = target_bbox
        else:
            self.current_bbox = tuple(
                int(c + (t - c) * self.smooth_factor)
                for c, t in zip(self.current_bbox, target_bbox)
            )

        return self.current_bbox


def apply_face_zoom(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
    config: EffectConfig,
    output_size: Tuple[int, int] = (1080, 1920)
) -> np.ndarray:
    """Zoom and crop to follow face/person."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox

    # Add padding
    pad_w = int((x2 - x1) * config.zoom_padding)
    pad_h = int((y2 - y1) * config.zoom_padding)

    x1 = max(0, x1 - pad_w)
    y1 = max(0, y1 - pad_h)
    x2 = min(w, x2 + pad_w)
    y2 = min(h, y2 + pad_h)

    # Ensure aspect ratio for vertical video
    target_aspect = output_size[0] / output_size[1]  # width/height
    current_aspect = (x2 - x1) / (y2 - y1)

    if current_aspect > target_aspect:
        # Too wide, expand height
        new_h = int((x2 - x1) / target_aspect)
        center_y = (y1 + y2) // 2
        y1 = max(0, center_y - new_h // 2)
        y2 = min(h, y1 + new_h)
    else:
        # Too tall, expand width
        new_w = int((y2 - y1) * target_aspect)
        center_x = (x1 + x2) // 2
        x1 = max(0, center_x - new_w // 2)
        x2 = min(w, x1 + new_w)

    # Crop and resize
    cropped = frame[y1:y2, x1:x2]
    result = cv2.resize(cropped, output_size)

    return result


def process_video(
    input_path: str,
    output_path: str,
    effect_config: EffectConfig,
    sam_processor: Optional[SAM3Processor] = None,
    progress_callback: Optional[callable] = None
) -> bool:
    """
    Process video with SAM 3 effects.

    Args:
        input_path: Input video path
        output_path: Output video path
        effect_config: Effect configuration
        sam_processor: SAM 3 processor (created if None)
        progress_callback: Optional callback(current_frame, total_frames)

    Returns:
        True if successful
    """
    if not CV2_AVAILABLE:
        raise ImportError("OpenCV required. Install: pip install opencv-python")

    # Initialize SAM if needed
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

    # Adjust output size for face zoom
    if effect_config.effect_type == EffectType.FACE_ZOOM:
        out_width, out_height = 1080, 1920  # Vertical format
    else:
        out_width, out_height = width, height

    # Create video writer
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (out_width, out_height))

    # Initialize trackers
    trail_tracker = MotionTrailTracker(effect_config.trail_length)
    zoom_tracker = FaceZoomTracker(effect_config.zoom_speed)

    # Load background image if needed
    bg_frame = None
    if effect_config.bg_image_path and Path(effect_config.bg_image_path).exists():
        bg_frame = cv2.imread(effect_config.bg_image_path)

    frame_count = 0

    print(f"Processing {total_frames} frames with {effect_config.effect_type.value} effect...")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Segment frame
        seg_result = sam_processor.segment_frame(
            frame,
            effect_config.prompt,
            return_boxes=(effect_config.effect_type == EffectType.BOUNDING_BOX)
        )
        masks = seg_result['masks']

        # Apply effect
        if effect_config.effect_type == EffectType.DESATURATE_BG:
            result = apply_desaturate_background(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.CONTOUR:
            result = apply_contour_effect(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.BOUNDING_BOX:
            result = apply_bounding_box(frame, masks, effect_config, effect_config.prompt)

        elif effect_config.effect_type == EffectType.MOTION_TRAIL:
            trails = trail_tracker.update(masks)
            result = apply_motion_trail(frame, trails, effect_config)

        elif effect_config.effect_type == EffectType.SPOTLIGHT:
            result = apply_spotlight(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.BLUR_FACE:
            result = apply_blur_face(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.GREEN_SCREEN:
            result = apply_green_screen(frame, masks, effect_config, bg_frame)

        elif effect_config.effect_type == EffectType.CLONE_SQUAD:
            result = apply_clone_squad(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.FACE_ZOOM:
            bbox = zoom_tracker.update(masks, frame.shape)
            result = apply_face_zoom(frame, bbox, effect_config, (out_width, out_height))

        # SAM 3D Object Effects
        elif effect_config.effect_type == EffectType.OBJECT_3D_GLOW:
            result = apply_object_3d_glow(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.OBJECT_3D_ISOLATE:
            result = apply_object_3d_isolate(frame, masks, effect_config)

        # SAM 3D Body Effects
        elif effect_config.effect_type == EffectType.BODY_SILHOUETTE:
            result = apply_body_silhouette(frame, masks, effect_config)

        elif effect_config.effect_type == EffectType.BODY_POSE_OVERLAY:
            # Use MediaPipe for pose estimation
            pose_estimator = BodyPoseEstimator()
            pose_data = pose_estimator.estimate_pose(frame)
            result = apply_body_pose_overlay(frame, pose_data, effect_config)

        else:
            result = frame

        out.write(result)
        frame_count += 1

        if progress_callback:
            progress_callback(frame_count, total_frames)
        elif frame_count % 30 == 0:
            print(f"  Progress: {frame_count}/{total_frames} ({100*frame_count/total_frames:.1f}%)")

    cap.release()
    out.release()

    print(f"Saved to: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Apply SAM 3 video effects',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Effects available:
  desaturate_bg  - Color subject, grayscale background
  contour        - Glowing outline around subjects
  bounding_box   - Track subjects with boxes
  motion_trail   - Visualize movement paths
  face_zoom      - Auto-crop following faces (vertical output)
  blur_face      - Blur faces for privacy
  spotlight      - Highlight subject, darken background
  clone_squad    - Duplicate subjects
  green_screen   - Remove/replace background

Examples:
  # Desaturate background, keeping people in color
  python sam_effects.py video.mp4 --effect desaturate_bg --prompt "person"

  # Add glowing contour around people
  python sam_effects.py video.mp4 --effect contour --prompt "person" --contour-color 0,255,255

  # Auto-zoom following faces for vertical video
  python sam_effects.py video.mp4 --effect face_zoom --prompt "face"

  # Blur all faces for privacy
  python sam_effects.py video.mp4 --effect blur_face --prompt "face"

  # Green screen with custom background
  python sam_effects.py video.mp4 --effect green_screen --prompt "person" --bg-image beach.jpg
        """
    )

    parser.add_argument('input', help='Input video path')
    parser.add_argument('--output', '-o', help='Output video path')
    parser.add_argument('--effect', '-e', required=True,
                        choices=[e.value for e in EffectType],
                        help='Effect type to apply')
    parser.add_argument('--prompt', '-p', default='person',
                        help='SAM 3 concept prompt (e.g., "person", "face", "dog")')

    # Contour options
    parser.add_argument('--contour-color', default='0,255,255',
                        help='Contour color as R,G,B (default: cyan)')
    parser.add_argument('--contour-thickness', type=int, default=3)
    parser.add_argument('--no-glow', action='store_true', help='Disable glow effect')

    # Bounding box options
    parser.add_argument('--box-color', default='0,255,0',
                        help='Box color as R,G,B (default: green)')
    parser.add_argument('--no-label', action='store_true', help='Hide labels on boxes')

    # Motion trail options
    parser.add_argument('--trail-length', type=int, default=30,
                        help='Motion trail length in frames')
    parser.add_argument('--trail-color', default='255,100,100',
                        help='Trail color as R,G,B')

    # Spotlight options
    parser.add_argument('--spotlight-intensity', type=float, default=0.3,
                        help='Background darkness (0-1, lower=darker)')

    # Blur options
    parser.add_argument('--blur-strength', type=int, default=51,
                        help='Blur kernel size (must be odd)')

    # Clone options
    parser.add_argument('--clone-count', type=int, default=3,
                        help='Number of clones to create')
    parser.add_argument('--clone-offset', type=int, default=100,
                        help='Pixels between clones')

    # Green screen options
    parser.add_argument('--bg-color', default='0,255,0',
                        help='Background color as R,G,B (default: green)')
    parser.add_argument('--bg-image', help='Background image path')

    # Face zoom options
    parser.add_argument('--zoom-padding', type=float, default=0.3,
                        help='Padding around face (0-1)')
    parser.add_argument('--zoom-speed', type=float, default=0.1,
                        help='Zoom smoothing factor (0-1, lower=smoother)')

    # Model options
    parser.add_argument('--model-size', default='large',
                        choices=['tiny', 'small', 'base', 'large'],
                        help='SAM 3 model size')
    parser.add_argument('--device', default='auto',
                        choices=['auto', 'cpu', 'cuda', 'mps'],
                        help='Compute device')

    args = parser.parse_args()

    # Parse colors
    def parse_color(s):
        return tuple(int(x) for x in s.split(','))

    # Build config
    config = EffectConfig(
        effect_type=EffectType(args.effect),
        prompt=args.prompt,
        contour_color=parse_color(args.contour_color),
        contour_thickness=args.contour_thickness,
        contour_glow=not args.no_glow,
        box_color=parse_color(args.box_color),
        show_label=not args.no_label,
        trail_length=args.trail_length,
        trail_color=parse_color(args.trail_color),
        spotlight_intensity=args.spotlight_intensity,
        blur_strength=args.blur_strength if args.blur_strength % 2 == 1 else args.blur_strength + 1,
        clone_count=args.clone_count,
        clone_offset=args.clone_offset,
        bg_color=parse_color(args.bg_color),
        bg_image_path=args.bg_image,
        zoom_padding=args.zoom_padding,
        zoom_speed=args.zoom_speed,
    )

    # Default output path
    input_path = Path(args.input)
    output_path = args.output or str(
        input_path.parent / f"{input_path.stem}_{args.effect}{input_path.suffix}"
    )

    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        # Initialize SAM
        sam = SAM3Processor(model_size=args.model_size, device=args.device)

        # Process video
        success = process_video(args.input, output_path, config, sam)

        if success:
            print(f"\n{config.effect_type.value} effect applied successfully!")
            print(f"Output: {output_path}")
        else:
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
