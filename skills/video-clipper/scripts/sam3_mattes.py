#!/usr/bin/env python3
"""
Generate a matte PNG sequence using SAM3 (via HuggingFace Transformers).

This is designed to plug into the existing pipeline via:
  run_overlay_pipeline.py --mattes-exec-cmd "python3 sam3_mattes.py --input {input} --out-dir {out_dir} ..."

Output contract:
  - Writes %06d.png into out_dir, one per video frame.
  - PNGs are RGBA, with grayscale mask in RGB and alpha=255 (renderer reads luma).

Notes:
  - SAM3 is expensive on CPU. We sample masks at a lower rate (sample_fps) and
    linearly interpolate masks in between for speed.
  - Requires: torch, opencv-python, pillow, transformers (dev with Sam3Model).
  - Requires HF_TOKEN (in env) + access to the model repo (facebook/sam3).
"""

import argparse
import os
from pathlib import Path
from typing import Optional, Tuple, List

import cv2
import numpy as np
import torch
from PIL import Image

from transformers import Sam3Processor, Sam3Model


def load_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def get_device(device: str) -> str:
    if device == "auto":
        if torch.cuda.is_available():
            return "cuda"
        # SAM3 + transformers has been flaky on MPS for some ops; default to CPU.
        return "cpu"
    return device


def segment_person(
    model: Sam3Model,
    processor: Sam3Processor,
    device: str,
    frame_bgr: np.ndarray,
    prompt: str,
    threshold: float,
) -> np.ndarray:
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(frame_rgb)
    h, w = frame_bgr.shape[:2]

    inputs = processor(images=pil_img, text=prompt, return_tensors="pt")
    inputs = {k: (v.to(device) if isinstance(v, torch.Tensor) else v) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    results = processor.post_process_instance_segmentation(
        outputs, target_sizes=[(h, w)], threshold=float(threshold)
    )
    if not results:
        return np.zeros((h, w), dtype=np.float32)
    r0 = results[0]
    masks = r0.get("masks", None)
    if masks is None or len(masks) == 0:
        return np.zeros((h, w), dtype=np.float32)

    # Combine instances.
    combined = None
    for m in masks:
        m = m.detach().cpu().numpy()
        if m.dtype != np.uint8:
            m = (m > 0).astype(np.uint8)
        if combined is None:
            combined = m
        else:
            combined = np.maximum(combined, m)
    if combined is None:
        return np.zeros((h, w), dtype=np.float32)
    return combined.astype(np.float32)


def write_mask_png(out_path: Path, mask01: np.ndarray) -> None:
    mask01 = np.clip(mask01, 0.0, 1.0)
    m = (mask01 * 255.0).round().astype(np.uint8)
    rgba = np.dstack([m, m, m, np.full_like(m, 255, dtype=np.uint8)])
    # OpenCV expects BGRA.
    bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
    cv2.imwrite(str(out_path), bgra)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate matte PNG sequence using SAM3")
    ap.add_argument("--input", required=True, help="Input video path")
    # argparse uses %-formatting internally for help strings; escape '%' as '%%'
    ap.add_argument("--out-dir", required=True, help="Output directory for %%06d.png mattes")
    ap.add_argument("--prompt", default="person", help="Text prompt (default: person)")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"], help="Compute device")
    ap.add_argument("--model", default="facebook/sam3", help="HF model id (default: facebook/sam3)")
    ap.add_argument("--threshold", type=float, default=0.5, help="Mask threshold (default: 0.5)")
    ap.add_argument("--sample-fps", type=float, default=1.0, help="How many SAM inferences per second (default: 1)")
    ap.add_argument("--max-secs", type=float, default=None, help="Optional: only process first N seconds")
    args = ap.parse_args()

    load_env()
    token = os.environ.get("HF_TOKEN")

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    device = get_device(args.device)
    print(f"[sam3-mattes] loading model={args.model} device={device}")
    processor = Sam3Processor.from_pretrained(args.model, token=token)
    model = Sam3Model.from_pretrained(args.model, token=token).to(device)
    model.eval()

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise SystemExit(f"Failed to open video: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    max_frames = total_frames
    if args.max_secs is not None and args.max_secs > 0:
        max_frames = min(max_frames, int(round(float(args.max_secs) * fps)))

    sample_every = max(1, int(round(fps / max(0.001, float(args.sample_fps)))))
    print(f"[sam3-mattes] fps={fps:.3f} frames={max_frames} sample_every={sample_every}")

    sample_indices: List[int] = []
    sample_masks: List[np.ndarray] = []

    frame_idx = 0
    while frame_idx < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % sample_every == 0:
            print(f"[sam3-mattes] segment frame={frame_idx}")
            mask = segment_person(
                model=model,
                processor=processor,
                device=device,
                frame_bgr=frame,
                prompt=str(args.prompt),
                threshold=float(args.threshold),
            )
            sample_indices.append(frame_idx)
            sample_masks.append(mask)
        frame_idx += 1

    cap.release()

    if not sample_indices:
        raise SystemExit("[sam3-mattes] No masks generated (model returned nothing).")

    # Now write a matte per frame using linear interpolation between samples.
    # Re-open capture for sequential frames.
    cap = cv2.VideoCapture(str(input_path))
    frame_idx = 0
    s = 0
    while frame_idx < max_frames:
        ok, frame = cap.read()
        if not ok:
            break

        # Find bracket samples.
        while s + 1 < len(sample_indices) and sample_indices[s + 1] <= frame_idx:
            s += 1
        i0 = sample_indices[s]
        m0 = sample_masks[s]
        if s + 1 < len(sample_indices):
            i1 = sample_indices[s + 1]
            m1 = sample_masks[s + 1]
        else:
            i1 = i0
            m1 = m0

        if i1 == i0:
            mask01 = m0
        else:
            t = (frame_idx - i0) / float(i1 - i0)
            mask01 = (m0 * (1.0 - t) + m1 * t)

        out_path = out_dir / f"{frame_idx:06d}.png"
        write_mask_png(out_path, mask01)
        frame_idx += 1

    cap.release()
    print(f"[sam3-mattes] wrote {frame_idx} mattes to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
