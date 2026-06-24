# 01 视频探测开发文档

## 目标

读取输入视频，生成统一的视频基础信息，作为后续镜头切分、字幕提取、抽帧、时间对齐的基准。

## 输入

```txt
outputs/{project_id}/init/project.json
outputs/{project_id}/init/config.json
```

需要读取：

- `project.json.input_video`
- 可选的媒体探测配置

## 输出

```txt
outputs/{project_id}/media_info/media_info.json
```

## media_info.json 契约

```json
{
  "video_path": "D:/videos/demo.mp4",
  "duration_sec": 165.88,
  "fps": 25.0,
  "frame_count": 4147,
  "width": 1080,
  "height": 1920,
  "has_audio": true,
  "audio_codec": "aac",
  "video_codec": "h264",
  "format": "mp4",
  "probe_backend": "ffprobe",
  "warnings": []
}
```

字段要求：

- 时间统一使用秒，保留最多 3 位小数。
- `fps` 统一为浮点数。
- 无音频时 `has_audio` 必须为 `false`，不能省略。
- 探测不到的非关键字段使用 `null`，不要编造。

## 实现策略

优先级：

1. 使用 `ffprobe` 获取完整媒体信息。
2. 如果 `ffprobe` 不可用，用 OpenCV 读取视频流基本信息。
3. 如果仍失败，阶段失败并写入 `logs/errors.jsonl`。

建议先实现 `ffprobe`，因为它能稳定识别音频流：

```powershell
ffprobe -v error -print_format json -show_format -show_streams D:\videos\demo.mp4
```

OpenCV fallback 可得到：

- `fps`
- `frame_count`
- `width`
- `height`
- 估算 `duration_sec`

但无法可靠判断音频流。

## 核心逻辑

1. 读取 `project.json` 并确认视频路径存在。
2. 调用媒体探测 backend。
3. 标准化字段名和单位。
4. 对关键字段做校验：`duration_sec > 0`、`fps > 0`、尺寸大于 0。
5. 写入 `media_info/media_info.json`。
6. 更新 `run_state.json` 中 `01_media_probe` 状态和产物。

## CLI

```powershell
python -m video2visualpage media-info --project outputs\demo_20260623_200000
```

也可以直接从视频启动该步骤：

```powershell
python -m video2visualpage media-info --video D:\videos\demo.mp4 --project-name demo
```

直接使用 `--video --project-name` 时会优先复用当天同项目名、同视频的已有项目；如需强制新建目录，追加 `--new-project`。

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 视频文件不存在 | 阶段失败 |
| 无法读取视频流 | 阶段失败 |
| 无音频 | 写 `has_audio: false`，流程继续 |
| ffprobe 不可用 | 尝试 OpenCV fallback，并写 warning |
| 元数据字段缺失 | 能推导则推导，不能推导则置 `null` 并写 warning |

## 验收标准

- `media_info.json` 是合法 JSON。
- 包含 `duration_sec`、`fps`、`frame_count`、`width`、`height`、`has_audio`。
- 无音频视频不会阻断流程。
- `run_state.json` 中 `01_media_probe` 更新为 `done`。

## 测试建议

- 用短 mp4 测试正常探测。
- 用无音频视频测试 `has_audio: false`。
- mock `ffprobe` 不存在，确认 OpenCV fallback 生效。
- 单测 ffprobe JSON 到 `media_info.json` 的字段映射。

