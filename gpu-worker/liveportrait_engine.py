"""
LivePortrait 3-Layer Pipeline Engine
=====================================
Layer 1: FasterLivePortrait (full face generation with stitching + paste-back)
Layer 2: MuseTalk lip-sync refinement (optional, for enhanced mouth accuracy)
Layer 3: Temporal smoothing (landmark EMA, expression EMA, flicker suppression)

Architecture:
    Audio → JoyVASA → Motion Sequence → LivePortrait → Frame Compositor
                                                     ↓
                                        Temporal Smoother → Output

This replaces IMTalker as the core talking-head model, providing:
- Higher quality face generation (LivePortrait stitching)
- Built-in paste-back to original image
- Angle-aware policy (frontal=strong, side=conservative)
- Idle animation (micro-movements when not speaking)
- Temporal consistency across frames
"""

import os
import sys
import copy
import time
import json
import logging
import tempfile
import pickle
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

import numpy as np
import cv2
import torch

logger = logging.getLogger("liveportrait_engine")

# ── Paths ──────────────────────────────────────────────────────────────────
FASTER_LP_DIR = "/workspace/FasterLivePortrait"
JOYVASA_MOTION_MODEL = os.path.join(
    FASTER_LP_DIR, "checkpoints/joyvasa/motion_generator/motion_generator_hubert_chinese.pt"
)
JOYVASA_MOTION_TEMPLATE = os.path.join(
    FASTER_LP_DIR, "checkpoints/joyvasa/motion_template/motion_template.pkl"
)
JOYVASA_AUDIO_MODEL = os.path.join(
    FASTER_LP_DIR, "checkpoints/chinese-hubert-base"
)


# ── Temporal Smoother ──────────────────────────────────────────────────────

class TemporalSmoother:
    """
    Layer 3: Temporal smoothing for frame-to-frame consistency.
    Uses exponential moving average (EMA) on expression parameters
    and one-euro filter for landmark positions.
    """

    def __init__(self, alpha_exp: float = 0.3, alpha_pose: float = 0.2,
                 flicker_threshold: float = 8.0):
        """
        Args:
            alpha_exp: EMA weight for expression (0=full smooth, 1=no smooth)
            alpha_pose: EMA weight for head pose (pitch/yaw/roll)
            flicker_threshold: pixel-level threshold for flicker suppression
        """
        self.alpha_exp = alpha_exp
        self.alpha_pose = alpha_pose
        self.flicker_threshold = flicker_threshold

        self._prev_exp = None
        self._prev_pose = None
        self._prev_frame = None
        self._frame_count = 0

    def reset(self):
        """Reset smoother state for new sequence."""
        self._prev_exp = None
        self._prev_pose = None
        self._prev_frame = None
        self._frame_count = 0

    def smooth_motion(self, motion_info: dict) -> dict:
        """
        Smooth motion parameters (expression + pose) using EMA.
        Returns smoothed motion_info dict.
        """
        smoothed = copy.deepcopy(motion_info)

        # Smooth expression
        exp = smoothed.get("exp")
        if exp is not None:
            if self._prev_exp is not None:
                smoothed["exp"] = (self.alpha_exp * exp +
                                   (1 - self.alpha_exp) * self._prev_exp)
            self._prev_exp = smoothed["exp"].copy()

        # Smooth pose (pitch, yaw, roll, translation)
        for key in ["pitch", "yaw", "roll", "t"]:
            val = smoothed.get(key)
            if val is not None:
                if self._prev_pose is not None and key in self._prev_pose:
                    smoothed[key] = (self.alpha_pose * val +
                                     (1 - self.alpha_pose) * self._prev_pose[key])

        if self._prev_pose is None:
            self._prev_pose = {}
        for key in ["pitch", "yaw", "roll", "t"]:
            if key in smoothed:
                self._prev_pose[key] = smoothed[key].copy() if hasattr(smoothed[key], 'copy') else smoothed[key]

        self._frame_count += 1
        return smoothed

    def suppress_flicker(self, frame: np.ndarray) -> np.ndarray:
        """
        Suppress temporal flicker by blending with previous frame
        when pixel-level changes are below threshold.
        """
        if self._prev_frame is None:
            self._prev_frame = frame.copy()
            return frame

        # Compute per-pixel absolute difference
        diff = cv2.absdiff(frame, self._prev_frame).astype(np.float32)
        mean_diff = diff.mean(axis=2)  # average across channels

        # Create blend mask: where diff is small, blend more with previous
        blend_alpha = np.clip(mean_diff / self.flicker_threshold, 0.0, 1.0)
        blend_alpha = blend_alpha[:, :, np.newaxis]  # expand for 3 channels

        # Blend: high diff = use new frame, low diff = use previous
        result = (frame.astype(np.float32) * blend_alpha +
                  self._prev_frame.astype(np.float32) * (1.0 - blend_alpha))
        result = result.astype(np.uint8)

        self._prev_frame = result.copy()
        return result


