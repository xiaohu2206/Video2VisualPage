# 00 项目初始化开发文档

## 目标

为一个输入视频创建独立项目目录，生成项目元信息、默认配置、阶段状态和日志目录。该阶段是全流程唯一允许创建 `outputs/{project_id}/` 根结构的步骤。

## 输入

命令行参数：

```txt
--video D:/videos/demo.mp4
--project-name demo
--output-root D:/wrok/workPro2/Video2VisualPage/outputs
--config optional_config.json
```

最小输入对象：

```json
{
  "video_path": "D:/videos/demo.mp4",
  "project_name": "demo",
  "output_root": "D:/wrok/workPro2/Video2VisualPage/outputs"
}
```

## 输出

```txt
outputs/{project_id}/init/project.json
outputs/{project_id}/init/run_state.json
outputs/{project_id}/init/config.json
outputs/{project_id}/logs/events.jsonl
outputs/{project_id}/logs/errors.jsonl
```

## project_id 规则

固定格式：

```txt
{safe_project_name}
```

示例：

```txt
demo
```

规则：

- `project_name` 只保留字母、数字、下划线、中划线，其他字符替换为 `_`。
- 如果同名目录已经存在，先覆盖该目录，再重新生成项目文件。
- `project_id` 一旦写入 `project.json`，后续阶段不能再改。

## project.json 契约

```json
{
  "project_id": "demo",
  "project_name": "demo",
  "input_video": "D:/videos/demo.mp4",
  "output_dir": "D:/wrok/workPro2/Video2VisualPage/outputs/demo",
  "created_at": "2026-06-23T20:00:00+08:00",
  "pipeline_version": "0.1.0"
}
```

字段要求：

- `input_video` 保存规范化绝对路径。
- `output_dir` 保存项目输出目录绝对路径。
- 时间使用 ISO 8601，并带时区。

## config.json 契约

默认配置至少包含：

```json
{
  "scene_detection": {
    "threshold": 0.5,
    "keyframe_positions": [0.2, 0.8],
    "min_gap_seconds": 0.35,
    "min_shot_seconds": 0.15
  },
  "subtitle": {
    "enabled": true,
    "language": "auto",
    "allow_empty": true
  },
  "llm": {
    "enabled": false,
    "max_shots_per_chunk": 50,
    "json_mode": true
  },
  "render": {
    "output_html": true,
    "output_pdf": true,
    "theme": "clean_report"
  }
}
```

## run_state.json 契约

```json
{
  "project_id": "demo",
  "pipeline_version": "0.1.0",
  "stages": [
    {
      "stage_id": "00_init",
      "name": "项目初始化",
      "stage_dir": "init",
      "status": "done",
      "outputs": [
        "init/project.json",
        "init/run_state.json",
        "init/config.json"
      ]
    },
    {
      "stage_id": "01_media_probe",
      "name": "视频探测",
      "stage_dir": "media_info",
      "status": "pending",
      "outputs": []
    }
  ],
  "updated_at": "2026-06-23T20:00:00+08:00"
}
```

必须预置 `00_init` 到 `11_qa` 的全部阶段。

## 实现任务

- 解析 CLI 参数并校验输入视频存在。
- 创建 `outputs/{project_id}/`、`init/`、`logs/`。
- 合并默认配置和用户传入配置。
- 写入 `project.json`、`config.json`、`run_state.json`。
- 追加 `logs/events.jsonl` 事件：`project_created`。
- 提供 `resume` 能力所需的项目目录识别函数。

## 推荐模块

```txt
video2visualpage/
  cli.py
  pipeline/init_project.py
  storage/json_store.py
  storage/run_state.py
```

## CLI

```powershell
python -m video2visualpage init `
  --video D:\videos\demo.mp4 `
  --project-name demo `
  --output-root D:\wrok\workPro2\Video2VisualPage\outputs
```

成功后打印：

```txt
Project created: outputs/demo
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 视频不存在 | 直接失败，不创建项目目录 |
| 输出目录不可写 | 直接失败，提示权限或路径问题 |
| 用户配置 JSON 非法 | 直接失败，指出配置文件路径 |
| 同名项目已存在 | 自动追加序号，避免覆盖 |

## 验收标准

- 项目目录创建成功。
- 三个初始化 JSON 都能被解析。
- `run_state.json` 中 `00_init` 为 `done`，其他阶段为 `pending`。
- `logs/events.jsonl` 至少有一条 `project_created`。
- 重复运行不会覆盖已有项目。

## 测试建议

- 单测 `project_id` 生成和非法字符替换。
- 单测默认配置与用户配置合并。
- 单测 `run_state.json` 的阶段数量和顺序。
- 集成测试：给一个存在的视频路径，确认完整目录结构生成。

