"""
Hybrid AI Live Streaming Engine
================================
Server-side complete live streaming engine that combines:
- Base video loop with natural movement
- MuseTalk real-time lip-sync (mouth-only replacement)
- FFmpeg RTMP streaming output
- Real-time GPT conversation + TTS audio pipeline
- Control API for external management

Architecture:
    Video Looper → LipSync Engine → Frame Compositor → RTMP Streamer
                                  ↑
    Audio Pipeline (TTS → Whisper features) ─┘
"""

import os
import sys
import time
import json
import copy
import queue
import threading
import subprocess
import logging
import asyncio
import signal
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import cv2
import torch

# MuseTalk imports (will be loaded after sys.path setup)
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
    video_path: str = ""           # Path to base portrait video
    fps: int = 25                  # Output frame rate
    width: int = 1080              # Output width (9:16 portrait)
    height: int = 1920             # Output height

    # MuseTalk settings
    version: str = "v15"           # MuseTalk version
    bbox_shift: int = 0
    extra_margin: int = 10
    batch_size: int = 20
    parsing_mode: str = "jaw"      # Face blending mode (jaw = mouth area only)
    left_cheek_width: int = 90
    right_cheek_width: int = 90

    # RTMP settings
    rtmp_url: str = ""             # RTMP destination URL
    video_bitrate: str = "4000k"
    audio_bitrate: str = "128k"
    preset: str = "ultrafast"      # FFmpeg encoding preset

    # Audio settings
    sample_rate: int = 16000
    audio_channels: int = 1

    # Model paths
    unet_config: str = os.path.join(MUSETALK_DIR, "models", "musetalk", "musetalk.json")
    unet_model_path: str = os.path.join(MUSETALK_DIR, "models", "musetalk", "pytorch_model.bin")
    vae_type: str = "sd-vae"
    whisper_dir: str = os.path.join(MUSETALK_DIR, "models", "whisper")

    # GPU
    gpu_id: int = 0


