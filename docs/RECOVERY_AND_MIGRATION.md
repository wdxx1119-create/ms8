# MS8 Recovery and Migration

MS8 的恢复流程由独立入口 `ms8-recovery` 提供。该入口与旧版 `ms8 backup` 并存；旧命令仍只保存兼容性的记忆 JSONL 快照，新入口负责完整运行时归档、校验、恢复计划和格式迁移。

## 备份范围

完整运行时备份默认包含 `MS8_HOME` 下的配置、规范记忆、隔离队列、审计记录、Absorb 数据库和派生状态，但排除：

- `backups/`，防止备份递归包含自身
- 临时文件和锁文件
- Python 与测试缓存
- 符号链接；跳过的链接会记录在 manifest 中

SQLite 文件通过 SQLite Backup API 生成一致快照，不直接复制活动 WAL 数据库。

## 创建并验证备份

```bash
ms8-recovery backup create --root ~/.ms8 --tag manual
ms8-recovery backup verify ~/.ms8/backups/ms8-runtime-manual-<timestamp>.zip
```

每个归档包含 `manifest.json`，记录：

- MS8 版本
- 运行时格式版本
- 文件清单、大小和 SHA-256
- SQLite 快照方式
- 被跳过的符号链接

校验会拒绝路径穿越、缺失文件、未声明文件、大小不一致和 SHA-256 不一致。

## 恢复前预览

```bash
ms8-recovery restore plan <archive.zip> --target ~/.ms8
```

恢复计划会列出：

- 将创建的文件
- 将覆盖的文件
- 内容未变化的文件
- 删除列表；当前始终为空

恢复默认不删除目标目录中额外存在的文件。

## 执行恢复

```bash
ms8-recovery restore apply <archive.zip> --target ~/.ms8 --confirm RESTORE
```

执行规则：

1. 重新验证归档。
2. 如果目标运行时已有数据，先创建 `pre-restore` 备份。
3. 在临时目录中校验待恢复文件。
4. 逐文件原子替换。
5. 写入 `memory/logs/restore_audit.jsonl`。

建议恢复前停止正在使用 MS8 的客户端和后台服务。

## 运行时格式版本

格式 manifest 位于：

```text
<MS8_HOME>/format_manifest.json
```

当前独立版本字段包括：

- `runtime_format_version`
- `canonical_record_schema_version`
- `absorb_schema_version`
- `graph_schema_version`
- `index_format_version`
- `config_schema_version`

查看未版本化或当前运行时：

```bash
ms8-recovery format-status --root ~/.ms8
```

## 迁移预览与执行

```bash
ms8-recovery migrate plan --root ~/.ms8
ms8-recovery migrate apply --root ~/.ms8 --confirm MIGRATE
```

迁移约束：

- 只允许逐版本向前迁移
- 不支持降级
- 修改前必须创建可验证备份
- 保留未知 manifest 字段
- 写入 `memory/logs/migration_audit.jsonl`
- 缺失迁移步骤时直接失败，不猜测或跳过

首个 `0 -> 1` 迁移只采用版本 manifest，不重写现有业务数据。

## 当前边界

本阶段建立的是完整运行时快照、校验、恢复和迁移注册表基础。知识图谱和搜索索引的自动重建仍属于后续恢复增强；当前备份会保存这些文件，但不会在恢复后自动删除或重建派生存储。
