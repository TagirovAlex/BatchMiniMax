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
- **Поддерживает ролики разной длины.** Из метаданных каждого ролика берётся его реальная длительность и **округляется вниз до целых секунд** (ролик 9,0 с → `9`). Это целое число подставляется в узел расчёта длины воркфлоу (в ту же ноду, куда при ручном прогоне вводились секунды) — точно как в ручном режиме. Сетку кадров считает сам узел MiniMax.
- **Качество не теряется.** Пакетные ноды **сами не грузят видео и картинки в генерацию** — они подставляют имена файлов в ручные `VHS_LoadVideo` / `LoadImage`, поэтому в генерацию попадают кадры, закодированные проверенным ручным пайплайном (без артефактов, которые возникали при прямой подаче кадров).
- **Первый запуск не зависит от имени папки.** Ручные `VHS_LoadVideo` / `LoadImage` по умолчанию указывают на **заглушки** `clear.mp4` / `clear.jpg`, которые пакет автоматически кладёт в папку `input` при загрузке страницы. Поэтому можно свободно переименовывать папку батча — первый прогон не упадёт, а со второго задания `BatchAutoQueue` начнёт подставлять реальные файлы.
- **Не привязан к имени воркфлоу.** Синхронизация реальных имён файлов применяется к **любому** воркфлоу, в котором есть нода `BatchMiniMaxLoader` (сканируются все `.json` в `workflows/` и рядом с модулем). Поэтому одну и ту же пару батч-нод можно вставить в любой подходящий воркфлоу — имя файла и папки можно менять свободно.

### Готовые воркфлоу (два варианта)

Пакет включает две доработанные версии официального воркфлоу MiniMax H3 «Ref2V / Clothing + BG edit»:

```
workflows/OF_MINIMAX_batch.json          ← просто батч (исходный пайплайн, VHS_VideoCombine)
OF MINIMAX batch upscale.json            ← батч + апскейл (2 стадии, латентный 3D-апскейл)
```

| | Просто батч | Батч + апскейл |
|---|---|---|
| Назначение | Прогнать ролики как есть | Прогнать **и апскейлить** результат |
| Пайплайн | Один `SamplerCustomAdvanced` → VAE-tiled decode → `VHS_VideoCombine` | `SamplerCustomAdvanced` → разделение AV-латента → `MinimaxH3LatentUpscaler3D` → второй `SamplerCustomAdvanced` → `VAEDecode` + `VAEDecodeAudio` → `CreateVideo`/`SaveVideo` |
| Результат | `01-1.mp4`, `01-2.mp4`, … | `01-1.mp4`, … (через `SaveVideo`) |
| Триггер `BatchAutoQueue` | `VHS_VideoCombine:Filenames` | `SaveVideo:video` |

В обоих ручные ноды `VHS_LoadVideo` и `LoadImage` сохранены, батч-ноды работают «вокруг» них (подставляют имена файлов и продвигают очередь). Откройте нужный воркфлоу в ComfyUI, укажите `folder_path` (например `batch1`) и запустите.

### Конвенция имён файлов

Файлы кладутся в папку внутри `input` ComfyUI (например `input/batch1`), а в `folder_path` ноды указывается её имя относительно `input` (например `batch1`) — тогда `VHS_LoadVideo` / `LoadImage` смогут их найти.

