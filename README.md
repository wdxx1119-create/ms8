<p align="center">
  <h1 align="center">MS8 — 本地记忆引擎</h1>
  <p align="center">
    <em>记忆属于人，不属于工具。</em><br>
    <em>Memory belongs to you, not to tools.</em>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.2.1-blue" alt="version">
  <img src="https://img.shields.io/badge/python-≥3.10-green" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-orange" alt="license">
  <img src="https://img.shields.io/badge/tests-123%20passed-brightgreen" alt="tests">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="platform">
</p>

---

## 为什么是 MS8？

市面上的 AI 记忆工具（Mem0、Zep、Letta）都在推云服务——你的记忆数据存在他们的服务器上。

**MS8 不同。** 你的记忆数据完全存在你自己的电脑上。没有云端，没有上传，没有订阅。

```
其他工具：  你的对话 → 云端存储 → 他们的服务器
MS8：       你的对话 → 本地存储 → 你的电脑
```

---

## 核心能力

| 能力 | 说明 |
|------|------|
| 🧠 **记忆引擎** | 写入/检索/注入/压缩，JSONL + SQLite + 知识图谱 |
| 🛡️ **治理管道** | 5 路准入、9 种拦截规则、13 种 PII 检测、风险评分 |
| 🔌 **MCP 连接** | 7 个 Tools + 3 个 Resources，支持 10 个 AI 工具 |
| 🔒 **安全系统** | AES-256-GCM 加密 + 影子系统（18 模块） |
| 🔧 **自维护** | 61 项自检查 + 24 条自修复策略 + 25 条维护策略 |
| 📊 **多 LLM 路由** | 3 Provider 自动切换 + 语义缓存 + 批量处理 |

---

## 30 秒上手

```bash
# 安装
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 验证
ms8 doctor

# 写入一条记忆
ms8 ask "remember: 我喜欢用 Python"

# 检索记忆
ms8 ask "我喜欢什么语言？"

# 查看概览
ms8 dashboard
```

---

## 连接 AI 工具

MS8 通过 MCP (Model Context Protocol) 连接你的 AI 工具，让它们共享同一份记忆。

### 支持的工具

| 工具 | 状态 | 配置方式 |
|------|------|---------|
| Claude Desktop | ✅ 已验证 | `ms8 connect apply --target claude_desktop` |
| OpenAI Codex | ✅ 已验证 | `~/.codex/config.toml` MCP 配置 |
| Cursor | ✅ 支持 | `ms8 connect apply --target cursor` |
| Windsurf | ✅ 支持 | `ms8 connect apply --target windsurf` |
| Cline | ✅ 支持 | `ms8 connect apply --target cline` |
| Roo | ✅ 支持 | `ms8 connect apply --target roo` |
| Continue | ✅ 支持 | `ms8 connect apply --target continue` |
| Cherry Studio | ✅ 支持 | `ms8 connect apply --target cherry_studio` |
| Hermes | ✅ 支持 | `ms8 connect apply --target hermes` |
| OpenClaw | ✅ 支持 | `ms8 connect apply --target openclaw` |

### MCP 接入流程

```bash
# 查看支持的工具
ms8 connect list-targets

# 查看连接指南（手动 / 自动）
ms8 connect guide --mode both

# 查看 AGENTS 模板路径（可自定义）
ms8 connect template --target claude_desktop

# 生成配置
ms8 connect generate --target claude_desktop

# 应用配置
ms8 connect apply --target claude_desktop

# 验证连接
ms8 connect verify --target claude_desktop
```

### 两种连接方式

- 手动连接：`generate -> apply -> verify -> smoke`
- Agent 自动配置：`bootstrap --target all` 一次完成检测、写入、验证

文档入口：

- [CONNECT_GUIDE.md](src/ms8/connect/CONNECT_GUIDE.md)
- [AGENTS.md](src/ms8/connect/AGENTS.md)

### MCP Tools & Resources

