# MS8 Release Security Guide

MS8 当前不通过 GitHub Actions 自动发布到 PyPI。本文件说明发布前必须验证的供应链证据，以及维护者手动发布时的凭证边界。

## 自动化安全证据

### 1. 严格依赖审计

`.github/workflows/dependency-audit.yml` 使用两个相互隔离的虚拟环境：

- `.audit-target-venv`：只安装待审计的 MS8 包及其运行时依赖。
- `.audit-tool-venv`：只安装 `pip-audit` 和 CycloneDX 工具。

工作流随后：

1. 用 `pip check` 验证目标环境依赖一致性。
2. 解析目标环境的 `site-packages`。
3. 使用 `pip-audit --strict --path <target-site-packages>` 只审计目标环境。
4. 生成 `pip-audit.json`。
5. 从目标 Python 环境生成 `ms8-dependencies.cdx.json`。
6. 无论扫描结果如何都先上传证据，再由最终 gate 检查扫描结果和文件完整性。

审计工具自身的依赖不会混入 MS8 目标依赖集合。已知漏洞、扫描错误、SBOM 生成错误或证据缺失都会使最终 gate 失败。

### 2. Release candidate 产物证据

`.github/workflows/release-candidate.yml` 在 wheel 和 source distribution 通过测试后：

- 在干净虚拟环境中安装 wheel。
- 验证版本、打包资源和 `pip check`。
- 从已安装 wheel 的环境生成 `ms8-<version>.cdx.json`。
- 校验 SBOM 为 CycloneDX 且包含预期 MS8 版本。
- 在另一干净环境中安装 source distribution。
- 为 wheel、source distribution 和 SBOM 生成 `SHA256SUMS`。
- 将所有文件作为同一 release-candidate artifact 保存。

依赖审计 SBOM 与 release-candidate SBOM 的职责不同：前者用于持续检查目标依赖集合，后者绑定到具体构建产物和提交。

## 发布前检查

维护者在创建 tag 或上传 PyPI 前应确认：

1. CI、Release candidate validation、CodeQL、Dependency Review 和 Python Dependency Audit 全部成功。
2. `CHANGELOG.md` 已整理当前版本内容。
3. wheel、source distribution、CycloneDX SBOM 和 `SHA256SUMS` 来自同一已验证提交。
4. 本地重新计算的 SHA-256 与 artifact 中的 `SHA256SUMS` 一致。
5. 从待发布 wheel 创建新的虚拟环境并运行：

```bash
python -m pip check
ms8 version
ms8 doctor
```

6. 发布后从 PyPI 重新安装公开产物并重复 smoke test。

## 手动 PyPI 凭证

仅在实际手动上传时设置：

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="<your-pypi-token>"
```

安全要求：

- 不把 token 写入仓库、文档示例的真实值、shell 脚本或 workflow。
- 使用项目范围、最小权限的 token。
- 不在包含 token 的命令中开启 shell tracing。
- 发布完成后清理当前 shell 的凭证变量。
- 凭证泄漏后立即撤销并轮换，同时检查 Actions 日志和 artifact。

## 手动发布流程

```bash
bash scripts/release_checklist.sh
bash scripts/publish_testpypi.sh --dry-run
bash scripts/publish_pypi.sh --dry-run
bash scripts/publish_testpypi.sh
# 在干净环境验证 TestPyPI 产物
bash scripts/publish_pypi.sh
source scripts/clear_release_env.sh
```

发布操作必须由维护者明确执行；安全工作流不会自动上传 PyPI。

## 紧急凭证轮换

1. 在 PyPI/TestPyPI 撤销暴露的 token。
2. 检查仓库、提交历史、Actions 日志和 artifact 是否包含凭证。
3. 创建新的最小范围 token。
4. 只在本地发布 shell 中设置新 token。
5. 完成复盘并补充防复发措施。

操作清单：`scripts/revoke_checklist.md`。
