# 步骤单独执行命令与输出位置汇总

本文档汇总 Video2VisualPage 每一个步骤的独立执行命令，以及该步骤会写入的主要输出文件。

约定示例变量：

```powershell
$VIDEO = "data\1.mp4"
$PROJECT = "outputs\demo"
```

## 一键执行全流程

从视频创建项目，并自动执行到 QA：

```powershell
python -m video2visualpage run $VIDEO --project-name demo --to-stage qa
```

只执行指定步骤范围，例如 01 到 05：

```powershell
python -m video2visualpage run $VIDEO --project-name demo --steps 1-5
```

如果已有项目中断了，继续从第一个未完成步骤往后跑：

```powershell
python -m video2visualpage resume $PROJECT --to-stage qa
```

说明：

- `$VIDEO` 使用的是从项目根目录执行命令时的相对路径。
- `init` 负责创建项目目录，后续步骤使用 `--project $PROJECT` 指向已有项目。
- `--steps 1-5` 等价于从 `01_media_probe` 执行到 `05_shot_package`；也支持 `--steps 6-11`、`--step-range 1-5`。
- 使用 `--video --project-name demo` 时固定写入 `outputs\demo`；如果目录已存在，会先覆盖再执行。
- 除 `init` 外，常规步骤命令默认会自动补齐缺失的上游 JSON 产物。
- 如果只想严格执行当前步骤，不自动补依赖，可对步骤别名命令或 `stage` 命令追加 `--no-deps`。
- 每个步骤目录都会写入 `step_manifest.json`，用于记录步骤名、状态、输出文件和运行摘要。
- 调用大模型的步骤会额外写入结构化监控日志：`outputs/{project_id}/logs/llm_monitor/index.jsonl`、`outputs/{project_id}/logs/llm_monitor/health.json`，并按功能分类写入 `logs/llm_monitor/by_function/{function}/`。
- 每个步骤的业务实现入口位于 `video2visualpage/stages/`；命令路由在 `video2visualpage/cli.py`，流水线调度在 `video2visualpage/pipeline.py`。

## 00. init

创建项目目录、配置、状态文件。

代码文件：`video2visualpage/stages/init_stage.py`

```powershell
python -m video2visualpage init --video $VIDEO --project-name demo
```

输出位置：

```txt
1. outputs/{project_id}/init/project.json
2. outputs/{project_id}/init/config.json
3. outputs/{project_id}/init/run_state.json
4. outputs/{project_id}/init/step_manifest.json
5. outputs/{project_id}/logs/events.jsonl
6. outputs/{project_id}/logs/errors.jsonl
```

## 01. media_info

探测视频时长、帧率、尺寸、音频、编码与格式。

代码文件：`video2visualpage/stages/media_probe.py`

```powershell
python -m video2visualpage media-info --project $PROJECT
```

也可以直接从视频启动：

```powershell
python -m video2visualpage media-info --video $VIDEO --project-name demo
```

输出位置：

```txt
1. outputs/{project_id}/media_info/media_info.json
2. outputs/{project_id}/media_info/step_manifest.json
```

## 02. shot_split

执行镜头分割，生成原始镜头结果、标准化镜头结果和关键帧。

代码文件：`video2visualpage/stages/shot_split.py`

```powershell
python -m video2visualpage shot-split --project $PROJECT
```

也可以直接从视频启动：

```powershell
python -m video2visualpage shot-split --video $VIDEO --project-name demo
```

输出位置：

```txt
1. outputs/{project_id}/shot_split/shots.json
2. outputs/{project_id}/shot_split/normalized_shots.json
3. outputs/{project_id}/shot_split/keyframes/
4. outputs/{project_id}/shot_split/step_manifest.json
```

## 03. subtitle_extract

提取字幕；优先读取同名字幕文件，找不到时默认调用在线 ASR；无音频、ASR 失败或显式关闭 ASR 时会生成合法空字幕文件。

代码文件：`video2visualpage/stages/subtitle_extract.py`

```powershell
python -m video2visualpage subtitle-extract --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/subtitle_extract/subtitles.json
2. outputs/{project_id}/subtitle_extract/subtitles.srt
3. outputs/{project_id}/subtitle_extract/asr_raw.json
4. outputs/{project_id}/subtitle_extract/step_manifest.json
```

## 04. subtitle_align

把字幕按时间重叠关系对齐到镜头。

代码文件：`video2visualpage/stages/subtitle_align.py`

```powershell
python -m video2visualpage subtitle-align --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/subtitle_align/shot_subtitles.json
2. outputs/{project_id}/subtitle_align/step_manifest.json
```

## 05. shot_package

合并镜头、关键帧、字幕和前后文，生成模型输入包。

代码文件：`video2visualpage/stages/shot_package.py`

```powershell
python -m video2visualpage shot-package --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/shot_package/shot_packages.jsonl
2. outputs/{project_id}/shot_package/step_manifest.json
```

## 06. shot_understanding

逐镜头生成结构化镜头卡片。

代码文件：`video2visualpage/stages/shot_understanding.py`

```powershell
python -m video2visualpage shot-understanding --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/shot_understanding/shot_analysis.jsonl
2. outputs/{project_id}/shot_understanding/step_manifest.json
3. outputs/{project_id}/logs/llm_monitor/by_function/shot_understanding/calls.jsonl
```

## 07. summary_reduce

对镜头卡片做分块摘要，并生成全局摘要。

处理逻辑：

