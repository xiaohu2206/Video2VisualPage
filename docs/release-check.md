# Release Check

Version: `0.1.0`

Date: 2026-06-23

## Checks

- Dependency lock recorded in `requirements.lock`.
- Root package version recorded in `pyproject.toml` and `video2visualpage/__init__.py`.
- README updated with install, configuration, commands, stages, output directory, and tests.
- Sample run completed with `utils/opencv-shot-segmenter/data/1.mp4`.
- Smoke project QA passed at `outputs/smoke_20260623_001/qa/qa_report.json`.
- Direct step smoke verified `shot-split --video ...` creates `media_info/`, `shot_split/`, and `step_manifest.json`.
- Test suite passed with `python -m pytest -q`.
- Generated outputs and Python caches are excluded by `.gitignore`.

## Commands Used

```powershell
python -m video2visualpage run utils\opencv-shot-segmenter\data\1.mp4 --project-name smoke --to-stage qa
python -m video2visualpage rerun --project outputs\smoke_20260623_001 --from shot_understanding --to qa
python -m video2visualpage qa --project outputs\smoke_20260623_001 --fix
python -m video2visualpage shot-split --video utils\opencv-shot-segmenter\data\1.mp4 --project-name step_smoke
python -m pytest -q
```
