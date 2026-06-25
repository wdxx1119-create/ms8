# Project Memory

`project_memory` 是 MS8 `absorb` 体系下的项目理解子模块。

它的定位很简单：

- `ms8` 是总系统，负责主记忆、治理、检索与注入。
- `absorb` 是本地资料吸收层，负责把授权目录中的内容安全扫描、解析、索引和审查。
- `project_memory` 是 `absorb` 下专门面向“大项目理解”的一层，负责把一个项目整理成 AI 可直接使用的上下文。

## 它和 Absorb 的关系

`project_memory` 不是替代 `absorb`，而是建立在 `absorb` 底座之上的专项能力。

- `absorb` 更像通用摄取管线。
- `project_memory` 更像项目级解释器。

前者回答“收什么、怎么收、能不能进系统”，后者回答“如何让 AI 快速理解这个项目”。

## 它实际做什么

在项目被注册后，`project_memory` 会围绕该项目生成一组高信号产物，例如：

- `AI_CONTEXT.md`
- `project_summary.md`
- `reading_order.json`
- `relations.jsonl`
- `hot_files.json`
- `code_index.json`

这些产物的目标不是单纯存档，而是帮助 AI 在本地就能建立更完整的项目认知。

## 新增能力

当前这一版又补了两件更偏“长期运行”的能力：

- 多项目 service 编排：
  现在不只是单项目可装 watcher service，也可以按“所有已注册项目”批量安装、查看状态和移除。
- 更细粒度的增量 build：
  `project_memory` 会记录 build 状态与文件级缓存，优先只重算受影响文件，而不是每次把整个项目分析全量重跑。

这两点的目标分别是：

- 让多个项目能一起长期挂着跑。
- 让大项目在持续更新时，构建速度更稳定、重复成本更低。

## 它和主记忆的关系

`project_memory` 可以把项目摘要提交到 MS8 主记忆，但仍然走主系统的治理写入链路。

这意味着：

- 它不会绕过主记忆规则直接写库。
- 它提交的内容可以被标记来源、审计和回滚。
- 它更适合写入“项目摘要”这类高价值信息，而不是把整份项目原文塞进长期记忆。

## 一句话总结

`absorb` 负责把资料安全吸进来，`project_memory` 负责把大型项目整理成 AI 真正能读懂、能用上的本地上下文。