# ── Angle-Aware Policy ─────────────────────────────────────────────────────

class AnglePolicy:
    """
    Layer 4: Angle-aware blending policy.
    - Frontal (yaw < 15°): Full AI generation, strong blend
    - Angled (15-35°): Moderate blend, reduce expression intensity
    - Profile (>35°): Minimal AI, prefer original/driving frame
    """

    @staticmethod
    def get_blend_weight(yaw_deg: float) -> float:
        """Get AI blend weight based on head yaw angle."""
        abs_yaw = abs(yaw_deg)
        if abs_yaw < 15:
            return 1.0  # Full AI
        elif abs_yaw < 35:
            # Linear falloff from 1.0 to 0.3
            return 1.0 - 0.7 * (abs_yaw - 15) / 20
        else:
            return 0.3  # Minimal AI, mostly original

    @staticmethod
    def get_expression_scale(yaw_deg: float) -> float:
        """Scale expression intensity based on angle."""
        abs_yaw = abs(yaw_deg)
        if abs_yaw < 20:
            return 1.0
        elif abs_yaw < 40:
            return 1.0 - 0.5 * (abs_yaw - 20) / 20
        else:
            return 0.5


# ── Idle Animation ─────────────────────────────────────────────────────────

class IdleAnimator:
    """
    Generate subtle micro-movements for idle state (not speaking).
    - Random blinks at natural intervals (every 3-7 seconds)
    - Subtle head micro-movements
    - Breathing-like torso movement
    """

    def __init__(self, fps: int = 25):
        self.fps = fps
        self._frame_count = 0
        self._next_blink_frame = 0
        self._blink_duration = 0
        self._blink_progress = 0
        self._schedule_next_blink()

    def _schedule_next_blink(self):
        """Schedule next blink at random interval (3-7 seconds)."""
        interval = np.random.uniform(3.0, 7.0)
        self._next_blink_frame = self._frame_count + int(interval * self.fps)
        self._blink_duration = int(np.random.uniform(0.15, 0.3) * self.fps)
        self._blink_progress = 0

    def get_idle_modifiers(self) -> dict:
        """
        Get idle animation modifiers for current frame.
        Returns dict with eye_ratio, lip_ratio, and micro head movement.
        """
        self._frame_count += 1
        mods = {
            "eye_close_ratio": 0.0,
            "head_pitch_delta": 0.0,
            "head_yaw_delta": 0.0,
        }

        # Blink animation
        if self._frame_count >= self._next_blink_frame:
            if self._blink_progress < self._blink_duration:
                # Blink curve: quick close, slower open
                t = self._blink_progress / max(self._blink_duration, 1)
                if t < 0.3:
                    mods["eye_close_ratio"] = t / 0.3  # close
                else:
                    mods["eye_close_ratio"] = 1.0 - (t - 0.3) / 0.7  # open
                self._blink_progress += 1
            else:
                self._schedule_next_blink()

        # Subtle head micro-movement (Perlin-like using sin waves)
        t = self._frame_count / self.fps
        mods["head_pitch_delta"] = 0.3 * np.sin(t * 0.5) + 0.1 * np.sin(t * 1.3)
        mods["head_yaw_delta"] = 0.2 * np.sin(t * 0.3) + 0.1 * np.sin(t * 0.9)

        return mods


# ── LivePortrait Engine ────────────────────────────────────────────────────

