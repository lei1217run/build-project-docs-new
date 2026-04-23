# 插件接口契约（Contract v1）

本文件定义 build-project-docs-new 的插件接口契约（面向扩展实现与维护者）。核心实现只消费插件产出的统一结构，不接受“隐式约定”。

术语：

- 必须：不满足即属于不兼容（调用方可直接失败）
- 建议：允许缺失，但可能导致降级（生成骨架/跳过）或验证失败

## 概览

- Discovery：repoRoot → module list（moduleId/roots/signals）
- Extractor：module roots → evidence-based surfaces（api/data/config/pitfalls…）
- Template：PRD-IR → planned IR fragments（architecture/tasklist/moduledocs）

核心只消费插件输出并落入 IR；插件不负责渲染，也不负责配置优先级解析。

## 错误模型

- 统一：code/message/severity/recoverable/hints/evidence
- severity：blocking|warning

建议最小字段集：

- `code`：稳定错误码
- `message`：短描述（不得包含敏感信息）
- `severity`：blocking|warning
- `recoverable`：true|false
- `hints[]`：建议动作
- `evidence[]`：可选证据链引用

## Capability Flags

插件必须声明能力，用于：

- 选择阶段/验证规则
- 渲染降级策略（缺失能力时生成骨架或跳过）

capability 建议采用稳定字符串集合，例如：

- `discoversModules`
- `extractsRestApi` / `extractsGrpcApi` / `extractsCliCommands` / `extractsToolApi`
- `extractsDataModel` / `extractsConfigKeys`
- `generatesTaskList` / `generatesDevChecklist`

## 证据链要求

关键结论必须提供 evidence（至少 file evidence），尤其是：

- `hasPublicApi`
- API 条目的 signature 与来源

证据链引用最小字段：

- `kind`：file|symbol|command|pattern
- `path`：相对 repoRoot 的路径
- `symbol`：可选
- `note`：可选

## 输出结构（最小闭环）

### Discovery 输出（ModuleList）

每个 module 必须包含：

- `moduleId`
- `displayName`
- `roots[]`
- `signals[]`（最少表达语言/构建系统信号）
- `suggestedLayerTags[]`（可选）

### Extractor 输出（ModuleExtract）

最小闭环字段：

- `hasPublicApi`：true|false|unknown（必须提供 evidence，不允许仅凭 README 关键词）
- `apiDomains[]`：可为空
  - `domainId`
  - `items[]`（kind/signature/name/evidence…）
- `dataModelTypes[]`：可为空
- `configItems[]`：可为空（支持 sensitive）
- `pitfalls[]`：可为空

### Template 输出（Planned IR Fragments）

最小闭环字段：

- `architecture.projectType`：single|multi|microservices
- `architecture.modulePlan[]`
- `taskList.tasks[]`
- `moduleDocsPlan[]`