**Tools (7 个):**

| Tool | 功能 |
|------|------|
| `prepare_reply` | 聚合查询：获取记忆上下文用于回复 |
| `submit` | 写入单条记忆 |
| `batch_submit` | 批量写入多条记忆 |
| `query` | 语义搜索记忆 |
| `context` | 获取对话相关记忆上下文 |
| `status` | 返回系统状态 |
| `profile` | 返回用户画像 |

**Resources (3 个):**

| Resource | 功能 |
|----------|------|
| `ms8://long-term` | 长期记忆 |
| `ms8://profile` | 用户画像 |
| `ms8://recent` | 近期记忆 |

---

## CLI 命令

### 常用命令

```bash
ms8 version          # 版本
ms8 doctor           # 健康检查
ms8 ask "..."        # 快速写入/检索
ms8 dashboard        # 运行概览
ms8 demo             # 演示
```

### 维护命令

```bash
ms8 watch            # 定时巡检
ms8 backup           # 备份记忆
ms8 cleanup          # 清理旧备份
ms8 clean            # 清理缓存（安全）
ms8 reset            # 重置派生状态
ms8 uninstall        # 卸载
```

### MCP 连接

```bash
ms8 connect list-targets     # 列出支持的工具
ms8 connect guide            # 连接说明（manual/agent）
ms8 connect template         # AGENTS 模板路径与快速步骤
ms8 connect bootstrap        # 自动检测+配置+验证
ms8 connect apply            # 应用配置
ms8 connect verify           # 验证连接
ms8 connect smoke            # 冒烟验证（save/search/context/status）
ms8 connect rollback         # 回滚配置
```

### 安全与维护

```bash
ms8 security enable          # 启用加密
ms8 shadow status            # 影子系统状态
ms8 ops self-check-report    # 自检查报告
ms8 ops self-repair-run      # 自修复
ms8 graph stats              # 知识图谱统计
ms8 review list              # 审批队列
```

### LLM 配置

```bash
ms8 llm status               # LLM 状态
ms8 llm setup                # 交互式配置
```

### Labs（实验能力，默认关闭）

MS8 主线默认只开放稳定能力。实验命令会被 gate，需显式开启：

```bash
ms8 labs status              # 查看实验开关状态
ms8 labs enable              # 开启实验命令
ms8 labs disable             # 关闭实验命令
```

常见实验命令（示例）：

```bash
ms8 synthetic list
ms8 ops meta-run
ms8 ops advanced-insight-status
```

如果你看到 “labs command disabled by default”，先执行 `ms8 labs enable`。

完整命令列表：`ms8 --help`（26 个一级命令 + 56 个 ops 子命令）

---

## 架构

```
src/ms8/                           215 文件, 50,566 行
│
├── cli.py                         CLI 入口（26 个命令）
├── runtime.py                     运行时调度
├── paths.py                       统一路径解析
│
├── app/                           治理管道层
│   ├── pipeline/                  准入引擎（5 路由 + 风险评分）
│   ├── rules/                     规则系统（10 个模块）
│   └── review/                    审批服务
│
├── connect/                       MCP 连通层
│   ├── mcp_server/                MCP Server（stdio）
│   │   ├── stdio_server.py        协议处理
│   │   ├── mcp_server.py          Tools/Resources 路由
│   │   └── memory_service_interface.py  统一服务接口
│   └── scripts/                   连接/验证/配置
│
└── engine_core/                   核心引擎层
    ├── core.py                    记忆系统核心（153 方法）
    ├── knowledge_graph.py         知识图谱
    ├── auto_memory.py             自动记忆提取
    ├── security/                  安全层（加密 + 影子系统）
    └── maintenance/               自维护（61 检查 + 24 修复）
```

---

## 数据存储

所有数据存储在本地 `~/.ms8_runtime/` 目录：

