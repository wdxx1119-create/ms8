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

## 今天完成的运行面收口

今天围绕上述增强面，又补上了几项稳定性和可发布性收口：

- `doctor` 对 session-sync 问题已经能给出正确的自修复入口
- project-memory 自检在前台 watch 是预期运行模式时，不再误报假告警
- validation-suite 现在会把空测试集或无效测试载荷判为失败，不再假成功
- `doctor` 退出码已区分“记忆质量退化”和“真正的运行/安全失败”
- 多处过宽异常捕获已收窄到类型化异常
- project-memory 状态文件损坏时，会明确回报 degraded，而不是静默吞错
- absorb 仓库初始化路径已正确关闭 SQLite 句柄
- Ollama provider 探测路径已正确关闭 HTTP 响应
- service 启动命令改为绑定当前 Python 环境，避免后台任务误落到 PATH 中的旧 `ms8`
- 默认 adapter 模板已恢复为可移植形态，不再写入本机绝对路径

## 验证结果

本次增强和收口完成后，已按仓库默认测试配置做验证。

- `python3 -m pytest`
- 结果：`1255 passed, 5 skipped`
- 覆盖率：`79.89%`

在补齐资源关闭和运行入口收口后，完整测试最后一轮为 `4 warnings`，相比此前明显收敛。

## 后续跟进

当前剩余工作主要是告警与细节层面的继续打磨，不是功能阻断：

- 继续压缩 warning 面
- 继续围绕 doctor / watch / runtime 的解释与后续动作做闭环
- 持续补强 project-memory、connect、service、watch 的日常可用性

## 推送准备状态

当前分支已经进入可整理、可评审、可推送的状态：

- 本周增强功能块已落到干净 macOS 树
- 回归测试在仓库默认设置下通过
- 剩余工作以稳定性打磨和体验收口为主，不是红线故障
