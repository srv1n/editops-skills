#!/usr/bin/env python3
"""
Scene Analyzer - Visual Analysis Pipeline

Tiered visual analysis system:
- Tier 1: Scene Detection (cheap, always run) - PySceneDetect
- Tier 2: Frame Sampling + VLM (selective) - Moondream/Qwen2-VL
- Tier 3: Dense Video Analysis (expensive, rare) - Cloud VLMs

Decides when visual analysis is needed based on content profile.
"""

import argparse
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
import base64

# Optional imports with graceful fallback
try:
    from scenedetect import detect, ContentDetector, ThresholdDetector
    from scenedetect.scene_manager import save_images
    SCENEDETECT_AVAILABLE = True
except ImportError:
    SCENEDETECT_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False


class AnalysisTier(Enum):
    """Analysis depth tiers."""
    NONE = 0
    SCENE_DETECTION = 1  # Just cut detection
    FRAME_SAMPLING = 2   # Sample frames + VLM
    DENSE_VIDEO = 3      # Full video analysis


class SceneType(Enum):
    """Types of scenes detected."""
    TALKING_HEAD = "talking_head"
    INTERVIEW = "interview"
    B_ROLL = "b_roll"
    PRODUCT_SHOT = "product_shot"
    OUTDOOR = "outdoor"
    CROWD = "crowd"
    ACTION = "action"
    SCREEN_SHARE = "screen_share"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


@dataclass
class SceneInfo:
    """Information about a detected scene."""
    scene_id: int
    start_time: float
    end_time: float
    duration: float
    scene_type: SceneType = SceneType.UNKNOWN
    frame_path: Optional[str] = None
    description: str = ""
    objects_detected: List[str] = field(default_factory=list)
    people_count: int = 0
    emotional_tone: str = "neutral"
    is_good_clip_boundary: bool = True
    confidence: float = 0.5


@dataclass
class VisualAnalysisResult:
    """Complete visual analysis result for a video."""
    video_path: str
    duration: float
    total_scenes: int
    scenes: List[SceneInfo]
    analysis_tier: AnalysisTier
    primary_scene_type: SceneType
    has_products: bool = False
    has_multiple_people: bool = False
    visual_complexity_score: float = 0.5
    recommended_effects: List[str] = field(default_factory=list)
    clip_boundaries: List[float] = field(default_factory=list)


