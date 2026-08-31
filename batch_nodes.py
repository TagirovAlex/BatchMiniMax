import os
import re
import json
import copy
import time
import glob
import logging
import subprocess
import urllib.request

import cv2
import numpy as np
import torch
from PIL import Image

import folder_paths

logger = logging.getLogger("BatchMiniMax")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif'}


def _scan_folder(folder, video_exts, image_exts):
    """Scan folder for video files, return sorted list of full paths."""
    if not os.path.isdir(folder):
        return []
    files = []
    for f in os.listdir(folder):
        ext = os.path.splitext(f)[1].lower()
        if ext in video_exts:
            files.append(os.path.join(folder, f))
    files.sort(key=lambda x: os.path.basename(x).lower())
    return files


def _find_matching_images(video_path, image_exts):
    """Find images with the same stem as the video file.

    Returns up to 2 image paths: primary and secondary (_ref suffix).
    """
    stem = os.path.splitext(os.path.basename(video_path))[0]
    folder = os.path.dirname(video_path)
    primary = None
    secondary = None

    for ext in image_exts:
        # Primary: exact stem match
        candidate = os.path.join(folder, stem + ext)
        if os.path.isfile(candidate):
            primary = candidate
            break

    if primary is None:
        # Try with _ref suffix
        for ext in image_exts:
            candidate = os.path.join(folder, stem + "_ref" + ext)
            if os.path.isfile(candidate):
                primary = candidate
                break

    # Secondary: look for _ref or _2 pattern
    for ext in image_exts:
        candidate = os.path.join(folder, stem + "_ref" + ext)
        if os.path.isfile(candidate) and candidate != primary:
            secondary = candidate
            break
    if secondary is None:
        for ext in image_exts:
            candidate = os.path.join(folder, stem + "_2" + ext)
            if os.path.isfile(candidate) and candidate != primary:
                secondary = candidate
                break

    return primary, secondary


def _parse_ext_csv(csv_str, defaults):
    """Parse comma-separated extension string, normalize with leading dot."""
    exts = set()
    for part in csv_str.split(','):
        part = part.strip().lower()
        if not part:
            continue
        if not part.startswith('.'):
            part = '.' + part
        exts.add(part)
    return exts if exts else defaults


def _load_video_frames(video_path, force_rate=0, frame_load_cap=0,
                       skip_first_frames=0, select_every_nth=1):
    """Load video frames using OpenCV. Returns (images_tensor, fps, total_frames)."""
    video_cap = cv2.VideoCapture(video_path)
    if not video_cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    fps = video_cap.get(cv2.CAP_PROP_FPS)
    width = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if width <= 0 or height <= 0:
        ret, frame = video_cap.read()
        if ret:
            height, width = frame.shape[:2]
        else:
            video_cap.release()
            raise ValueError(f"Could not read frame from: {video_path}")

    base_frame_time = 1.0 / fps if fps > 0 else 1.0 / 24.0
    if force_rate > 0:
        target_frame_time = 1.0 / force_rate
    else:
        target_frame_time = base_frame_time

    frames = []
    frame_count = 0
    evaluated = 0
    time_offset = target_frame_time

    while video_cap.isOpened():
        if time_offset < target_frame_time:
            if not video_cap.grab():
                break
            time_offset += base_frame_time
            continue
        time_offset -= target_frame_time
        frame_count += 1

        if frame_count <= skip_first_frames:
            continue
        if select_every_nth > 1 and evaluated % select_every_nth != 0:
            evaluated += 1
            continue

        ret, frame = video_cap.read()
        if not ret:
            break
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = np.array(frame, dtype=np.float32) / 255.0
        frames.append(frame)
        evaluated += 1

        if frame_load_cap > 0 and len(frames) >= frame_load_cap:
            break

    video_cap.release()

    if not frames:
        raise RuntimeError(f"No frames loaded from: {video_path}")

    images = torch.from_numpy(np.stack(frames))
    return images, fps, total_frames


