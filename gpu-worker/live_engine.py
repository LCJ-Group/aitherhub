"""
Hybrid AI Live Streaming Engine
================================
Server-side complete live streaming engine that combines:
- Base video loop with natural movement
- MuseTalk real-time lip-sync (mouth-only replacement)
- FFmpeg RTMP streaming output
- Real-time GPT conversation + TTS audio pipeline

Architecture:
    Video Looper → LipSync Engine → Frame Compositor → RTMP Streamer
                                  ↑
    Audio Pipeline (TTS → Whisper features) ─┘
"""

import os
import sys
import time
import copy
import queue
import math
import glob
import pickle
import shutil
import threading
import subprocess
import logging
import tempfile
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import cv2
import torch
from tqdm import tqdm

# MuseTalk directory
MUSETALK_DIR = "/workspace/MuseTalk"

logger = logging.getLogger("live_engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


class EngineState(Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    STREAMING = "streaming"
    SPEAKING = "speaking"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class EngineConfig:
    """Configuration for the live streaming engine."""
    # Video settings
    video_path: str = ""
    fps: int = 25
    width: int = 1080
    height: int = 1920

    # MuseTalk settings
    version: str = "v15"
    bbox_shift: int = 0
    extra_margin: int = 10
    batch_size: int = 20
    parsing_mode: str = "jaw"
    left_cheek_width: int = 90
    right_cheek_width: int = 90
    audio_padding_length_left: int = 2
    audio_padding_length_right: int = 2

    # RTMP settings
    rtmp_url: str = ""
    video_bitrate: str = "4000k"
    audio_bitrate: str = "128k"
    preset: str = "ultrafast"

    # Audio settings
    sample_rate: int = 16000

    # Model paths (relative to MUSETALK_DIR, used via chdir)
    unet_config: str = "models/musetalkV15/musetalk.json"
    unet_model_path: str = "models/musetalkV15/unet.pth"
    vae_type: str = "sd-vae"
    whisper_dir: str = "models/whisper"

    # GPU
    gpu_id: int = 0


class MuseTalkEngine:
    """
    Handles MuseTalk model loading, avatar preparation, and real-time lip-sync inference.
    Uses the exact same API as MuseTalk's realtime_inference.py Avatar class.
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.device = torch.device(f"cuda:{config.gpu_id}" if torch.cuda.is_available() else "cpu")
        self.models_loaded = False

        # Model references
        self.vae = None
        self.unet = None
        self.pe = None
        self.timesteps = None
        self.audio_processor = None
        self.whisper = None
        self.fp = None
        self.weight_dtype = None

        # Avatar data (pre-processed)
        self.frame_list_cycle = []
        self.coord_list_cycle = []
        self.input_latent_list_cycle = []
        self.mask_list_cycle = []
        self.mask_coords_list_cycle = []

    def load_models(self):
        """Load all MuseTalk models into GPU memory."""
        logger.info("Loading MuseTalk models...")

        original_cwd = os.getcwd()
        os.chdir(MUSETALK_DIR)
        if MUSETALK_DIR not in sys.path:
            sys.path.insert(0, MUSETALK_DIR)

        try:
            from musetalk.utils.utils import load_all_model
            from musetalk.utils.face_parsing import FaceParsing
            from musetalk.utils.audio_processor import AudioProcessor
            from transformers import WhisperModel

            # load_all_model uses relative paths internally (e.g., "models/sd-vae")
            self.vae, self.unet, self.pe = load_all_model(
                unet_model_path=self.config.unet_model_path,
                vae_type=self.config.vae_type,
                unet_config=self.config.unet_config,
                device=self.device
            )
            self.timesteps = torch.tensor([0], device=self.device)

            # Half precision for speed
            self.pe = self.pe.half().to(self.device)
            self.vae.vae = self.vae.vae.half().to(self.device)
            self.unet.model = self.unet.model.half().to(self.device)
            self.weight_dtype = self.unet.model.dtype

            # Audio processor + Whisper
            whisper_abs = os.path.join(MUSETALK_DIR, self.config.whisper_dir)
            self.audio_processor = AudioProcessor(feature_extractor_path=whisper_abs)
            self.whisper = WhisperModel.from_pretrained(whisper_abs)
            self.whisper = self.whisper.to(device=self.device, dtype=self.weight_dtype).eval()
            self.whisper.requires_grad_(False)

            # Face parser
            if self.config.version == "v15":
                self.fp = FaceParsing(
                    left_cheek_width=self.config.left_cheek_width,
                    right_cheek_width=self.config.right_cheek_width
                )
            else:
                self.fp = FaceParsing()

            self.models_loaded = True
            logger.info("MuseTalk models loaded successfully.")
        finally:
            os.chdir(original_cwd)

    def prepare_avatar(self, video_path: str) -> bool:
        """
        Pre-process the base video: extract frames, detect faces, compute latents and masks.
        Follows the exact same flow as MuseTalk's Avatar.prepare_material().
        """
        if not self.models_loaded:
            self.load_models()

        logger.info(f"Preparing avatar from: {video_path}")

        original_cwd = os.getcwd()
        os.chdir(MUSETALK_DIR)

        try:
            from musetalk.utils.preprocessing import get_landmark_and_bbox, read_imgs
            from musetalk.utils.blending import get_image_prepare_material

            # Step 1: Extract frames to temp directory (get_landmark_and_bbox needs file paths)
            avatar_tmp = os.path.join(tempfile.gettempdir(), "live_engine_avatar")
            full_imgs_path = os.path.join(avatar_tmp, "full_imgs")
            os.makedirs(full_imgs_path, exist_ok=True)

            # Clear previous frames
            for f in glob.glob(os.path.join(full_imgs_path, "*.png")):
                os.remove(f)

            # Extract frames from video
            cap = cv2.VideoCapture(video_path)
            count = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                cv2.imwrite(os.path.join(full_imgs_path, f"{count:08d}.png"), frame)
                count += 1
            cap.release()

            if count == 0:
                logger.error("No frames extracted from video!")
                return False

            logger.info(f"Extracted {count} frames from video")

            # Step 2: Get sorted image list (same as MuseTalk)
            input_img_list = sorted(
                glob.glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]')),
                key=lambda x: int(os.path.splitext(os.path.basename(x))[0])
            )

            # Step 3: Detect face landmarks and bounding boxes
            logger.info("Extracting landmarks...")
            coord_list, frame_list = get_landmark_and_bbox(input_img_list, self.config.bbox_shift)

            # Step 4: Compute VAE latents for each face crop
            input_latent_list = []
            coord_placeholder = (0.0, 0.0, 0.0, 0.0)

            for idx, (bbox, frame) in enumerate(zip(coord_list, frame_list)):
                if bbox == coord_placeholder:
                    continue
                x1, y1, x2, y2 = bbox
                if self.config.version == "v15":
                    y2 = y2 + self.config.extra_margin
                    y2 = min(y2, frame.shape[0])
                    coord_list[idx] = [x1, y1, x2, y2]

                crop_frame = frame[y1:y2, x1:x2]
                resized_crop_frame = cv2.resize(crop_frame, (256, 256), interpolation=cv2.INTER_LANCZOS4)
                latents = self.vae.get_latents_for_unet(resized_crop_frame)
                input_latent_list.append(latents)

            # Step 5: Create forward-backward loop for seamless looping
            self.frame_list_cycle = frame_list + frame_list[::-1]
            self.coord_list_cycle = coord_list + coord_list[::-1]
            self.input_latent_list_cycle = input_latent_list + input_latent_list[::-1]

            # Step 6: Pre-compute masks for blending
            self.mask_list_cycle = []
            self.mask_coords_list_cycle = []

            mode = self.config.parsing_mode if self.config.version == "v15" else "raw"

            for i, frame in enumerate(tqdm(self.frame_list_cycle, desc="Computing masks")):
                x1, y1, x2, y2 = self.coord_list_cycle[i]
                mask, crop_box = get_image_prepare_material(
                    frame, [x1, y1, x2, y2], fp=self.fp, mode=mode
                )
                self.mask_list_cycle.append(mask)
                self.mask_coords_list_cycle.append(crop_box)

            logger.info(f"Avatar prepared: {len(self.frame_list_cycle)} frames in loop cycle")
            return True

        except Exception as e:
            logger.error(f"Avatar preparation failed: {e}", exc_info=True)
            return False
        finally:
            os.chdir(original_cwd)

    @torch.no_grad()
    def generate_lipsync_frames(self, audio_path: str) -> List[np.ndarray]:
        """
        Generate lip-synced frames from audio.
        Uses the exact same audio processing pipeline as MuseTalk's realtime_inference.py.
        Returns list of composited frames (full resolution, mouth replaced).
        """
        original_cwd = os.getcwd()
        os.chdir(MUSETALK_DIR)

        try:
            from musetalk.utils.utils import datagen
            from musetalk.utils.blending import get_image_blending

            # Step 1: Extract audio features (same API as realtime_inference.py)
            whisper_input_features, librosa_length = self.audio_processor.get_audio_feature(
                audio_path, weight_dtype=self.weight_dtype
            )

            if whisper_input_features is None:
                logger.error(f"Failed to load audio: {audio_path}")
                return []

            # Step 2: Get whisper chunks
            whisper_chunks = self.audio_processor.get_whisper_chunk(
                whisper_input_features,
                self.device,
                self.weight_dtype,
                self.whisper,
                librosa_length,
                fps=self.config.fps,
                audio_padding_length_left=self.config.audio_padding_length_left,
                audio_padding_length_right=self.config.audio_padding_length_right,
            )

            # Step 3: Inference batch by batch
            video_num = len(whisper_chunks)
            gen = datagen(whisper_chunks, self.input_latent_list_cycle, self.config.batch_size)

            result_frames = []
            frame_idx = 0

            for whisper_batch, latent_batch in gen:
                audio_feature_batch = self.pe(whisper_batch.to(self.device))
                latent_batch = latent_batch.to(device=self.device, dtype=self.weight_dtype)

                pred_latents = self.unet.model(
                    latent_batch, self.timesteps,
                    encoder_hidden_states=audio_feature_batch
                ).sample
                pred_latents = pred_latents.to(device=self.device, dtype=self.vae.vae.dtype)
                recon = self.vae.decode_latents(pred_latents)

                for res_frame in recon:
                    if frame_idx >= video_num:
                        break

                    bbox = self.coord_list_cycle[frame_idx % len(self.coord_list_cycle)]
                    ori_frame = copy.deepcopy(
                        self.frame_list_cycle[frame_idx % len(self.frame_list_cycle)]
                    )
                    x1, y1, x2, y2 = bbox

                    try:
                        res_frame = cv2.resize(
                            res_frame.astype(np.uint8), (x2 - x1, y2 - y1)
                        )
                    except Exception:
                        frame_idx += 1
                        continue

                    mask = self.mask_list_cycle[frame_idx % len(self.mask_list_cycle)]
                    mask_crop_box = self.mask_coords_list_cycle[
                        frame_idx % len(self.mask_coords_list_cycle)
                    ]

                    combine_frame = get_image_blending(
                        ori_frame, res_frame, bbox, mask, mask_crop_box
                    )
                    result_frames.append(combine_frame)
                    frame_idx += 1

            logger.info(f"Generated {len(result_frames)} lip-sync frames from {video_num} audio chunks")
            return result_frames

        except Exception as e:
            logger.error(f"Lip-sync generation failed: {e}", exc_info=True)
            return []
        finally:
            os.chdir(original_cwd)

    def generate_test_video(self, audio_path: str, output_path: str) -> bool:
        """
        Generate a test MP4 video with lip-sync (no RTMP needed).
        Useful for verifying lip-sync quality before streaming.
        """
        frames = self.generate_lipsync_frames(audio_path)
        if not frames:
            logger.error("No frames generated!")
            return False

        # Write frames to temp images
        tmp_dir = os.path.join(tempfile.gettempdir(), "live_engine_test")
        os.makedirs(tmp_dir, exist_ok=True)

        for i, frame in enumerate(frames):
            cv2.imwrite(os.path.join(tmp_dir, f"{i:08d}.png"), frame)

        h, w = frames[0].shape[:2]

        # Use ffmpeg to combine frames + audio into MP4
        temp_video = os.path.join(tmp_dir, "temp.mp4")
        cmd_frames = (
            f"ffmpeg -y -v warning -r {self.config.fps} -f image2 "
            f"-i {tmp_dir}/%08d.png -vcodec libx264 -vf format=yuv420p "
            f"-crf 18 {temp_video}"
        )
        os.system(cmd_frames)

        cmd_audio = (
            f"ffmpeg -y -v warning -i {audio_path} -i {temp_video} "
            f"-c:v copy -c:a aac -shortest {output_path}"
        )
        os.system(cmd_audio)

        # Cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)

        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"Test video saved: {output_path} ({size_mb:.1f}MB, {len(frames)} frames, {w}x{h})")
            return True
        else:
            logger.error("Failed to create test video")
            return False


class RTMPStreamer:
    """
    Manages FFmpeg RTMP output stream.
    Accepts raw frames and audio, encodes and streams to RTMP destination.
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.is_streaming = False

    def start(self, rtmp_url: str, width: int, height: int):
        """Start the FFmpeg RTMP streaming process."""
        if not rtmp_url:
            raise ValueError("RTMP URL is required")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(self.config.fps),
            "-i", "-",
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-tune", "zerolatency",
            "-b:v", self.config.video_bitrate,
            "-maxrate", self.config.video_bitrate,
            "-bufsize", str(int(self.config.video_bitrate.replace("k", "")) * 2) + "k",
            "-pix_fmt", "yuv420p",
            "-g", str(self.config.fps * 2),
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-f", "flv",
            rtmp_url
        ]

        logger.info(f"Starting RTMP stream to: {rtmp_url} ({width}x{height} @ {self.config.fps}fps)")
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        self.is_streaming = True
        self._width = width
        self._height = height
        logger.info("RTMP stream started.")

    def write_frame(self, frame: np.ndarray):
        """Write a single frame to the RTMP stream."""
        if not self.is_streaming or self.process is None:
            return

        try:
            h, w = frame.shape[:2]
            if w != self._width or h != self._height:
                frame = cv2.resize(frame, (self._width, self._height))
            self.process.stdin.write(frame.tobytes())
        except BrokenPipeError:
            logger.error("RTMP stream pipe broken!")
            self.is_streaming = False

    def stop(self):
        """Stop the RTMP stream."""
        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None
        self.is_streaming = False
        logger.info("RTMP stream stopped.")


class LiveStreamEngine:
    """
    Main orchestrator for the hybrid AI live streaming engine.
    Manages the video loop, lip-sync, audio pipeline, and RTMP output.
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.state = EngineState.IDLE
        self.musetalk = MuseTalkEngine(config)
        self.rtmp = RTMPStreamer(config)

        # Threading
        self._stream_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Lip-sync state
        self._speaking = False
        self._current_lipsync_frames: List[np.ndarray] = []
        self._lipsync_frame_idx = 0
        self._speak_lock = threading.Lock()

        # Audio playback
        self._current_audio_path: Optional[str] = None
        self._audio_process: Optional[subprocess.Popen] = None

        # Stats
        self._frames_sent = 0
        self._stream_start_time = 0.0

    def prepare(self, video_path: str) -> bool:
        """Load models and prepare avatar from base video."""
        self.state = EngineState.PREPARING
        try:
            self.musetalk.load_models()
            success = self.musetalk.prepare_avatar(video_path)
            if success:
                self.config.video_path = video_path
                # Detect frame dimensions from first frame
                if self.musetalk.frame_list_cycle:
                    h, w = self.musetalk.frame_list_cycle[0].shape[:2]
                    self.config.width = w
                    self.config.height = h
                    logger.info(f"Frame dimensions: {w}x{h}")
                self.state = EngineState.IDLE
                logger.info("Engine prepared and ready.")
            else:
                self.state = EngineState.ERROR
            return success
        except Exception as e:
            logger.error(f"Preparation failed: {e}", exc_info=True)
            self.state = EngineState.ERROR
            return False

    def start_stream(self, rtmp_url: str) -> bool:
        """Start the live stream to the given RTMP URL."""
        if self.state not in (EngineState.IDLE,):
            logger.error(f"Cannot start stream in state: {self.state}")
            return False

        if not self.musetalk.frame_list_cycle:
            logger.error("No avatar frames prepared!")
            return False

        self.config.rtmp_url = rtmp_url
        self._stop_event.clear()
        self._frames_sent = 0
        self._stream_start_time = time.time()

        # Start RTMP with actual frame dimensions
        try:
            self.rtmp.start(rtmp_url, self.config.width, self.config.height)
        except Exception as e:
            logger.error(f"Failed to start RTMP: {e}")
            return False

        # Start streaming thread
        self.state = EngineState.STREAMING
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()

        logger.info("Live stream started!")
        return True

    def _stream_loop(self):
        """Main streaming loop - runs in a separate thread."""
        frame_duration = 1.0 / self.config.fps
        frame_idx = 0
        cycle_len = len(self.musetalk.frame_list_cycle)

        while not self._stop_event.is_set():
            loop_start = time.time()

            with self._speak_lock:
                if self._speaking and self._current_lipsync_frames:
                    if self._lipsync_frame_idx < len(self._current_lipsync_frames):
                        frame = self._current_lipsync_frames[self._lipsync_frame_idx]
                        self._lipsync_frame_idx += 1
                    else:
                        self._speaking = False
                        self._current_lipsync_frames = []
                        self._lipsync_frame_idx = 0
                        frame = self.musetalk.frame_list_cycle[frame_idx % cycle_len]
                        frame_idx += 1
                else:
                    frame = self.musetalk.frame_list_cycle[frame_idx % cycle_len]
                    frame_idx += 1

            self.rtmp.write_frame(frame)
            self._frames_sent += 1

            elapsed = time.time() - loop_start
            sleep_time = frame_duration - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("Stream loop ended.")

    def speak(self, audio_path: str) -> bool:
        """
        Generate lip-sync frames from audio and inject into the stream.
        """
        if self.state not in (EngineState.STREAMING, EngineState.SPEAKING):
            logger.error(f"Cannot speak in state: {self.state}")
            return False

        logger.info(f"Generating lip-sync for: {audio_path}")

        try:
            frames = self.musetalk.generate_lipsync_frames(audio_path)
            if not frames:
                logger.error("No lip-sync frames generated!")
                return False

            logger.info(f"Generated {len(frames)} lip-sync frames")

            with self._speak_lock:
                self._current_lipsync_frames = frames
                self._lipsync_frame_idx = 0
                self._speaking = True

            self.state = EngineState.SPEAKING
            return True

        except Exception as e:
            logger.error(f"Lip-sync generation failed: {e}", exc_info=True)
            return False

    def generate_test_video(self, audio_path: str, output_path: str) -> bool:
        """Generate a test MP4 with lip-sync (no RTMP needed)."""
        return self.musetalk.generate_test_video(audio_path, output_path)

    def stop_stream(self):
        """Stop the live stream."""
        self.state = EngineState.STOPPING
        self._stop_event.set()

        if self._stream_thread:
            self._stream_thread.join(timeout=5)

        self.rtmp.stop()

        with self._speak_lock:
            self._speaking = False
            self._current_lipsync_frames = []

        elapsed = time.time() - self._stream_start_time if self._stream_start_time else 0
        logger.info(
            f"Stream stopped. Total frames: {self._frames_sent}, "
            f"Duration: {elapsed:.1f}s, Avg FPS: {self._frames_sent / max(elapsed, 1):.1f}"
        )
        self.state = EngineState.IDLE

    def get_status(self) -> Dict[str, Any]:
        """Get current engine status."""
        elapsed = time.time() - self._stream_start_time if self._stream_start_time else 0
        return {
            "state": self.state.value,
            "frames_sent": self._frames_sent,
            "uptime_seconds": round(elapsed, 1),
            "avg_fps": round(self._frames_sent / max(elapsed, 1), 1) if elapsed > 0 else 0,
            "is_speaking": self._speaking,
            "rtmp_url": self.config.rtmp_url,
            "avatar_frames": len(self.musetalk.frame_list_cycle),
            "frame_size": f"{self.config.width}x{self.config.height}",
        }
