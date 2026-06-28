# 09 章节写作开发文档

## 目标

按 `outline.json` 逐章生成正文 JSON。每个章节应能单独重跑，正文只能基于当前章节引用的镜头卡片和全局摘要生成。

当章节包含 `subsections` 时，写作阶段不再把整章一次性提交给模型，而是以小节为最小输入单位，按镜头数阈值把相邻小节打包成一次或多次模型调用，最后仍组装成一个 `chapter_*.json`。

## 输入

```txt
outputs/{project_id}/outline_plan/outline.json
outputs/{project_id}/shot_understanding/shot_analysis.jsonl
outputs/{project_id}/summary_reduce/global_summary.json
outputs/{project_id}/init/config.json
```

## 输出

```txt
outputs/{project_id}/chapter_write/chapter_001.json
outputs/{project_id}/chapter_write/chapter_002.json
outputs/{project_id}/chapter_write/chapters_index.json
outputs/{project_id}/chapter_write/subsection_write_batches.jsonl
```

## chapter_*.json 契约

```json
{
  "chapter_id": "chapter_001",
  "title": "开场求助",
  "representative_frame": "shot_split/keyframes/shot_000001_fifth_1_20.jpg",
  "body_markdown": "这一部分通过人物的求助台词和近景画面建立了故事的起点。",
  "key_points": [
    "角色处于求助状态",
    "字幕提供了明确的情节信息",
    "近景画面强化了紧张情绪"
  ],
  "referenced_shots": ["shot_000001", "shot_000002"],
  "warnings": []
}
```

## chapters_index.json 契约

```json
{
  "chapters": [
    {
      "chapter_id": "chapter_001",
      "title": "开场求助",
      "path": "chapter_write/chapter_001.json"
    }
  ],
  "warnings": []
}
```

## 写作约束

- 只读取当前章节的 `shot_ids` 对应镜头卡片。
- 不使用未分配给本章的镜头来写具体内容。
- 不添加视频中没有的信息。
- 正文使用中文解释型表达。
- `referenced_shots` 必须和章节引用镜头保持可追溯关系。
- 代表图必须来自 `recommended_display_frame` 或输入 frames。
- 如果当前章节包含 `subsections`，正文应按小节顺序组织，并使用 Markdown `## 小节标题`。
- 如果当前章节没有 `subsections`，不要强行添加空洞小节。
- 有 `subsections` 时，小节是最小模型输入单位；即使某个小节镜头很多，也不再拆成更小写作单元。
- 一次模型调用可以包含多个相邻小节，但这些小节的总镜头数不得超过 `chapter_write.max_shots_per_call`。
- 不限制单次调用包含的小节数量，只限制单次调用包含的镜头总数。
- 如果某个小节自身镜头数超过 `chapter_write.max_shots_per_call`，该小节单独调用，并在章节 warning 中记录 `oversized_subsection`。
- 模型可以一次返回多个小节的正文，但输出必须按小节分开；最终小节标题由本地按 `outline.json` 注入，不能依赖模型自由组织。

## 小节分批写作

`09_chapter_write` 内部采用章节级调度、小节级分批：

1. 对没有 `subsections` 的章节，继续按整章调用 `write_chapter`。
2. 对有 `subsections` 的章节，先把每个小节转为写作单元，写作单元只包含该小节的 `shot_ids` 和对应镜头卡片。
3. 按小节顺序贪心打包：如果当前批次加上下一个小节后镜头总数不超过 `chapter_write.max_shots_per_call`，则加入同一批次；否则先提交当前批次，再开启新批次。
4. 如果单个小节镜头数已经超过阈值，不继续拆分，直接单独提交该小节，并记录 warning。
5. 每个批次模型输出解析为多个小节结果，本地按 `outline.json` 小节顺序拼接为 `body_markdown`。

批次输出需要保持小节边界，例如：

```txt
<SUBSECTION id="chapter_001_sub_001">
<BODY_MARKDOWN>
小节正文
</BODY_MARKDOWN>
<KEY_POINTS>
- 关键点
</KEY_POINTS>
<REFERENCED_SHOTS>
- shot_001
</REFERENCED_SHOTS>
</SUBSECTION>
```

