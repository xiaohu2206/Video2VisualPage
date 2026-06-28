# 08 目录规划开发文档

## 目标

根据镜头卡片、分块摘要和全局摘要生成网页报告目录，并把镜头分配到章节。目录是后续章节写作和静态渲染的结构来源。

目录规划的一级章节必须优先按语义边界生成，而不是按镜头数量均分。完整改造方案见 [Outline Planning Refactor 设计文档](../../designs/outline-planning-refactor/README.md)。

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
outputs/{project_id}/outline_plan/chapter_boundary_decisions.jsonl
outputs/{project_id}/outline_plan/outline_structure_qa.json
outputs/{project_id}/outline_plan/subsection_decisions.jsonl
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
      "end_sec": 12.8,
      "subsections": [
        {
          "subsection_id": "chapter_001_sub_001",
          "title": "求助情境建立",
          "shot_ids": ["shot_000001", "shot_000002"],
          "representative_shot_id": "shot_000001",
          "start_sec": 0.0,
          "end_sec": 8.4
        }
      ]
    }
  ],
  "warnings": []
}
```

## 规划约束

- 章节顺序默认按视频时间线排列。
- 一级章节应对应页面级目录主题，不能只是平均切出来的大镜头容器。
- 优先使用 `global_summary.section_sources` 和 `chunk_summaries.shot_range` 推导章节边界。
- 当 `section_sources` 缺失、歧义或无法解析时，才回退到模型规划或本地兜底策略。
- 每个章节必须引用真实存在的 `shot_id`。
- 每章必须有 `representative_shot_id`，且它必须属于本章 `shot_ids`。
- 一个镜头默认只属于一个主章节。
- 章节标题不能凭空创造视频中没有的信息。
- 章节数量建议 3 到 8 个，短视频可以少于 3 个。
- 高 `importance_score` 镜头应优先被分配到章节。
- `subsections` 是可选字段；只有大章节确实需要细分时才生成。
- 小节必须只引用当前章节内的 `shot_id`。
- 小节代表镜头必须属于当前小节。
- 下游 `09_chapter_write` 会把小节作为最小写作输入单位，因此这里不需要为了写作调用阈值继续拆碎小节。
- 如果大章节没有生成 `subsections`，`09_chapter_write` 只能按整章写作；因此明显过大的章节应优先在本阶段生成小节或写入结构风险。

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

如果本地可以根据 `section_sources` 稳定生成章节边界，则不需要调用一级目录模型。只有在 chunk 与 section 的对应关系不足以确定章节边界时，才调用 Outline Planner Agent。

## 实现任务

- 读取成功的镜头分析记录。
- 读取 `chunk_summaries.jsonl`，解析每个 chunk 的镜头或时间范围。
- 生成轻量 `shot_briefs`。
- 优先根据 `global_summary.section_sources` 生成一级章节边界。
- 对 `section_sources` 缺失或歧义的项目，调用 Outline Planner Agent 生成一级章节草案。
- 校验并修正章节引用。
- 为每章补齐 `start_sec` 和 `end_sec`。
- 写入章节边界决策调试信息。
- 对一级章节结构做风险检测，识别“章节过大”“小节像一级章”“相邻章节主题重复”等问题。
- 对信息密度较高的大章节判断是否需要二级小节。
- 需要小节时调用 Chapter Subsection Agent，并用标签结构解析输出。
- 写入 `subsection_decisions.jsonl` 记录每章决策。
- 如果模型漏掉重要镜头，追加到最近章节或写 warning。
- 更新 `run_state.json`。

## 二级小节策略

`08_outline_plan` 先用本地规则判断一级章节是否需要继续拆分。只有满足镜头多、主题多、topic shift 明显、重点镜头分布分散等条件时，才调用模型生成二级小节。

二级小节不是用来修复明显错误的一级章节。如果一个小节已经可以独立成为页面目录中的一章，应该回到一级目录规划阶段重新切分章节。

小节数量和大小主要服务语义结构，不直接承担模型调用批次控制。写作阶段会按 `chapter_write.max_shots_per_call` 将相邻小节打包调用；如果单个小节超过该阈值，也不会在写作阶段继续拆分，而是单独调用并记录 warning。

模型不输出 JSON，只输出标签：

```txt
<KEEP/>
```

或：

```txt
<SUB shots="shot_001-shot_006">上下文丢失的表现</SUB>
<SUB shots="shot_007-shot_012">源码中的上下文入口</SUB>
```

最终 `subsections` 由本地解析、校验和补齐生成。

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
| `section_sources` 缺失或无法解析 | 调用 Outline Planner Agent；失败后使用本地兜底 |
| Outline Planner Agent 输出无法解析 | 保留本地兜底章节并写 warning |
| 引用不存在的 `shot_id` | 移除引用并写 warning |
| 章节没有镜头 | 删除该章节或合并到相邻章节 |
| 没有代表镜头 | 选择本章最高 `importance_score` 镜头 |
| 章节覆盖过大且 topic shift 很多 | 写入结构风险；必要时触发结构 QA |
| 大章节未生成小节 | 保持兼容但写结构风险，提示下游可能整章写作 |
| 所有章节都无效 | 阶段失败 |

## 验收标准

- `outline.json` 是合法 JSON。
- 至少有一个章节。
- 每个章节都有 `chapter_id`、`title`、`summary`、`shot_ids`、`representative_shot_id`。
- 所有 `shot_ids` 都来自 `shot_analysis.jsonl`。
- `representative_shot_id` 属于当前章节。
- 一级章节边界优先匹配 `section_sources` 指向的 chunk 范围。
- 二级小节标题不应与其他一级章节标题高度重复。
- 对明显过大的章节，应生成 `subsections` 或在 `outline_structure_qa.json` / warnings 中说明风险。
- `run_state.json` 中 `08_outline_plan` 为 `done`。

## 测试建议

- 用 `section_sources` mock 数据测试 chunk 到章节边界的映射。
- 用 mock model 测 Outline Planner Agent 标签输出解析。
- 单测不存在 shot 引用的清理逻辑。
- 单测空章节删除或合并。
- 单测代表镜头自动补齐。
- 单测章节时间范围计算。
- 单测结构风险检测：章节过大、小节与一级章节重复、相邻章节主题重复。