```
input/batch1/
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

Selects one (video, image) task at a time and supplies the **file names** (paths relative to ComfyUI's `input` dir) plus the video duration. It does **not** carry the video/image tensors — it substitutes the names into the manual `VHS_LoadVideo` / `LoadImage` nodes, so MiniMax H3 receives frames produced by the proven manual pipeline (no quality loss).

| Output | Type | Description |
|--------|------|-------------|
| `prompt` | STRING | Per-video prompt (from `.txt`/`.prompt`, else `fallback_prompt`) |
| `filename` | STRING | Output file stem for this task, e.g. `01-2` |
| `duration` | FLOAT | Source video duration in **whole seconds, rounded down** from its metadata (e.g. `9`) |
| `video_name` | STRING | Relative path from `input`, e.g. `batch1/01.mp4` (substituted into `VHS_LoadVideo`) |
| `ref_image_name` | STRING | Relative path from `input` of the **current** task's image, e.g. `batch1/01-2.jpg` |
| `next_ref_image_name` | STRING | Relative path from `input` of the **next** task's image (substituted into the `LoadImage` that feeds `ref_image_0` — avoids the off-by-one when queuing) |
| `task_index` | INT | Flat index of the current task |
| `total_tasks` | INT | Total number of tasks across all videos |

### Batch Auto Queue

Place after your save node. Advances the next task by incrementing the loader's `task_index` **and** substituting the next video/reference image file names into the manual `VHS_LoadVideo` / `LoadImage` nodes.

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `task_index` | INT | — | From BatchMiniMaxLoader |
| `total_tasks` | INT | — | From BatchMiniMaxLoader |
| `trigger` | ANY | — | Connect from your save node (e.g. `VHS_VideoCombine`) so it runs only after generation finishes |
| `video_name` | STRING | `""` | Current video relative path; substituted into `VHS_LoadVideo.video` |
| `ref_image_name` | STRING | `""` | Current reference image relative path; fallback image source |
| `next_ref_image_name` | STRING | `""` | **Next** task's reference image; substituted into the `LoadImage` feeding `ref_image_0` (background `LoadImage` is left untouched). Falls back to `ref_image_name` when empty |
| `auto_next` | BOOLEAN | `True` | Enable auto-queue |
| `delay_seconds` | FLOAT | `1.0` | Delay before next queue (seconds) |

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/TagirovAlex/BatchMiniMax.git
```

No extra dependencies — uses OpenCV (cv2) and PIL which come with ComfyUI.

## Ready-made workflows

Modified versions of the official MiniMax H3 "Ref2V / Clothing + BG edit" workflow are included — **two variants**:

```
workflows/OF_MINIMAX_batch.json          ← plain batch (original pipeline, VHS_VideoCombine)
OF MINIMAX batch upscale.json            ← batch + latent 3D upscale (2-stage)
```

| | Plain batch | Batch + upscale |
|---|---|---|
| Purpose | Batch process clips as-is | Batch process **and upscale** the result |
| Pipeline | Single `SamplerCustomAdvanced` → VAE tiled decode → `VHS_VideoCombine` | `SamplerCustomAdvanced` → separable AV latent → `MinimaxH3LatentUpscaler3D` → 2nd `SamplerCustomAdvanced` → `VAEDecode` + `VAEDecodeAudio` → `CreateVideo`/`SaveVideo` |
| Output | `01-1.mp4`, `01-2.mp4`, … | `01-1.mp4`, … (via `SaveVideo`) |
| `BatchAutoQueue` trigger | `VHS_VideoCombine:Filenames` | `SaveVideo:video` |

Both keep the **manual `VHS_LoadVideo` and `LoadImage` nodes intact**. The two batch nodes sit *around* them: `BatchMiniMaxLoader` computes each task's video/reference-image file names (plus their durations), and `BatchAutoQueue` substitutes those file names into the manual nodes when queuing the next task. Everything else (models, LoRA, sampler, prompt chain) is untouched. Open either in ComfyUI, set the loader's `folder_path` (e.g. `batch1`), and hit Queue.

The batch nodes are **not** tied to a specific workflow file name — the auto-sync persists the real first-task file names into **any** workflow that carries a `BatchMiniMaxLoader`, so you can rename folder / workflow freely.

## Workflow setup

### Before (manual, single file)

```
VHS_LoadVideo ──→ MiniMaxH3ReferenceToVideo (ref_video)
LoadImage ──────→ MiniMaxH3ReferenceToVideo (ref_image_0)   ← outfit reference
LoadImage ──────→ MiniMaxH3ReferenceToVideo (ref_image_1)   ← optional background
```

### After (batch processing)

```
VHS_LoadVideo ────────────→ MiniMaxH3ReferenceToVideo (ref_video)   ← manual node, kept
LoadImage (outfit) ───────→ MiniMaxH3ReferenceToVideo (ref_image_0) ← manual node, kept
LoadImage (bg, optional) ─→ MiniMaxH3ReferenceToVideo (ref_image_1) ← toggled by group
BatchMiniMaxLoader:filename ──→ VHS_VideoCombine (filename_prefix)  → saves 01-2.mp4 etc.
BatchMiniMaxLoader:duration  ──→ ComfyMathExpression (values.a)     → length in whole sec
BatchMiniMaxLoader:video_name ──→ BatchAutoQueue:video_name
BatchMiniMaxLoader:ref_image_name ──→ BatchAutoQueue:ref_image_name
BatchMiniMaxLoader:next_ref_image_name ──→ BatchAutoQueue:next_ref_image_name

... (rest of workflow unchanged) ...

VHS_VideoCombine:Filenames ──→ BatchAutoQueue:trigger
BatchMiniMaxLoader:task_index ──→ BatchAutoQueue:task_index
BatchMiniMaxLoader:total_tasks ──→ BatchAutoQueue:total_tasks
```

