# 本周更新 - 2026-06-27

## 本周范围

本周的 macOS 端集成工作主要集中在两块之前只存在于本地混合树中的能力：

- 本地 absorb / project-memory 的增强能力
- 面向日常 Agent 使用的更完整 MCP 记忆面

这次工作的目标不是重做 MS8，而是把已经在本地验证过的增强能力安全地补回干净的 macOS 源码树，同时保持运行面可测试、可发布、可继续演进。

## 本地 Absorb 增强

现在干净的 macOS 工作区已经具备以下 absorb 侧增强能力：

- authorized-root 初始化与更稳妥的本地 absorb 仓库行为
- 更丰富的解析覆盖：Markdown、文本、JSON、代码注释、DOCX、扫描 PDF 以及 OCR 回退路径
- search / review CLI 增强，包含更清晰的下一步操作提示
- autosubmit 的启停、分层、dry-run、回滚预览与批量 review 流程
- project-memory 的 scan、index、build、submit、watch、search、health 与 service 运行辅助面
- 后台 service、前台 watch、手工 fallback 三种运行方式之间的 runtime 模式标准化

这次对 project-memory 采用整块增强方式接入，而不是零散拼接，目的是让 repository、scanner、parser、health、scope、submit、search、watch 和 CLI 面保持一致，避免重新引入旧的弱路径。

## MCP 全量记忆面增强

当前干净的 macOS 工作区已经带上了项目分支所需的更完整 MCP 记忆面，而不是旧版偏兼容层的窄接口。

实际效果主要体现在：

- connect / status 路径能更准确反映当前 runtime 与 health 分层
- MCP 面向状态输出时，已经和新的 self-check、health-card、runtime-report 结构对齐
- 日常记忆操作可以依赖更完整的上下文面，而不是缩减版兼容路径

这部分工作是通过对照项目中的更强实现，将兼容功能块补强到干净 macOS 树上完成的，没有改动项目参考源本身。
