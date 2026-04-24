# 验证规则集 v1（Spec）

本文件定义验证规则集 v1（含 blocking/warning）。

术语：

- blocking：失败时视为不可用（run 输出 ok=false；progress 最终阶段不得为 done）
- warning：可用但需关注（不影响 ok，但应在 report 中呈现）

## blocking

以下规则失败时：progress 不允许进入完成态（不得标记 done / 不得删除进度事实文件）。

- V1-STRUCT-001：默认输出根目录存在且可写（默认 `.claude/`）
- V1-STRUCT-002：`CLAUDE.md` 存在且行数 ≤ 150
- V1-STRUCT-003：每个模块必须有 `.claude/docs/{module}/README.md`
- V1-STRUCT-004：`CLAUDE.md` 必须链接到 `docs/_modules.md`
- V1-STRUCT-005：模块 README 必须链接到目录下所有子 md（除 README/CHANGELOG）
- V1-STRUCT-006：`docs/_modules.md` 必须存在并覆盖每个模块 `README.md`
- V1-API-001：若 IR `hasPublicApi=true`，则必须存在 `api-*.md`（至少 1 个）
- V1-API-002：若 IR `hasPublicApi=true`，则必须存在 `data-model.md`
- V1-API-003：文档模式：若 IR `hasPublicApi=true`，则必须存在 `pitfalls.md`
- V1-API-004：新项目模式：若 IR `hasPublicApi=true`，则必须存在 `dev-checklist.md`
- V1-LINK-001：解析 Markdown 链接语法，目标路径必须可达
- V1-LINK-002：禁止绝对路径链接
- V1-CHANGELOG-002：禁止占位符（TBD/需查看/placeholder 等）
- V1-LAYER-001：若 module IR 的 `layerTags` 含非 `unknown` 值，则必须存在 `module.extensions.layerEvidence`
- V1-SEC-002：检测到高置信明文凭据必须阻断

## warning（v1）

- V1-CONSIST-001：README 的 API 索引条目应能在 api-*.md 中定位（按 signature 匹配）
- V1-CONSIST-002：api-*.md 引用的数据结构名称应在 data-model.md 或 IR 中存在
- V1-CHANGELOG-001：模块 CHANGELOG.md 存在（Phase C 后升级为 blocking）
- V1-SEC-001：检测到低置信 secret 关键词（如 `token/password` 概念词）应在 report 中提示，但不直接阻断

## 证据来源约定

- IR 证据：`ir/project.json`、`ir/modules/*.json`
- 渲染产物证据：`.claude/**.md`