class SceneDetector:
    """Tier 1: Basic scene detection using PySceneDetect."""

    def __init__(self, threshold: float = 27.0):
        """
        Initialize scene detector.

        Args:
            threshold: Content detector threshold (lower = more sensitive)
        """
        self.threshold = threshold

        if not SCENEDETECT_AVAILABLE:
            print("Warning: PySceneDetect not available. Install with: pip install scenedetect[opencv]")

    def detect_scenes(self, video_path: str) -> List[SceneInfo]:
        """
        Detect scene changes in video.

        Args:
            video_path: Path to video file

        Returns:
            List of SceneInfo objects
        """
        if not SCENEDETECT_AVAILABLE:
            return self._fallback_detection(video_path)

        try:
            # Detect scenes using content-based detection
            scene_list = detect(video_path, ContentDetector(threshold=self.threshold))

            scenes = []
            for i, (start, end) in enumerate(scene_list):
                start_time = start.get_seconds()
                end_time = end.get_seconds()

                scenes.append(SceneInfo(
                    scene_id=i,
                    start_time=start_time,
                    end_time=end_time,
                    duration=end_time - start_time,
                    is_good_clip_boundary=True,
                ))

            return scenes

        except Exception as e:
            print(f"Scene detection failed: {e}")
            return self._fallback_detection(video_path)

    def _fallback_detection(self, video_path: str) -> List[SceneInfo]:
        """Fallback: split video into fixed intervals."""
        # Get video duration using ffprobe
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                capture_output=True, text=True
            )
            duration = float(result.stdout.strip())
        except:
            duration = 600  # Default 10 min

        # Create scenes every 30 seconds
        scenes = []
        interval = 30
        for i in range(int(duration // interval)):
            start = i * interval
            end = min((i + 1) * interval, duration)
            scenes.append(SceneInfo(
                scene_id=i,
                start_time=start,
                end_time=end,
                duration=end - start,
            ))

        return scenes

    def extract_keyframes(
        self,
        video_path: str,
        scenes: List[SceneInfo],
        output_dir: Path,
        sample_per_scene: int = 1
    ) -> List[SceneInfo]:
        """
        Extract keyframes from each scene.

        Args:
            video_path: Path to video
            scenes: List of detected scenes
            output_dir: Where to save frames
            sample_per_scene: Number of frames per scene

        Returns:
            Updated scenes with frame paths
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for scene in scenes:
            # Extract frame at scene midpoint
            mid_time = (scene.start_time + scene.end_time) / 2
            frame_path = output_dir / f"scene_{scene.scene_id:04d}.jpg"

            try:
                subprocess.run([
                    'ffmpeg', '-y', '-ss', str(mid_time),
                    '-i', video_path, '-frames:v', '1',
                    '-q:v', '2', str(frame_path)
                ], capture_output=True, check=True)

                scene.frame_path = str(frame_path)
            except Exception as e:
                print(f"Failed to extract frame for scene {scene.scene_id}: {e}")

        return scenes


class VLMAnalyzer:
    """Tier 2 & 3: Visual Language Model analysis."""

    # VLM options in order of preference
    VLM_OPTIONS = [
        ('moondream', 'vikhyatk/moondream2'),
        ('qwen2_vl', 'Qwen/Qwen2-VL-7B-Instruct'),
    ]

    def __init__(self, model_name: str = 'moondream', device: str = 'auto'):
        """
        Initialize VLM analyzer.

        Args:
            model_name: Which VLM to use ('moondream', 'qwen2_vl', or 'api')
            device: Device for inference ('auto', 'cuda', 'cpu', 'mps')
        """
        self.model_name = model_name
        self.model = None
        self.processor = None
        self.device = device

        if TRANSFORMERS_AVAILABLE and model_name != 'api':
            self._load_model()

    def _load_model(self):
        """Load the selected VLM model."""
        if not TRANSFORMERS_AVAILABLE:
            print("Transformers not available. Install with: pip install transformers torch")
            return

        try:
            if self.model_name == 'moondream':
                # Moondream 2 - small and fast
                self.model = AutoModelForCausalLM.from_pretrained(
                    "vikhyatk/moondream2",
                    trust_remote_code=True,
                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                )
                self.processor = AutoTokenizer.from_pretrained(
                    "vikhyatk/moondream2",
                    trust_remote_code=True
                )

                if self.device == 'auto':
                    if torch.cuda.is_available():
                        self.model = self.model.cuda()
                    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                        self.model = self.model.to('mps')

                print(f"✅ Loaded Moondream 2 VLM")

        except Exception as e:
            print(f"Failed to load VLM: {e}")
            self.model = None

    def analyze_frame(
        self,
        image_path: str,
        prompts: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Analyze a single frame with VLM.

        Args:
            image_path: Path to image file
            prompts: Optional custom prompts (uses defaults if None)

        Returns:
            Dict with analysis results
        """
        if not self.model:
            return self._fallback_analysis(image_path)

        default_prompts = [
            "Describe what you see in this image briefly.",
            "Is this a talking head shot, product shot, B-roll, or action scene?",
            "What objects are visible?",
            "How many people are in the frame?",
            "What is the emotional tone?",
        ]

        prompts = prompts or default_prompts[:2]  # Limit for speed
        results = {}

        try:
            from PIL import Image
            image = Image.open(image_path)

            if self.model_name == 'moondream':
                for prompt in prompts:
                    enc_image = self.model.encode_image(image)
                    answer = self.model.answer_question(enc_image, prompt, self.processor)
                    results[prompt] = answer

        except Exception as e:
            print(f"VLM analysis failed: {e}")
            return self._fallback_analysis(image_path)

        return self._parse_vlm_results(results)

    def _fallback_analysis(self, image_path: str) -> Dict[str, Any]:
        """Basic analysis without VLM."""
        return {
            'description': 'VLM not available',
            'scene_type': SceneType.UNKNOWN,
            'objects': [],
            'people_count': 1,
            'emotional_tone': 'neutral',
        }

    def _parse_vlm_results(self, raw_results: Dict[str, str]) -> Dict[str, Any]:
        """Parse VLM responses into structured data."""
        parsed = {
            'description': '',
            'scene_type': SceneType.UNKNOWN,
            'objects': [],
            'people_count': 1,
            'emotional_tone': 'neutral',
        }

        for prompt, response in raw_results.items():
            response_lower = response.lower()

            # Extract description
            if 'describe' in prompt.lower():
                parsed['description'] = response

            # Detect scene type
            if 'talking head' in response_lower:
                parsed['scene_type'] = SceneType.TALKING_HEAD
            elif 'product' in response_lower:
                parsed['scene_type'] = SceneType.PRODUCT_SHOT
            elif 'b-roll' in response_lower or 'b roll' in response_lower:
                parsed['scene_type'] = SceneType.B_ROLL
            elif 'action' in response_lower or 'movement' in response_lower:
                parsed['scene_type'] = SceneType.ACTION
            elif 'interview' in response_lower or 'conversation' in response_lower:
                parsed['scene_type'] = SceneType.INTERVIEW
            elif 'outdoor' in response_lower or 'outside' in response_lower:
                parsed['scene_type'] = SceneType.OUTDOOR

            # Extract objects
            if 'object' in prompt.lower():
                # Simple extraction - look for nouns
                words = response.split()
                objects = [w.strip('.,') for w in words
                          if len(w) > 3 and w[0].isupper()]
                parsed['objects'] = objects[:5]

            # Count people
            if 'people' in prompt.lower() or 'person' in prompt.lower():
                import re
                numbers = re.findall(r'\d+', response)
                if numbers:
                    parsed['people_count'] = int(numbers[0])
                elif 'one' in response_lower or 'single' in response_lower:
                    parsed['people_count'] = 1
                elif 'two' in response_lower:
                    parsed['people_count'] = 2
                elif 'multiple' in response_lower or 'several' in response_lower:
                    parsed['people_count'] = 3

            # Emotional tone
            if 'emotional' in prompt.lower() or 'tone' in prompt.lower():
                if any(w in response_lower for w in ['serious', 'intense', 'focused']):
                    parsed['emotional_tone'] = 'serious'
                elif any(w in response_lower for w in ['happy', 'joyful', 'excited', 'laughing']):
                    parsed['emotional_tone'] = 'positive'
                elif any(w in response_lower for w in ['sad', 'emotional', 'vulnerable']):
                    parsed['emotional_tone'] = 'emotional'
                elif any(w in response_lower for w in ['angry', 'confrontational']):
                    parsed['emotional_tone'] = 'confrontational'

        return parsed

    def analyze_frames_batch(
        self,
        scenes: List[SceneInfo],
        prompts: Optional[List[str]] = None
    ) -> List[SceneInfo]:
        """
        Analyze multiple scene frames.

        Args:
            scenes: List of scenes with frame_path set
            prompts: Optional custom prompts

        Returns:
            Updated scenes with VLM analysis
        """
        for scene in scenes:
            if scene.frame_path:
                analysis = self.analyze_frame(scene.frame_path, prompts)
                scene.scene_type = analysis.get('scene_type', SceneType.UNKNOWN)
                scene.description = analysis.get('description', '')
                scene.objects_detected = analysis.get('objects', [])
                scene.people_count = analysis.get('people_count', 1)
                scene.emotional_tone = analysis.get('emotional_tone', 'neutral')
                scene.confidence = 0.8 if self.model else 0.3

        return scenes


class SceneAnalyzer:
    """
    Main scene analyzer that combines detection + VLM analysis.

    Automatically decides analysis depth based on content profile.
    """

    def __init__(
        self,
        vlm_model: str = 'moondream',
        scene_threshold: float = 27.0
    ):
        """
        Initialize analyzer.

        Args:
            vlm_model: VLM to use for frame analysis
            scene_threshold: Sensitivity for scene detection
        """
        self.scene_detector = SceneDetector(threshold=scene_threshold)
        self.vlm_analyzer = VLMAnalyzer(model_name=vlm_model)

    def analyze(
        self,
        video_path: str,
        tier: AnalysisTier = AnalysisTier.SCENE_DETECTION,
        vlm_prompts: Optional[List[str]] = None,
        output_dir: Optional[Path] = None
    ) -> VisualAnalysisResult:
        """
        Analyze video at specified tier.

        Args:
            video_path: Path to video file
            tier: Analysis depth
            vlm_prompts: Optional custom VLM prompts
            output_dir: Where to save extracted frames

        Returns:
            VisualAnalysisResult with complete analysis
        """
        video_path = str(video_path)
        output_dir = output_dir or Path(tempfile.mkdtemp())

        # Always do scene detection (Tier 1)
        print(f"🎬 Tier 1: Detecting scenes...")
        scenes = self.scene_detector.detect_scenes(video_path)
        print(f"   Found {len(scenes)} scenes")

        # Get video duration
        duration = scenes[-1].end_time if scenes else 0

        if tier.value >= AnalysisTier.FRAME_SAMPLING.value:
            # Extract keyframes
            print(f"📸 Extracting keyframes...")
            scenes = self.scene_detector.extract_keyframes(
                video_path, scenes, output_dir
            )

            # Run VLM analysis
            print(f"🤖 Tier 2: Running VLM analysis...")
            scenes = self.vlm_analyzer.analyze_frames_batch(scenes, vlm_prompts)

        # Compute summary statistics
        scene_types = [s.scene_type for s in scenes]
        primary_type = max(set(scene_types), key=scene_types.count) if scene_types else SceneType.UNKNOWN

        has_products = any(
            s.scene_type == SceneType.PRODUCT_SHOT or
            any('product' in obj.lower() for obj in s.objects_detected)
            for s in scenes
        )

        has_multiple_people = any(s.people_count > 1 for s in scenes)

        # Calculate visual complexity
        unique_scenes = len(set(scene_types))
        cuts_per_min = len(scenes) / (duration / 60) if duration > 0 else 0
        complexity = min(1.0, (unique_scenes / 5 + cuts_per_min / 10) / 2)

        # Recommend effects based on analysis
        effects = self._recommend_effects(primary_type, has_products, has_multiple_people)

        # Find good clip boundaries (scene changes)
        boundaries = [s.start_time for s in scenes]

        return VisualAnalysisResult(
            video_path=video_path,
            duration=duration,
            total_scenes=len(scenes),
            scenes=scenes,
            analysis_tier=tier,
            primary_scene_type=primary_type,
            has_products=has_products,
            has_multiple_people=has_multiple_people,
            visual_complexity_score=complexity,
            recommended_effects=effects,
            clip_boundaries=boundaries,
        )

    def _recommend_effects(
        self,
        scene_type: SceneType,
        has_products: bool,
        has_multiple_people: bool
    ) -> List[str]:
        """Recommend effects based on visual analysis."""
        effects = []

        if scene_type == SceneType.TALKING_HEAD:
            effects = ['desaturate_bg', 'spotlight', 'face_zoom']
        elif scene_type == SceneType.INTERVIEW:
            effects = ['desaturate_bg', 'contour']
        elif scene_type == SceneType.PRODUCT_SHOT:
            effects = ['spotlight', 'object_3d_glow', 'bounding_box']
        elif scene_type == SceneType.B_ROLL:
            effects = ['spotlight']
        elif scene_type == SceneType.ACTION:
            effects = ['motion_trail', 'contour']
        elif scene_type == SceneType.OUTDOOR:
            effects = ['desaturate_bg', 'contour']
        elif scene_type == SceneType.SCREEN_SHARE:
            effects = ['bounding_box']
        else:
            effects = ['desaturate_bg']

        if has_products:
            effects.insert(0, 'object_3d_glow')

        if has_multiple_people:
            effects.append('contour')

        return list(dict.fromkeys(effects))[:3]  # Dedupe, limit to 3

    def decide_analysis_tier(
        self,
        content_type: str,
        visual_complexity: str,
        has_products: bool
    ) -> AnalysisTier:
        """
        Decide appropriate analysis tier based on content profile.

        This is the intelligent decision that saves compute on simple content.
        """
        # Podcast/interview - transcript is enough
        if content_type in ['educational_podcast', 'intellectual_podcast', 'spiritual_wisdom']:
            if visual_complexity in ['minimal', 'low']:
                return AnalysisTier.NONE

        # Fashion/product - need visual analysis
        if content_type in ['lifestyle_fashion', 'tech_review', 'product_review']:
            return AnalysisTier.FRAME_SAMPLING

        # Action/sports - need dense analysis
        if content_type in ['sports', 'action', 'gaming']:
            return AnalysisTier.DENSE_VIDEO

        # Default based on visual complexity
        if visual_complexity in ['high', 'very_high']:
            return AnalysisTier.FRAME_SAMPLING
        elif visual_complexity == 'medium':
            return AnalysisTier.SCENE_DETECTION
        else:
            return AnalysisTier.NONE

    def find_optimal_clip_boundaries(
        self,
        analysis: VisualAnalysisResult,
        target_duration: Tuple[int, int] = (15, 45)
    ) -> List[Tuple[float, float]]:
        """
        Find optimal clip boundaries based on scene analysis.

        Ensures clips start and end on natural scene breaks.
        """
        boundaries = analysis.clip_boundaries
        min_dur, max_dur = target_duration
        optimal_clips = []

        for i, start in enumerate(boundaries):
            # Find end points that give good clip length
            for j, end in enumerate(boundaries[i+1:], i+1):
                duration = end - start

                if min_dur <= duration <= max_dur:
                    # Check if this is a good segment
                    scenes_in_clip = [
                        s for s in analysis.scenes
                        if start <= s.start_time < end
                    ]

                    # Prefer clips with consistent scene type
                    types = [s.scene_type for s in scenes_in_clip]
                    if len(set(types)) <= 2:  # Max 2 scene types
                        optimal_clips.append((start, end))
                    break

                elif duration > max_dur:
                    break

        return optimal_clips


def main():
    """CLI for scene analyzer."""
    parser = argparse.ArgumentParser(description='Scene Analyzer - Visual Analysis Pipeline')
    parser.add_argument('video', help='Path to video file')
    parser.add_argument('--tier', '-t', type=int, default=1, choices=[0, 1, 2, 3],
                       help='Analysis tier (0=none, 1=scene, 2=vlm, 3=dense)')
    parser.add_argument('--output', '-o', help='Output directory for frames')
    parser.add_argument('--vlm', default='moondream', choices=['moondream', 'qwen2_vl', 'api'],
                       help='VLM model to use')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--threshold', type=float, default=27.0,
                       help='Scene detection threshold')

    args = parser.parse_args()

    analyzer = SceneAnalyzer(
        vlm_model=args.vlm,
        scene_threshold=args.threshold
    )

    tier = AnalysisTier(args.tier)
    output_dir = Path(args.output) if args.output else None

    print(f"\n🎥 Analyzing: {args.video}")
    print(f"   Tier: {tier.name}")

    result = analyzer.analyze(args.video, tier=tier, output_dir=output_dir)

    if args.json:
        # Convert to JSON-serializable format
        output = {
            'video_path': result.video_path,
            'duration': result.duration,
            'total_scenes': result.total_scenes,
            'analysis_tier': result.analysis_tier.name,
            'primary_scene_type': result.primary_scene_type.value,
            'has_products': result.has_products,
            'has_multiple_people': result.has_multiple_people,
            'visual_complexity': result.visual_complexity_score,
            'recommended_effects': result.recommended_effects,
            'clip_boundaries': result.clip_boundaries[:20],  # Limit output
            'scenes': [
                {
                    'id': s.scene_id,
                    'start': s.start_time,
                    'end': s.end_time,
                    'type': s.scene_type.value,
                    'description': s.description[:100] if s.description else '',
                    'objects': s.objects_detected,
                }
                for s in result.scenes[:20]  # Limit output
            ]
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\n📊 Results:")
        print(f"   Duration: {result.duration:.1f}s")
        print(f"   Scenes: {result.total_scenes}")
        print(f"   Primary type: {result.primary_scene_type.value}")
        print(f"   Visual complexity: {result.visual_complexity_score:.2f}")
        print(f"   Has products: {result.has_products}")
        print(f"   Multiple people: {result.has_multiple_people}")
        print(f"   Recommended effects: {', '.join(result.recommended_effects)}")

        if result.scenes:
            print(f"\n🎬 First 5 scenes:")
            for s in result.scenes[:5]:
                print(f"   [{s.scene_id}] {s.start_time:.1f}s-{s.end_time:.1f}s: {s.scene_type.value}")
                if s.description:
                    print(f"       {s.description[:60]}...")


if __name__ == '__main__':
    main()
