# 08 目录规划开发文档

## 目标

根据镜头卡片、分块摘要和全局摘要生成网页报告目录，并把镜头分配到章节。目录是后续章节写作和静态渲染的结构来源。

## 输入

```txt
outputs/{project_id}/shot_understanding/shot_analysis.jsonl
outputs/{project_id}/summary_reduce/chunk_summaries.jsonl
outputs/{project_id}/summary_reduce/global_summary.json
outputs/{project_id}/init/config.json
```

## 输出

```txt
outputs/{project_id}/outline_plan/outline.json
```

## outline.json 契约

```json
{
  "title": "视频内容可视化解读",
  "description": "根据镜头、字幕和关键帧自动生成的结构化报告。",
  "chapters": [
    {
      "chapter_id": "chapter_001",
      "title": "开场求助",
      "summary": "介绍视频开头的核心冲突和人物诉求。",
      "shot_ids": ["shot_000001", "shot_000002", "shot_000003"],
      "representative_shot_id": "shot_000001",
      "start_sec": 0.0,
      "end_sec": 12.8
    }
  ],
  "warnings": []
}
```

## 规划约束

- 章节顺序默认按视频时间线排列。
- 每个章节必须引用真实存在的 `shot_id`。
- 每章必须有 `representative_shot_id`，且它必须属于本章 `shot_ids`。
- 一个镜头默认只属于一个主章节。
- 章节标题不能凭空创造视频中没有的信息。
- 章节数量建议 3 到 8 个，短视频可以少于 3 个。
- 高 `importance_score` 镜头应优先被分配到章节。

## 输入给模型的材料

建议不要把完整镜头卡片全部塞给模型，而是压缩成目录规划输入：

```json
{
  "global_summary": {},
  "chunk_summaries": [],
  "shot_briefs": [
    {
      "shot_id": "shot_000001",
      "start_sec": 0.0,
      "end_sec": 3.24,
      "merged_summary": "本镜头建立求助情境。",
      "topic_tags": ["剧情推进"],
      "importance_score": 0.82
    }
  ]
}
```

## 实现任务

- 读取成功的镜头分析记录。
- 生成轻量 `shot_briefs`。
- 调用目录规划模型生成 outline。
- 校验并修正章节引用。
- 为每章补齐 `start_sec` 和 `end_sec`。
- 如果模型漏掉重要镜头，追加到最近章节或写 warning。
- 更新 `run_state.json`。

## CLI

```powershell
python -m video2visualpage plan-outline --project outputs\demo_20260623_200000
```

可选参数：

```powershell
python -m video2visualpage plan-outline `
  --project outputs\demo_20260623_200000 `
  --chapter-count 5
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 模型输出非 JSON | 尝试 JSON repair |
| 引用不存在的 `shot_id` | 移除引用并写 warning |
| 章节没有镜头 | 删除该章节或合并到相邻章节 |
| 没有代表镜头 | 选择本章最高 `importance_score` 镜头 |
| 所有章节都无效 | 阶段失败 |

## 验收标准

- `outline.json` 是合法 JSON。
- 至少有一个章节。
- 每个章节都有 `chapter_id`、`title`、`summary`、`shot_ids`、`representative_shot_id`。
- 所有 `shot_ids` 都来自 `shot_analysis.jsonl`。
- `representative_shot_id` 属于当前章节。
- `run_state.json` 中 `08_outline_plan` 为 `done`。

## 测试建议

- 用 mock model 测目录生成。
- 单测不存在 shot 引用的清理逻辑。
- 单测空章节删除或合并。
- 单测代表镜头自动补齐。
- 单测章节时间范围计算。

