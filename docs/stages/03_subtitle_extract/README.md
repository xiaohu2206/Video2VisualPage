# 03 字幕提取开发文档

## 目标

复用同名字幕文件或 `utils/subtitle-extractor` 在线 ASR 生成字幕，输出标准化字幕 JSON、SRT 和可选原始 ASR 结果。无音频、显式关闭 ASR 或识别失败时也要输出合法空字幕 JSON。

## 输入

```txt
outputs/{project_id}/init/project.json
outputs/{project_id}/init/config.json
outputs/{project_id}/media_info/media_info.json
```

主要字段：

- `project.json.input_video`
- `media_info.has_audio`
- `config.subtitle.enabled`
- `config.subtitle.language`
- `config.subtitle.allow_empty`

## 输出

```txt
outputs/{project_id}/subtitle_extract/subtitles.json
outputs/{project_id}/subtitle_extract/subtitles.srt
outputs/{project_id}/subtitle_extract/asr_raw.json
```

其中 `asr_raw.json` 可以在 ASR 工具支持时生成；没有原始数据时可省略，但需要在 `subtitles.json.warnings` 中说明。

## 工具

工具目录：

```txt
utils/subtitle-extractor
```

工具说明：

- 依赖可用 `ffmpeg`。
- 当前实现使用 Bcut 在线 ASR，运行时需要网络访问 `https://member.bilibili.com`。
- 在线 ASR 默认开启；需要离线降级时设置 `$env:VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR = "0"`。
- 支持输出 `srt`、`txt`、`json`、`raw-json`。

推荐调用：

```powershell
python -m subtitle_extractor D:\videos\demo.mp4 `
  -o D:\wrok\workPro2\Video2VisualPage\outputs\demo_20260623_200000\03_subtitle_extract\subtitles.json `
  --format json
```

SRT 输出：

```powershell
python -m subtitle_extractor D:\videos\demo.mp4 `
  -o D:\wrok\workPro2\Video2VisualPage\outputs\demo_20260623_200000\03_subtitle_extract\subtitles.srt `
  --format srt
```

原始 ASR 输出：

```powershell
python -m subtitle_extractor D:\videos\demo.mp4 `
  -o D:\wrok\workPro2\Video2VisualPage\outputs\demo_20260623_200000\03_subtitle_extract\asr_raw.json `
  --format raw-json
```

## subtitles.json 契约

```json
{
  "language": "zh",
  "source": "asr",
  "segments": [
    {
      "segment_id": "sub_0001",
      "start_sec": 1.06,
      "end_sec": 2.9,
      "duration_sec": 1.84,
      "text": "求你救救我娘"
    }
  ],
  "warnings": []
}
```

空字幕示例：

```json
{
  "language": "unknown",
  "source": "none",
  "segments": [],
  "warnings": ["no_audio_detected"]
}
```

## 实现任务

- 根据 `media_info.has_audio` 判断是否需要调用 ASR。
- 封装 `subtitle-extractor` 的 JSON、SRT、raw-json 输出。
- 将工具输出标准化为 `segment_id`、`start_sec`、`end_sec`、`text`。
- 保证字幕段按时间升序排列。
- 当 ASR 失败且允许空字幕时，输出合法空字幕 JSON。
- 更新 `run_state.json`。

## CLI

```powershell
python -m video2visualpage subtitle-extract --project outputs\demo_20260623_200000
```

调试参数：

```powershell
python -m video2visualpage subtitle-extract `
  --project outputs\demo_20260623_200000 `
  --format json,srt,raw-json `
  --no-cache
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| `media_info.has_audio=false` | 输出空字幕 JSON，阶段成功 |
| ASR 网络失败 | 如果 `allow_empty=true`，输出空字幕并写 warning；否则阶段失败 |
| ffmpeg 不可用 | 阶段失败，提示安装或配置 |
| 工具 JSON 字段不符合预期 | 尝试标准化，失败则写错误 |
| 某条字幕时间非法 | 丢弃该条并写 warning |

## 验收标准

- `subtitles.json` 一定存在且可解析。
- `segments` 一定是数组。
- 每条字幕都有 `segment_id`、`start_sec`、`end_sec`、`text`。
- 无字幕场景不会阻断后续流程。
- `run_state.json` 中 `03_subtitle_extract` 为 `done`。

## 测试建议

- 用有音频视频跑一次真实工具调用。
- 用 `media_info.has_audio=false` 的 fixture 测空字幕输出。
- mock ASR 网络失败，确认降级逻辑。
- 单测工具输出到标准字幕结构的字段映射。

