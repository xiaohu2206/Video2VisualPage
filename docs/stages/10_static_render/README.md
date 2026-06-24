# 10 静态渲染开发文档

## 目标

把目录和章节 JSON 渲染为可直接打开的静态 HTML，并可选导出 PDF。HTML 是主产物，PDF 是副产物，PDF 失败不应影响 HTML 成功。

## 输入

```txt
outputs/{project_id}/outline_plan/outline.json
outputs/{project_id}/chapter_write/chapters_index.json
outputs/{project_id}/chapter_write/chapter_*.json
outputs/{project_id}/init/config.json
```

## 输出

```txt
outputs/{project_id}/static_render/index.html
outputs/{project_id}/static_render/report.pdf
outputs/{project_id}/static_render/assets/
```

## HTML 内容结构

建议结构：

```txt
标题
摘要
目录
章节列表
  章节标题
  代表镜头图片
  正文
  关键点
  涉及镜头列表
附录
  生成信息
  产物引用
```

## 渲染原则

- 不需要前端框架。
- HTML 应能离线打开。
- 图片资源应复制到 `static_render/assets/images/`，避免引用上游目录导致移动产物后失效。
- CSS 写入 `static_render/assets/style.css`。
- 相对路径从 `index.html` 所在目录出发。
- PDF 从 HTML 渲染得到。

## 模板建议

```txt
video2visualpage/templates/
  report.html
  style.css
```

如果暂不引入模板引擎，可以先用 Python 标准库字符串模板；后续可替换为 Jinja2。

## 资源复制规则

输入：

```txt
shot_split/keyframes/shot_000001_fifth_1_20.jpg
```

输出：

```txt
static_render/assets/images/shot_000001_fifth_1_20.jpg
```

HTML 引用：

```html
<img src="./assets/images/shot_000001_fifth_1_20.jpg" alt="chapter_001" />
```

## 实现任务

- 读取 `outline.json` 和 `chapters_index.json`。
- 加载每个 `chapter_*.json`。
- 复制章节代表图到 `assets/images/`。
- 渲染 `assets/style.css`。
- 渲染 `index.html`。
- 可选使用 Playwright、wkhtmltopdf 或浏览器打印生成 `report.pdf`。
- PDF 失败时写入 `errors.jsonl`，但保留 HTML。
- 更新 `run_state.json`。

## CLI

只生成 HTML：

```powershell
python -m video2visualpage render --project outputs\demo_20260623_200000 --format html
```

生成 HTML 和 PDF：

```powershell
python -m video2visualpage render --project outputs\demo_20260623_200000 --format html,pdf
```

## 异常处理

| 场景 | 处理 |
| --- | --- |
| 某章节 JSON 缺失 | 跳过该章并写 warning |
| 图片缺失 | 渲染占位区域并写 warning |
| HTML 渲染失败 | 阶段失败 |
| PDF 渲染失败 | 阶段仍可成功，但 `report.pdf` 不写入 outputs |
| CSS 模板缺失 | 使用内置最小样式 |

## 验收标准

- `index.html` 存在。
- HTML 中章节数量与有效章节数量一致。
- 章节图片相对路径可访问，或有明确缺失标记。
- `assets/style.css` 存在。
- 配置要求 PDF 时，成功生成 `report.pdf` 或记录 PDF 错误。
- `run_state.json` 中 `10_static_render` 为 `done`。

## 测试建议

- 用 fixture 章节 JSON 渲染 HTML 快照。
- 单测图片复制和相对路径生成。
- 单测章节缺失时的 warning。
- 如果实现 PDF，加入 PDF 生成失败的降级测试。
- 用浏览器打开 `index.html` 做一次人工视觉验收。

