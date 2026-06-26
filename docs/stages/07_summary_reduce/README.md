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

默认以目标镜头数量为基准做全局均衡分块：

```txt
target_shots_per_chunk = config.llm.max_shots_per_chunk 或 40
```

规则：

- 只使用成功的镜头分析记录。
- 失败镜头计入统计，但不传给摘要模型。
- 镜头少于阈值时也生成一个 chunk。
- 先用 `ceil(成功镜头数 / target_shots_per_chunk)` 计算 chunk 数量。
- 再把成功镜头按时间顺序平均分配到这些 chunk 中，避免出现 `40 + 1` 这类过小尾块。
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
  "section_sources": [
    {
      "title": "开场求助",
      "source_chunks": ["chunk_001"]
    }
  ],
  "important_shots": ["shot_000001", "shot_000012"],
  "warnings": []
}
```

## 全局大纲 Agent

`global_summary.json` 不再直接用所有 chunk 的 `main_topics` 顺序去重后截断生成 `main_sections`。

新逻辑：

1. 先生成所有 `chunk_summaries`。
2. 把轻量 chunk 摘要交给 Global Outline Agent。
3. Agent 输出标签结构，不输出 JSON。
4. 本地解析标签、校验 chunk 引用、过滤重复 section。
5. 本地组装最终 `global_summary.json`。

Agent 输出格式：

```txt
<THEME>AI上下文管理与记忆压缩机制</THEME>
<STYLE>structured_report</STYLE>
<SECTION chunks="chunk_001">问题背景：上下文丢失与源码入口</SECTION>
<SECTION chunks="chunk_001,chunk_002">四层压缩与缓存保护机制</SECTION>
```

字段来源：

| 字段 | 来源 |
| --- | --- |
| `video_main_theme` | Agent 的 `THEME` |
| `main_sections` | Agent 的 `SECTION` 标题 |
| `suggested_chapter_count` | 有效 section 数量，上限 6 |
| `narrative_style` | Agent 的 `STYLE` 或默认 `structured_report` |
| `source_chunks` | 本地从所有 chunk 摘要收集 |
| `section_sources` | Agent 的 `SECTION chunks` 属性，经本地校验 |
| `important_shots` | 本地从所有 chunk 的 `important_shots` 顺序去重，最多 20 个 |
| `warnings` | 本地解析、校验和降级过程生成 |

## 实现任务

- 读取 `shot_analysis.jsonl`，过滤失败记录。
- 按配置生成 chunk。
- 对每个 chunk 调用摘要模型或 mock 摘要器。
- 校验 `important_shots` 必须来自当前 chunk。
- 用 Global Outline Agent 基于所有 chunk 摘要生成全局 section。
- 解析标签结构并组装 `global_summary.json`。
- 全局大纲 Agent 失败时回退到规则聚合。
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
- 使用标签结构输出，不直接输出 JSON。

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
| 全局大纲 Agent 失败 | 回退到规则聚合，并写 warning |
| 模型输出引用不存在镜头 | 修正或移除引用，并写 warning |
| SECTION 引用不存在 chunk | 移除该 chunk 引用，并写 warning |
| 重要镜头为空 | 允许为空数组，但写 warning |

## 验收标准

- `chunk_summaries.jsonl` 至少一行。
- 每行 JSON 可解析。
- `global_summary.json` 可解析，包含 `video_main_theme` 和 `main_sections`。
- `global_summary.json` 包含 `source_chunks`、`section_sources` 和 `warnings`。
- 所有 `important_shots` 都存在于 `shot_analysis.jsonl`。
- `run_state.json` 中 `07_summary_reduce` 为 `done`。

## 测试建议

- 单测 0、1、50、51 个镜头的分块边界。
- 用 mock model 测 chunk 和 global 输出。
- 单测 Global Outline Agent 标签解析。
- 单测 Global Outline Agent 失败后回退规则聚合。
- 单测不存在 chunk 引用的清理逻辑。
- 单测模型引用不存在 shot 的修复逻辑。
- 单测失败镜头被过滤但统计不丢失。

