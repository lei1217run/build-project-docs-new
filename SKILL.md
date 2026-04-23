---
name: build-project-docs-new
description: 为项目构建分层式 LLM 友好文档体系的重构版。用于需要生成/增量更新 .claude 文档、可恢复进度、基于 IR 渲染与可验证输出的场景。
metadata:
  author: lei1217run
  originalAuthor: zhairuitao
  originalProject: https://github.com/zrt-ai-lab/opencode-skills/tree/main/build-project-docs
  refactorsFrom: build-project-docs
  version: "0.1"
  language: zh-CN
compatibility: 需要 Python 3.10+（标准库）；如需读取 YAML 配置文件需额外安装 PyYAML
allowed-tools: Bash Read Write Edit Glob Grep
---

# build-project-docs-new

## 这份 SKILL.md 给谁看

- 给调用方（Claude Code / Hermes / OpenCode 等 agent 与调度器）：提供稳定的“输入/输出/边界/错误模型”契约
- 给维护者：提供可追溯的行为约束（并发、幂等、副作用）以便长期演进不跑偏

## 运行模式矩阵

| mode | 输入 | 主要产物 | verify 差异 |
|------|------|----------|-------------|
| docs | `--repo-root` | `.claude/CLAUDE.md`、`.claude/docs/{module}/...`、`_ir/*`、`_progress.json` | 校验 CLAUDE 链接、模块 README 约束、安全与链接规则 |
| new-project | `--repo-root` + `--prd` + `--stack` | docs 模式产物 + `docs/_task-list.md` + PRD/Plan IR | 额外强制 task-list 存在且在 CLAUDE 可达；API 模块要求 `dev-checklist.md` |

## CLI 接口

### init

用于生成可选的配置文件（默认 JSON），并写入 `integration.profile` 以支持多 agent 适配。

必填：
- `--repo-root <path>`

可选：
- `--profile generic|claude-code|hermes|opencode`（默认 `generic`）
- `--format json|yaml`（默认 `json`）
- `--force`（覆盖已有配置文件）

### run

必填：
- `--repo-root <path>`

可选：
- `--mode auto|docs|new-project`（默认 `auto`，由配置决定）
- `--config <path>`（默认优先使用 `<repo-root>/build-project-docs-new.yaml`，不存在则尝试 `<repo-root>/build-project-docs-new.json`；支持 json/yaml）
- `--prd <path|text>`（仅 new-project 必需）
- `--stack spring|fastapi|nestjs|go|rust|react|vue`（仅 new-project，默认 `spring`）
- `--stage <stageId>`（细粒度；例如 `docs-7`）
- `--module <moduleId|displayName>`（细粒度；用于仅处理/校验指定模块）
- `--skip-verify`（跳过末尾 verify）
- `--output-rootdir <dir>`（覆盖输出根目录，默认 `.claude`）
- `--output-indexfile <file>`（覆盖入口文件名，默认 `CLAUDE.md`）
- `--agent-id <id>`（写入 progress 的 runIdentity，便于多智能体追溯）

### verify

必填：
- `--repo-root <path>`

可选：
- `--mode auto|docs|new-project`（默认 `auto`：优先读 `_progress.json` 的 mode）
- `--module <moduleId|displayName>`（仅校验指定模块范围）
- `--config <path>`
- `--output-rootdir <dir>`
- `--output-indexfile <file>`

### doctor

用于输出当前运行环境能力（git/pyyaml/输出路径等）与推荐 profile 的结构化报告。

必填：
- `--repo-root <path>`

可选：
- `--config <path>`
- `--mode auto|docs|new-project`
- `--output-rootdir <dir>`
- `--output-indexfile <file>`

## 配置来源与优先级

- 优先级：CLI > YAML/JSON 配置文件 > Env
- Env keys：
  - `BPD_NEW_OUTPUT_ROOTDIR`
  - `BPD_NEW_OUTPUT_INDEXFILE`
  - `BPD_NEW_SECURITY_REDACTIONMODE`
  - `BPD_NEW_VERIFICATION_FAILONWARNINGS`

## 副作用边界

- 写入目录：`output.rootDir`（默认 `.claude`）
- 关键文件：
  - `CLAUDE.md`
  - `docs/_ir/*`、`docs/{module}/*.md`
  - `docs/_progress.json`
  - `docs/_progress.lock`（仅运行期间持有）
- 不会删除任何文件；相同内容不会重复写入（幂等/增量）。

## 并发与编排建议

- 同一 repoRoot + outputRoot 不允许并行执行；以 `docs/_progress.lock` 做互斥。
- 若获取锁失败：返回结构化 JSON（`errorCode=PROGRESS_LOCKED`），建议切换 `--output-rootdir` 或串行编排。
- 在不支持 `fcntl` 的平台，锁采用锁文件兜底；会在启动时进行 holder 存活检测回收；若无法判断则使用租约到期回收（`progress.lockLeaseSeconds`，默认 900 秒）。

## Changelog 行为边界

- `changelog.enabled=true` 且环境可用 git 且 repoRoot 为 git 仓库：生成/更新各模块 `CHANGELOG.md`
- 环境缺 git 或 repoRoot 非 git 仓库：`docs-7` 自动 skipped，并在 `_progress.json` 的 `docs-7.notes` 写明原因（docs 仍可生成）

## 失败与排错

- `CONFIG_YAML_REQUIRES_PYYAML`：使用 yaml 配置但缺少 PyYAML
- `PYTHON_TOO_OLD`：Python 版本低于 3.10
- `REPO_ROOT_INVALID`：repoRoot 不是本地目录路径
- `OUTPUT_NOT_WRITABLE` / `OUTPUT_IO_ERROR`：输出目录不可写或 IO 异常
- `PROGRESS_LOCKED`：另一个进程正在写入 progress
- `CONFIG_EXISTS` / `CONFIG_NOT_WRITABLE`：init 写配置失败
- `PRD_REQUIRED`：new-project 缺少 `--prd`
- `INVALID_STAGE` / `MODULE_NOT_FOUND`：编排参数不合法
- `PERMISSION_DENIED` / `OS_ERROR`：运行期权限或系统异常（通常与 sandbox/路径策略相关）

更多契约与 schema：见 `docs/` 与 `schemas/`。

## 兼容性矩阵（通过 profile 机制收敛差异）

目标：不绑定单一 agent；由调用方选择 profile，或先 `doctor` 探测环境后再 `init` 固化配置。

| profile | 适用场景 | 关键差异点 |
|--------|----------|------------|
| generic | 默认；未知/混合环境 | 保守依赖：git 缺失时 changelog 自动 skipped；以结构化错误码输出初始化失败 |
| claude-code | Claude Code 挂载 skill | 默认输出 `.claude/**`；强调 `.claude` 结构契约与可验证输出 |
| hermes | Hermes 挂载 skill | 通过 `doctor` 输出能力报告；在受限环境下依赖降级（无 git 时 changelog skipped） |
| opencode | OpenCode 挂载 skill | 同上；以 profile 固化差异，避免在 README/docs 中散落平台细节 |
