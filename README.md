# BatchMiniMax — Batch Processor for MiniMax H3

![GitHub License](https://img.shields.io/github/license/HELPMEEADICE/BatchMiniMax)
![GitHub Release](https://img.shields.io/github/v/release/HELPMEEADICE/BatchMiniMax)
![ComfyUI](https://img.shields.io/badge/ComfyUI-compatible-brightgreen)

Custom nodes for [ComfyUI](https://github.com/comfyanonymous/ComfyUI) that add batch video processing to MiniMax H3 workflows. Scans a folder for video files, automatically finds matching reference images, and processes them one by one with auto-queue support.

---

## 📖 Краткое описание (RU)

Этот пакет добавляет в **ComfyUI** пакетную (batch) обработку видео для **MiniMax H3**. Вместо того чтобы вручную открывать каждый ролик, менять видео и картинку-референс и запускать генерацию снова, вы один раз настраиваете workflow, складываете все ролики в папку и запускаете процесс — ComfyUI сам переберёт их один за другим.

### Что он умеет

- **Сканирует папку** и находит все видеофайлы (`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm` и т.д.), сортирует их по имени.
- **Подбирает референс-картинку** по имени файла. Если рядом с `clip001.mp4` лежит `clip001.png` — она автоматически подхватится как `Picture`.
- **Может передавать вторую картинку** (например, фон) по паттерну `имя_ref.png`.
- **Тянет промпт из текстового файла.** Если рядом с роликом лежит `clip001.txt` — промпт берётся оттуда. Если файла нет — используется стандартный промпт из workflow.
- **Не ломает работу без картинок.** Если у ролика нет референса — нода просто возвращает «пусто», ошибок не будет.
- **Автоматически ставит следующий ролик в очередь**, когда текущий готов.

### Зачем это нужно

Раньше для каждого видео приходилось: вручную указывать файл в `VHS_LoadVideo`, менять картинку, при необходимости править промпт и снова жать Queue. С этим пакетом всё делается само — вы кладёте папку с роликами, нажимаете Queue один раз и получаете обработанные ролики друг за другом.

### Конвенция имён файлов

```
моя_папка/
├── clip_001.mp4        ← видео
├── clip_001.png        ← референс 1 (опционально)
├── clip_001_ref.png    ← референс 2 (фон, опционально)
├── clip_001.txt        ← свой промпт для этого ролика (опционально)
├── clip_002.mp4        ← видео без картинок
└── clip_003.mov        ← ещё одно видео
```

---

## What it does

- **Scans** a folder for video files (mp4, mov, avi, mkv, webm, etc.)
- **Reads** an optional per-video prompt from a matching `.txt`/`.prompt` file (falls back to the workflow prompt if none)
- **Passes** reference images to `MiniMaxH3ReferenceToVideo` (None if absent — no errors)
- **Auto-queues** the next file after each save

## Nodes

### Batch Mini Max Loader

Replaces `VHS_LoadVideo` + `LoadImage` nodes for batch processing.

| Output | Type | Description |
|--------|------|-------------|
| `video_frames` | IMAGE | Loaded video frames `[B, H, W, C]` |
| `video_audio` | AUDIO | Audio track `{"waveform": [1,C,T], "sample_rate": int}` |
| `ref_image_1` | IMAGE | First matching reference image (or `None`) |
| `ref_image_2` | IMAGE | Second matching reference image (or `None`) |
| `prompt` | STRING | Per-video prompt (from `.txt`/`.prompt`, else `fallback_prompt`) |
| `filename` | STRING | Base filename without extension |
| `current_index` | INT | Current batch index |
| `total_count` | INT | Total number of video files found |

### Batch Auto Queue

Place after your save node. Automatically queues the next batch item by modifying the loader's index in the workflow.

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `current_index` | INT | — | From BatchMiniMaxLoader |
| `total_count` | INT | — | From BatchMiniMaxLoader |
| `trigger` | ANY | — | Connect from your save node (e.g. `VHS_VideoCombine`) to run only after generation finishes |
| `auto_next` | BOOLEAN | `True` | Enable auto-queue |
| `delay_seconds` | FLOAT | `1.0` | Delay before next queue (seconds) |

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/HELPMEEADICE/BatchMiniMax.git
```

No extra dependencies — uses OpenCV (cv2) and PIL which come with ComfyUI.

## Ready-made workflow

A modified version of the official MiniMax H3 "Ref2V / Clothing + BG edit" workflow is included at:

```
workflows/OF_MINIMAX_batch.json
```

It swaps the manual `VHS_LoadVideo` + two `LoadImage` nodes + the `StringConcatenate` prompt-chain for the two batch nodes. Everything else (models, LoRA, sampler, VAE tiling decode, video combine) is untouched. Open it in ComfyUI, set the loader's `folder_path`, and hit Queue.

## Workflow setup

### Before (manual, single file)

```
VHS_LoadVideo ──→ MiniMaxH3ReferenceToVideo (ref_video)
LoadImage ──────→ MiniMaxH3ReferenceToVideo (ref_image_1)
LoadImage ──────→ MiniMaxH3ReferenceToVideo (ref_image_2)
```

### After (batch processing)

```
BatchMiniMaxLoader:video_frames ──→ MiniMaxH3ReferenceToVideo (ref_video)
BatchMiniMaxLoader:ref_image_1  ──→ MiniMaxH3ReferenceToVideo (ref_image_1)
BatchMiniMaxLoader:ref_image_2  ──→ MiniMaxH3ReferenceToVideo (ref_image_2)

... (rest of workflow unchanged) ...

VHS_VideoCombine:Filenames ──→ BatchAutoQueue:trigger
BatchMiniMaxLoader:index ──→ BatchAutoQueue:current_index
BatchMiniMaxLoader:total ──→ BatchAutoQueue:total_count
```

Delete the old `VHS_LoadVideo` and `LoadImage` nodes. Connect `BatchMiniMaxLoader` outputs to `MiniMaxH3ReferenceToVideo`. Connect `VHS_VideoCombine:Filenames` to `BatchAutoQueue:trigger` so the next file is queued only after the video is saved.

### File naming convention

```
my_folder/
├── clip_001.mp4          ← video
├── clip_001.png          ← reference image 1 (matched by name)
├── clip_001_ref.png      ← reference image 2 (optional, _ref suffix)
├── clip_001.txt          ← per-video prompt (optional)
├── clip_002.mp4          ← video without reference image
├── clip_003.mov          ← another video
├── clip_003.jpg          ← reference image
```

Matching logic: `{video_stem}.{image_ext}` → `ref_image_1`, `{video_stem}_ref.{image_ext}` → `ref_image_2`. A `{video_stem}.txt` (or `.prompt`) next to the video overrides the `fallback_prompt` for that clip.

### Per-clip prompts

Connect the static workflow prompt into the loader's `fallback_prompt` input. Then, for any clip that has a same-named `.txt`/`.prompt` file next to it, that file's text is used instead:

- `clip_001.mp4` + **`clip_001.txt`** → prompt from the file
- `clip_002.mp4` (no txt) → uses `fallback_prompt`

Wire the loader's `prompt` output into `MiniMaxH3ReferenceToVideo:prompt` (instead of the old concatenated-prompt connection), and delete the old `StringConcatenate` prompt-chain nodes.

The bundled `OF_MINIMAX_batch.json` keeps all four prompt blocks combined into the loader's `fallback_prompt` widget (paste them there). To override per clip, drop a same-named `.txt` next to the video.

> **Note:** the reference-tag syntax in the original workflow stays intact because `fallback_prompt` flows straight into `MiniMaxH3ReferenceToVideo:prompt`. Only the `<Picture 1>` / `<Picture 2>` / `<Video 1>` tags can be used there; per-clip `.txt` files must respect the same syntax.

## Parameters

### BatchMiniMaxLoader

| Parameter | Default | Description |
|-----------|---------|-------------|
| `folder_path` | `""` | Path to folder with video files |
| `index` | `0` | Current file index (auto-managed by BatchAutoQueue) |
| `fallback_prompt` | `""` | Prompt used when no `.txt`/`.prompt` file matches the video |
| `video_extensions` | `.mp4,.mov,.avi,.mkv,.webm` | Video file extensions to scan |
| `image_extensions` | `.png,.jpg,.jpeg,.webp` | Image file extensions to match |
| `prompt_extensions` | `.txt,.prompt` | Prompt file extensions to match |
| `force_rate` | `0` | Force FPS (0 = original) |
| `frame_load_cap` | `0` | Max frames to load (0 = all) |
| `skip_first_frames` | `0` | Skip first N frames |
| `select_every_nth` | `1` | Select every Nth frame |

## How it works

1. `BatchMiniMaxLoader` scans the folder, sorts files alphabetically, and loads the file at the current `index`.
2. It outputs video frames + audio + optional reference images (None if no matching image found).
3. `MiniMaxH3ReferenceToVideo` already handles `None` images gracefully (`if img is None: continue`).
4. After the workflow completes and the video is saved, `BatchAutoQueue` increments the index and POSTs the modified prompt to `http://127.0.0.1:8188/prompt`.
5. ComfyUI picks up the new prompt and processes the next file.

## Requirements

- ComfyUI (tested on latest)
- OpenCV (`cv2`) — included with ComfyUI
- ffmpeg — required for audio extraction (same as VHS Video Helper Suite)

## License

LGPL-3.0
