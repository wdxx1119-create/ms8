# MS8 使用场景

本文用“问题 → 配置 → 操作 → 结果 → 安全边界”的方式说明 MS8 的典型用法。命令和能力以当前版本为准；执行写入、批量操作或外部模型调用前，请先检查本地配置和数据范围。

## 场景一：多个 AI 工具共享长期偏好

### 问题

用户在不同 AI 工具中反复说明语言偏好、工作习惯和项目约定，且不希望把长期记忆绑定到某一家云服务。

### 配置

1. 安装并验证 MS8。
2. 使用 MCP 将需要的 AI 工具连接到同一个本地 MS8 实例。

```bash
python -m pip install ms8
ms8 doctor
ms8 connect list-targets
ms8 connect bootstrap --target all
```

### 操作

```bash
ms8 ask "remember: 默认使用中文回答，代码注释使用英文"
ms8 ask "我的回答和代码注释偏好是什么？"
```

### 结果

已连接的工具可以通过受治理的 MCP 接口读取相关记忆上下文，减少重复配置。

### 安全边界

- 只保存确实需要长期保留的信息。
- 不写入令牌、密码、恢复码或高敏感 PII。
- 外部模型是否接收内容取决于 Provider 配置，应单独审查。

## 场景二：AI 编程助手保留项目上下文

### 问题

跨会话开发时，AI 助手容易忘记架构约束、测试命令、兼容性要求和已做出的技术决策。

### 配置

将项目约定写入记忆，并连接正在使用的 MCP 客户端。

```bash
ms8 ask "remember: 当前项目支持 Python 3.10 到 3.13，提交前运行 pytest、ruff 和 mypy"
ms8 connect verify --target <tool>
```

### 操作

在新会话中让 AI 助手先查询项目约束，再开始修改代码。重要决策完成后，将精炼后的结论写回长期记忆。

### 结果

AI 助手可以在多个会话和工具之间复用相同的项目约束，降低重复说明和不一致修改。

### 安全边界

- 只记录经过确认的项目事实，不把猜测写成长期规则。
- 不保存私有仓库代码、客户数据或内部凭据，除非你已明确授权相应本地流程。
- 发生架构变化时及时更新或淘汰旧记忆。

## 场景三：本地项目文档和代码知识检索

### 问题

项目文档、Markdown、代码片段和决策记录分散在本地目录中，普通关键词搜索难以形成统一入口。

### 配置

只授权需要检索的项目目录：

```bash
ms8 absorb add ./docs
ms8 absorb rescan
ms8 absorb ingest --limit 100
```

### 操作

```bash
ms8 absorb search "为什么选择本地优先架构" --pretty
ms8 absorb review list
```

### 结果

MS8 为已授权资料建立本地索引，并允许按治理规则审查、检索和提交低风险摘要。

### 安全边界

- 默认只扫描显式授权的目录。
- 授权前先排除密钥、生产转储、客户资料和无关目录。
- 高风险内容应进入待审或隔离，不应直接写入长期记忆。
- 批量提交前先 dry-run，再按需使用 `--apply`。

## 场景四：隐私敏感的个人知识管理

### 问题

用户希望保存学习笔记、个人偏好和长期计划，但不希望核心记忆默认存放在第三方托管记忆服务中。

### 配置

使用本地数据目录，并根据需要启用加密：

```bash
export MS8_HOME=~/private/ms8
ms8 security enable
ms8 doctor
```

### 操作

```bash
ms8 ask "remember: 本季度学习重点是 Python 类型系统和本地 AI 工具链"
ms8 backup
ms8 dashboard
```

### 结果

核心记忆、索引和备份保存在用户控制的目录中，可自行备份、迁移和删除。

### 安全边界

- 本地存储不等于绝对安全，仍需依赖操作系统账户、磁盘权限和备份保护。
- 妥善保存加密密钥和恢复材料。
- 不要把 `MS8_HOME`、备份或诊断日志提交到公开仓库。

## 场景五：受治理的资料吸收、人工审查和回滚

### 问题

团队或个人希望让 AI 使用本地资料，但需要控制哪些内容能进入长期记忆，并保留审计和回滚能力。

### 配置

```bash
ms8 absorb add ./approved-materials
ms8 absorb rescan
ms8 absorb ingest --limit 100
```

### 操作

```bash
# 查看候选与待审内容
ms8 absorb review list

# 先预览低风险摘要自动提交
ms8 absorb autosubmit run --limit 20

# 检查系统状态
ms8 doctor
ms8 ops self-check-report
```

### 结果

资料经过来源标记、风险治理和人工审查后再进入主记忆；由 Absorb 写入的记录带有来源信息，便于审计和按来源处理。

### 安全边界

- 自动写入主记忆默认关闭。
- 不绕过风险评分、待审或隔离流程。
- 对批量写入、删除和回滚操作保留预览与人工确认。

## 场景六：本地运行状态检查和自维护

### 问题

长期运行的本地记忆系统可能出现索引不同步、可选组件缺失、质量样本不足或备份积累等问题。

### 配置与操作

```bash
ms8 doctor
ms8 dashboard
ms8 ops self-check-report
ms8 ops self-repair-run --mode dry-run
ms8 backup
ms8 cleanup
```

### 结果

用户可以先获得诊断和修复预览，再决定是否执行实际变更。

### 安全边界

- 优先使用 dry-run。
- 在修复、清理、重置或卸载前创建备份。
- 不把包含本地路径、个人记忆或密钥的完整报告公开发布。

## 选择合适的入口

| 目标 | 推荐入口 |
|---|---|
| 第一次安装和验证 | [`QUICK_START.md`](QUICK_START.md) |
| 常见错误和配置问题 | [`FAQ.md`](FAQ.md) |
| MCP 连接 | [`src/ms8/connect/CONNECT_GUIDE.md`](../src/ms8/connect/CONNECT_GUIDE.md) |
| 安全漏洞报告 | [`SECURITY.md`](../SECURITY.md) |
| 版本变化 | [`CHANGELOG.md`](../CHANGELOG.md) |