```
~/.ms8_runtime/
├── memory/
│   ├── auto_memory_records.jsonl   # 主记忆记录
│   ├── auto_memory_index.json      # 索引
│   ├── knowledge_graph.db          # 知识图谱
│   ├── memory.db                   # SQLite 存储
│   ├── MEMORY.md                   # 长期记忆文本
│   └── backups/                    # 自动备份
├── health/                         # 健康报告
└── connect/                        # MCP 连接状态
```

环境变量覆盖路径：

```bash
export MS8_HOME=~/custom_path
export MS8_DATA_DIR=~/custom_data
export MS8_CONFIG_DIR=~/custom_config
export MS8_LOG_DIR=~/custom_logs
```

---

## 安全

| 特性 | 说明 |
|------|------|
| AES-256-GCM | 内存记录加密 |
| Argon2id | 密钥派生 |
| 影子系统 | 18 模块，链式哈希事件账本 |
| PII 检测 | 13 种模式（邮箱/手机/身份证/银行卡等） |
| 9 种拦截规则 | 阻止敏感内容写入 |
| 隔离区 | 损坏数据自动隔离 |

---

## 自维护

MS8 能自己诊断和修复问题：

- **61 项自检查**：分 L1（快速）→ L4（趋势）四级
- **24 条自修复策略**：分 L1（安全自动）/ L2（半自动）/ L3（需人工确认）
- **25 条维护策略**：调度检查频率和修复时机
- **自动备份**：每 24 小时

```bash
# 查看健康状态
ms8 doctor

# 运行自检查
ms8 ops self-check-report

# 运行自修复
ms8 ops self-repair-run --dry-run
```

说明：
- `l4_capture_trend` 出现 `warn` 时，如果提示“无质量样本（仅噪声/策略丢弃样本）”，属于可解释告警，不代表系统故障。

---

## 开发

### 运行测试

```bash
pytest -q                    # 全量测试（123 个）
pytest src/ms8/engine_core/tests/ -q   # 引擎测试
```

### 代码检查

```bash
ruff check src/ms8/
```

### 构建

```bash
python -m build --no-isolation
```

### 发布检查

```bash
scripts/check_release_artifacts.sh
scripts/release_isolated_test.sh
scripts/publish_testpypi.sh
scripts/publish_pypi.sh
```

### 安全发布（Token 不落盘）

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-***"

# 先做发布前检查
bash scripts/release_checklist.sh

# 预发布到 TestPyPI
bash scripts/publish_testpypi.sh

# 正式发布到 PyPI
bash scripts/publish_pypi.sh

# 或使用 Make 一步化
make publish-test
make clear-release-env
```

可先用 `--dry-run` 验证上传目标，不会实际发布。
详细说明见：[RELEASE_SECURITY.md](docs/RELEASE_SECURITY.md)

---

## 与竞品对比

| 特性 | MS8 | Mem0 | Zep | Letta |
|------|-----|------|-----|-------|
| 数据完全本地 | ✅ | ❌ 推云 | ❌ 推云 | ❌ 推云 |
| 无需订阅 | ✅ | ❌ | ❌ | ❌ |
| MCP 原生支持 | ✅ 10 工具 | ❌ | ❌ | ❌ |
| 自检查/自修复 | ✅ 61+24 | ❌ | ❌ | ❌ |
| 加密存储 | ✅ AES-256 | ❌ | ❌ | ❌ |
| 影子安全系统 | ✅ 18 模块 | ❌ | ❌ | ❌ |
| 知识图谱 | ✅ | ❌ | ✅ Graphiti | ❌ |
| 治理管道 | ✅ 5 路准入 | ❌ | ❌ | ❌ |

---

## 系统要求

- Python ≥ 3.10
- macOS / Linux（Windows 适配计划中）
- 磁盘空间：约 50MB（引擎）+ 记忆数据

---

## 许可证

MIT License

---

<p align="center">
  <em>"记忆是习惯的载体。它改变人，也改变模型。记忆是个人资产。"</em>
</p>
