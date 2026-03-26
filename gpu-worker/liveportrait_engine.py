"""
LivePortrait 3-Layer Pipeline Engine (v2 - PyTorch Backend)
============================================================
Layer 1: LivePortrait (PyTorch) for full face generation with stitching + paste-back
Layer 2: Temporal smoothing (expression EMA, pose EMA, flicker suppression)
Layer 3: Angle-aware blending policy + idle animation

Architecture:
    Audio → JoyVASA → Motion Sequence → LivePortrait (PyTorch) → paste_back
                                                                ↓
                                                   Temporal Smoother → Output

Key changes from v1 (ONNX):
- Uses original LivePortrait PyTorch models (no GridSample 5D issue)
- Direct access to warp_decode, stitching, retarget_eye/lip
- Built-in paste_back with mask for seamless compositing
"""

import os
import sys
import copy
import time
import json
import logging
import tempfile
import subprocess
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

import numpy as np
import cv2
import torch

logger = logging.getLogger("liveportrait_engine")

# ── Paths ──────────────────────────────────────────────────────────────────
LIVEPORTRAIT_DIR = "/workspace/LivePortrait"
FASTER_LP_DIR = "/workspace/FasterLivePortrait"

# JoyVASA models (still from FasterLivePortrait)
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
    Layer 2: Temporal smoothing for frame-to-frame consistency.
    Uses exponential moving average (EMA) on expression parameters
    and flicker suppression on pixel level.
    """

    def __init__(self, alpha_exp: float = 0.3, alpha_pose: float = 0.2,
                 flicker_threshold: float = 8.0):
        self.alpha_exp = alpha_exp
        self.alpha_pose = alpha_pose
        self.flicker_threshold = flicker_threshold

        self._prev_exp = None
        self._prev_pose = None
        self._prev_frame = None
        self._frame_count = 0

    def reset(self):
        self._prev_exp = None
        self._prev_pose = None
        self._prev_frame = None
        self._frame_count = 0

    def smooth_expression(self, exp_tensor: torch.Tensor) -> torch.Tensor:
        """Smooth expression tensor using EMA."""
        if self._prev_exp is None:
            self._prev_exp = exp_tensor.clone()
            return exp_tensor

        smoothed = self.alpha_exp * exp_tensor + (1 - self.alpha_exp) * self._prev_exp
        self._prev_exp = smoothed.clone()
        return smoothed

    def smooth_rotation(self, R: torch.Tensor) -> torch.Tensor:
        """Smooth rotation matrix using EMA."""
        if self._prev_pose is None:
            self._prev_pose = R.clone()
            return R

        smoothed = self.alpha_pose * R + (1 - self.alpha_pose) * self._prev_pose
        self._prev_pose = smoothed.clone()
        return smoothed

    def suppress_flicker(self, frame: np.ndarray) -> np.ndarray:
        """Suppress temporal flicker by blending with previous frame."""
        if self._prev_frame is None:
            self._prev_frame = frame.copy()
            return frame

        diff = cv2.absdiff(frame, self._prev_frame).astype(np.float32)
        mean_diff = diff.mean(axis=2)

        blend_alpha = np.clip(mean_diff / self.flicker_threshold, 0.0, 1.0)
        blend_alpha = blend_alpha[:, :, np.newaxis]

        result = (frame.astype(np.float32) * blend_alpha +
                  self._prev_frame.astype(np.float32) * (1.0 - blend_alpha))
        result = result.astype(np.uint8)

        self._prev_frame = result.copy()
        self._frame_count += 1
        return result


# ── Angle-Aware Policy ─────────────────────────────────────────────────────

class AnglePolicy:
    """
    Layer 3: Angle-aware blending policy.
    - Frontal (yaw < 15 deg): Full AI generation
    - Angled (15-35 deg): Moderate blend
    - Profile (>35 deg): Minimal AI, prefer original
    """

    @staticmethod
    def get_blend_weight(yaw_deg: float) -> float:
        abs_yaw = abs(yaw_deg)
        if abs_yaw < 15:
            return 1.0
        elif abs_yaw < 35:
            return 1.0 - 0.7 * (abs_yaw - 15) / 20
        else:
            return 0.3

    @staticmethod
    def get_expression_scale(yaw_deg: float) -> float:
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
    - Random blinks at natural intervals (3-7 seconds)
    - Subtle head micro-movements
    """

    def __init__(self, fps: int = 25):
        self.fps = fps
        self._frame_count = 0
        self._next_blink_frame = 0
        self._blink_duration = 0
        self._blink_progress = 0
        self._schedule_next_blink()

    def _schedule_next_blink(self):
        interval = np.random.uniform(3.0, 7.0)
        self._next_blink_frame = self._frame_count + int(interval * self.fps)
        self._blink_duration = int(np.random.uniform(0.15, 0.3) * self.fps)
        self._blink_progress = 0

    def get_idle_modifiers(self) -> dict:
        self._frame_count += 1
        mods = {
            "eye_close_ratio": 0.0,
            "head_pitch_delta": 0.0,
            "head_yaw_delta": 0.0,
        }

        if self._frame_count >= self._next_blink_frame:
            if self._blink_progress < self._blink_duration:
                t = self._blink_progress / max(self._blink_duration, 1)
                if t < 0.3:
                    mods["eye_close_ratio"] = t / 0.3
                else:
                    mods["eye_close_ratio"] = 1.0 - (t - 0.3) / 0.7
                self._blink_progress += 1
            else:
                self._schedule_next_blink()

        t = self._frame_count / self.fps
        mods["head_pitch_delta"] = 0.3 * np.sin(t * 0.5) + 0.1 * np.sin(t * 1.3)
        mods["head_yaw_delta"] = 0.2 * np.sin(t * 0.3) + 0.1 * np.sin(t * 0.9)

        return mods


