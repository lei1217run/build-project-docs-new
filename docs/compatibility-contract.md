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
- `.claude/docs/_modules.md`
- `.claude/docs/{module}/README.md`
- `.claude/docs/{module}/CHANGELOG.md`
- 进度文件：
  - 事实载体：`.claude/docs/_progress.json`
  - 可选人类视图：`.claude/docs/_progress.md`（由 `_progress.json` 同步生成；不是事实源，可从 `_progress.json` 重建）

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

- `CLAUDE.md` 必须链接到 `docs/_modules.md`（相对路径）
- `docs/_modules.md` 必须覆盖每个模块 `README.md` 的链接
- 模块 `README.md` 必须链接到目录下所有子 md（除 README/CHANGELOG）
- 禁止绝对路径链接

## 5. 安全基线（必须）

- 禁止把任何明文凭据写入文档
- “测试凭据”只能写获取方式/变量名/脱敏示例
- 高置信明文泄漏（如私钥/JWT/明显密码赋值）必须阻断或按 `security.redactionMode=redact` 脱敏
- 低置信关键词（如文档中的 `token/password` 概念词）不应直接阻断

## 6. progress 并发与写入语义（必须）

- 进度文件：`.claude/docs/_progress.json`
- 写入策略：文件锁（`_progress.lock`）
  - 同一 repoRoot + outputRoot 的同一时刻只允许一个进程写入 progress
  - 获取锁失败必须失败退出，并输出结构化 JSON（包含 errorCode / reason / hint）
- 追溯信息：`_progress.json` 必须包含 `runIdentity`（hostname/pid/startedAt，可选 agentId）
- `_progress.md`：
  - 生成：每次写入 `_progress.json` 时同步生成
  - 语义：仅作为人类可读摘要视图，不参与增量判定与 verify 的 blocking 口径

## 6.1 docs 阶段语义（兼容声明）

- 阶段集合保持 `docs-1..docs-8` 不变（便于编排与审计）。
- `docs-6` 在 v1 中弃用并固定为 `skipped`：
  - 语义：原“配置层文档”能力合并进 `docs-5` 的模块 facts（存在配置证据时生成 `facts-config.md`）
  - 可观测性：`_progress.json` 的 `docs-6.notes` 会写明 “deprecated/merged” 以避免误判

## 7. 结构事实输入（Manifest，可选）

当 marker/浅扫描无法稳定表达模块边界时，可显式提供 manifest 覆盖 discovery 输出。

- 配置：
  - `discovery.strategy=manifest`：必须存在 manifest，否则失败并返回结构化错误码
  - `discovery.strategy=manifest-first`：优先 manifest；缺失时回退 `default`，并在 `_progress.json` 的 `docs-1.notes` 记录“回退事实”
  - `discovery.manifestPath`：manifest 相对路径（默认 `build-project-docs-new.manifest.json`）
- Manifest v1（JSON）最小结构：
  - `modules[]`：每项包含 `displayName`（string）与 `roots[]`（相对 repoRoot 的路径数组）
  - 可选：`deps[]`（string[]，以目标模块 `displayName` 引用）、`signals[]`（{name,value}[]）、`extensions.layerHints[]`（string[]）
  - 禁止：`layerTags`（事实必须保持 unknown，由 extractor 证据生成）
- 错误码（稳定）：
  - `MANIFEST_REQUIRED`：strategy=manifest 时缺失 manifest 文件
  - `MANIFEST_PATH_INVALID`：manifestPath 非法（绝对路径/越界/包含 `..`）
  - `MANIFEST_ROOT_INVALID`：roots 非法或不存在（绝对路径/越界/包含 `..`/路径不存在）
  - `MANIFEST_INVALID`：JSON/结构非法或包含不允许字段
  - `MANIFEST_DEP_NOT_FOUND`：deps 引用不存在的模块 displayName
