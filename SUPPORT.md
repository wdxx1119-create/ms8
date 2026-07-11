# MS8 Support

MS8 当前处于 Alpha 阶段。项目欢迎可复现的问题报告和明确的改进建议，但暂不提供有响应时间承诺的商业支持。

## 选择正确渠道

| 情况 | 渠道 |
|---|---|
| 可复现错误、回归、崩溃 | GitHub Issue：Bug report |
| 文档错误或缺失 | GitHub Issue：Documentation issue |
| 明确的功能建议 | GitHub Issue：Feature request |
| 使用方法、方案讨论、经验分享 | [GitHub Discussions](https://github.com/wdxx1119-create/ms8/discussions) |
| 安全漏洞、密钥泄漏、权限绕过、隐私问题 | GitHub Security Advisory |
| 发布版本变化 | `CHANGELOG.md` |

安全问题不得通过公开 Issue、PR 或 Discussion 报告。具体流程见 [SECURITY.md](SECURITY.md)。

## 提交问题前

先运行：

```bash
ms8 version
ms8 doctor
ms8 dashboard
```

连接问题再运行：

```bash
ms8 connect list-targets
ms8 connect verify --target <tool>
```

安装问题请确认：

- Python 版本为 3.10–3.13
- 使用的操作系统和版本
- 安装方式：PyPI、wheel 或 editable install
- 是否位于虚拟环境中
- 源码路径是否包含空格

常见问题见 [docs/FAQ.md](docs/FAQ.md)。

## 有效问题报告

请提供：

- MS8 版本或 commit SHA
- Python 和操作系统版本
- 安装方式
- 最小复现步骤
- 预期行为和实际行为
- 已脱敏的错误栈或 `ms8 doctor` 输出
- 最近是否修改了配置、数据目录或 MCP 客户端

不要提供：

- API Key、Token、密码、恢复码
- 真实个人记忆或对话
- 身份证件、银行卡、电话、邮箱等 PII
- 未授权的客户或公司资料
- 完整 `MS8_HOME`、备份或生产日志
- 未修复漏洞的公开利用代码

## 支持范围

项目会优先处理：

- 当前 `0.2.x` 版本线和 `main`
- Python 3.10–3.13
- macOS、Linux、Windows
- 官方 CLI、MCP 连接、治理管道、Absorb 和本地存储流程
- 能够在干净环境中复现的问题

以下内容通常按 best-effort 处理：

- 已停止支持的旧版本
- 修改过的第三方分支或私有补丁
- 未列入支持目标的 MCP 客户端
- 第三方模型、网络服务或系统依赖自身的问题
- 无法提供脱敏复现信息的问题

## 响应说明

维护者会根据严重程度、可复现性、用户影响和当前路线图安排处理。创建 Issue 不表示一定接受功能或承诺发布时间。

Alpha 阶段可能发生接口、配置或数据结构变化。破坏性变化应尽量在 CHANGELOG、迁移说明和发布说明中记录。
