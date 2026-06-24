# 09 章节写作开发文档

## 目标

按 `outline.json` 逐章生成正文 JSON。每个章节应能单独重跑，正文只能基于当前章节引用的镜头卡片和全局摘要生成。

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

## 实现任务

- 读取 `outline.json`。
- 建立 `shot_id -> shot_analysis` 索引。
- 逐章构造模型输入。
- 解析模型输出并校验字段。
- 为每章选择 `representative_frame`。
- 写入独立 `chapter_*.json`。
- 写入 `chapters_index.json`。
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
| 模型输出空正文 | 重试一次，仍失败则该章失败 |
| 代表图不存在 | 选择本章其他可用镜头图 |
| 章节引用镜头缺失 | 跳过缺失镜头并写 warning |
| 单章重跑成功 | 只替换该章 JSON，并更新 index |

## 验收标准

- 每个有效章节都有独立 `chapter_*.json`。
- `chapters_index.json` 引用的文件都存在。
- 每章 `body_markdown` 非空。
- 每章 `referenced_shots` 都来自 `outline.json` 当前章节。
- 每章 `representative_frame` 路径存在或有明确 warning。
- `run_state.json` 中 `09_chapter_write` 为 `done`。

## 测试建议

- 用 mock model 测多章节输出。
- 单测单章重跑只改目标章节。
- 单测代表图 fallback。
- 单测模型输出字段缺失时的修复或失败逻辑。
- 单测 `chapters_index.json` 和实际文件一致。

