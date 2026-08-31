# BatchMiniMax — Batch Processor for MiniMax H3

![GitHub License](https://img.shields.io/github/license/TagirovAlex/BatchMiniMax)
![GitHub Release](https://img.shields.io/github/v/release/TagirovAlex/BatchMiniMax)
![ComfyUI](https://img.shields.io/badge/ComfyUI-compatible-brightgreen)

Custom nodes for [ComfyUI](https://github.com/comfyanonymous/ComfyUI) that add batch video processing to MiniMax H3 workflows. Each video is run once **for every reference image it has** (e.g. `01-1.jpg`, `01-2.jpg`, `01-3.jpg` → 3 runs), with a flat auto-queue that advances task by task.

---

## 📖 Краткое описание (RU)

Этот пакет добавляет в **ComfyUI** пакетную (batch) обработку видео для **MiniMax H3**. Вы один раз настраиваете workflow, складываете все ролики и картинки в папку и запускаете процесс — ComfyUI сам переберёт все задания друг за другом.

### Главная идея: «ролик × картинки»

**Каждый ролик прогоняется столько раз, сколько у него референс-картинок.** Каждый прогон использует **одну** конкретную картинку. Например:

```
01.mp4 + 01-1.jpg ─┐
01.mp4 + 01-2.jpg ─┼─ 3 прогона (сохраняются как 01-1.mp4, 01-2.mp4, 01-3.mp4)
01.mp4 + 01-3.jpg ─┘
02.mp4 + 02-1.jpg ─ 1 прогон 02-1.mp4
03.mp4 + 03-1.jpg ─┐
03.mp4 + 03-2.jpg ─┼─ 3 прогона
```

Итого заданий = сумма картинок по всем роликам (пример выше: 3+1+3 = **7**). Число картинок у ролика может различаться.

### Что он умеет

- **Сканирует папку** и находит все видеофайлы (`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm` и т.д.), сортирует их по имени.
- **Подбирает картинки-референсы** по префиксу имени: видео `01.mp4` → картинки `01-1.jpg`, `01-2.jpg`, `01-3.jpg` (любой суффикс после `01-`).
- **Прогоняет каждый ролик по разу на каждую картинку**, каждый раз с одной картинкой как `Picture 1`.
- **Один промпт на весь ролик.** Если рядом лежит `01.txt` — его текст применяется ко **всем** прогонам `01-1`, `01-2`, `01-3`. Если файла нет — используется стандартный промпт workflow.
- **Не ломается без картинок.** Если у ролика нет ни одной картинки — он просто прогонится один раз без референса (без ошибок).
- **Автоматически ставит следующее задание в очередь**, когда текущее сохранено.
- **Поддерживает ролики разной длины.** Длительность каждого ролика автоматически подставляется в узел расчёта длины (как при ручном указании): ролик 9 с даст ~8.7 с — длина **округляется вниз** до сетки кадров 17k+5, без добавления лишних кадров (это сохраняет плавность, узел MiniMax не «додумывает» движения).

### Конвенция имён файлов

```
моя_папка/
├── 01.mp4              ← ролик
├── 01-1.jpg            ← референс (прогон A)
├── 01-2.jpg            ← референс (прогон B)
├── 01-3.jpg            ← референс (прогон C)
├── 01.txt              ← свой промпт на весь ролик 01 (опционально)
├── 02.mp4              ← ролик (прогон один раз)
├── 02-1.jpg
├── 03.mp4              ← ролик
├── 03-1.jpg
└── 03-2.jpg
```

Картинка относится к ролику, если её имя начинается с имени ролика + дефиса (`01-...`). Порядок прогонов — «ролик за роликом»: сначала все картинки первого ролика, затем второго и т.д.

---

## What it does

- **Scans** a folder for video files.
- **Finds per-video reference images** by name prefix (`01.mp4` → `01-1.jpg`, `01-2.jpg`, ...).
- **Runs each video once per reference image**, one image as `Picture 1` per run.
- **One prompt per video** — a `01.txt` next to the video applies to all its runs (falls back to the workflow prompt otherwise).
- **Auto-queues** the next task after each save.

## Nodes

### Batch Mini Max Loader

Replaces `VHS_LoadVideo` + a per-image `LoadImage` node.

| Output | Type | Description |
|--------|------|-------------|
| `video_frames` | IMAGE | Loaded video frames `[B, H, W, C]` |
| `video_audio` | AUDIO | Audio track `{"waveform": [1,C,T], "sample_rate": int}` |
| `ref_image` | IMAGE | The single reference image for this task (or `None`) |
| `prompt` | STRING | Per-video prompt (from `.txt`/`.prompt`, else `fallback_prompt`) |
| `filename` | STRING | Output file stem for this task, e.g. `01-2` |
| `duration` | FLOAT | Actual duration of the source video in seconds |
| `task_index` | INT | Flat index of the current task |
| `total_tasks` | INT | Total number of tasks across all videos |

### Batch Auto Queue

Place after your save node. Queues the next task by incrementing the loader's `task_index`.

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `task_index` | INT | — | From BatchMiniMaxLoader |
| `total_tasks` | INT | — | From BatchMiniMaxLoader |
| `trigger` | ANY | — | Connect from your save node (e.g. `VHS_VideoCombine`) so it runs only after generation finishes |
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

It replaces the manual `VHS_LoadVideo` + outfit `LoadImage` nodes with the two batch nodes. The static background `LoadImage` and the whole `StringConcatenate` prompt-chain are kept intact. Everything else (models, LoRA, sampler, VAE tiling decode, video combine) is untouched. Open it in ComfyUI, set the loader's `folder_path`, and hit Queue.

## Workflow setup

### Before (manual, single file)

```
VHS_LoadVideo ──→ MiniMaxH3ReferenceToVideo (ref_video)
LoadImage ──────→ MiniMaxH3ReferenceToVideo (ref_image_0)   ← outfit, now from batch
LoadImage ──────→ MiniMaxH3ReferenceToVideo (ref_image_1)   ← static background
```

### After (batch processing)

```
BatchMiniMaxLoader:video_frames ──→ MiniMaxH3ReferenceToVideo (ref_video)
BatchMiniMaxLoader:ref_image     ──→ MiniMaxH3ReferenceToVideo (ref_image_0)
LoadImage (bg)  ──────────────────→ MiniMaxH3ReferenceToVideo (ref_image_1, static)
BatchMiniMaxLoader:filename      ──→ VHS_VideoCombine (filename_prefix)   → saves 01-2.mp4 etc.
BatchMiniMaxLoader:duration      ──→ ComfyMathExpression (values.a)      → length from real clip

... (rest of workflow unchanged) ...

VHS_VideoCombine:Filenames ──→ BatchAutoQueue:trigger
BatchMiniMaxLoader:task_index ──→ BatchAutoQueue:task_index
BatchMiniMaxLoader:total_tasks ──→ BatchAutoQueue:total_tasks
```

Delete the old `VHS_LoadVideo` and the outfit `LoadImage`. Connect `BatchMiniMaxLoader` outputs to `MiniMaxH3ReferenceToVideo`, and connect the loader's `filename` output into `VHS_VideoCombine`'s `filename_prefix` input (convert the widget to an input) so each run saves under its own name (`01-1`, `01-2`, ...). Connect `VHS_VideoCombine:Filenames` to `BatchAutoQueue:trigger` so the next task is queued only after the video is saved.

### File naming convention

```
my_folder/
├── 01.mp4          ← video
├── 01-1.jpg        ← reference (run A)
├── 01-2.jpg        ← reference (run B)
├── 01-3.jpg        ← reference (run C)
├── 01.txt          ← per-video prompt, applies to ALL runs of 01 (optional)
├── 02.mp4          ← video run once
├── 02-1.jpg
└── 03-2.jpg
```

Matching logic: a video stem `01` claims every image whose stem starts with `01-`. Output filename for a run is `{video stem}` + image suffix, e.g. `01-2`. A `01.txt` (or `.prompt`) next to the video overrides the `fallback_prompt` for **all** runs of that video.

### Prompts

The bundled workflow keeps the four prompt blocks (motion from `<Video 1>`, outfit from `<Picture 1>`, background from `<Picture 2>`). They build the **fallback** prompt.

There is an automatic **prompt switch** in the loader:

- The static prompt-chain's final output (`StringConcatenate`) feeds the loader's `fallback_prompt` input.
- The loader's `prompt` output is wired into `MiniMaxH3ReferenceToVideo:prompt`.
- **If a `01.txt` (or `.prompt`) sits next to the video → its text is used for every run of that video (overrides the chain).**
- **If there is no `.txt` → the prompt-chain text is used** (from `fallback_prompt`).

So the workflow blocks stay as the default prompt for clips without a `.txt` file, and a per-video `.txt` replaces them entirely when present.

> **Note:** the reference-tag syntax (`<Picture 1>` / `<Picture 2>` / `<Video 1>`) is used by the prompt-chain; per-video `.txt` files must respect the same syntax.

## Parameters

### BatchMiniMaxLoader

| Parameter | Default | Description |
|-----------|---------|-------------|
| `folder_path` | `""` | Path to folder with video + image files |
| `task_index` | `0` | Flat task index (auto-managed by BatchAutoQueue) |
| `fallback_prompt` | `""` | Prompt used when no `.txt`/`.prompt` file matches the video |
| `video_extensions` | `.mp4,.mov,.avi,.mkv,.webm` | Video file extensions to scan |
| `image_extensions` | `.png,.jpg,.jpeg,.webp` | Image file extensions to match |
| `prompt_extensions` | `.txt,.prompt` | Prompt file extensions to match |
| `force_rate` | `0` | Force FPS (0 = original) |
| `frame_load_cap` | `0` | Max frames to load (0 = all) |
| `skip_first_frames` | `0` | Skip first N frames |
| `select_every_nth` | `1` | Select every Nth frame |

## How it works

1. `BatchMiniMaxLoader` scans the folder, sorts videos, pairs each video with its reference images (`01-` prefix), and flattens them into tasks **video-by-video**: all images of the first video, then all of the second, etc.
2. It loads the video for the current `task_index`, plus the **single** reference image for that task (None if the video has no images).
3. Output `filename` gives the save stem for this task (`01-2`), which feeds `VHS_VideoCombine:filename_prefix`. Output `duration` (in seconds) feeds the length-computation node (`ComfyMathExpression`), so the video's `length` is derived from each clip's real duration instead of a fixed value — clips of different lengths are handled automatically. The frame count is **snapped down** to the model's 17k+5 grid (24 fps) so MiniMax H3 never has to invent extra frames — this keeps the motion smooth instead of slightly stretched/jittery (a 9 s clip becomes ~8.7 s / 209 frames).
4. `MiniMaxH3ReferenceToVideo` already handles `None` images gracefully (`if img is None: continue`).
5. After the workflow completes and the video is saved, `BatchAutoQueue` increments `task_index` and POSTs the modified prompt to `http://127.0.0.1:8188/prompt`.
6. ComfyUI picks up the new prompt and processes the next task.

## Requirements

- ComfyUI (tested on latest)
- OpenCV (`cv2`) — included with ComfyUI
- ffmpeg — required for audio extraction (same as VHS Video Helper Suite)

## License

MIT
