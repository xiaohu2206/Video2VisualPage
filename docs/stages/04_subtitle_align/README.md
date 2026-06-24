# 04 字幕对齐开发文档

## 目标

把字幕段按时间重叠关系归属到镜头，生成每个镜头对应的字幕文本和字幕片段列表。该阶段只做确定性时间计算，不调用模型。

## 输入

```txt
outputs/{project_id}/shot_split/normalized_shots.json
outputs/{project_id}/subtitle_extract/subtitles.json
```

## 输出

```txt
outputs/{project_id}/subtitle_align/shot_subtitles.json
```

## 对齐规则

字幕段和镜头区间存在重叠时，将字幕归属到该镜头：

```txt
overlap_start = max(shot.start_sec, segment.start_sec)
overlap_end = min(shot.end_sec, segment.end_sec)
overlap_sec = max(0, overlap_end - overlap_start)
```

当 `overlap_sec > 0` 时记录对齐关系。

重叠比例建议同时记录两个：

```txt
overlap_ratio_of_segment = overlap_sec / segment.duration_sec
overlap_ratio_of_shot = overlap_sec / shot.duration_sec
```

## shot_subtitles.json 契约

```json
{
  "items": [
    {
      "shot_id": "shot_000001",
      "start_sec": 0.0,
      "end_sec": 3.24,
      "subtitle_text": "求你救救我娘",
      "subtitle_segments": [
        {
          "segment_id": "sub_0001",
          "start_sec": 1.06,
          "end_sec": 2.9,
          "overlap_sec": 1.84,
          "overlap_ratio_of_segment": 1.0,
          "overlap_ratio_of_shot": 0.568,
          "text": "求你救救我娘"
        }
      ],
      "warnings": []
    }
  ]
}
```

空字幕示例：

```json
{
  "items": [
    {
      "shot_id": "shot_000001",
      "start_sec": 0.0,
      "end_sec": 3.24,
      "subtitle_text": "",
      "subtitle_segments": [],
      "warnings": []
    }
  ]
}
```

## 文本拼接规则

- 同一镜头内字幕按 `start_sec` 升序拼接。
- 相邻字幕之间用空格分隔。
- 完全重复文本可以去重，但必须保留原始 `subtitle_segments`。
- 单个镜头字幕过长时可先保留完整文本，压缩留给后续模型阶段。

## 实现任务

- 读取并校验 `normalized_shots.json` 和 `subtitles.json`。
- 使用双指针或区间扫描实现对齐，避免镜头和字幕都很多时变成低效嵌套循环。
- 为每个镜头输出一条记录，即使没有字幕。
- 记录跨镜头字幕的重叠比例。
- 写入 `shot_subtitles.json`。
- 更新 `run_state.json`。

## CLI

```powershell
python -m video2visualpage subtitle-align --project outputs\demo_20260623_200000
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 字幕为空 | 每个镜头输出空字幕记录，阶段成功 |
| 某字幕时间非法 | 跳过该字幕并写 warning |
| 镜头时间非法 | 阶段失败，因为下游无法可信使用 |
| 字幕跨多个镜头 | 记录到所有有重叠的镜头 |
| 字幕刚好贴边 | `overlap_sec = 0` 时不归属 |

## 验收标准

- 每个 `shot_id` 都有一条对齐记录。
- `items.length` 等于 `normalized_shots.shot_count`。
- 所有 `subtitle_segments` 引用的字幕 `segment_id` 都存在。
- 跨镜头字幕有正确的 `overlap_sec` 和比例。
- `run_state.json` 中 `04_subtitle_align` 为 `done`。

## 测试建议

- 单测字幕完全落在一个镜头内。
- 单测字幕跨两个镜头。
- 单测空字幕。
- 单测字幕和镜头边界贴边。
- 单测多字幕按时间拼接。