`BatchMiniMaxLoader` does **not** feed `MiniMaxH3ReferenceToVideo` directly. The manual `VHS_LoadVideo` (video) and `LoadImage` (outfit reference) remain connected to it. For each task, `BatchAutoQueue` rewrites the `video` / `image` widgets of those manual nodes to the current task's files (paths relative to `input`), using the **next** task's reference image (`next_ref_image_name`) so there is no off-by-one, and `MiniMaxH3ReferenceToVideo` decodes them through the exact same code path as the manual single-file workflow — preserving quality. The optional background `LoadImage` (`ref_image_1`) is left untouched: its group bypasser toggles it (empty = no background, filled = same background for all tasks).

> **First run / placeholders.** The manual nodes default to fixed placeholder files `clear.jpg` / `clear.mp4` which the package re-creates inside ComfyUI's `input` directory on every page load if missing. That keeps the first run folder-independent (no error when the batch folder is renamed). On the very first run with a new/renamed folder, the loader detects the widgets still point at a placeholder/stale file, **queues a corrected copy of the prompt with the real first-task files, and aborts the placeholder run before generation** — so no placeholder video is ever produced. As it runs, the loader also **persists the real first-task file names back into the workflow file**, so after reopening (or a page refresh) the widgets are already correct and the very first Run starts cleanly with no abort.

### File naming convention

Put your files in a folder inside ComfyUI's `input` directory (e.g. `input/batch1`) and set the loader's `folder_path` to its name relative to `input` (e.g. `batch1`) — that way `VHS_LoadVideo` / `LoadImage` can find them by the relative paths the loader emits.

```
batch1/
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
| `folder_path` | `""` | Folder with video + image files (path **relative to `input`**, e.g. `batch1`) |
| `task_index` | `0` | Flat task index (auto-managed by BatchAutoQueue) |
| `fallback_prompt` | `""` | Prompt used when no `.txt`/`.prompt` file matches the video |
| `video_extensions` | `.mp4,.mov,.avi,.mkv,.webm` | Video file extensions to scan |
| `image_extensions` | `.png,.jpg,.jpeg,.webp` | Image file extensions to match |
| `prompt_extensions` | `.txt,.prompt` | Prompt file extensions to match |

## How it works

1. `BatchMiniMaxLoader` scans the folder, sorts videos, pairs each video with its reference images (`01-` prefix), and flattens them into tasks **video-by-video**: all images of the first video, then all of the second, etc.
2. For the current `task_index` it reads only the source video's **metadata** (fps, frame count) to compute its real duration in **whole seconds, rounded down** (e.g. a 9.0 s clip → `9`). It does **not** decode the video, since the manual `VHS_LoadVideo` / `LoadImage` nodes will load the actual frames.
3. It emits the per-video `prompt`, the save `filename` stem, `duration`, and the relative paths `video_name` / `ref_image_name` / **`next_ref_image_name`** (from ComfyUI's `input` root, e.g. `batch1/01.mp4`, `batch1/01-2.jpg`), plus `task_index` / `total_tasks`.
4. `filename` feeds `VHS_VideoCombine:filename_prefix` so each run saves as `01-1`, `01-2`, … `duration` feeds the length node (`ComfyMathExpression`) — the same node that took the seconds input in the manual workflow — so length is derived per clip (the node then pads to MiniMax's 17k+5 frame grid exactly as the manual workflow does).
5. After the workflow completes and the video is saved, `BatchAutoQueue` increments `task_index`, substitutes `video_name` into the manual `VHS_LoadVideo` widget and `next_ref_image_name` (falling back to `ref_image_name`) into the `LoadImage` that feeds `ref_image_0`, and POSTs the modified prompt to `http://127.0.0.1:8188/prompt`.
6. ComfyUI picks up the new prompt and processes the next task — with the video and reference image loaded through the unmodified manual pipeline.

## Requirements

- ComfyUI (tested on latest)
- OpenCV (`cv2`) — included with ComfyUI
- ffmpeg — required for audio extraction (same as VHS Video Helper Suite)

## License

MIT
