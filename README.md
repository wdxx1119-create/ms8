<p align="center">
  <h1 align="center">MS8 — 本地记忆引擎</h1>
  <p align="center">
    <em>记忆属于人，不属于工具。</em><br>
    <em>Memory belongs to you, not to tools.</em>
  </p>
</p>

<p align="center">
  <a href="https://github.com/wdxx1119-create/ms8/actions/workflows/ci.yml"><img src="https://github.com/wdxx1119-create/ms8/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/ms8/"><img src="https://img.shields.io/pypi/v/ms8" alt="PyPI version"></a>
  <img src="https://img.shields.io/pypi/pyversions/ms8" alt="Python versions">
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha status">
  <img src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue" alt="GPL-3.0-or-later">
</p>

MS8 是一个 **local-first** 的本地记忆引擎，用于写入、检索、治理和维护长期记忆，并可通过 MCP 让多个 AI 工具共享同一份用户控制的记忆。

核心记忆默认保存在你的电脑上。云端或本地模型只作为可选增强，不改变本地存储主线。

> **项目状态：Alpha。** 核心能力已可使用，但接口、配置和数据格式仍可能调整。重要数据请保持备份，并在升级前阅读 [CHANGELOG.md](CHANGELOG.md)。

## 文档导航

- [快速开始](docs/QUICK_START.md)：安装、首次写入、检索、MCP、备份和卸载
- [典型使用场景](docs/USE_CASES.md)：多工具共享记忆、本地资料检索和受治理资料吸收
- [常见问题](docs/FAQ.md)：数据位置、模型依赖、迁移和故障排查
- [完整文档索引](docs/README.md)
- [MCP 连接指南](src/ms8/connect/CONNECT_GUIDE.md)
- [支持渠道](SUPPORT.md)
- [贡献指南](CONTRIBUTING.md)
- [Alpha 路线图](ROADMAP.md)
- [安全政策](SECURITY.md)
- [版本记录](CHANGELOG.md)

## 为什么是 MS8？

许多 AI 记忆工具依赖托管服务，用户的长期上下文被绑定到某个工具或平台。MS8 的默认路径不同：

```text
托管式记忆：对话 → 第三方存储 → 单一平台
MS8：      对话 → 本地治理 → 用户控制的存储
```

MS8 重点解决：

- 多个 AI 工具之间重复说明偏好和项目约束
- 本地资料缺少统一、可治理的检索入口
- 自动记忆容易写入噪声、敏感信息或未经确认的内容
- 本地记忆系统缺少诊断、备份、审计和回滚能力

## 适合谁？

适合：

- 希望核心记忆默认留在本地的个人用户
- 同时使用多个 MCP 客户端或 AI 编程工具的开发者
- 需要把项目文档、笔记和代码资料接入本地检索的用户
- 重视授权范围、风险治理、人工审查和可恢复性的场景

暂不适合：

- 需要托管式多人实时协作记忆云的团队
- 需要固定 SLA 或商业支持承诺的生产系统
- 希望完全无人确认地执行高风险批量写入、删除或回滚的流程
- 无法为 Alpha 软件保留备份和升级验证流程的关键业务

## 核心能力

| 能力 | 说明 |
|---|---|
| 记忆引擎 | 写入、检索、上下文注入、压缩和知识图谱 |
| 治理管道 | 准入路由、拦截规则、PII 检测、风险评分和审批队列 |
| MCP 连接 | Tools、Resources、配置生成、验证、smoke 和 rollback |
| Absorb | 授权目录扫描、文件解析、本地索引、待审、隔离和来源标记 |
| 安全系统 | AES-256-GCM、Argon2id、影子审计和敏感内容防护 |
| 自维护 | doctor、健康报告、备份、自检查和 dry-run 修复流程 |
| LLM 路由 | 可选 Provider、语义增强、缓存和降级路径 |

## 30 秒上手

```bash
python -m pip install ms8

# 验证安装
ms8 version
ms8 doctor

# 写入一条记忆
ms8 ask "remember: 我喜欢用 Python"

# 检索记忆
ms8 ask "我喜欢什么语言？"

# 查看概览
ms8 dashboard
```

完整安装、虚拟环境和平台说明见 [Quick Start](docs/QUICK_START.md)。

## AI 助理自动安装编排

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

`DEFAULT_SAFE` 是默认推荐模式，不会执行高风险自动修复，也不会绕过授权边界。

## 连接 AI 工具

MS8 通过 MCP 让多个工具使用同一份本地记忆。

