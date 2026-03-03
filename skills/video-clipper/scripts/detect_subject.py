#!/usr/bin/env python3
"""
Subject detection for smart cropping.

Uses MediaPipe for fast face/pose detection to determine optimal crop
position when converting 16:9 → 9:16 for vertical video.

Usage:
    # Detect subject position at a specific timestamp
    python detect_subject.py video.mp4 --timestamp 10.5

    # Output JSON for use with clip_extractor
    python detect_subject.py video.mp4 --timestamp 10.5 --output-json pos.json

    # Multi-face detection for interviews
    python detect_subject.py video.mp4 --timestamp 10.5 --multi-face
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import subprocess
import tempfile
import urllib.request

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

MP_AVAILABLE = False
TASKS_AVAILABLE = False
try:
    import mediapipe as mp
    MP_AVAILABLE = True
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        TASKS_AVAILABLE = True
    except Exception:
        TASKS_AVAILABLE = False
except ImportError:
    MP_AVAILABLE = False
    TASKS_AVAILABLE = False


def get_model_path(model_name: str) -> str:
    """Get or download MediaPipe model file."""
    models_dir = Path(__file__).parent.parent / 'models'
    models_dir.mkdir(exist_ok=True)

    model_path = models_dir / model_name

    if not model_path.exists():
        # Download the model
        base_url = "https://storage.googleapis.com/mediapipe-models"

        if 'face_detector' in model_name or 'blaze_face' in model_name:
            url = f"{base_url}/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
            model_path = models_dir / 'blaze_face_short_range.tflite'
        elif 'pose' in model_name:
            url = f"{base_url}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
            model_path = models_dir / 'pose_landmarker_lite.task'
        else:
            return None

        if not model_path.exists():
            print(f"Downloading {model_path.name}...")
            try:
                urllib.request.urlretrieve(url, model_path)
            except Exception as e:
                print(f"Failed to download model: {e}")
                return None

    return str(model_path)


def extract_frame(video_path: str, timestamp: float) -> Optional[np.ndarray]:
    """Extract a single frame from video at given timestamp."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    # Seek to timestamp
    cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
    ret, frame = cap.read()
    cap.release()

    return frame if ret else None


def detect_faces_mediapipe(frame: np.ndarray, multi_face: bool = False) -> List[Dict]:
    """
    Detect faces using MediaPipe Face Detection (Tasks API).

    Returns list of face detections with normalized coordinates (0-1).
    """
    if not MP_AVAILABLE:
        raise ImportError("MediaPipe required. Install: pip install mediapipe")

    def _solutions_face_detect(*, model_selection: int) -> List[Dict]:
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        with mp.solutions.face_detection.FaceDetection(  # type: ignore[attr-defined]
            model_selection=int(model_selection),
            min_detection_confidence=0.5,
        ) as detector:
            res = detector.process(rgb_frame)
        dets = getattr(res, "detections", None) or []
        out: List[Dict] = []
        for det in dets:
            try:
                bbox = det.location_data.relative_bounding_box
                # RelativeBoundingBox uses xmin/ymin/width/height in [0..1]
                cx = float(bbox.xmin) + float(bbox.width) / 2.0
                cy = float(bbox.ymin) + float(bbox.height) / 2.0
                conf = float(det.score[0]) if getattr(det, "score", None) else 0.5
                out.append(
                    {
                        "x": cx,
                        "y": cy,
                        "width": float(bbox.width),
                        "height": float(bbox.height),
                        "confidence": conf,
                    }
                )
            except Exception:
                continue
        if not out:
            return []
        out.sort(key=lambda f: float(f.get("width", 0.0)) * float(f.get("height", 0.0)), reverse=True)
        return out if multi_face else out[:1]

    # Prefer Tasks API when available (fast + deterministic model download). For multi-face,
    # we lower the threshold slightly to avoid missing smaller/dimmer faces in wide shots.
    if TASKS_AVAILABLE:
        model_path = get_model_path("blaze_face_short_range.tflite")
        if model_path and os.path.exists(model_path):
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]

            base_options = mp_python.BaseOptions(model_asset_path=model_path)
            min_conf = 0.30 if bool(multi_face) else 0.50
            options = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=float(min_conf))

            faces: List[Dict] = []
            with vision.FaceDetector.create_from_options(options) as detector:
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                result = detector.detect(mp_image)
                for detection in result.detections:
                    bbox = detection.bounding_box
                    center_x = (bbox.origin_x + bbox.width / 2) / w
                    center_y = (bbox.origin_y + bbox.height / 2) / h
                    faces.append(
                        {
                            "x": center_x,
                            "y": center_y,
                            "width": bbox.width / w,
                            "height": bbox.height / h,
                            "confidence": detection.categories[0].score if detection.categories else 0.5,
                        }
                    )
                    if not multi_face:
                        break
            if faces:
                faces.sort(key=lambda f: float(f.get("width", 0.0)) * float(f.get("height", 0.0)), reverse=True)
                # Avoid exploding work downstream on false positives.
                if bool(multi_face):
                    return faces[:8]
                return faces[:1]

    # Fallback: solutions full-range for multi-face, then short-range, then OpenCV.
    try:
        if bool(multi_face):
            faces = _solutions_face_detect(model_selection=1)
            if faces:
                return faces
        return _solutions_face_detect(model_selection=0)
    except Exception:
        return detect_faces_opencv(frame, multi_face)


