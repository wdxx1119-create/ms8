# Contributing to MS8

感谢你参与 MS8。MS8 是一个处于 Alpha 阶段的 local-first 本地记忆引擎，贡献时需要同时考虑功能正确性、跨平台兼容性、隐私和治理边界。

## 开始之前

适合通过 Pull Request 提交的内容包括：

- 可复现缺陷修复
- 测试和跨平台兼容性改进
- 文档修正
- 性能和可维护性改进
- 已在 Issue 或 Discussion 中明确范围的功能

安全漏洞、密钥泄漏、权限绕过或可能暴露个人记忆的问题，不要创建公开 Issue。请按照 [SECURITY.md](SECURITY.md) 使用 GitHub Security Advisory 私密报告。

## 开发环境

要求：

- Python 3.10–3.13
- Git
- macOS、Linux 或 Windows

建议使用独立虚拟环境：

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

如果源码路径包含空格且 editable install 失败，请改用 wheel 安装：

```bash
python -m build --wheel --outdir dist
python -m pip install --force-reinstall dist/ms8-*.whl
```

## 分支与提交

从最新 `main` 创建短生命周期分支：

```bash
git switch main
git pull --ff-only
git switch -c fix/short-description
```

推荐分支前缀：

- `fix/`：缺陷修复
- `feat/`：功能
- `docs/`：文档
- `test/`：测试
- `chore/`：维护或构建

提交信息应说明目的，例如：

```text
fix: preserve governed memory scope during migration
test: cover packaged MCP resources
docs: clarify Absorb authorization boundary
```

不要提交真实记忆、密钥、令牌、PII、用户目录、运行时数据、诊断转储或本地配置。

## 必须执行的检查

至少运行：

```bash
python -m ruff check src/ms8
python -m mypy src/ms8
python -m pytest tests/ -q
python -m ms8 doctor
python -m build --no-isolation
```

涉及打包、CLI、MCP、Absorb 或安装流程时，还应运行：

```bash
bash scripts/check_release_artifacts.sh
bash scripts/release_isolated_test.sh --cleanup
```

CI 会在 Python 3.10–3.13 上运行测试，并验证 wheel、source distribution、clean-room 安装以及 macOS/Linux 隔离安装。

## 测试要求

- 缺陷修复应增加能够在修复前失败、修复后通过的测试。
- 新命令应覆盖成功路径、无效参数和退出码。
- 治理、安全和隐私相关变更应覆盖拒绝、降级、隔离或审计路径。
- 文件系统变更应考虑 macOS、Linux、Windows 路径差异。
- 测试必须使用临时目录，不得访问真实 `~/.ms8/`。
- 测试数据不得包含真实密钥、个人信息或用户记忆。

## 代码风格

项目使用 Ruff 和 mypy：

```bash
python -m ruff check src/ms8
python -m mypy src/ms8
```

请保持：

- Python 3.10 语法兼容
- 公共行为清晰、可测试
- 错误信息可操作且不泄露敏感内容
- 可选依赖缺失时有明确降级路径
- 不绕过现有治理、授权、审计或安全边界

## 文档要求

新增或修改用户可见行为时，同步更新对应文档：

- [README.md](README.md)：项目入口和主要能力
- [Quick Start](docs/QUICK_START.md)：安装和首次使用
- [FAQ](docs/FAQ.md)：常见问题和排查
- [Use Cases](docs/USE_CASES.md)：端到端场景
- [CHANGELOG.md](CHANGELOG.md)：发布相关变化
- CLI `--help`：命令、参数和默认值

## Pull Request

PR 应保持单一目的，并说明：

- 问题和目标
- 实现方式
- 已执行的测试
- Python 和操作系统兼容性
- 安全、隐私和数据迁移影响
- 是否存在破坏性变更
- 是否需要更新文档或 CHANGELOG

提交后请等待 CI 全部通过，并解决所有 review threads。默认优先使用 Squash Merge。

## 行为规范与支持

参与项目即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。使用问题、缺陷、安全报告和支持边界见 [SUPPORT.md](SUPPORT.md)。