class MuseTalkEngine:
    """
    Handles MuseTalk model loading, avatar preparation, and real-time lip-sync inference.
    Wraps the core MuseTalk functionality for streaming use.
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
        self.fp = None  # FaceParsing

        # Avatar data (pre-processed)
        self.frame_list_cycle = []
        self.coord_list_cycle = []
        self.input_latent_list_cycle = []
        self.mask_list_cycle = []
        self.mask_coords_list_cycle = []

    def load_models(self):
        """Load all MuseTalk models into GPU memory."""
        logger.info("Loading MuseTalk models...")

        sys.path.insert(0, MUSETALK_DIR)
        from musetalk.utils.utils import load_all_model
        from musetalk.utils.face_parsing import FaceParsing
        from musetalk.utils.audio_processor import AudioProcessor
        from transformers import WhisperModel

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

        # Audio processor + Whisper
        self.audio_processor = AudioProcessor(feature_extractor_path=self.config.whisper_dir)
        weight_dtype = self.unet.model.dtype
        self.whisper = WhisperModel.from_pretrained(self.config.whisper_dir)
        self.whisper = self.whisper.to(device=self.device, dtype=weight_dtype).eval()
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

    def prepare_avatar(self, video_path: str) -> bool:
        """
        Pre-process the base video: extract frames, detect faces, compute latents and masks.
        This is done once and cached for real-time inference.
        """
        if not self.models_loaded:
            self.load_models()

        logger.info(f"Preparing avatar from: {video_path}")

        sys.path.insert(0, MUSETALK_DIR)
        from musetalk.utils.preprocessing import get_landmark_and_bbox, read_imgs
        from musetalk.utils.blending import get_image_prepare_material

        # Extract frames from video
        cap = cv2.VideoCapture(video_path)
        frames = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
        cap.release()

        if not frames:
            logger.error("No frames extracted from video!")
            return False

        logger.info(f"Extracted {len(frames)} frames from video")

        # Detect face landmarks and bounding boxes
        coord_list, frame_list = get_landmark_and_bbox(frames, self.config.bbox_shift)

        # Compute VAE latents for each face crop
        input_latent_list = []
        coord_placeholder = (0.0, 0.0, 0.0, 0.0)
        valid_coords = []

        for idx, (bbox, frame) in enumerate(zip(coord_list, frame_list)):
            if bbox == coord_placeholder:
                valid_coords.append(bbox)
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
            valid_coords.append(coord_list[idx])

        # Create forward-backward loop for seamless looping
        self.frame_list_cycle = frame_list + frame_list[::-1]
        self.coord_list_cycle = coord_list + coord_list[::-1]
        self.input_latent_list_cycle = input_latent_list + input_latent_list[::-1]

        # Pre-compute masks for blending
        self.mask_list_cycle = []
        self.mask_coords_list_cycle = []

        mode = self.config.parsing_mode if self.config.version == "v15" else "raw"

        for i, frame in enumerate(self.frame_list_cycle):
            x1, y1, x2, y2 = self.coord_list_cycle[i]
            mask, crop_box = get_image_prepare_material(
                frame, [x1, y1, x2, y2], fp=self.fp, mode=mode
            )
            self.mask_list_cycle.append(mask)
            self.mask_coords_list_cycle.append(crop_box)

        logger.info(f"Avatar prepared: {len(self.frame_list_cycle)} frames in loop cycle")
        return True

    @torch.no_grad()
    def generate_lipsync_frames(self, audio_path: str) -> List[np.ndarray]:
        """
        Generate lip-synced frames from audio.
        Returns list of composited frames (full resolution, mouth replaced).
        """
        sys.path.insert(0, MUSETALK_DIR)
        from musetalk.utils.utils import datagen
        from musetalk.utils.blending import get_image_blending

        # Extract audio features
        whisper_input_features = self.audio_processor.audio2feat(audio_path)
        whisper_chunks = self.audio_processor.feature2chunks(
            whisper_input_features,
            self.device,
            self.unet.model.dtype,
            self.whisper,
            len(self.input_latent_list_cycle),
            fps=self.config.fps,
            audio_padding_length_left=2,
            audio_padding_length_right=2,
        )

        video_num = len(whisper_chunks)
        gen = datagen(whisper_chunks, self.input_latent_list_cycle, self.config.batch_size)

        result_frames = []
        frame_idx = 0

        for whisper_batch, latent_batch in gen:
            audio_feature_batch = self.pe(whisper_batch.to(self.device))
            latent_batch = latent_batch.to(device=self.device, dtype=self.unet.model.dtype)

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

        return result_frames

    @torch.no_grad()
    def generate_lipsync_frames_streaming(self, audio_path: str, frame_callback):
        """
        Generate lip-synced frames and call frame_callback for each frame.
        This is the streaming version - frames are sent as they are generated.
        """
        sys.path.insert(0, MUSETALK_DIR)
        from musetalk.utils.utils import datagen
        from musetalk.utils.blending import get_image_blending

        whisper_input_features = self.audio_processor.audio2feat(audio_path)
        whisper_chunks = self.audio_processor.feature2chunks(
            whisper_input_features,
            self.device,
            self.unet.model.dtype,
            self.whisper,
            len(self.input_latent_list_cycle),
            fps=self.config.fps,
            audio_padding_length_left=2,
            audio_padding_length_right=2,
        )

        video_num = len(whisper_chunks)
        gen = datagen(whisper_chunks, self.input_latent_list_cycle, self.config.batch_size)
        frame_idx = 0

        for whisper_batch, latent_batch in gen:
            audio_feature_batch = self.pe(whisper_batch.to(self.device))
            latent_batch = latent_batch.to(device=self.device, dtype=self.unet.model.dtype)

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
                frame_callback(combine_frame, frame_idx)
                frame_idx += 1


class RTMPStreamer:
    """
    Manages FFmpeg RTMP output stream.
    Accepts raw frames and audio, encodes and streams to RTMP destination.
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.is_streaming = False

    def start(self, rtmp_url: str = None):
        """Start the FFmpeg RTMP streaming process."""
        url = rtmp_url or self.config.rtmp_url
        if not url:
            raise ValueError("RTMP URL is required")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.config.width}x{self.config.height}",
            "-r", str(self.config.fps),
            "-i", "-",                    # Video from stdin
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",  # Silent audio (replaced when speaking)
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-b:v", self.config.video_bitrate,
            "-maxrate", self.config.video_bitrate,
            "-bufsize", str(int(self.config.video_bitrate.replace("k", "")) * 2) + "k",
            "-pix_fmt", "yuv420p",
            "-g", str(self.config.fps * 2),  # Keyframe interval
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-f", "flv",
            url
        ]

        logger.info(f"Starting RTMP stream to: {url}")
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        self.is_streaming = True
        logger.info("RTMP stream started.")

    def start_with_audio(self, rtmp_url: str, audio_pipe_path: str):
        """Start FFmpeg with both video stdin and audio from named pipe."""
        url = rtmp_url or self.config.rtmp_url

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.config.width}x{self.config.height}",
            "-r", str(self.config.fps),
            "-i", "-",                    # Video from stdin
            "-f", "s16le",
            "-ar", "44100",
            "-ac", "2",
            "-i", audio_pipe_path,        # Audio from named pipe
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-b:v", self.config.video_bitrate,
            "-maxrate", self.config.video_bitrate,
            "-bufsize", str(int(self.config.video_bitrate.replace("k", "")) * 2) + "k",
            "-pix_fmt", "yuv420p",
            "-g", str(self.config.fps * 2),
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-shortest",
            "-f", "flv",
            url
        ]

        logger.info(f"Starting RTMP stream with audio to: {url}")
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        self.is_streaming = True

    def write_frame(self, frame: np.ndarray):
        """Write a single frame to the RTMP stream."""
        if not self.is_streaming or self.process is None:
            return

        try:
            # Resize frame to output dimensions if needed
            h, w = frame.shape[:2]
            if w != self.config.width or h != self.config.height:
                frame = cv2.resize(frame, (self.config.width, self.config.height))

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
            self.process.wait(timeout=5)
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

        # Frame queues
        self._lipsync_queue = queue.Queue(maxsize=100)
        self._speaking = False
        self._current_lipsync_frames: List[np.ndarray] = []
        self._lipsync_frame_idx = 0

        # Audio state
        self._audio_pipe_path = "/tmp/live_engine_audio"
        self._current_audio_path: Optional[str] = None

        # Stats
        self._frames_sent = 0
        self._stream_start_time = 0

    def prepare(self, video_path: str) -> bool:
        """Load models and prepare avatar from base video."""
        self.state = EngineState.PREPARING
        try:
            self.musetalk.load_models()
            success = self.musetalk.prepare_avatar(video_path)
            if success:
                self.config.video_path = video_path
                self.state = EngineState.IDLE
                logger.info("Engine prepared and ready.")
            else:
                self.state = EngineState.ERROR
            return success
        except Exception as e:
            logger.error(f"Preparation failed: {e}")
            self.state = EngineState.ERROR
            return False

    def start_stream(self, rtmp_url: str) -> bool:
        """Start the live stream to the given RTMP URL."""
        if self.state not in (EngineState.IDLE,):
            logger.error(f"Cannot start stream in state: {self.state}")
            return False

        self.config.rtmp_url = rtmp_url
        self._stop_event.clear()
        self._frames_sent = 0
        self._stream_start_time = time.time()

        # Start RTMP
        try:
            self.rtmp.start(rtmp_url)
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

        while not self._stop_event.is_set():
            loop_start = time.time()

            if self._speaking and self._current_lipsync_frames:
                # Use lip-synced frame
                if self._lipsync_frame_idx < len(self._current_lipsync_frames):
                    frame = self._current_lipsync_frames[self._lipsync_frame_idx]
                    self._lipsync_frame_idx += 1
                else:
                    # Lip-sync frames exhausted, back to idle loop
                    self._speaking = False
                    self._current_lipsync_frames = []
                    self._lipsync_frame_idx = 0
                    frame = self.musetalk.frame_list_cycle[
                        frame_idx % len(self.musetalk.frame_list_cycle)
                    ]
                    frame_idx += 1
            else:
                # Idle: loop base video frames
                frame = self.musetalk.frame_list_cycle[
                    frame_idx % len(self.musetalk.frame_list_cycle)
                ]
                frame_idx += 1

            # Write frame to RTMP
            self.rtmp.write_frame(frame)
            self._frames_sent += 1

            # Maintain frame rate
            elapsed = time.time() - loop_start
            sleep_time = frame_duration - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("Stream loop ended.")

    def speak(self, audio_path: str) -> bool:
        """
        Generate lip-sync frames from audio and inject into the stream.
        This is called when the AI needs to speak.
        """
        if self.state != EngineState.STREAMING:
            logger.error(f"Cannot speak in state: {self.state}")
            return False

        logger.info(f"Generating lip-sync for: {audio_path}")
        self.state = EngineState.SPEAKING

        try:
            # Generate all lip-sync frames
            frames = self.musetalk.generate_lipsync_frames(audio_path)
            logger.info(f"Generated {len(frames)} lip-sync frames")

            # Inject into stream
            self._current_lipsync_frames = frames
            self._lipsync_frame_idx = 0
            self._speaking = True
            self.state = EngineState.STREAMING

            return True
        except Exception as e:
            logger.error(f"Lip-sync generation failed: {e}")
            self.state = EngineState.STREAMING
            return False

    def stop_stream(self):
        """Stop the live stream."""
        self.state = EngineState.STOPPING
        self._stop_event.set()

        if self._stream_thread:
            self._stream_thread.join(timeout=5)

        self.rtmp.stop()
        self._speaking = False
        self._current_lipsync_frames = []

        elapsed = time.time() - self._stream_start_time
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
        }