- 先按 `max_shots_per_chunk` 生成 `chunk_summaries.jsonl`。
- 再把轻量 chunk 摘要交给 Global Outline Agent。
- Global Outline Agent 使用标签结构输出 `THEME`、`STYLE` 和 `SECTION`，不直接输出 JSON。
- 本地解析标签、校验 chunk 引用，并组装 `global_summary.json`。
- 如果 Global Outline Agent 失败，回退到基于 `main_topics` 的规则聚合。

代码文件：`video2visualpage/stages/summary_reduce.py`

```powershell
python -m video2visualpage summary-reduce --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/summary_reduce/chunk_summaries.jsonl
2. outputs/{project_id}/summary_reduce/global_summary.json
3. outputs/{project_id}/summary_reduce/step_manifest.json
4. outputs/{project_id}/logs/llm_monitor/by_function/summary_reduce/calls.jsonl
5. outputs/{project_id}/logs/llm_monitor/by_function/global_outline/calls.jsonl
```

`global_summary.json` 主要字段：

```txt
video_main_theme
main_sections
suggested_chapter_count
narrative_style
important_shots
source_chunks
section_sources
warnings
```

## 08. outline_plan

生成网页报告目录、章节和镜头分配。

处理逻辑：

- 先生成一级章节，并把镜头按章节分配。
- 对每个一级章节做本地评分，判断是否需要继续拆成二级小节。
- 只有镜头多、主题多、topic shift 明显或重点镜头分布分散的大章节才调用 Chapter Subsection Agent。
- Chapter Subsection Agent 使用 `<KEEP/>` / `<SUB>` 标签结构输出，不直接输出 JSON。
- 本地解析标签、校验小节镜头引用，并补齐 `subsection_id`、`representative_shot_id`、`start_sec`、`end_sec`。
- 如果章节已经足够细，或模型输出不可用，则不写 `subsections`，保持旧的整章写作逻辑。

代码文件：`video2visualpage/stages/outline_plan.py`

```powershell
python -m video2visualpage outline-plan --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/outline_plan/outline.json
2. outputs/{project_id}/outline_plan/subsection_decisions.jsonl
3. outputs/{project_id}/outline_plan/step_manifest.json
4. outputs/{project_id}/logs/llm_monitor/by_function/chapter_subsections/calls.jsonl
```

`outline.json` 章节对象可选字段：

```txt
subsections
```

`subsections` 为空或不存在时表示该章节不需要继续细分。

## 09. chapter_write

按章节生成正文 JSON。有 `subsections` 的章节会按小节顺序组织 Markdown 二级标题；没有 `subsections` 的章节继续按整章写作。

代码文件：`video2visualpage/stages/chapter_write.py`

```powershell
python -m video2visualpage chapter-write --project $PROJECT
```

输出位置：

```txt
1. outputs/{project_id}/chapter_write/chapters_index.json
2. outputs/{project_id}/chapter_write/chapter_001.json
3. outputs/{project_id}/chapter_write/chapter_002.json
4. outputs/{project_id}/chapter_write/chapter_*.json
5. outputs/{project_id}/chapter_write/step_manifest.json
6. outputs/{project_id}/logs/llm_monitor/by_function/chapter_write/calls.jsonl
```

单独重写某一章：

```powershell
python -m video2visualpage write-chapter --project $PROJECT --chapter-id chapter_001
```

## 10. static_render

渲染静态 HTML，并按配置或参数可选生成 PDF。

代码文件：`video2visualpage/stages/static_render.py`

```powershell
python -m video2visualpage static-render --project $PROJECT
```

也可以使用专用渲染命令：

```powershell
python -m video2visualpage render --project $PROJECT --format html
python -m video2visualpage render --project $PROJECT --format html,pdf
```

输出位置：

```txt
1. outputs/{project_id}/static_render/index.html
2. outputs/{project_id}/static_render/render_result.json
3. outputs/{project_id}/static_render/assets/style.css
4. outputs/{project_id}/static_render/assets/images/
5. outputs/{project_id}/static_render/report.pdf
6. outputs/{project_id}/static_render/pdf_status.json
7. outputs/{project_id}/static_render/step_manifest.json
```

说明：

- `report.pdf` 只有在请求 PDF 且本地 PDF 渲染可用时生成。
- PDF 失败不会阻断 HTML，失败信息会写入 `pdf_status.json` 和 `logs/errors.jsonl`。

## 11. qa

校验 JSON / JSONL、路径、图片、章节正文、镜头引用和最终 HTML。

代码文件：`video2visualpage/stages/qa.py`

```powershell
python -m video2visualpage qa --project $PROJECT
```

开启自动修复：

```powershell
python -m video2visualpage qa --project $PROJECT --fix
```

输出位置：

```txt
1. outputs/{project_id}/qa/qa_report.json
2. outputs/{project_id}/qa/step_manifest.json
```

## 常用范围执行命令

从某一步跑到某一步：

```powershell
python -m video2visualpage rerun --project $PROJECT --from shot_understanding --to qa
```

从第一个未完成步骤继续：

```powershell
python -m video2visualpage resume $PROJECT
```

完整执行到 QA：

```powershell
python -m video2visualpage run $VIDEO --project-name demo --to-stage qa
```

## 测试命令

跑完整自动化测试：

```powershell
python -m pytest -q
```

只跑模型适配器、模型监控和摘要相关测试：

```powershell
python -m pytest tests/test_shot_understanding.py tests/test_llm_monitoring.py tests/test_summary_reduce_global_outline.py -q
```

真实调用 `.env` 中配置的文本模型和视觉模型：

```powershell
python tests/manual_model_smoke.py
```
