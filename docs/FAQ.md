# MS8 FAQ

## MS8 是什么？

MS8 是一个 local-first 的本地记忆引擎，用于写入、检索、治理和维护长期记忆，并可通过 MCP 连接多个 AI 工具。

## MS8 会把我的记忆上传到云端吗？

核心记忆默认保存在本地 `MS8_HOME` 目录中，默认路径为 `~/.ms8/`。云端或本地模型能力是可选增强；是否调用外部模型取决于你的配置和所使用的 Provider。

在启用任何外部模型前，应检查 Provider 配置、隐私政策和将要发送的内容。不要把密钥、PII 或未授权资料放进公开日志和 Issue。

## 没有 Ollama 或本地模型还能使用吗？

可以。基础记忆和规则检索能力仍可运行；依赖模型的语义增强能力可能自动降级。使用下面的命令检查当前状态：

```bash
ms8 doctor
ms8 llm status
```

## 数据保存在哪里？

默认保存在：

```text
~/.ms8/
```

常见内容包括记忆记录、索引、知识图谱、备份、健康报告、MCP 连接状态和 Absorb 本地索引。

可使用环境变量覆盖路径：

```bash
export MS8_HOME=~/custom_path
export MS8_DATA_DIR=~/custom_data
export MS8_CONFIG_DIR=~/custom_config
export MS8_LOG_DIR=~/custom_logs
```

## 如何备份和迁移？

先创建备份：

```bash
ms8 backup
```

迁移时停止正在访问 MS8 的进程，完整复制 `MS8_HOME` 目录到新设备，并保持适当的文件权限。迁移完成后运行：

```bash
ms8 doctor
ms8 dashboard
```

不要把包含真实记忆、密钥或 PII 的备份提交到 Git 仓库。

## 如何完全卸载？

先执行：

```bash
ms8 uninstall
```

卸载 Python 包不会自动表示你希望删除本地记忆。删除 `MS8_HOME` 前请先备份，并确认目录中没有需要保留的数据。

## `ms8 doctor` 出现 `warn` 是否代表故障？

不一定。部分告警表示功能降级、可选组件未安装或当前没有足够质量样本。例如趋势捕获没有有效样本时可能出现可解释告警。

排查顺序：

```bash
ms8 version
ms8 doctor
ms8 dashboard
```

若仍无法判断，可创建 Bug Issue，并只提供已脱敏的诊断信息。

## MCP 连接失败怎么办？

先确认目标名称和当前状态：

```bash
ms8 connect list-targets
ms8 connect verify --target <tool>
ms8 doctor
```

需要重新生成配置时：

```bash
ms8 connect generate --target <tool>
ms8 connect apply --target <tool>
ms8 connect verify --target <tool>
```

完整流程见 [`src/ms8/connect/CONNECT_GUIDE.md`](../src/ms8/connect/CONNECT_GUIDE.md)。在提交日志前删除用户名、绝对路径、令牌和记忆内容。

## Windows、macOS 和 Linux 有什么差异？

MS8 支持 Python 3.10–3.13，并面向 macOS、Linux 和 Windows。主要差异通常来自：

- 虚拟环境激活命令
- 路径格式和权限
- MCP 客户端配置文件位置
- 可选 OCR 或系统级依赖

遇到平台问题时，请在 Issue 中写明操作系统版本、Python 版本、安装方式和 MS8 版本。

## 源码路径包含空格导致安装失败怎么办？

在部分环境中，editable install 可能因路径包含空格而导致入口脚本找不到包。可改用 wheel 安装：

```bash
python -m build --wheel --outdir dist
python -m pip install --force-reinstall dist/ms8-*.whl
```

## Absorb 会扫描整个电脑吗？

不会默认扫描整个电脑。Absorb 只扫描明确授权的目录，并排除常见高噪声目录。高风险根目录需要显式确认。

```bash
ms8 absorb add ./docs
ms8 absorb rescan
ms8 absorb ingest --limit 100
```

自动写入主记忆默认关闭；批量提交和回滚通常先以 dry-run 预览，再显式执行。

## 如何查看待审或隔离内容？

```bash
ms8 absorb review list
ms8 review list
```

在批准内容前检查来源、敏感性、风险评分和写入范围。

## 如何启用加密？

```bash
ms8 security enable
ms8 shadow status
```

妥善保存密钥和恢复材料。密钥丢失可能导致加密数据无法恢复；不要把密钥写入仓库、Issue、CI 日志或截图。

## 如何报告安全漏洞？

不要创建公开 Issue。请通过 GitHub Security Advisory 私密报告，并遵循 [`SECURITY.md`](../SECURITY.md) 中的披露流程。

## 应该使用 Issue 还是 Discussion？

- 可复现错误、明确功能请求和文档问题：Issue
- 使用交流、方案讨论、展示案例和一般问答：Discussions（启用后）
- 安全漏洞：GitHub Security Advisory

## 如何参与开发？

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check src/ms8/
python -m mypy src/ms8
python -m build --no-isolation
```

提交 PR 前请阅读 PR 模板，说明测试结果、安全与隐私影响以及兼容性。

## 仍然无法解决怎么办？

创建 Issue 前请准备：

- MS8 版本或 commit SHA
- Python 和操作系统版本
- 安装方式
- 最小复现步骤
- 预期行为与实际行为
- 已脱敏的 `ms8 doctor` 输出或错误栈

不得包含真实密钥、个人记忆、PII 或未授权文件内容。