# ── LivePortrait Engine (PyTorch) ─────────────────────────────────────────

class LivePortraitEngine:
    """
    Main engine using original LivePortrait PyTorch models.
    Orchestrates: JoyVASA (audio→motion) + LivePortrait (motion→face) + paste_back
    """

    def __init__(self, gpu_id: int = 0):
        self.gpu_id = gpu_id
        self.device = torch.device(f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu")

        # Pipeline components
        self.pipeline = None       # LivePortraitPipeline
        self.wrapper = None        # LivePortraitWrapper
        self.cropper = None        # Cropper
        self.joyvasa = None        # JoyVASAAudio2MotionPipeline

        # Layers
        self.smoother = TemporalSmoother()
        self.angle_policy = AnglePolicy()
        self.idle_animator = IdleAnimator()

        # Source state
        self.is_initialized = False
        self.source_prepared = False
        self._source_info = None  # cached source info

    def initialize(self):
        """Load LivePortrait PyTorch models and JoyVASA."""
        if self.is_initialized:
            return True

        logger.info("Initializing LivePortrait engine (PyTorch backend)...")

        # Add LivePortrait to path
        if LIVEPORTRAIT_DIR not in sys.path:
            sys.path.insert(0, LIVEPORTRAIT_DIR)

        try:
            from src.config.inference_config import InferenceConfig
            from src.config.crop_config import CropConfig
            from src.live_portrait_pipeline import LivePortraitPipeline
            from src.live_portrait_wrapper import LivePortraitWrapper
            from src.utils.cropper import Cropper

            # Initialize configs
            inf_cfg = InferenceConfig()
            crop_cfg = CropConfig()

            # Enable stitching and paste-back for best quality
            inf_cfg.flag_stitching = True
            inf_cfg.flag_pasteback = True
            inf_cfg.flag_do_crop = True
            inf_cfg.flag_relative_motion = True
            inf_cfg.animation_region = "all"

            # Initialize pipeline
            self.pipeline = LivePortraitPipeline(
                inference_cfg=inf_cfg,
                crop_cfg=crop_cfg,
            )
            self.wrapper = self.pipeline.live_portrait_wrapper
            self.cropper = self.pipeline.cropper
            self.inf_cfg = inf_cfg
            self.crop_cfg = crop_cfg

            logger.info("LivePortrait PyTorch pipeline loaded.")

            # Initialize JoyVASA for audio→motion
            if os.path.exists(JOYVASA_MOTION_MODEL):
                if FASTER_LP_DIR not in sys.path:
                    sys.path.insert(0, FASTER_LP_DIR)
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
            logger.info("LivePortrait engine initialized successfully.")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize LivePortrait engine: {e}", exc_info=True)
            return False

    def prepare_source(self, source_path: str) -> bool:
        """Prepare source image for animation."""
        if not self.is_initialized:
            if not self.initialize():
                return False

        try:
            from src.utils.io import load_image_rgb, resize_to_limit

            # Load and resize source image
            img_rgb = load_image_rgb(source_path)
            img_rgb = resize_to_limit(img_rgb, self.inf_cfg.source_max_dim, self.inf_cfg.source_division)

            # Crop source face
            crop_info = self.cropper.crop_source_image(img_rgb, self.crop_cfg)
            if crop_info is None:
                logger.error("No face detected in source image!")
                return False

            # Prepare for network
            img_crop_256 = crop_info["img_crop_256x256"]
            I_s = self.wrapper.prepare_source(img_crop_256)

            # Extract source features
            x_s_info = self.wrapper.get_kp_info(I_s)
            f_s = self.wrapper.extract_feature_3d(I_s)
            x_s = self.wrapper.transform_keypoint(x_s_info)

            # Get source landmark for retargeting
            source_lmk = crop_info.get("lmk_crop")

            # Prepare paste-back mask
            from src.utils.crop import prepare_paste_back
            mask_ori_float = prepare_paste_back(
                self.inf_cfg.mask_crop,
                crop_info['M_c2o'],
                dsize=(img_rgb.shape[1], img_rgb.shape[0])
            )

            # Cache everything
            self._source_info = {
                "img_rgb": img_rgb,
                "crop_info": crop_info,
                "I_s": I_s,
                "f_s": f_s,
                "x_s": x_s,
                "x_s_info": x_s_info,
                "source_lmk": source_lmk,
                "mask_ori_float": mask_ori_float,
            }

            self.source_prepared = True
            logger.info(f"Source prepared: {source_path}")
            return True

        except Exception as e:
            logger.error(f"Source preparation error: {e}", exc_info=True)
            return False

    def _render_frame(
        self,
        x_d_i_info: dict,
        c_d_eyes_i,
        c_d_lip_i,
        frame_idx: int,
        R_d_0=None,
        x_d_0_info=None,
        enable_smoothing: bool = True,
    ) -> Tuple[Optional[np.ndarray], dict]:
        """
        Render a single frame using LivePortrait PyTorch.

        Returns:
            (frame_rgb, state_dict) where frame_rgb is the paste-back result
        """
        from src.utils.crop import paste_back
        from src.utils.helper import dct2device

        si = self._source_info
        device = self.wrapper.device

        # Move driving info to device
        x_d_i_info_dev = dct2device(x_d_i_info, device)

        R_d_i = x_d_i_info_dev.get('R', x_d_i_info_dev.get('R_d'))
        is_first = (frame_idx == 0)

        if is_first:
            R_d_0 = R_d_i
            x_d_0_info = copy.deepcopy(x_d_i_info_dev)

        # Apply temporal smoothing on expression
        delta_new = si["x_s_info"]['exp'].clone()
        if self.inf_cfg.flag_relative_motion:
            # Relative rotation
            R_new = (R_d_i @ R_d_0.permute(0, 2, 1)) @ si["x_s_info"]['R']

            # Smooth rotation
            if enable_smoothing and not is_first:
                R_new = self.smoother.smooth_rotation(R_new)

            # Relative expression
            delta_new = si["x_s_info"]['exp'] + (x_d_i_info_dev['exp'] - x_d_0_info['exp'])

            # Smooth expression
            if enable_smoothing and not is_first:
                delta_new = self.smoother.smooth_expression(delta_new)
        else:
            R_new = R_d_i
            delta_new = x_d_i_info_dev['exp']

        # Compute new keypoint
        from src.utils.helper import get_rotation_matrix
        scale = si["x_s_info"]['scale']
        t_new = si["x_s_info"]['t'] + (x_d_i_info_dev['t'] - x_d_0_info['t']) if self.inf_cfg.flag_relative_motion else x_d_i_info_dev['t']
        x_d_i_new = scale * (delta_new @ R_new) + t_new

        # Eye retargeting
        if si["source_lmk"] is not None and c_d_eyes_i is not None:
            try:
                combined_eye = self.wrapper.calc_combined_eye_ratio(c_d_eyes_i, si["source_lmk"])
                eyes_delta = self.wrapper.retarget_eye(si["x_s"], combined_eye)
                x_d_i_new = x_d_i_new + eyes_delta
            except Exception:
                pass

        # Lip retargeting
        if si["source_lmk"] is not None and c_d_lip_i is not None:
            try:
                combined_lip = self.wrapper.calc_combined_lip_ratio(c_d_lip_i, si["source_lmk"])
                lip_delta = self.wrapper.retarget_lip(si["x_s"], combined_lip)
                x_d_i_new = x_d_i_new + lip_delta
            except Exception:
                pass

        # Stitching
        if self.inf_cfg.flag_stitching:
            x_d_i_new = self.wrapper.stitching(si["x_s"], x_d_i_new)

        # Apply driving multiplier
        x_d_i_new = si["x_s"] + (x_d_i_new - si["x_s"]) * self.inf_cfg.driving_multiplier

        # Warp + Decode
        out = self.wrapper.warp_decode(si["f_s"], si["x_s"], x_d_i_new)
        I_p_i = self.wrapper.parse_output(out['out'])[0]  # HxWx3 uint8 RGB

        # Paste back to original image
        frame_rgb = paste_back(
            I_p_i,
            si["crop_info"]['M_c2o'],
            si["img_rgb"],
            si["mask_ori_float"]
        )

        state = {"R_d_0": R_d_0, "x_d_0_info": x_d_0_info}
        return frame_rgb, state

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
        """
        if not self.is_initialized:
            if not self.initialize():
                return False

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
            _progress(5, "Generating motion from audio via JoyVASA...")

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

            # ── Step 2: Render frames with LivePortrait PyTorch ──
            _progress(25, "Rendering frames with LivePortrait (PyTorch)...")

            si = self._source_info
            h, w = si["img_rgb"].shape[:2]

            # Setup video writer
            temp_video = output_path + ".tmp.mp4"
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(temp_video, fourcc, output_fps, (w, h))

            # Reset smoother
            self.smoother.reset()

            R_d_0 = None
            x_d_0_info = None
            render_times = []

            for frame_idx in range(total_frames):
                t0 = time.time()

                # Get motion for this frame
                x_d_i_info = motion_lst[frame_idx]
                c_eyes_i = c_eyes_lst[frame_idx] if frame_idx < len(c_eyes_lst) else None
                c_lip_i = c_lip_lst[frame_idx] if frame_idx < len(c_lip_lst) else None

                # Render frame
                frame_rgb, state = self._render_frame(
                    x_d_i_info, c_eyes_i, c_lip_i,
                    frame_idx=frame_idx,
                    R_d_0=R_d_0,
                    x_d_0_info=x_d_0_info,
                    enable_smoothing=enable_smoothing,
                )

                if frame_idx == 0:
                    R_d_0 = state["R_d_0"]
                    x_d_0_info = state["x_d_0_info"]

                if frame_rgb is None:
                    logger.warning(f"Frame {frame_idx}: render failed, skipping")
                    continue

                # Flicker suppression
                if enable_smoothing:
                    frame_rgb = self.smoother.suppress_flicker(frame_rgb)

                # Convert RGB to BGR for OpenCV
                frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                writer.write(frame_bgr)
                render_times.append(time.time() - t0)

                # Progress update
                if frame_idx % max(1, total_frames // 10) == 0:
                    pct = 25 + int(60 * frame_idx / total_frames)
                    avg_ms = np.mean(render_times[-10:]) * 1000
                    _progress(pct, f"Frame {frame_idx}/{total_frames} ({avg_ms:.0f}ms/frame)")

            writer.release()

            avg_time = np.mean(render_times) * 1000 if render_times else 0
            logger.info(f"Rendering complete: {len(render_times)} frames, avg {avg_time:.1f}ms/frame")
            _progress(85, "Adding audio...")

            # ── Step 3: Mux audio with video ──
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
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    logger.warning(f"Audio mux failed: {result.stderr.decode()[:200]}")
                    import shutil
                    shutil.move(temp_video, output_path)
                else:
                    if os.path.exists(temp_video):
                        os.remove(temp_video)
            except Exception as e:
                logger.warning(f"Audio mux error: {e}")
                import shutil
                shutil.move(temp_video, output_path)

            _progress(100, "Complete!")
            return os.path.exists(output_path)

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            return False

    def cleanup(self):
        """Release GPU resources."""
        self.pipeline = None
        self.wrapper = None
        self.cropper = None
        self.joyvasa = None
        self._source_info = None
        self.is_initialized = False
        self.source_prepared = False
        torch.cuda.empty_cache()
        logger.info("LivePortrait engine cleaned up.")
