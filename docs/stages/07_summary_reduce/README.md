# 07 摘要压缩开发文档

## 目标

把逐镜头分析结果压缩为分块摘要和全局摘要，避免长视频在目录规划和章节写作阶段出现上下文过大问题。

## 输入

```txt
outputs/{project_id}/shot_understanding/shot_analysis.jsonl
outputs/{project_id}/init/config.json
```

## 输出

```txt
outputs/{project_id}/summary_reduce/chunk_summaries.jsonl
outputs/{project_id}/summary_reduce/global_summary.json
```

## 分块策略

默认按镜头数量分块：

```txt
max_shots_per_chunk = config.llm.max_shots_per_chunk 或 50
```

规则：

- 只使用成功的镜头分析记录。
- 失败镜头计入统计，但不传给摘要模型。
- 镜头少于阈值时也生成一个 chunk。
- 分块必须保持时间顺序。

## chunk_summaries.jsonl 契约

单行示例：

```json
{"chunk_id":"chunk_001","shot_range":["shot_000001","shot_000050"],"start_sec":0.0,"end_sec":188.4,"main_topics":["开场求助","冲突升级"],"summary":"这一段主要建立人物诉求，并通过连续近景推动冲突。","important_shots":["shot_000001","shot_000012"],"topic_tags":["剧情推进"],"warnings":[]}
```

## global_summary.json 契约

```json
{
  "video_main_theme": "视频内容围绕一段剧情冲突展开。",
  "main_sections": [
    "开场求助",
    "冲突升级",
    "人物关系揭示"
  ],
  "suggested_chapter_count": 3,
  "narrative_style": "剧情解读型",
  "source_chunks": ["chunk_001", "chunk_002"],
  "warnings": []
}
```

## 实现任务

- 读取 `shot_analysis.jsonl`，过滤失败记录。
- 按配置生成 chunk。
- 对每个 chunk 调用摘要模型或 mock 摘要器。
- 校验 `important_shots` 必须来自当前 chunk。
- 用所有 chunk 摘要生成 `global_summary.json`。
- 更新 `run_state.json`。

## 模型提示要求

分块摘要：

- 只总结当前 chunk 内镜头。
- 保留关键 `shot_id`。
- 不生成章节目录。

全局摘要：

- 只基于 chunk 摘要。
- 输出主题、主要部分、建议章节数量和叙述风格。
- 不引用不存在的镜头。

## CLI

```powershell
python -m video2visualpage reduce-summary --project outputs\demo_20260623_200000
```

调参示例：

```powershell
python -m video2visualpage reduce-summary `
  --project outputs\demo_20260623_200000 `
  --max-shots-per-chunk 30
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 没有成功镜头分析 | 阶段失败 |
| 某 chunk 摘要失败 | 写失败 chunk，继续处理其他 chunk |
| 全局摘要失败 | 阶段失败，因为下游目录规划依赖它 |
| 模型输出引用不存在镜头 | 修正或移除引用，并写 warning |
| 重要镜头为空 | 允许为空数组，但写 warning |

## 验收标准

- `chunk_summaries.jsonl` 至少一行。
- 每行 JSON 可解析。
- `global_summary.json` 可解析，包含 `video_main_theme` 和 `main_sections`。
- 所有 `important_shots` 都存在于 `shot_analysis.jsonl`。
- `run_state.json` 中 `07_summary_reduce` 为 `done`。

## 测试建议

- 单测 0、1、50、51 个镜头的分块边界。
- 用 mock model 测 chunk 和 global 输出。
- 单测模型引用不存在 shot 的修复逻辑。
- 单测失败镜头被过滤但统计不丢失。

