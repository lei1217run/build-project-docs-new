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
| docs | `--repo-root` | `.claude/CLAUDE.md`、`.claude/docs/{module}/...`、`_ir/*`、`_progress.json`、`_progress.md` | 校验 CLAUDE 链接、模块 README 约束、安全与链接规则 |
| new-project | `--repo-root` + `--prd` + `--stack` | docs 模式产物 + `docs/_task-list.md` + PRD/Plan IR | 额外强制 task-list 存在且在 CLAUDE 可达；API 模块要求 `dev-checklist.md` |

## CLI 接口

### init

用于生成可选的配置文件（默认 JSON），写入 `integration.profile` 字段以支持多 agent 适配。

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

## 策略（静态注册的“插件化”）

说明：

- v1 的“插件化”落地形态为静态注册策略（static registry），而不是动态插件加载。
- 可通过配置选择三类策略（未配置则使用默认）：
  - `discovery.strategy`：模块发现策略
  - `extractor.strategy`：模块抽取策略
  - `incremental.evidenceStrategy`：增量证据策略（evidenceHash）
- 若选择未知策略：`run` 会失败并返回结构化错误码 `STRATEGY_NOT_FOUND`，并在 `details.available` 给出可用策略列表。

当前内置策略（v1）：

- discovery：`default`、`manifest`、`manifest-first`
- extractor：`default`
- evidence：`mtime-v1`（同时提供别名 `default`）

配置示例（JSON）：

```json
{
  "discovery": { "strategy": "default", "maxDepth": 1 },
  "extractor": { "strategy": "default" },
  "incremental": { "enabled": true, "evidenceStrategy": "mtime-v1" }
}
```

manifest 覆盖 discovery（可选）：

- `discovery.strategy=manifest`：模块列表完全来自 manifest；缺失 manifest 会失败（结构化错误码）
- `discovery.strategy=manifest-first`：优先 manifest；缺失时回退 `default`，并在 `_progress.json` 的 `docs-1.notes` 记录回退事实
- `discovery.manifestPath`：manifest 文件相对路径（默认 `build-project-docs-new.manifest.json`）
- manifest `deps[]`：以目标模块 `displayName` 引用；输出写入 IR 时会解析为目标模块 `moduleId`

查看生效策略：

- `doctor` 输出包含 `strategies.discovery/extractor/evidence`。

## 副作用边界

- 写入目录：`output.rootDir`（默认 `.claude`）
- 关键文件：
  - `CLAUDE.md`
  - `docs/_ir/*`、`docs/{module}/*.md`
  - `docs/_progress.json`
  - `docs/_progress.md`（与 `_progress.json` 同步生成；仅视图）
  - `docs/_progress.lock`（仅运行期间持有）
- 不会删除任何文件；相同内容不会重复写入（幂等/增量）。

## 并发与编排建议

- 同一 repoRoot + outputRoot 不允许并行执行；以 `docs/_progress.lock` 做互斥。
- 若获取锁失败：返回结构化 JSON（`errorCode=PROGRESS_LOCKED`），建议切换 `--output-rootdir` 或串行编排。
- 在不支持 `fcntl` 的平台，锁采用锁文件兜底；会在启动时进行 holder 存活检测回收；若无法判断则使用租约到期回收（`progress.lockLeaseSeconds`，默认 900 秒）。

## Changelog 行为边界

- `changelog.enabled=true` 且环境可用 git 且 repoRoot 为 git 仓库：生成/更新各模块 `CHANGELOG.md`
- 环境缺 git 或 repoRoot 非 git 仓库：`docs-7` 自动 skipped，并在 `_progress.json` 的 `docs-7.notes` 写明原因（docs 仍可生成）

## 阶段语义（docs 模式）

- docs 模式阶段集合固定为 `docs-1..docs-8`（兼容与可编排）。
- `docs-6` 在 v1 中弃用并固定为 `skipped`：原“配置层文档”能力合并进 `docs-5` 的模块 facts（存在配置证据时生成 `facts-config.md`），并在 `_progress.json` 的 `docs-6.notes` 写明原因。

## 失败与排错