```bash
# 查看支持的目标
ms8 connect list-targets

# 查看连接说明
ms8 connect guide --mode both

# 自动检测、配置和验证
ms8 connect bootstrap --target all
```

按目标手动配置：

```bash
ms8 connect generate --target claude_desktop
ms8 connect apply --target claude_desktop
ms8 connect verify --target claude_desktop
ms8 connect smoke --target claude_desktop
```

已覆盖或支持的目标包括 Claude Desktop、Claude Code、OpenAI Codex、Cursor、Windsurf、Cline、Roo、Continue、Cherry Studio、Hermes 和 OpenClaw。不同版本的第三方客户端可能存在配置差异，以 `ms8 connect list-targets` 和连接指南为准。

## Absorb：受治理的本地资料吸收

Absorb 只扫描你明确授权的目录，把可用内容建立本地索引。高风险内容进入待审或隔离，不会默认把整份文件直接写入长期记忆。

```bash
# 授权目录
ms8 absorb add ./docs

# 扫描和解析
ms8 absorb rescan
ms8 absorb ingest --limit 100

# 搜索本地资料
ms8 absorb search "项目决策" --pretty

# 查看待审内容
ms8 absorb review list

# 预览低风险摘要提交
ms8 absorb autosubmit run --limit 20
```

安全边界：

- 默认只扫描显式授权目录
- 默认排除 `.git`、`.venv`、`node_modules` 和缓存目录
- 高风险根目录需要显式确认
- 自动写入主记忆默认关闭
- 批量写入和回滚应先 dry-run，再显式使用 `--apply`
- 来源记录用于审计和按来源处理

## 数据存储

默认数据目录：

```text
~/.ms8/
├── memory/       # 主记忆、索引、知识图谱、备份和审计数据
├── health/       # 健康报告
├── connect/      # MCP 连接状态
├── absorb/       # 本地资料索引、事件和隔离内容
└── config.json   # 本地配置
```

可通过环境变量覆盖：

```bash
export MS8_HOME=~/custom_path
export MS8_DATA_DIR=~/custom_data
export MS8_CONFIG_DIR=~/custom_config
export MS8_LOG_DIR=~/custom_logs
```

迁移设备时应停止正在访问 MS8 的进程，完整备份并迁移 `MS8_HOME`，然后运行 `ms8 doctor` 验证。

## 安全与隐私

```bash
ms8 security enable
ms8 shadow status
ms8 review list
ms8 ops self-check-report
ms8 ops self-repair-run --mode dry-run
```

本地存储不等于绝对安全。仍应使用操作系统账户、磁盘权限、加密和安全备份保护数据。

不要把以下内容提交到 Issue、PR、Discussion 或 CI 日志：

- API Key、Token、密码或恢复码
- 真实个人记忆和对话
- 身份证件、银行卡、电话、邮箱等 PII
- 完整 `MS8_HOME`、备份或生产日志
- 未授权的客户或公司资料

安全漏洞请通过 GitHub Security Advisory 私密报告，具体流程见 [SECURITY.md](SECURITY.md)。

## 常用维护命令

```bash
ms8 doctor
ms8 dashboard
ms8 backup
ms8 cleanup
ms8 clean --dry-run
ms8 reset --dry-run
ms8 uninstall --dry-run
```

实验能力默认关闭：

```bash
ms8 labs status
ms8 labs enable
ms8 labs disable
```

## 开发

```bash
git clone https://github.com/wdxx1119-create/ms8.git
cd ms8
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

python -m ruff check src/ms8
python -m mypy src/ms8
python -m pytest tests/ -q
python -m build --no-isolation
bash scripts/release_isolated_test.sh --cleanup
```

CI 在 Python 3.10–3.13 上运行测试，并验证 wheel、source distribution、clean-room 安装以及 macOS/Linux 隔离安装。Python 3.11 任务同时生成覆盖率报告，目前覆盖率用于建立基线，尚未设置硬性门槛。

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 系统要求

- Python 3.10–3.13
- macOS、Linux 或 Windows
- 基础引擎约需几十 MB 磁盘空间，实际占用取决于记忆、索引和备份规模
- OCR、文档解析和本地模型可能需要额外系统依赖

## 路线图

当前重点是数据完整性、迁移恢复、MCP 兼容性、Absorb 安全边界、跨平台测试和覆盖率基线。Beta 准入条件见 [ROADMAP.md](ROADMAP.md)。

## 许可证

GNU General Public License v3.0 or later。详情见 [LICENSE](LICENSE)。

---

<p align="center">
  <em>“记忆是习惯的载体。它改变人，也改变模型。记忆是个人资产。”</em>
</p>
