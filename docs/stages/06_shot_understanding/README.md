# 06 单镜头理解开发文档

## 目标

逐镜头使用本地 PaddleOCR 识别关键帧中的可读文字，并生成结构化镜头卡片。该阶段面向知识类视频优先提取画面文字与字幕文本，不写长文，只把单个镜头压缩成可组合、可检索、可追溯的分析记录。

## 输入

```txt
outputs/{project_id}/shot_package/shot_packages.jsonl
outputs/{project_id}/init/config.json
```

## 输出

```txt
outputs/{project_id}/shot_understanding/shot_analysis.jsonl
```

可选日志：

```txt
outputs/{project_id}/logs/llm_calls.jsonl
outputs/{project_id}/logs/llm_monitor/index.jsonl
outputs/{project_id}/logs/llm_monitor/by_function/shot_understanding/calls.jsonl
outputs/{project_id}/logs/errors.jsonl
```

## shot_analysis.jsonl 契约

单行示例：

```json
{"shot_id":"shot_000001","start_sec":0.0,"end_sec":3.24,"visual_summary":"画面展示人物近景，情绪紧张。","subtitle_summary":"角色正在请求帮助。","merged_summary":"本镜头通过台词和近景表演建立求助情境。","key_entities":["人物","求助"],"actions":["请求帮助"],"on_screen_text":[],"topic_tags":["剧情推进"],"narrative_role":"introduction","importance_score":0.82,"recommended_display_frame":"shot_split/keyframes/shot_000001_fifth_1_20.jpg","confidence":0.78,"warnings":[]}
```

## 输出字段要求

| 字段 | 要求 |
| --- | --- |
| `shot_id` | 必须和输入一致 |
| `visual_summary` | 只描述画面可见内容 |
| `subtitle_summary` | 只总结字幕语义，空字幕时写明没有可用字幕 |
| `merged_summary` | 结合画面和字幕，可保留模型输出的简洁 Markdown 结构 |
| `key_entities` | 字符串数组 |
| `topic_tags` | 字符串数组 |
| `narrative_role` | 枚举或 `unknown` |
| `importance_score` | 0 到 1 |
| `recommended_display_frame` | 必须来自输入 frames |
| `confidence` | 0 到 1 |
| `warnings` | 字符串数组 |

## PaddleOCR 配置

默认新项目配置使用本仓库内的 PaddleOCR 代码与模型：

```json
{
  "vision_model": {
    "provider": "paddleocr",
    "model": "PP-OCRv6_medium",
    "max_images_per_shot": 2,
    "ocr_root": "utils/PaddleOCR-main",
    "ocr_det_model_dir": "utils/PaddleOCR-main/models/PP-OCRv6_medium_det",
    "ocr_rec_model_dir": "utils/PaddleOCR-main/models/PP-OCRv6_medium_rec",
    "ocr_device": "gpu",
    "ocr_engine": "paddle",
    "ocr_min_score": 0.6,
    "ocr_crop_min_score": 0.6,
    "ocr_crop_fallback": true,
    "ocr_allow_cpu_fallback": true
  }
}
```

可用环境变量覆盖：