def detect_faces_opencv(frame: np.ndarray, multi_face: bool = False) -> List[Dict]:
    """
    Fallback face detection using OpenCV's DNN face detector.
    """
    h, w = frame.shape[:2]

    # Use OpenCV's Haar Cascade as fallback
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    detected = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(30, 30))

    faces = []
    for (x, y, fw, fh) in detected:
        center_x = (x + fw / 2) / w
        center_y = (y + fh / 2) / h

        faces.append({
            'x': center_x,
            'y': center_y,
            'width': fw / w,
            'height': fh / h,
            'confidence': 0.7  # OpenCV doesn't give confidence
        })

        if not multi_face:
            break

    return faces


def detect_pose_mediapipe(frame: np.ndarray) -> Optional[Dict]:
    """
    Detect body pose using MediaPipe Pose (Tasks API).

    Returns pose center (useful for full-body shots).
    """
    if not MEDIAPIPE_AVAILABLE:
        return None

    # Get model path
    model_path = get_model_path('pose_landmarker_lite.task')
    if not model_path or not os.path.exists(model_path):
        return None

    # Convert BGR to RGB
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Create pose landmarker
    base_options = mp_python.BaseOptions(model_asset_path=model_path)
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        output_segmentation_masks=False
    )

    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = landmarker.detect(mp_image)

        if result.pose_landmarks and len(result.pose_landmarks) > 0:
            landmarks = result.pose_landmarks[0]

            # Get key points for body center (nose, shoulders, hips)
            key_indices = [0, 11, 12, 23, 24]  # nose, shoulders, hips

            xs = []
            ys = []
            for i in key_indices:
                if i < len(landmarks):
                    lm = landmarks[i]
                    if lm.visibility > 0.5:
                        xs.append(lm.x)
                        ys.append(lm.y)

            if xs and ys:
                return {
                    'x': sum(xs) / len(xs),
                    'y': sum(ys) / len(ys),
                    'method': 'pose'
                }

    return None


def detect_subject(
    video_path: str,
    timestamp: float = 0.0,
    method: str = 'face',
    multi_face: bool = False
) -> Dict:
    """
    Detect subject position in video frame.

    Args:
        video_path: Path to video file
        timestamp: Time in seconds to sample
        method: Detection method ('face', 'pose', 'auto')
        multi_face: Return all faces (for interviews)

    Returns:
        Dict with subject position data
    """
    if not CV2_AVAILABLE:
        raise ImportError("OpenCV required. Install: pip install opencv-python")

    # Extract frame
    frame = extract_frame(video_path, timestamp)
    if frame is None:
        return {'error': f'Could not extract frame at {timestamp}s', 'success': False}

    h, w = frame.shape[:2]
    result = {
        'video': video_path,
        'timestamp': timestamp,
        'frame_width': w,
        'frame_height': h,
        'success': False
    }

    # Try face detection first (or if method='face')
    if method in ('face', 'auto'):
        try:
            faces = detect_faces_mediapipe(frame, multi_face)
        except Exception as e:
            print(f"MediaPipe face detection failed: {e}, trying OpenCV fallback")
            faces = detect_faces_opencv(frame, multi_face)

        if faces:
            if multi_face:
                result['faces'] = faces
                # Calculate center point between all faces
                avg_x = sum(f['x'] for f in faces) / len(faces)
                result['x'] = avg_x
            else:
                result['x'] = faces[0]['x']
                result['y'] = faces[0]['y']
                # Include bbox size for downstream smart-crop constraints.
                result['width'] = faces[0].get('width')
                result['height'] = faces[0].get('height')
                result['confidence'] = faces[0]['confidence']

            result['method'] = 'face'
            result['success'] = True
            return result

    # Fall back to pose detection
    if method in ('pose', 'auto'):
        pose = detect_pose_mediapipe(frame)
        if pose:
            result['x'] = pose['x']
            result['y'] = pose['y']
            result['method'] = 'pose'
            result['success'] = True
            return result

    # No detection - return center as fallback
    result['x'] = 0.5
    result['y'] = 0.5
    result['method'] = 'fallback'
    result['success'] = True
    result['warning'] = 'No subject detected, using center'

    return result