本地组装章节时：

- `body_markdown` 按小节顺序拼接，并由本地生成 `## 小节标题`。
- `referenced_shots` 为各小节引用镜头的有序去重结果。
- `key_points` 为各小节关键点的去重汇总。
- `representative_frame` 从章节引用镜头中选择可用帧。
- 如果某个小节输出缺失或为空，优先只重试该小节所在批次；仍失败时记录失败小节 warning。

## 配置

建议新增独立配置项，避免和摘要阶段 chunk 大小混用：

```json
{
  "chapter_write": {
    "max_shots_per_call": 20
  }
}
```

## subsection_write_batches.jsonl 契约

当章节包含 `subsections` 时，写作阶段会记录小节批次调用情况。没有小节批次时文件可以为空。

```json
{
  "chapter_id": "chapter_001",
  "batch_id": "chapter_001_batch_001",
  "subsection_ids": ["chapter_001_sub_001", "chapter_001_sub_002"],
  "shot_count": 8,
  "max_shots_per_call": 20,
  "oversized_subsection_ids": [],
  "status": "done",
  "warnings": []
}
```

`max_shots_per_call` 只控制单次写作模型调用包含的镜头总数，不控制输入字符数，也不控制小节数量。

## 实现任务

- 读取 `outline.json`。
- 建立 `shot_id -> shot_analysis` 索引。
- 逐章调度写作任务；无小节章节按整章调用，有小节章节按小节分批调用。
- 为小节批次构造模型输入，并要求模型按小节返回正文。
- 解析整章或小节批次输出并校验字段。
- 本地按 `outline.json` 顺序组装小节正文，生成最终章节正文。
- 为每章选择 `representative_frame`。
- 写入独立 `chapter_*.json`。
- 写入 `chapters_index.json`。
- 写入 `subsection_write_batches.jsonl` 记录小节分批和降级情况。
- 支持 `--chapter-id` 单章重跑。
- 更新 `run_state.json`。

## CLI

全量写作：

```powershell
python -m video2visualpage write-chapters --project outputs\demo_20260623_200000
```

单章重跑：

```powershell
python -m video2visualpage write-chapter `
  --project outputs\demo_20260623_200000 `
  --chapter-id chapter_001
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 某章模型失败 | 写失败记录，继续其他章节 |
| 某小节批次模型失败 | 缩小到单小节重试；仍失败则记录该小节 warning |
| 模型输出空正文 | 重试一次，仍失败则该章失败 |
| 批次输出缺失某个小节 | 只针对缺失小节重试，并保留其他小节结果 |
| 代表图不存在 | 选择本章其他可用镜头图 |
| 章节引用镜头缺失 | 跳过缺失镜头并写 warning |
| 单小节镜头数超过阈值 | 不拆小节，单独调用并写 `oversized_subsection` warning |
| 单章重跑成功 | 只替换该章 JSON，并更新 index |

## 验收标准

- 每个有效章节都有独立 `chapter_*.json`。
- `chapters_index.json` 引用的文件都存在。
- `subsection_write_batches.jsonl` 存在；有小节批次时记录每个批次的 `subsection_ids`、`shot_count` 和 `status`。
- 每章 `body_markdown` 非空。
- 每章 `referenced_shots` 都来自 `outline.json` 当前章节。
- 有 `subsections` 的章节，`body_markdown` 应包含每个小节标题，且顺序与 `outline.json` 一致。
- 小节批次调用不得跨章节，不得打乱小节顺序。
- 每章 `representative_frame` 路径存在或有明确 warning。
- `run_state.json` 中 `09_chapter_write` 为 `done`。

## 测试建议

- 用 mock model 测多章节输出。
- 用 mock model 测多个小节合并到一次调用但输出仍按小节分开。
- 单测小节分批逻辑：只按 `max_shots_per_call` 限制镜头数，不限制小节数量。
- 单测超大单小节单独调用并写 `oversized_subsection` warning。
- 单测单章重跑只改目标章节。
- 单测代表图 fallback。
- 单测模型输出字段缺失时的修复或失败逻辑。
- 单测 `chapters_index.json` 和实际文件一致。

