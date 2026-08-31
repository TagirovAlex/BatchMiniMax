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


def _scan_folder(folder, video_exts):
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


def _resolve_folder(path):
    """Resolve a possibly-relative folder to an absolute path.

    Order of resolution:
      1. as-is (absolute path, or relative to the current working dir)
      2. relative to ComfyUI's ``input`` directory
    """
    if not path:
        return ""
    if os.path.isdir(path):
        return os.path.abspath(path)
    base = folder_paths.get_input_directory()
    candidate = os.path.join(base, path)
    if os.path.isdir(candidate):
        return candidate
    # fall back to abspath so the error message reflects the tried cwd path
    return os.path.abspath(path)


def _images_for_video(video_path, image_exts):
    """Find all images belonging to a video file.

    A video stem like ``01`` matches every image whose stem starts with
    ``01-`` (e.g. ``01-1.jpg``, ``01-2.jpg``, ``01-ref.jpg``). Images are
    returned sorted by their suffix so the run order is stable:

        video 01.mp4  ->  [01-1.jpg, 01-2.jpg, 01-3.jpg]
    """
    stem = os.path.splitext(os.path.basename(video_path))[0]
    folder = os.path.dirname(video_path)
    prefix = stem + "-"
    matched = []
    for f in os.listdir(folder):
        f_stem, ext = os.path.splitext(f)
        if ext.lower() not in image_exts:
            continue
        if not f_stem.lower().startswith(prefix.lower()):
            continue
        suffix = f_stem[len(stem):]
        matched.append((suffix.lower(), os.path.join(folder, f)))
    matched.sort(key=lambda x: x[0])
    return [path for _, path in matched]


def _build_tasks(videos, image_exts):
    """Build the flat list of batch tasks.

    Iteration order is "video by video": all image-references of the first
    video first, then all of the second, and so on.

    Returns a list of dicts:
        {'video': abs_path, 'refs': [image abs paths...], 'video_stem': str}
    """
    tasks = []
    for video in videos:
        stem = os.path.splitext(os.path.basename(video))[0]
        refs = _images_for_video(video, image_exts)
        tasks.append({
            "video": video,
            "refs": refs,
            "video_stem": stem,
        })
    return tasks