- `VIDEO2VISUALPAGE_VISION_MODEL_PROVIDER=paddleocr`
- `VIDEO2VISUALPAGE_VISION_MODEL_MODEL=PP-OCRv6_medium`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_ROOT=utils/PaddleOCR-main`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_DET_MODEL_DIR=utils/PaddleOCR-main/models/PP-OCRv6_medium_det`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_REC_MODEL_DIR=utils/PaddleOCR-main/models/PP-OCRv6_medium_rec`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_DEVICE=gpu`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_MIN_SCORE=0.6`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_CROP_MIN_SCORE=0.6`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_CROP_FALLBACK=1`
- `VIDEO2VISUALPAGE_VISION_MODEL_OCR_ALLOW_CPU_FALLBACK=1`

运行时要求：

- `utils/PaddleOCR-main/paddleocr/` 存在。
- `utils/PaddleOCR-main/models/PP-OCRv6_medium_det/` 和 `utils/PaddleOCR-main/models/PP-OCRv6_medium_rec/` 存在。
- Python 环境安装 PaddlePaddle GPU 运行时、PaddleX OCR 依赖，并可导入本地 PaddleOCR 包。

## 识别要点

PaddleOCR 输出会被转换为：

- `on_screen_text`：关键帧 OCR 去重后的文字数组。
- `visual_summary`：画面 OCR 文本的 Markdown 列表。
- `subtitle_summary`：当前镜头字幕摘要，空字幕时写明没有可用字幕。
- `merged_summary`：OCR 文本加字幕文本的知识点列表。
- `recommended_display_frame`：优先选择 OCR 文字最多的关键帧。
- `warnings`：记录缺失图片、无字幕、未识别文字、单帧 OCR 失败、GPU 自检失败或 crop fallback。

知识类视频里常见大标题、PPT 字幕和网格背景。若 PaddleOCR pipeline 把整张关键帧误判为一个文本框，容易产生高置信度乱码。当前实现会检测整图框/空结果，并改用候选 crop 加 `TextRecognition` 逐块识别。启动 crop 识别时会先用配置的 `ocr_device` 做 sanity check；如果 GPU 因 CUDNN/架构不匹配输出乱码或空结果，并且 `ocr_allow_cpu_fallback=true`，会自动回退 CPU 识别并写入 warning，避免把乱码写入 `shot_analysis.jsonl`。

## 实现任务

- 在 `ModelAdapter` 中支持 `provider=paddleocr`，屏蔽 PaddleOCR 本地加载细节。
- 逐行读取 `shot_packages.jsonl`，支持断点续跑。
- 对每个镜头按 `max_images_per_shot` 读取关键帧并执行 OCR。
- 将 PaddleOCR `rec_texts` / `rec_scores` 转换为流水线内部使用的 `shot_analysis.jsonl`。
- PaddleOCR pipeline 整图误检或空结果时，生成候选文字 crop 并用 `TextRecognition` 识别。
- GPU OCR 自检失败时可回退 CPU 识别，warning 中记录 `ocr_device_fallback`。
- 记录模型调用日志和耗时等元信息。
- 更新 `run_state.json`。

## CLI

```powershell
python -m video2visualpage analyze-shots --project outputs\demo_20260623_200000
```

调试参数：

```powershell
python -m video2visualpage analyze-shots `
  --project outputs\demo_20260623_200000 `
  --shot-id shot_000001 `
  --mock-model
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 图片缺失 | 跳过该帧，写 warning；可只用字幕生成分析 |
| 字幕为空 | 只用 OCR 画面文字分析，写 warning |
| PaddleOCR 依赖缺失 | 阶段失败，提示安装 PaddlePaddle GPU 运行时和本地 PaddleOCR |
| OCR 模型目录缺失 | 阶段失败，提示缺失目录 |
| pipeline 整图误检 | 使用 crop fallback 重新识别，写 warning |
| GPU 识别自检失败 | `ocr_allow_cpu_fallback=1` 时回退 CPU，写 warning |
| 单帧 OCR 失败 | 写入该帧 warning，继续处理同镜头其他帧 |
| 某镜头失败 | 记录 `shot_id` 和错误信息，阶段失败 |

## 验收标准

- 输出 JSONL 可逐行解析。
- 成功时每个输入镜头都有分析记录。
- 成功记录包含 `merged_summary`、`on_screen_text` 和 `recommended_display_frame`。
- 有可读画面文字时，`on_screen_text` 应包含 PaddleOCR 识别文本。
- PaddleOCR 初始化失败时命令直接报错，`run_state.json` 中 `06_shot_understanding` 为 `failed`。

## 测试建议

- 用 mock model 测完整 JSONL 输出。
- 单测 PaddleOCR provider 的 GPU 参数、模型目录和 `rec_texts` 提取。
- 单测 PaddleOCR 整图误检时的 crop fallback 和 GPU sanity fallback。
- 单测远程 OCR/model 分支的兼容输出。
- 单测图片缺失时的降级输入。
- 单测指定 `--shot-id` 只重跑单个镜头。
- 压测大量镜头时的断点续跑。

