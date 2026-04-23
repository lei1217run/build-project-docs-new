# 输出契约（Contract v1）

本文件定义 build-project-docs-new 的默认输出结构与关键行为边界（面向消费端/调度器/agent 的稳定契约）。

术语：

- 必须：不满足即视为失败（verify 可阻断 / 调度器不可认为执行完成）
- 可选：不影响正确性，仅用于提升可读性或可追溯性

## 1. 默认输出（必须）

- 输出根目录：`.claude/`
- 主索引文件：`.claude/CLAUDE.md`
- 模块文档目录：`.claude/docs/{module}/`

## 2. 产物集合（必须）

- `.claude/CLAUDE.md`
- `.claude/docs/{module}/README.md`
- `.claude/docs/{module}/CHANGELOG.md`
- 进度文件：
  - 事实载体：`.claude/docs/_progress.json`
  - 可选人类视图：`.claude/docs/_progress.md`

对外暴露 API 的模块必须有：
- `.claude/docs/{module}/api-*.md`（至少一个）
- `.claude/docs/{module}/data-model.md`
- 文档模式：`.claude/docs/{module}/pitfalls.md`
- 新项目模式：`.claude/docs/{module}/dev-checklist.md`

## 3. 行数阈值（必须）

- `.claude/CLAUDE.md`：≤ 150 行
- `.claude/docs/{module}/README.md`：≤ 200 行
- 超过 250 行必须拆分

## 4. 链接约束（必须）

- `CLAUDE.md` 必须链接到每个模块 docs（相对路径）
- 模块 `README.md` 必须链接到目录下所有子 md（除 README/CHANGELOG）
- 禁止绝对路径链接

## 5. 安全基线（必须）

- 禁止把任何明文凭据写入文档
- “测试凭据”只能写获取方式/变量名/脱敏示例

## 6. progress 并发与写入语义（必须）

- 进度文件：`.claude/docs/_progress.json`
- 写入策略：文件锁（`_progress.lock`）
  - 同一 repoRoot + outputRoot 的同一时刻只允许一个进程写入 progress
  - 获取锁失败必须失败退出，并输出结构化 JSON（包含 errorCode / reason / hint）
- 追溯信息：`_progress.json` 必须包含 `runIdentity`（hostname/pid/startedAt，可选 agentId）