- `CONFIG_YAML_REQUIRES_PYYAML`：使用 yaml 配置但缺少 PyYAML
- `PYTHON_TOO_OLD`：Python 版本低于 3.10
- `REPO_ROOT_INVALID`：repoRoot 不是本地目录路径
- `OUTPUT_NOT_WRITABLE` / `OUTPUT_IO_ERROR`：输出目录不可写或 IO 异常
- `PROGRESS_LOCKED`：另一个进程正在写入 progress
- `CONFIG_EXISTS` / `CONFIG_NOT_WRITABLE`：init 写配置失败
- `PRD_REQUIRED`：new-project 缺少 `--prd`
- `INVALID_STAGE` / `MODULE_NOT_FOUND`：编排参数不合法
- `MANIFEST_REQUIRED` / `MANIFEST_INVALID` / `MANIFEST_PATH_INVALID` / `MANIFEST_ROOT_INVALID` / `MANIFEST_DEP_NOT_FOUND`：manifest discovery 输入不合法
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

## 语言能力矩阵（字段级）

说明：

- 目标：明确“当前版本可以稳定产出的字段”与“尚未实现或弱实现的空位”，便于 agent 按边界消费。
- 判定：
  - ✅ 稳定产出（在该语言典型项目上可直接依赖）
  - ⚠️ 部分产出（仅有启发式/基础信号，覆盖不完整）
  - ❌ 当前空位（未实现专门抽取）

### 模块级 IR / facts 字段

| 字段 | Python | JS/TS | Java | Go | Rust | C# | C++ | 说明 |
|------|--------|-------|------|----|------|----|-----|------|
| `api.hasPublicApi` / `api.domains` | ✅ | ⚠️ | ✅ | ❌ | ❌ | ⚠️ | ⚠️ | 基于模式识别；C# 覆盖 Minimal API 与部分 attribute；C++ 稳定覆盖 proto 的 rpc（grpc），HTTP 不强承诺 |
| `publicSurface.exports` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ⚠️ | C++ 以 `include/**` public headers 作为 exports（文件级） |
| `publicSurface.entrypoints` | ✅ | ✅ | ❌ | ❌ | ❌ | ⚠️ | ⚠️ | C# 覆盖 Main/host signals；C++ 覆盖 `main()` |
| `publicSurface.types` | ✅ | ✅ | ❌ | ❌ | ❌ | ⚠️ | ⚠️ | C# 识别 `public class/struct/interface/enum/record`；C++ 识别公共头内 `class/struct/enum`（词法级） |
| `publicSurface.keyFiles` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 关键词与路径启发式（graph/middleware/config/adapter 等） |
| `module.layerTags` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 分层标签可产出；来源为模块发现与后续证据补充 |
| `module.extensions.layerEvidence` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 路径/文件名规则证据（core/shared/config/application.yml/.env 等） |
| `module.extensions.languageSignals` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 按文件后缀与构建文件统计语言信号 |
| `dataModel.types` | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | 当前主要由 publicSurface.types 回填（多语言均为启发式/弱语义） |

### 文档侧 facts 子文档（模块 README 关联）

| 文档 | 触发条件 | 备注 |
|------|----------|------|
| `facts-overview.md` | 任一 `publicSurface` 列表非空 | 汇总 facets 计数与 language signals |
| `facts-exports.md` | `publicSurface.exports` 非空 | 主要对 Python/JS-TS 有效 |
| `facts-entrypoints.md` | `publicSurface.entrypoints` 非空 | 主要对 Python/JS-TS 有效 |
| `facts-keyfiles.md` | `publicSurface.keyFiles` 非空 | 多语言可用（启发式） |
| `facts-types.md` | `publicSurface.types` 非空 | 主要对 Python/TS 有效 |
| `facts-config.md` | `layerEvidence` 非空 | 多语言可用（规则证据） |

### 项目级字段（跨模块聚合）

| 字段 | 当前能力 | 说明 |
|------|----------|------|
| `workspace.type` | ✅ | 可识别 `uv/poetry/pnpm/yarn/npm/cargo-workspace/go-work/go/maven/gradle/unknown` |
| `workspace.packages[]` | ✅ | 聚合模块 package facts（路径/语言/名称/版本/部分 entrypoints） |
| `entrypoints[]` | ✅ | 汇总模块级 entrypoints；对 Python/JS-TS更完整 |

补充：

- `workspace.type` 将扩展为可识别 `dotnet/cmake`（以 `.sln` / `CMakeLists.txt` 作为信号）。

### 当前空位与使用建议

- Java/Go/Rust 的 `publicSurface.exports/entrypoints/types` 目前为功能空位，不应作为强依赖字段。
- 若场景是 SDK/CLI 能力抽取，优先期待 Python 与 JS/TS 模块的 facts 完整性。
- 若场景是跨语言架构梳理，可稳定依赖 `workspace.*`、`layerEvidence`、`keyFiles`、`languageSignals`，再结合源码做二次判断。