def _load_image_tensor(image_path):
    """Load an image file and return as ComfyUI IMAGE tensor [1, H, W, C]."""
    img = Image.open(image_path).convert('RGB')
    arr = np.array(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def _find_prompt(video_path, prompt_exts, fallback_prompt=""):
    """Look for a per-video prompt file (same stem, .txt / .prompt) next to the video.

    Returns (prompt_text, is_from_file). If no matching file is found, returns
    (fallback_prompt, False).
    """
    stem = os.path.splitext(os.path.basename(video_path))[0]
    folder = os.path.dirname(video_path)
    for ext in prompt_exts:
        candidate = os.path.join(folder, stem + ext)
        if os.path.isfile(candidate):
            try:
                with open(candidate, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
                if text:
                    return text, True
            except (OSError, UnicodeDecodeError):
                logger.warning(f"BatchMiniMax: could not read prompt file {candidate}, "
                               "using fallback prompt")
                break
    return fallback_prompt, False


def _get_audio(file):
    """Extract audio using ffmpeg. Returns AUDIO dict or None."""
    try:
        from videohelpersuite.utils import ffmpeg_path
    except ImportError:
        try:
            import shutil
            ffmpeg_path = shutil.which("ffmpeg")
        except Exception:
            ffmpeg_path = "ffmpeg"

    if not ffmpeg_path:
        return None

    args = [ffmpeg_path, "-i", file, "-f", "f32le", "-"]
    try:
        res = subprocess.run(args, capture_output=True, check=True, timeout=120)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if not res.stdout:
        return None

    audio = torch.frombuffer(bytearray(res.stdout), dtype=torch.float32)
    match = re.search(r', (\d+) Hz, (\w+), ', res.stderr.decode(errors='replace'))

    if match:
        ar = int(match.group(1))
        ac = {"mono": 1, "stereo": 2}.get(match.group(2), 2)
    else:
        ar = 44100
        ac = 2

    if audio.numel() == 0:
        return None

    audio = audio.reshape((-1, ac)).transpose(0, 1).unsqueeze(0)
    return {"waveform": audio, "sample_rate": ar}


# ---------------------------------------------------------------------------
# Node: BatchMiniMaxLoader
# ---------------------------------------------------------------------------

class BatchMiniMaxLoader:
    """Scans a folder for video files, loads the current one, finds matching
    reference images. Replaces VHS_LoadVideo + LoadImage nodes for batch
    processing of MiniMax H3 workflows."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "folder_path": ("STRING", {"default": "", "multiline": False,
                    "tooltip": "Path to folder containing video files"}),
                "index": ("INT", {"default": 0, "min": 0, "step": 1,
                    "tooltip": "Index of the file to process (auto-incremented by BatchAutoQueue)"}),
                "fallback_prompt": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "Prompt used when no matching .txt/.prompt file is found "
                               "next to the video"}),
            },
            "optional": {
                "video_extensions": ("STRING", {
                    "default": ".mp4,.mov,.avi,.mkv,.webm",
                    "tooltip": "Comma-separated video file extensions to scan for"}),
                "image_extensions": ("STRING", {
                    "default": ".png,.jpg,.jpeg,.webp",
                    "tooltip": "Comma-separated image file extensions to look for"}),
                "prompt_extensions": ("STRING", {
                    "default": ".txt,.prompt",
                    "tooltip": "Comma-separated text file extensions used as per-video prompt"}),
                "force_rate": ("INT", {"default": 0, "min": 0, "max": 120,
                    "tooltip": "Force FPS (0 = original)"}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0,
                    "tooltip": "Max frames to load (0 = all)"}),
                "skip_first_frames": ("INT", {"default": 0, "min": 0,
                    "tooltip": "Skip first N frames"}),
                "select_every_nth": ("INT", {"default": 1, "min": 1,
                    "tooltip": "Select every Nth frame"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "IMAGE", "IMAGE", "STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("video_frames", "video_audio", "ref_image_1", "ref_image_2",
                    "prompt", "filename", "current_index", "total_count")
    FUNCTION = "load"
    CATEGORY = "BatchMiniMax"
    DESCRIPTION = ("Scans a folder for video files and optional matching reference images "
                   "and prompt files. Outputs frames, audio, up to 2 reference images (None "
                   "if absent) and a per-video prompt (from a matching .txt file, or the "
                   "fallback_prompt if none exists).")

    def load(self, folder_path, index, fallback_prompt="",
             video_extensions=".mp4,.mov,.avi,.mkv,.webm",
             image_extensions=".png,.jpg,.jpeg,.webp",
             prompt_extensions=".txt,.prompt", force_rate=0,
             frame_load_cap=0, skip_first_frames=0, select_every_nth=1):

        v_exts = _parse_ext_csv(video_extensions, VIDEO_EXTENSIONS)
        i_exts = _parse_ext_csv(image_extensions, IMAGE_EXTENSIONS)
        p_exts = _parse_ext_csv(prompt_extensions, {'.txt', '.prompt'})

        videos = _scan_folder(folder_path, v_exts, i_exts)
        if not videos:
            raise RuntimeError(f"No video files found in: {folder_path}")

        total = len(videos)
        idx = max(0, min(index, total - 1))
        video_path = videos[idx]

        # Load video frames
        images, fps, total_frames = _load_video_frames(
            video_path, force_rate, frame_load_cap, skip_first_frames, select_every_nth)

        # Load audio
        audio = _get_audio(video_path)
        if audio is None:
            # Return silent audio placeholder
            audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 44100}

        # Find matching reference images
        img1_path, img2_path = _find_matching_images(video_path, i_exts)
        ref1 = _load_image_tensor(img1_path) if img1_path else None
        ref2 = _load_image_tensor(img2_path) if img2_path else None

        # Find matching prompt file (per-video), fall back to workflow prompt
        prompt, prompt_is_fallback = _find_prompt(video_path, p_exts, fallback_prompt)

        filename = os.path.splitext(os.path.basename(video_path))[0]

        logger.info(f"BatchMiniMax: [{idx+1}/{total}] {filename}"
                     f" | ref1={'yes' if ref1 is not None else 'no'}"
                     f" | ref2={'yes' if ref2 is not None else 'no'}"
                     f" | prompt={'file' if not prompt_is_fallback else 'fallback'}"
                     f" | frames={images.shape[0]}")

        return (images, audio, ref1, ref2, prompt, filename, idx, total)


# ---------------------------------------------------------------------------
# Node: BatchAutoQueue
# ---------------------------------------------------------------------------

class BatchAutoQueue:
    """Place after save nodes. Automatically queues the next batch item
    by modifying the BatchMiniMaxLoader's index in the workflow prompt."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "current_index": ("INT", {"default": 0, "min": 0}),
                "total_count": ("INT", {"default": 1, "min": 1}),
            },
            "optional": {
                "trigger": ("*", {"tooltip": "Connect from your save node to ensure "
                                          "this runs only AFTER generation finishes"}),
                "auto_next": ("BOOLEAN", {"default": True,
                    "tooltip": "Automatically queue the next file"}),
                "delay_seconds": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 60.0, "step": 0.1,
                    "tooltip": "Delay before queuing next (seconds)"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "BatchMiniMax"
    DESCRIPTION = ("Auto-queues the next batch item after generation. "
                   "Connect trigger from your save node (e.g. VHS_VideoCombine) so it "
                   "only runs after the video is saved.")

    def run(self, current_index, total_count, trigger=None, auto_next=True, delay_seconds=1.0,
            prompt=None, unique_id=None):

        status = f"[Batch] {current_index + 1}/{total_count}"

        if not auto_next or current_index >= total_count - 1:
            if current_index >= total_count - 1:
                status += " — DONE (all files processed)"
            else:
                status += " — auto_next disabled"
            return {"ui": {"text": [status]}, "result": (status,)}

        next_index = current_index + 1
        status += f" — queuing next ({next_index + 1}/{total_count})..."

        # Build modified prompt
        if prompt is None:
            logger.warning("BatchAutoQueue: prompt data not available")
            return {"ui": {"text": [status + " ERROR: no prompt data"]}, "result": (status,)}

        prompt_data = prompt[0] if isinstance(prompt, list) else prompt
        modified = copy.deepcopy(prompt_data)

        # Find BatchMiniMaxLoader node and update its index
        found = False
        for node_id, node_info in modified.items():
            if not isinstance(node_info, dict):
                continue
            if node_info.get("class_type") == "BatchMiniMaxLoader":
                inputs = node_info.get("inputs", {})
                if isinstance(inputs, dict):
                    inputs["index"] = next_index
                    found = True
                    break

        if not found:
            logger.warning("BatchAutoQueue: BatchMiniMaxLoader node not found in prompt")
            return {"ui": {"text": [status + " ERROR: node not found"]}, "result": (status,)}

        # Queue via HTTP API
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        try:
            payload = json.dumps({"prompt": modified}).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8188/prompt",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                prompt_id = result.get("prompt_id", "?")
                status += f" queued ({prompt_id[:8]}...)"
                logger.info(f"BatchAutoQueue: queued prompt {prompt_id} for index {next_index}")
        except Exception as e:
            status += f" ERROR: {e}"
            logger.error(f"BatchAutoQueue: failed to queue: {e}")

        return {"ui": {"text": [status]}, "result": (status,)}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "BatchMiniMaxLoader": BatchMiniMaxLoader,
    "BatchAutoQueue": BatchAutoQueue,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BatchMiniMaxLoader": "Batch MiniMax Loader",
    "BatchAutoQueue": "Batch Auto Queue",
}
