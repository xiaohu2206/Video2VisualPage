# 11 QA 校验开发文档

## 目标

对全流程产物做完整性、合法性、可追溯性检查，生成 `qa_report.json`。QA 阶段默认不重新创作内容，只负责发现问题；可选提供低风险自动修复。

## 输入

```txt
outputs/{project_id}/
```

重点读取：

```txt
init/project.json
init/run_state.json
media_info/media_info.json
shot_split/normalized_shots.json
subtitle_extract/subtitles.json
subtitle_align/shot_subtitles.json
shot_package/shot_packages.jsonl
shot_understanding/shot_analysis.jsonl
summary_reduce/global_summary.json
outline_plan/outline.json
chapter_write/chapters_index.json
static_render/index.html
```

## 输出

```txt
outputs/{project_id}/qa/qa_report.json
```

## qa_report.json 契约

```json
{
  "status": "passed",
  "summary": {
    "checks_total": 8,
    "passed": 8,
    "warnings": 0,
    "errors": 0
  },
  "checks": [
    {
      "name": "json_parse",
      "status": "passed",
      "message": "All JSON and JSONL files are valid."
    }
  ],
  "warnings": [],
  "errors": []
}
```

状态枚举：

| 状态 | 含义 |
| --- | --- |
| `passed` | 无阻断错误 |
| `warning` | 有非阻断问题 |
| `failed` | 有阻断错误 |

## 检查项

| 检查名 | 规则 |
| --- | --- |
| `json_parse` | 所有 JSON / JSONL 可解析 |
| `stage_outputs` | `run_state.json` 标记完成的阶段产物存在 |
| `shot_refs` | 字幕、镜头包、镜头分析引用的 `shot_id` 存在 |
| `outline_refs` | `outline.json` 章节引用的镜头存在 |
| `chapter_refs` | 章节引用镜头属于对应 outline 章节 |
| `chapter_subsection_body` | 有 `subsections` 的章节正文包含所有小节标题，且顺序与 outline 一致 |
| `chapter_subsection_refs` | 小节级正文生成的引用不越过所在章节；如保存小节级中间结果，则还需校验不越过所在小节 |
| `image_paths` | 关键帧和章节代表图存在 |
| `render_outputs` | `index.html` 存在，PDF 按配置检查 |
| `empty_content` | 章节正文和摘要不应为空 |

## 可选自动修复

低风险修复可放在 `--fix` 模式：

- 从章节其他镜头中重新选择存在的代表图。
- 移除 outline 中不存在的 `shot_id`。
- 重新生成 `chapters_index.json`。
- 对缺失 PDF 只降级为 warning。

不建议自动修复：

- 重写正文。
- 重写目录。
- 重跑模型。
- 修改上游镜头切分结果。

## 实现任务

- 实现 JSON / JSONL 通用校验器。
- 建立全局 `shot_id` 索引。
- 检查每个阶段的关键产物。
- 检查引用链：shot -> package -> analysis -> outline -> chapter -> render。
- 对有 `subsections` 的章节，检查最终 `body_markdown` 是否保留所有小节标题和顺序。
- 检查章节 warning 中的 `oversized_subsection` 等写作分批降级信息，并作为非阻断 warning 汇总。
- 输出结构化 `qa_report.json`。
- 根据错误严重程度决定 CLI exit code。
- 更新 `run_state.json`。

## CLI

只检查：

```powershell
python -m video2visualpage qa --project outputs\demo_20260623_200000
```

检查并做低风险修复：

```powershell
python -m video2visualpage qa --project outputs\demo_20260623_200000 --fix
```

## CLI 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | passed 或 warning |
| 1 | failed |
| 2 | QA 自身运行错误 |

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 上游阶段未运行 | 记为 warning 或 error，取决于是否影响最终 HTML |
| 某 JSON 损坏 | 记为 error |
| HTML 缺失 | 记为 error |
| PDF 缺失 | 如果配置要求 PDF，记 warning；否则忽略 |
| QA 自身异常 | 写最小 `qa_report.json` 并返回退出码 2 |

## 验收标准

- `qa_report.json` 存在且可解析。
- 能准确统计 warnings 和 errors。
- JSON 损坏时能定位具体文件。
- 不存在的 `shot_id` 引用能被发现。
- `run_state.json` 中 `11_qa` 为 `done`，除非 QA 自身失败。

## 测试建议

- 构造完整通过的 fixture。
- 构造损坏 JSON fixture。
- 构造 outline 引用不存在 shot 的 fixture。
- 构造有 `subsections` 但章节正文缺失小节标题的 fixture。
- 构造 `oversized_subsection` warning fixture，确认 QA 只报告 warning 不自动重写正文。
- 构造章节图片缺失 fixture。
- 单测 `--fix` 只做低风险修复。

