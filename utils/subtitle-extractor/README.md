# Subtitle Extractor

从 `movie-recap-clone` 中独立拆出的字幕提取工具。

它可以把视频或音频转换成 16k 单声道 MP3，调用 Bcut 在线 ASR 识别，并输出 `srt`、`txt`、`json` 或原始 ASR JSON。

## 安装

```powershell
cd subtitle-extractor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

需要系统可用 `ffmpeg`。工具会按以下顺序寻找：

1. `FFMPEG_PATH`
2. `FFMPEG_DIR` / `FFMPEG_HOME`
3. 系统 `PATH`
4. `imageio-ffmpeg` 内置 ffmpeg
5. 项目内 `ffmpeg/bin/ffmpeg.exe`

## 使用

```powershell
python -m subtitle_extractor "D:\videos\movie.mp4" -o outputs\movie.srt
```

常用参数：

```powershell
python -m subtitle_extractor input.mp4 -o outputs\movie.json --format json
python -m subtitle_extractor input.mp4 -o outputs\movie.txt --format txt
python -m subtitle_extractor input.mp4 -o outputs\asr_raw.json --format raw-json
python -m subtitle_extractor input.mp4 -o outputs\movie.srt --compressed-srt
python -m subtitle_extractor input.mp4 --no-cache --keep-audio
python -m subtitle_extractor --test-connection
```

## Python 调用

```python
from pathlib import Path
from subtitle_extractor import extract_subtitles

result = extract_subtitles(
    "movie.mp4",
    output_path=Path("outputs/movie.srt"),
    output_format="srt",
    use_cache=True,
)

print(result.output_path)
print(len(result.utterances))
```

## 说明

- 当前独立项目抽取的是原仓库内完整可追溯的 Bcut 在线 ASR 字幕提取链路。
- 原 Web 服务中的项目状态、WebSocket、上传目录、FunASR 模型管理等前端/服务端耦合代码没有带入。
- Bcut 是在线服务，运行时需要网络可访问 `https://member.bilibili.com`。
