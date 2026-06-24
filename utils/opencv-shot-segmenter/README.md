# OpenCV Shot Segmenter

一个从原项目中独立提取出来的 OpenCV 镜头分割小项目。它不依赖原仓库的 `clone_narration_video` 包、TransNetV2、ASR 或项目路径配置，只使用 `opencv-python` 和 `numpy` 完成：

- 基于灰度帧差、HSV 直方图差异、边缘差异检测镜头边界
- 输出结构化 `shots.json`
- 按镜头导出多张关键帧
- 可选用 OpenCV 导出无音频镜头片段

## 安装

```powershell
cd .\opencv-shot-segmenter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

## 命令行使用

```powershell
opencv-shot-segmenter `
  --video D:\videos\movie.mp4 `
  --output-dir .\outputs `
  --threshold 0.5 `
  --shot-prefix movie_shot
```

也可以不用安装脚本，直接运行模块：

```powershell
python -m opencv_shot_segmenter `
  --video D:\videos\movie.mp4 `
  --output-dir .\outputs
```

常用参数：

- `--threshold`：检测灵敏度，范围通常 `0.1` 到 `0.9`。值越低越容易切出更多镜头。
- `--keyframe-positions`：每个镜头内导出关键帧的位置，例如 `0.12,0.5,0.88`。
- `--sample-fps` 和 `--max-sample-frames-per-shot`：额外抽取采样帧，便于后续视觉匹配。
- `--export-clips`：导出每个镜头的视频片段。此模式只使用 OpenCV，因此导出的片段没有音频。
- `--no-keyframes`：只输出镜头时间轴，不导出关键帧。

## 输出结构

默认输出：

```text
outputs/
  shots.json
  keyframes/
    movie_shot_000001_fifth_1_12.jpg
    movie_shot_000001_fifth_2_50.jpg
```

`shots.json` 示例：

```json
{
  "video_path": "D:\\videos\\movie.mp4",
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
      "shot_id": "movie_shot_000001",
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

## 作为库使用

```python
from pathlib import Path
from opencv_shot_segmenter import detect_shots

result = detect_shots(
    Path("movie.mp4"),
    output_dir=Path("outputs"),
    threshold=0.5,
    shot_prefix="movie_shot",
)
print(result["shot_count"])
```

## 算法说明

每帧会被缩小到 `96x54`，然后计算三类相邻帧差异：

- 灰度图平均绝对差异，占 50%
- HSV 二维直方图 Bhattacharyya 距离，占 40%
- Canny 边缘图平均绝对差异，占 10%

检测阈值会结合分数中位数、MAD 和高分位数自适应计算，并通过最小镜头间隔过滤过密边界。这个方案轻量、可解释、适合作为没有模型权重时的 CPU 后端。
