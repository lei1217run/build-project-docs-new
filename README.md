# build-project-docs-new

这是一个用于生成/增量更新 `.claude/**` 项目文档的技能实现（支持 docs / new-project 两种模式），并提供可恢复进度与可验证输出。

## 这份 README 给谁看

- 给仓库/skill 的使用者：知道它是什么、入口在哪里、去哪里看完整使用说明

## 如何使用（统一入口）

- 完整、唯一的“使用动作合集”（参数、示例、输出、并发、错误码、排错）统一放在 [SKILL.md](file:///Users/clawbot/data/opencode-skills/build-project-docs-new/SKILL.md)。

## 目录结构

- 入口脚本：`scripts/bpd_new.py`
- 契约与规则：`docs/*.md`
- JSON Schema：`schemas/*.schema.json`

## 原始项目信息

- 原作者：zhairuitao
- 原始项目（GitHub）：https://github.com/zrt-ai-lab/opencode-skills/tree/main/build-project-docs
