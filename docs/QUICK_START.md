# MS8 Quick Start

MS8 是一个 local-first 的本地记忆引擎。核心记忆默认保存在你的电脑上；本地或云端模型仅作为可选增强，不改变本地存储主线。

## 系统要求

- Python 3.10–3.13
- macOS、Linux 或 Windows
- 建议使用独立虚拟环境

## 1. 安装

### 从 PyPI 安装

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install ms8
```

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

如果源码路径包含空格且 editable install 无法找到 `ms8`，改用 wheel：

```bash
python -m build --wheel --outdir dist
python -m pip install --force-reinstall dist/ms8-*.whl
```

## 2. 验证安装

```bash
ms8 version
ms8 doctor
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

Absorb 只扫描明确授权的目录，并将高风险内容送入待审或隔离流程。

```bash
ms8 absorb add ./docs
ms8 absorb rescan
ms8 absorb ingest --limit 100
ms8 absorb search "项目决策" --pretty
ms8 absorb review list
```

在批量写入或回滚前检查命令是否处于 dry-run；需要实际执行时再显式使用 `--apply`。

## 7. 备份、迁移和卸载

```bash
# 创建备份
ms8 backup

# 健康检查
ms8 doctor

# 卸载流程
ms8 uninstall
```

迁移到新设备时，先停止正在使用 MS8 的工具，再完整备份并迁移 `MS8_HOME` 目录。迁移后运行 `ms8 doctor` 验证状态。

## 下一步

- [典型使用场景](USE_CASES.md)
- [常见问题](FAQ.md)
- [安全政策](../SECURITY.md)
- [版本记录](../CHANGELOG.md)
- [完整 README](../README.md)
