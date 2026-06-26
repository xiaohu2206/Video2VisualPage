# 大模型输入输出监控日志系统

## 目标

所有经过 `LocalModelAdapter` 的模型功能调用都会写入结构化监控日志。监控系统独立放在 `video2visualpage/monitoring/`，运行期输出独立放在项目目录的 `logs/llm_monitor/`，不依赖数据库，也不会因为日志写入失败阻断主流程。

## 覆盖范围

当前已覆盖这些模型功能：

| 功能目录 | 来源功能 |
| --- | --- |
| `shot_understanding` | 单镜头理解、OCR 模型分支 |
| `summary_reduce` | chunk 摘要 |
| `global_outline` | 全局大纲 Agent |
| `chapter_subsections` | 章节二级小节 Agent |
| `chapter_write` | 章节正文写作 |

新增模型功能只要继续通过 `LocalModelAdapter._with_retries()` 或 `_chat_raw()` 调用，就会自动进入监控。

## 输出目录

```txt
outputs/{project_id}/logs/llm_monitor/
1. index.jsonl
2. health.json
3. by_function/{function}/calls.jsonl
4. by_function/{function}/details/{call_id}.json
```

保留兼容日志：

```txt
outputs/{project_id}/logs/llm_calls.jsonl
```

## 两层记录

`function_call` 记录一次业务功能调用，例如分析一个镜头、摘要一个 chunk、写一个章节。它保存：

- 功能分类、模型角色、provider、model
- attempt、retry 信息、耗时
- 结构化输入 payload、输入摘要、输入 hash
- 解析和校验后的输出 result、输出摘要、输出 hash
- `output_ok`、空字段、warnings 数量、错误类型

`provider_call` 记录一次真实远端 `/chat/completions` 请求。它保存：

- 请求 URL、模型请求 body、system prompt、response instruction
- payload 摘要、图片引用、图片是否存在、实际附带图片数量
- 原始模型响应、usage、finish_reason
- HTTP / 网络 / 空输出 / 格式错误等可靠性信号

API key、Authorization、token、secret 字段会脱敏；图片 base64 不会落盘，只记录 data URL 前缀、长度和 hash。

## 稳定性视图

`health.json` 会随着每次记录刷新，按功能聚合：

- 总记录数、成功数、失败数
- `function_call` / `provider_call` 数量
- 平均耗时
- 最近状态和最近错误类型
- success rate

这份文件用于快速判断项目在模型调用层面的稳定性；详细定位再进入对应功能目录的 `calls.jsonl` 和 `details/*.json`。

## 配置

默认配置位于 `config.py`：

```json
{
  "llm_monitoring": {
    "enabled": true,
    "capture_payloads": true,
    "capture_raw_response": true,
    "max_text_chars": 50000
  }
}
```

`max_text_chars` 只限制单个超长字符串，超限时保留预览、原始长度和 SHA256，避免日志变成不可读的大块文本。
