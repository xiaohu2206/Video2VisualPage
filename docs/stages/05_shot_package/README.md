# 05 镜头包构建开发文档

## 目标

把镜头、关键帧、字幕和相邻上下文合并成模型可读取的标准输入包。`shot_packages.jsonl` 是后续单镜头理解的唯一输入来源。

## 输入

```txt
outputs/{project_id}/shot_split/normalized_shots.json
outputs/{project_id}/subtitle_align/shot_subtitles.json
```

## 输出

```txt
outputs/{project_id}/shot_package/shot_packages.jsonl
```

每个镜头一行 JSON。

## shot_packages.jsonl 契约

单行示例：

```json
{"shot_id":"shot_000001","index":1,"time_range":{"start_sec":0.0,"end_sec":3.24,"duration_sec":3.24},"frames":[{"frame_id":"shot_000001_20","position":0.2,"time_sec":0.648,"path":"shot_split/keyframes/shot_000001_fifth_1_20.jpg","exists":true}],"subtitle_text":"求你救救我娘","subtitle_segments":["sub_0001"],"neighbor_context":{"previous_shot_id":null,"next_shot_id":"shot_000002"},"warnings":[]}
```

字段要求：

- `shot_id` 必须来自 `normalized_shots.json`。
- `frames` 中保留关键帧元信息，不只保存字符串路径。
- `subtitle_text` 来自 `shot_subtitles.json`。
- `neighbor_context` 至少包含前后镜头 id。
- `warnings` 用于记录缺失图片、空字幕等非阻断问题。

## 设计原则

这个阶段负责“整理输入”，不负责理解内容：

- 不调用 LLM / VLM。
- 不压缩字幕。
- 不选择代表帧。
- 不改写字幕文本。
- 不过滤镜头，除非输入镜头本身非法。

## 实现任务

- 读取 `normalized_shots.json` 并构建 `shot_id -> shot`。
- 读取 `shot_subtitles.json` 并构建 `shot_id -> subtitle`。
- 按镜头顺序逐行写入 JSONL。
- 检查关键帧文件是否存在，写入 `exists` 和 `warnings`。
- 添加前后镜头上下文。
- 统计输出行数并更新 `run_state.json`。

## CLI

```powershell
python -m video2visualpage build-shot-packages --project outputs\demo_20260623_200000
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 某镜头没有字幕记录 | 使用空字幕并写 warning |
| 关键帧路径不存在 | `exists=false`，写 warning，流程继续 |
| 镜头顺序混乱 | 按 `start_sec` 重新排序并写 warning |
| 重复 `shot_id` | 阶段失败 |
| 输出 JSONL 某行无法解析 | 写入前校验，禁止产出坏文件 |

## 验收标准

- JSONL 行数等于镜头数。
- 每行都能被独立解析为 JSON。
- 每行包含 `shot_id`、`time_range`、`frames`、`subtitle_text`、`neighbor_context`。
- 图片路径存在时 `exists=true`。
- `run_state.json` 中 `05_shot_package` 为 `done`。

## 测试建议

- 单测镜头和字幕正常合并。
- 单测缺失字幕记录。
- 单测缺失关键帧文件。
- 单测 JSONL 写入和逐行解析。
- 集成测试：完成 `00` 到 `05` 后确认能生成稳定 `shot_packages.jsonl`。