def _flatten_tasks(tasks):
    """Expand per-video tasks into per-(video, image) run entries.

    A video with N reference images yields N entries; each entry carries one
    image so the batch runs the video once per reference. Videos with no
    matching images still yield a single entry with an empty image ref.

    Entries iterate video-by-video:
        video 01 (3 refs) -> 01.1, 01.2, 01.3
        video 02 (1 ref)  -> 02.1
    """
    flat = []
    for task in tasks:
        stem = task["video_stem"]
        refs = task["refs"]
        if not refs:
            flat.append({
                "video": task["video"],
                "image": None,
                "video_stem": stem,
                "out_stem": stem,
            })
            continue
        for i, img in enumerate(refs, start=1):
            suffix = os.path.splitext(os.path.basename(img))[0][len(stem):]
            if not suffix.startswith("-"):
                suffix = f"-{i}"
            flat.append({
                "video": task["video"],
                "image": img,
                "video_stem": stem,
                "out_stem": f"{stem}{suffix}",
            })
    return flat


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
    """Scans a folder for videos and their per-video reference images, then
    serves one (video, image) task at a time.

    A video is run once for every reference image it has. For a folder like::

        clip_001.mp4    clip_001-1.jpg    clip_001-2.jpg    clip_001-3.jpg
        clip_002.mp4    clip_002-1.jpg

    the loader produces 4 tasks, video by video:

        task 0 -> clip_001-x.mp4 with clip_001-1.jpg
        task 1 -> clip_001-x.mp4 with clip_001-2.jpg
        task 2 -> clip_001-x.mp4 with clip_001-3.jpg
        task 3 -> clip_002-x.mp4 with clip_002-1.jpg

    ``task_index`` is a flat index over all tasks and ``total_tasks`` the
    total count, so BatchAutoQueue just increments a single counter.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "folder_path": ("STRING", {"default": "", "multiline": False,
                    "tooltip": "Path to folder containing video files"}),
                "task_index": ("INT", {"default": 0, "min": 0, "step": 1,
                    "tooltip": "Flat index of the task to process "
                               "(auto-incremented by BatchAutoQueue)"}),
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

    RETURN_TYPES = ("IMAGE", "AUDIO", "IMAGE", "STRING", "STRING", "FLOAT", "INT", "INT")
    RETURN_NAMES = ("video_frames", "video_audio", "ref_image",
                    "prompt", "filename", "duration", "task_index", "total_tasks")
    FUNCTION = "load"
    CATEGORY = "BatchMiniMax"
    DESCRIPTION = ("Scans a folder of videos and their per-video reference images, "
                   "then serves one (video, image) task at a time. A video is run once "
                   "per reference image. Outputs frames, audio, one reference image "
                   "(None if absent), a per-video prompt, the video's duration in "
                   "seconds (for the length-computation node), and flat "
                   "task_index/total_tasks counters for BatchAutoQueue.")

    def load(self, folder_path, task_index, fallback_prompt="",
             video_extensions=".mp4,.mov,.avi,.mkv,.webm",
             image_extensions=".png,.jpg,.jpeg,.webp",
             prompt_extensions=".txt,.prompt", force_rate=0,
             frame_load_cap=0, skip_first_frames=0, select_every_nth=1):

        v_exts = _parse_ext_csv(video_extensions, VIDEO_EXTENSIONS)
        i_exts = _parse_ext_csv(image_extensions, IMAGE_EXTENSIONS)
        p_exts = _parse_ext_csv(prompt_extensions, {'.txt', '.prompt'})

        resolved = _resolve_folder(folder_path)
        videos = _scan_folder(resolved, v_exts)
        if not videos:
            raise RuntimeError(
                f"No video files found in: '{folder_path}' "
                f"(resolved to '{resolved}'). "
                f"Provide the full absolute path to the folder.")

        tasks = _build_tasks(videos, i_exts)
        entries = _flatten_tasks(tasks)
        total = len(entries)

        # Resolve current task
        task = entries[max(0, min(task_index, total - 1))]

        # Load video frames
        images, fps, total_frames = _load_video_frames(
            task["video"], force_rate, frame_load_cap, skip_first_frames, select_every_nth)

        # Load audio
        audio = _get_audio(task["video"])
        if audio is None:
            audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 44100}

        # Load the single reference image for this task
        ref_image = _load_image_tensor(task["image"]) if task["image"] else None

        # Per-video prompt (one per video, not per task)
        prompt, prompt_is_fallback = _find_prompt(task["video"], p_exts, fallback_prompt)

        filename = task["out_stem"]

        # Actual duration of the source video in seconds (informs the length node)
        duration = float(total_frames) / fps if fps > 0 else float(images.shape[0]) / 24.0

        logger.info(f"BatchMiniMax: [{task_index + 1}/{total}] "
                    f"{os.path.basename(task['video'])}"
                    f" | ref={'yes' if ref_image is not None else 'no'}"
                    f" | out={filename}"
                    f" | prompt={'file' if not prompt_is_fallback else 'fallback'}"
                    f" | frames={images.shape[0]}"
                    f" | dur={duration:.2f}s")

        return (images, audio, ref_image, prompt, filename, duration, task_index, total)


# ---------------------------------------------------------------------------
# Node: BatchAutoQueue
# ---------------------------------------------------------------------------

class BatchAutoQueue:
    """Place after save nodes. Automatically queues the next batch task
    by incrementing the BatchMiniMaxLoader's task_index in the workflow prompt."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "task_index": ("INT", {"default": 0, "min": 0}),
                "total_tasks": ("INT", {"default": 1, "min": 1}),
            },
            "optional": {
                "trigger": ("*", {"tooltip": "Connect from your save node to ensure "
                                          "this runs only AFTER generation finishes"}),
                "auto_next": ("BOOLEAN", {"default": True,
                    "tooltip": "Automatically queue the next task"}),
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
    DESCRIPTION = ("Auto-queues the next batch task after generation. Connects to "
                   "BatchMiniMaxLoader's task_index/total_tasks and increments "
                   "task_index in the next prompt so the loader advances to the next "
                   "(video, image) pair. Connect trigger from your save node so it only "
                   "runs after the video is saved.")

    def run(self, task_index, total_tasks, trigger=None, auto_next=True, delay_seconds=1.0,
            prompt=None, unique_id=None):

        status = f"[Batch] {task_index + 1}/{total_tasks}"

        if not auto_next or task_index >= total_tasks - 1:
            if task_index >= total_tasks - 1:
                status += " — DONE (all tasks processed)"
            else:
                status += " — auto_next disabled"
            return {"ui": {"text": [status]}, "result": (status,)}

        next_index = task_index + 1
        status += f" — queuing next ({next_index + 1}/{total_tasks})..."

        # Build modified prompt
        if prompt is None:
            logger.warning("BatchAutoQueue: prompt data not available")
            return {"ui": {"text": [status + " ERROR: no prompt data"]}, "result": (status,)}

        prompt_data = prompt[0] if isinstance(prompt, list) else prompt
        modified = copy.deepcopy(prompt_data)

        # Find BatchMiniMaxLoader node and update its task_index
        found = False
        for node_id, node_info in modified.items():
            if not isinstance(node_info, dict):
                continue
            if node_info.get("class_type") == "BatchMiniMaxLoader":
                inputs = node_info.get("inputs", {})
                if isinstance(inputs, dict):
                    inputs["task_index"] = next_index
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
                logger.info(f"BatchAutoQueue: queued prompt {prompt_id} for task_index {next_index}")
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