def calculate_crop_position(
    subject_x: float,
    source_width: int,
    source_height: int,
    target_aspect: float = 9/16
) -> Tuple[int, int, int]:
    """
    Calculate optimal crop window for aspect ratio conversion.

    Args:
        subject_x: Normalized X position of subject (0-1)
        source_width: Source video width
        source_height: Source video height
        target_aspect: Target aspect ratio (width/height)

    Returns:
        Tuple of (crop_x, crop_width, crop_height)
    """
    # For 9:16 from 16:9, we crop width to match height
    crop_height = source_height
    crop_width = int(source_height * target_aspect)

    # Calculate crop X position centered on subject
    subject_pixel_x = int(subject_x * source_width)
    crop_x = subject_pixel_x - crop_width // 2

    # Clamp to valid range
    crop_x = max(0, min(crop_x, source_width - crop_width))

    return crop_x, crop_width, crop_height


def main():
    parser = argparse.ArgumentParser(
        description='Detect subject position for smart cropping'
    )
    parser.add_argument('video', help='Input video path')
    parser.add_argument('--timestamp', '-t', type=float, default=0.0,
                        help='Timestamp in seconds to sample (default: 0)')
    parser.add_argument('--method', '-m', choices=['face', 'pose', 'auto'],
                        default='auto', help='Detection method')
    parser.add_argument('--multi-face', action='store_true',
                        help='Detect multiple faces (for interviews)')
    parser.add_argument('--output-json', '-o', help='Output JSON file path')
    parser.add_argument('--show', action='store_true',
                        help='Display frame with detection overlay')

    args = parser.parse_args()

    if not Path(args.video).exists():
        print(f"Error: Video not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    # Detect subject
    result = detect_subject(
        args.video,
        timestamp=args.timestamp,
        method=args.method,
        multi_face=args.multi_face
    )

    if not result.get('success'):
        print(f"Error: {result.get('error', 'Detection failed')}", file=sys.stderr)
        sys.exit(1)

    # Calculate crop position for 9:16
    if result.get('x') is not None:
        crop_x, crop_w, crop_h = calculate_crop_position(
            result['x'],
            result['frame_width'],
            result['frame_height']
        )
        result['crop_x'] = crop_x
        result['crop_width'] = crop_w
        result['crop_height'] = crop_h
        # Normalized crop position (for clip_extractor --crop-x)
        result['crop_x_normalized'] = crop_x / (result['frame_width'] - crop_w) if result['frame_width'] > crop_w else 0.5

    # Output
    if args.output_json:
        with open(args.output_json, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Saved to: {args.output_json}")
    else:
        print(json.dumps(result, indent=2))

    # Show preview
    if args.show and CV2_AVAILABLE:
        frame = extract_frame(args.video, args.timestamp)
        if frame is not None:
            h, w = frame.shape[:2]

            # Draw subject position
            if 'faces' in result:
                for face in result['faces']:
                    cx = int(face['x'] * w)
                    cy = int(face['y'] * h)
                    cv2.circle(frame, (cx, cy), 10, (0, 255, 0), -1)
            elif result.get('x'):
                cx = int(result['x'] * w)
                cy = int(result.get('y', 0.5) * h)
                cv2.circle(frame, (cx, cy), 10, (0, 255, 0), -1)

            # Draw crop region
            if 'crop_x' in result:
                cv2.rectangle(
                    frame,
                    (result['crop_x'], 0),
                    (result['crop_x'] + result['crop_width'], h),
                    (0, 255, 255), 3
                )

            cv2.imshow('Subject Detection', frame)
            cv2.waitKey(0)
            cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
