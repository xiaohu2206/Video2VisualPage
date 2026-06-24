# 02 镜头分割开发文档

## 目标

复用 `utils/opencv-shot-segmenter` 对视频做镜头边界检测，并导出关键帧。该阶段生成工具原始输出和后续流程使用的标准化镜头 JSON。

## 输入

```txt
outputs/{project_id}/init/project.json
outputs/{project_id}/init/config.json
outputs/{project_id}/media_info/media_info.json
```

主要字段：

- `project.json.input_video`
- `config.scene_detection.threshold`
- `config.scene_detection.keyframe_positions`
- `media_info.duration_sec`
- `media_info.fps`

## 输出

```txt
outputs/{project_id}/shot_split/shots.json
outputs/{project_id}/shot_split/normalized_shots.json
outputs/{project_id}/shot_split/keyframes/*.jpg
```

## 工具

工具目录：

```txt
utils/opencv-shot-segmenter
```

工具支持的关键参数：

- `--video`
- `--output-dir`
- `--threshold`
- `--shot-prefix`
- `--keyframe-positions`
- `--min-gap-seconds`
- `--min-shot-seconds`
- `--no-keyframes`
- `--export-clips`

推荐调用：

```powershell
python -m opencv_shot_segmenter `
  --video D:\videos\demo.mp4 `
  --output-dir D:\wrok\workPro2\Video2VisualPage\outputs\demo_20260623_200000\02_shot_split `
  --threshold 0.5 `
  --shot-prefix shot `
  --keyframe-positions 0.2,0.8
```

## shots.json

`shots.json` 保留工具原始输出，不做字段重命名，方便排查工具问题。

工具输出示例：

```json
{
  "video_path": "D:\\videos\\demo.mp4",
  "duration": 120.0,
  "fps": 25.0,
  "frame_count": 3000,
  "shot_count": 42,
  "detection": {
    "backend": "opencv_frame_diff",
    "device": "cpu"
  },
  "shots": [
    {
      "shot_id": "shot_000001",
      "start": 0.0,
      "end": 3.24,
      "duration": 3.24,
      "start_frame": 0,
      "end_frame": 80,
      "keyframes": []
    }
  ]
}
```

## normalized_shots.json 契约

```json
{
  "video_path": "D:/videos/demo.mp4",
  "shot_count": 1,
  "keyframe_positions": [0.2, 0.8],
  "shots": [
    {
      "shot_id": "shot_000001",
      "index": 1,
      "start_sec": 0.0,
      "end_sec": 3.24,
      "duration_sec": 3.24,
      "start_frame": 0,
      "end_frame": 80,
      "keyframes": [
        {
          "frame_id": "shot_000001_20",
          "position": 0.2,
          "time_sec": 0.648,
          "path": "shot_split/keyframes/shot_000001_fifth_1_20.jpg"
        }
      ],
      "warnings": []
    }
  ]
}
```

字段映射：

| 工具字段 | 标准字段 |
| --- | --- |
| `start` | `start_sec` |
| `end` | `end_sec` |
| `duration` | `duration_sec` |

## 实现任务

- 封装 `opencv-shot-segmenter` 调用。
- 将配置里的关键帧位置转成逗号分隔字符串。
- 保留原始 `shots.json`。
- 生成标准化 `normalized_shots.json`。
- 校验关键帧路径存在。
- 更新 `run_state.json`。

## CLI

```powershell
python -m video2visualpage shot-split --project outputs\demo_20260623_200000
```

调参示例：

```powershell
python -m video2visualpage shot-split `
  --project outputs\demo_20260623_200000 `
  --threshold 0.35 `
  --keyframe-positions 0.2,0.8
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 工具执行失败 | 阶段失败，写 `errors.jsonl` |
| 未检测到切点 | 生成单镜头，覆盖完整视频时长 |
| 关键帧导出失败 | 保留镜头记录，在该镜头 `warnings` 中记录 |
| 时间超过视频总时长 | 裁剪到 `media_info.duration_sec` |
| 镜头过短 | 保留但标记 `short_shot`，后续步骤降级处理 |

## 验收标准

- `shots.json` 存在且可解析。
- `normalized_shots.json` 中每个镜头都有 `shot_id`、`start_sec`、`end_sec`、`duration_sec`。
- 镜头时间按升序排列，不应重叠。
- 每个关键帧路径真实存在，或镜头里有明确 warning。
- `run_state.json` 中 `02_shot_split` 为 `done`。

## 测试建议

- 用 `utils/opencv-shot-segmenter/data/1.mp4` 跑集成测试。
- 单测原始 `shots.json` 到 `normalized_shots.json` 的映射。
- mock 工具返回空镜头，确认 fallback 单镜头逻辑。
- mock 缺失关键帧，确认 warning 记录。

