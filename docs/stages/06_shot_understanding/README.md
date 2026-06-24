# 06 单镜头理解开发文档

## 目标

逐镜头调用视觉或多模态模型，生成结构化镜头卡片。该阶段不写长文，只把单个镜头压缩成可组合、可检索、可追溯的分析记录。

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

## Prompt 要点

模型输入应包含：

- `shot_id`
- 开始和结束时间
- 当前镜头关键帧路径或图片
- 当前镜头字幕
- 前后镜头 id，可选前后镜头一句摘要

模型必须被要求：

- 不直接输出 JSON，而是使用固定 HTML-like 标签分割必要字段。
- `visual_summary`、`subtitle_summary`、`merged_summary` 允许 Markdown 结构。
- 不要编造画面外信息。
- 字幕和画面冲突时分别说明。
- 不写长文，不生成章节。
- 失败或不确定时写 `warnings`。

## 实现任务

- 实现 `ModelAdapter`，屏蔽具体模型供应商。
- 逐行读取 `shot_packages.jsonl`，支持断点续跑。
- 对每个镜头构造模型输入。
- 解析模型标签输出，转换为流水线内部使用的 `shot_analysis.jsonl`。
- 模型调用或解析失败时直接抛错，当前阶段标记为 `failed`，不写伪成功分析。
- 记录模型调用日志和 token / 耗时等元信息。
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
| 图片缺失 | 只用字幕分析，写 warning |
| 字幕为空 | 只用画面分析 |
| 模型超时 | 重试，仍失败则阶段失败 |
| 模型输出缺少必要标签 | 重试，仍失败则阶段失败 |
| 某镜头失败 | 记录 `shot_id` 和错误信息，阶段失败 |

## 验收标准

- 输出 JSONL 可逐行解析。
- 成功时每个输入镜头都有分析记录。
- 成功记录包含 `merged_summary` 和 `recommended_display_frame`。
- 模型失败时命令直接报错，`run_state.json` 中 `06_shot_understanding` 为 `failed`。

## 测试建议

- 用 mock model 测完整 JSONL 输出。
- 单测模型标签解析和 Markdown 保留。
- 单测图片缺失时的降级输入。
- 单测指定 `--shot-id` 只重跑单个镜头。
- 压测大量镜头时的断点续跑。