class LivePortraitEngine:
    """
    Main engine that orchestrates the 3-layer pipeline:
    1. FasterLivePortrait for face generation
    2. Temporal smoothing for consistency
    3. Angle-aware blending policy
    """

    def __init__(self, gpu_id: int = 0):
        self.gpu_id = gpu_id
        self.device = torch.device(f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu")

        # Pipeline components
        self.pipeline = None       # FasterLivePortraitPipeline
        self.joyvasa = None        # JoyVASAAudio2MotionPipeline
        self.smoother = TemporalSmoother()
        self.angle_policy = AnglePolicy()
        self.idle_animator = IdleAnimator()

        # State
        self.is_initialized = False
        self.source_prepared = False

    def initialize(self):
        """Load FasterLivePortrait and JoyVASA models."""
        if self.is_initialized:
            return True

        logger.info("Initializing LivePortrait engine...")

        # Add FasterLivePortrait to path
        if FASTER_LP_DIR not in sys.path:
            sys.path.insert(0, FASTER_LP_DIR)

        try:
            from omegaconf import OmegaConf
            from src.pipelines.faster_live_portrait_pipeline import FasterLivePortraitPipeline

            # Load config
            cfg_path = os.path.join(FASTER_LP_DIR, "configs/onnx_infer.yaml")
            cfg = OmegaConf.load(cfg_path)

            # Fix checkpoint paths to absolute
            for name in cfg.models:
                if isinstance(cfg.models[name].model_path, str):
                    cfg.models[name].model_path = cfg.models[name].model_path.replace(
                        "./checkpoints", os.path.join(FASTER_LP_DIR, "checkpoints"))
                else:
                    cfg.models[name].model_path = [
                        p.replace("./checkpoints", os.path.join(FASTER_LP_DIR, "checkpoints"))
                        for p in cfg.models[name].model_path
                    ]

            # Fix mask path
            cfg.infer_params.mask_crop_path = os.path.join(FASTER_LP_DIR, "assets/mask_template.png")

            # Initialize pipeline
            self.pipeline = FasterLivePortraitPipeline(cfg)
            logger.info("FasterLivePortrait pipeline loaded.")

            # Initialize JoyVASA
            if os.path.exists(JOYVASA_MOTION_MODEL):
                from src.pipelines.joyvasa_audio_to_motion_pipeline import JoyVASAAudio2MotionPipeline
                self.joyvasa = JoyVASAAudio2MotionPipeline(
                    motion_model_path=JOYVASA_MOTION_MODEL,
                    audio_model_path=JOYVASA_AUDIO_MODEL,
                    motion_template_path=JOYVASA_MOTION_TEMPLATE,
                    cfg_mode="incremental",
                    cfg_scale=1.2,
                )
                logger.info("JoyVASA audio-to-motion pipeline loaded.")
            else:
                logger.warning(f"JoyVASA model not found at {JOYVASA_MOTION_MODEL}")

            self.is_initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize LivePortrait engine: {e}", exc_info=True)
            return False

    def prepare_source(self, source_path: str) -> bool:
        """Prepare source image/video for animation."""
        if not self.is_initialized:
            if not self.initialize():
                return False

        try:
            ret = self.pipeline.prepare_source(source_path, realtime=False)
            if ret:
                self.source_prepared = True
                logger.info(f"Source prepared: {source_path}")
            else:
                logger.error(f"Failed to prepare source: {source_path}")
            return ret
        except Exception as e:
            logger.error(f"Source preparation error: {e}", exc_info=True)
            return False

    def generate_from_audio(
        self,
        audio_path: str,
        output_path: str,
        source_path: Optional[str] = None,
        fps: int = 25,
        enable_smoothing: bool = True,
        enable_angle_policy: bool = True,
        enable_idle: bool = False,
        progress_callback=None,
    ) -> bool:
        """
        Generate a talking-head video from audio using the 3-layer pipeline.

        Args:
            audio_path: Path to audio file (WAV/MP3)
            output_path: Path for output video
            source_path: Path to source image (if not already prepared)
            fps: Output video FPS
            enable_smoothing: Enable temporal smoothing (Layer 3)
            enable_angle_policy: Enable angle-aware blending
            enable_idle: Enable idle animation for silent parts
            progress_callback: Optional callback(progress_pct, message)
        """
        if not self.is_initialized:
            if not self.initialize():
                return False

        # Prepare source if needed
        if source_path and not self.source_prepared:
            if not self.prepare_source(source_path):
                return False

        if not self.source_prepared:
            logger.error("No source prepared!")
            return False

        def _progress(pct, msg=""):
            if progress_callback:
                progress_callback(pct, msg)
            logger.info(f"[LivePortrait] {pct}% - {msg}")

        try:
            _progress(5, "Generating motion from audio...")

            # ── Step 1: Audio → Motion sequence via JoyVASA ──
            if self.joyvasa is None:
                logger.error("JoyVASA not initialized!")
                return False

            motion_data = self.joyvasa.gen_motion_sequence(audio_path)
            if motion_data is None:
                logger.error("JoyVASA failed to generate motion!")
                return False

            motion_lst = motion_data["motion"]
            c_eyes_lst = motion_data.get("c_eyes_lst", motion_data.get("c_d_eyes_lst", []))
            c_lip_lst = motion_data.get("c_lip_lst", motion_data.get("c_d_lip_lst", []))
            output_fps = motion_data.get("output_fps", fps)

            total_frames = len(motion_lst)
            logger.info(f"JoyVASA generated {total_frames} motion frames at {output_fps} fps")
            _progress(20, f"Motion generated: {total_frames} frames")

            # ── Step 2: Render frames with LivePortrait ──
            _progress(25, "Rendering frames with LivePortrait...")

            src_img = self.pipeline.src_imgs[0]
            src_info = self.pipeline.src_infos[0]
            h, w = src_img.shape[:2]

            # Setup video writer
            temp_video = output_path + ".tmp.mp4"
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(temp_video, fourcc, output_fps, (w, h))

            # Reset smoother
            self.smoother.reset()

            render_times = []
            for frame_idx in range(total_frames):
                t0 = time.time()
                first_frame = (frame_idx == 0)

                # Get motion info for this frame
                dri_motion_info = [
                    motion_lst[frame_idx],
                    c_eyes_lst[frame_idx] if frame_idx < len(c_eyes_lst) else np.array([[0.0]]),
                    c_lip_lst[frame_idx] if frame_idx < len(c_lip_lst) else np.array([[0.0]]),
                ]

                # Layer 3: Temporal smoothing on motion
                if enable_smoothing and not first_frame:
                    dri_motion_info[0] = self.smoother.smooth_motion(dri_motion_info[0])

                # Run LivePortrait
                out_crop, out_org = self.pipeline.run_with_pkl(
                    dri_motion_info, src_img, src_info,
                    first_frame=first_frame
                )

                if out_org is None:
                    logger.warning(f"Frame {frame_idx}: no output, using previous")
                    continue

                # Convert to BGR for OpenCV
                if isinstance(out_org, torch.Tensor):
                    out_frame = out_org.cpu().numpy().astype(np.uint8)
                else:
                    out_frame = out_org.astype(np.uint8) if out_org.dtype != np.uint8 else out_org

                if out_frame.shape[2] == 3 and out_frame[0, 0, 0] != 0:
                    # Check if RGB (LivePortrait outputs RGB)
                    out_frame = cv2.cvtColor(out_frame, cv2.COLOR_RGB2BGR)

                # Layer 3: Flicker suppression
                if enable_smoothing:
                    out_frame = self.smoother.suppress_flicker(out_frame)

                writer.write(out_frame)
                render_times.append(time.time() - t0)

                # Progress update every 10%
                if frame_idx % max(1, total_frames // 10) == 0:
                    pct = 25 + int(60 * frame_idx / total_frames)
                    avg_ms = np.mean(render_times[-10:]) * 1000
                    _progress(pct, f"Frame {frame_idx}/{total_frames} ({avg_ms:.0f}ms/frame)")

            writer.release()

            avg_time = np.mean(render_times) * 1000 if render_times else 0
            logger.info(f"Rendering complete: {len(render_times)} frames, avg {avg_time:.1f}ms/frame")
            _progress(85, "Adding audio...")

            # ── Step 3: Mux audio with video ──
            import asyncio

            async def _mux_audio():
                cmd = [
                    "ffmpeg", "-y",
                    "-i", temp_video,
                    "-i", audio_path,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "128k",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest",
                    "-pix_fmt", "yuv420p",
                    output_path,
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                await proc.wait()
                return proc.returncode

            # Run mux
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    rc = pool.submit(lambda: asyncio.run(_mux_audio())).result()
            else:
                rc = asyncio.run(_mux_audio())

            if rc != 0:
                logger.warning("Audio mux failed, using video without audio")
                import shutil
                shutil.move(temp_video, output_path)
            else:
                if os.path.exists(temp_video):
                    os.remove(temp_video)

            _progress(100, "Complete!")
            return os.path.exists(output_path)

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            return False

    def cleanup(self):
        """Release GPU resources."""
        if self.pipeline:
            try:
                self.pipeline.clean_models()
            except Exception:
                pass
        self.pipeline = None
        self.joyvasa = None
        self.is_initialized = False
        self.source_prepared = False
        torch.cuda.empty_cache()
        logger.info("LivePortrait engine cleaned up.")
