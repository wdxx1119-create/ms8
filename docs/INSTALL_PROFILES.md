# MS8 Installation Profiles

MS8 0.2.16 将基础记忆能力与可选增强依赖分开。普通安装不再自动安装 Ollama、文档解析器或 OCR 依赖。

## 基础安装

```bash
pip install ms8
```

包含：

- 本地记忆、治理和检索主线
- MCP 连接
- 加密与恢复工具
- 基础规则检索

不包含：

- Ollama Python 客户端
- Absorb 文档解析依赖
- OCR Python 依赖
- 可选闭源策略后端

未安装增强依赖时，相关能力必须明确降级，不应阻止 `ms8 doctor`、基础写入、规则检索或 MCP 主线启动。

## 本地模型增强

```bash
pip install "ms8[llm]"
```

增加 Ollama Python 客户端。Ollama 服务和模型本体仍需用户独立安装和管理。

## Absorb 文档解析

```bash
pip install "ms8[absorb]"
```

增加目录监听、PDF 和 DOCX 解析依赖：

- watchdog
- pypdf
- python-docx

## OCR 增强

```bash
pip install "ms8[ocr]"
```

`ocr` profile 包含完整 `absorb` 依赖，并增加：

- pytesseract
- pdf2image
- Pillow

`ms8[absorb-ocr]` 作为兼容别名暂时保留，新的安装说明统一使用 `ms8[ocr]`。

Python 包不能自动提供 Tesseract、Poppler 等系统组件。OCR 启用前仍需根据操作系统安装对应系统工具；缺少系统工具时应报告可操作的降级信息，不得静默声称 OCR 已可用。

## 策略后端

```bash
pip install "ms8[policy]"
```

安装可选的 `ms8-policy-core` 后端。基础 MS8 不依赖该 profile，后端不可用时继续使用公开兼容路径，除非用户显式启用了严格 fail-closed 配置。

## 完整 Python 能力

```bash
pip install "ms8[full]"
```

包含 `llm`、`absorb`、`ocr` 和 `policy` 的 Python 依赖。系统级 OCR 工具仍不包含在内。

## 开发环境

```bash
pip install -e ".[dev]"
```

开发 profile 只提供测试、类型检查、构建和安全审计工具。需要验证某个增强能力时，应显式叠加对应 profile，例如：

```bash
pip install -e ".[dev,ocr]"
```

## 验证契约

CI 必须在独立虚拟环境中验证以下 profile：

```text
core
llm
absorb
ocr
policy
full
```

每个 profile 至少执行：

- wheel 安装
- `pip check`
- `import ms8`
- 对应可选依赖导入或 distribution metadata 校验

Release candidate 的核心 wheel 环境还必须确认未意外安装 `ollama`，防止可选依赖重新泄漏到基础安装。
