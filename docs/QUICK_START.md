# MS8 Quick Start

MS8 是一个 local-first 的本地记忆引擎。核心记忆默认保存在你的电脑上；本地或云端模型仅作为可选增强，不改变本地存储主线。

## 系统要求

- Python 3.10–3.13
- macOS、Linux 或 Windows
- 建议使用独立虚拟环境

## 1. 安装

### 从 PyPI 安装基础能力

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install ms8
```

基础安装包含本地记忆、治理、MCP、安全和恢复主线，不强制安装 Ollama、文档解析器或 OCR 依赖。

按需安装增强能力：

```bash
python -m pip install "ms8[llm]"      # Ollama Python 客户端
python -m pip install "ms8[absorb]"   # PDF/DOCX 与目录监听
python -m pip install "ms8[ocr]"      # Absorb + OCR Python 依赖
python -m pip install "ms8[policy]"   # 可选策略后端
python -m pip install "ms8[full]"     # 全部 Python 可选能力
```

OCR 系统工具需要单独安装。完整说明见 [Installation Profiles](INSTALL_PROFILES.md)。

### 从源码安装

```bash
git clone https://github.com/wdxx1119-create/ms8.git
cd ms8
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

需要同时开发增强能力时，显式叠加 profile，例如：

```bash
python -m pip install -e ".[dev,ocr]"
```

如果源码路径包含空格且 editable install 无法找到 `ms8`，改用 wheel：

```bash
python -m build --wheel --outdir dist
python -m pip install --force-reinstall dist/ms8-*.whl
```

## 2. 验证安装

```bash
ms8 version
ms8 doctor
ms8-recovery --help
```

`doctor` 中可解释的 `warn` 不一定代表系统故障。若命令失败，请先记录已脱敏的输出，并查看 [FAQ](FAQ.md)。

## 3. 完成第一次写入和检索

```bash
# 写入一条记忆
ms8 ask "remember: 我喜欢用 Python"

# 检索记忆
ms8 ask "我喜欢什么语言？"

# 查看运行概览
ms8 dashboard
```

默认数据目录为 `~/.ms8/`。可通过以下环境变量覆盖：

```bash
export MS8_HOME=~/custom_path
export MS8_DATA_DIR=~/custom_data
export MS8_CONFIG_DIR=~/custom_config
export MS8_LOG_DIR=~/custom_logs
```

## 4. 连接 AI 工具（可选）

MS8 通过 MCP 让多个 AI 工具使用同一份本地记忆。

```bash
# 查看支持的目标
ms8 connect list-targets

# 查看连接说明
ms8 connect guide --mode both

# 自动检测、配置并验证所有支持目标
ms8 connect bootstrap --target all
```

也可以按目标手动执行：

```bash
ms8 connect generate --target claude_desktop
ms8 connect apply --target claude_desktop
ms8 connect verify --target claude_desktop
```

详细说明见 [`src/ms8/connect/CONNECT_GUIDE.md`](../src/ms8/connect/CONNECT_GUIDE.md)。

## 5. AI 助理自动安装编排（可选）

```bash
python -m pip install ms8
ms8 agent run install --profile DEFAULT_SAFE
```

日常检查与报告：

```bash
ms8 agent run check
ms8 agent run report
ms8 agent run daily
```

`DEFAULT_SAFE` 是默认推荐模式，不会绕过授权边界或执行高风险自动修复。

## 6. 吸收本地资料（可选）

先安装 Absorb profile：

```bash
python -m pip install "ms8[absorb]"
```

Absorb 只扫描明确授权的目录，并将高风险内容送入待审或隔离流程。

```bash
ms8 absorb add ./docs
ms8 absorb rescan
ms8 absorb ingest --limit 100
ms8 absorb search "项目决策" --pretty
ms8 absorb review list
```

在批量写入或回滚前检查命令是否处于 dry-run；需要实际执行时再显式使用 `--apply`。

## 7. 备份、恢复、迁移和卸载

旧的 `ms8 backup` 保留兼容性记忆快照。完整运行时备份和恢复使用 `ms8-recovery`：

```bash
# 创建完整运行时备份
ms8-recovery backup create --root ~/.ms8 --tag manual

# 验证备份
ms8-recovery backup verify ~/.ms8/backups/ms8-runtime-manual-<timestamp>.zip

# 预览恢复
ms8-recovery restore plan <archive.zip> --target ~/.ms8

# 查看格式迁移计划
ms8-recovery migrate plan --root ~/.ms8

# 健康检查
ms8 doctor

# 卸载流程
ms8 uninstall
```

执行恢复或迁移前，请停止正在使用 MS8 的客户端和后台服务，并先阅读 [Recovery and Migration](RECOVERY_AND_MIGRATION.md)。

## 下一步

- [安装层级](INSTALL_PROFILES.md)
- [备份、恢复与迁移](RECOVERY_AND_MIGRATION.md)
- [典型使用场景](USE_CASES.md)
- [常见问题](FAQ.md)
- [安全政策](../SECURITY.md)
- [版本记录](../CHANGELOG.md)
- [完整 README](../README.md)
