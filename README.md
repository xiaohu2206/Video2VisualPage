# Video2VisualPage

Local JSON/JSONL pipeline for turning a video into a static visual report. The project runs without a database: every step reads files from `outputs/{project_id}/{step_name}/` and writes its own JSON, JSONL, images, HTML, and logs back to disk.

## Install

```powershell
python -m pip install -e .
```

The bundled OpenCV shot splitter under `utils/opencv-shot-segmenter` is imported directly. Online ASR is enabled by default when no sidecar subtitles are found. Set this only when you want to force offline empty-subtitle fallback:

```powershell
$env:VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR = "0"
```

## Model Configuration

Model settings are read from `.env` at runtime, so existing projects can switch from `local_heuristic` to real models without running `init` again. The supported remote provider is any OpenAI-compatible `/v1/chat/completions` endpoint.

There are two model roles:

- `vision_model`: used by `06_shot_understanding` for keyframe/image understanding.
- `copywriting_model`: used by `07_summary_reduce` and `09_chapter_write` for summaries and chapter prose.

```dotenv
VIDEO2VISUALPAGE_LLM_OUTPUT_LANGUAGE=zh-CN

VIDEO2VISUALPAGE_VISION_MODEL_PROVIDER=openai_compatible
VIDEO2VISUALPAGE_VISION_MODEL_BASE_URL=https://api.openai.com/v1
VIDEO2VISUALPAGE_VISION_MODEL_MODEL=your-vision-model
VIDEO2VISUALPAGE_VISION_MODEL_API_KEY=your-api-key

VIDEO2VISUALPAGE_COPYWRITING_MODEL_PROVIDER=openai_compatible
VIDEO2VISUALPAGE_COPYWRITING_MODEL_BASE_URL=https://api.openai.com/v1
VIDEO2VISUALPAGE_COPYWRITING_MODEL_MODEL=your-better-writing-model
VIDEO2VISUALPAGE_COPYWRITING_MODEL_API_KEY=your-api-key
```

Legacy `VIDEO2VISUALPAGE_LLM_*` variables are still supported as shared fallbacks. Keep `VIDEO2VISUALPAGE_LLM_PROVIDER=local_heuristic` only when you intentionally want the deterministic local placeholder.

## Progress Output

Commands print progress to `stderr` and keep the final JSON result on `stdout`. Model stages show the current item, percentage, provider/model, attempt number, input preview, elapsed time, and response key preview. Disable terminal progress when needed:

```powershell
$env:VIDEO2VISUALPAGE_PROGRESS = "0"
```

## Commands

Create a project and run the whole pipeline:

```powershell
python -m video2visualpage run .\utils\opencv-shot-segmenter\data\1.mp4 --project-name demo
```

Run only a numeric stage range:

```powershell
python -m video2visualpage run .\utils\opencv-shot-segmenter\data\1.mp4 --project-name demo --steps 1-5
```

Resume from the first incomplete stage:

```powershell
python -m video2visualpage resume outputs\demo
```

Run or rerun specific stages:

```powershell
python -m video2visualpage stage --project outputs\demo --stage subtitle_align
python -m video2visualpage rerun --project outputs\demo --from shot_understanding --to qa
python -m video2visualpage rerun --project outputs\demo --steps 6-11
```

Run one step directly from a video. `--project-name demo` always writes `outputs\demo`; if that folder exists, it is overwritten. Missing upstream JSON steps are created automatically:

```powershell
python -m video2visualpage media-info --video .\utils\opencv-shot-segmenter\data\1.mp4 --project-name demo
python -m video2visualpage shot-split --video .\utils\opencv-shot-segmenter\data\1.mp4 --project-name demo
```

Render and QA:

```powershell
python -m video2visualpage render --project outputs\demo --format html
python -m video2visualpage qa --project outputs\demo --fix
```

## Steps

Each step owns one output folder and writes `step_manifest.json` plus its data artifacts:

- `init/`: create `project.json`, `config.json`, `run_state.json`, and logs.
- `media_info/`: probe duration, fps, dimensions, codec, and audio status.
- `shot_split/`: reuse the bundled OpenCV tool and write `shots.json` plus `normalized_shots.json`.
- `subtitle_extract/`: load sidecar subtitles or optionally call online ASR, with empty-subtitle fallback.
- `subtitle_align/`: align subtitles to shots by time overlap.
- `shot_package/`: write model-ready `shot_packages.jsonl`.
- `shot_understanding/`: generate isolated shot cards; `local_heuristic` is an explicit local adapter, and unimplemented model providers fail instead of falling back silently.
- `summary_reduce/`: chunk shot cards and write `global_summary.json`.
- `outline_plan/`: create chapters and assign valid shot references.
- `chapter_write/`: write per-chapter JSON; `write-chapter` can regenerate one chapter.
- `static_render/`: copy images, render `index.html`, and always write `render_result.json`.
- `qa/`: validate JSON/JSONL, contracts, references, images, chapter bodies, and final HTML.

The numbered stage IDs, such as `01_media_probe` and `02_shot_split`, still work as aliases for compatibility, but new outputs use the step folders above. Project folders use the sanitized project name directly, for example `outputs\demo`.

## Development

Run tests:

```powershell
python -m pytest -q
```

The test suite includes generated tiny-video tests for the full pipeline and direct step commands such as `shot-split --video ...`.
