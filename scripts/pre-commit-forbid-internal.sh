#!/usr/bin/env bash
# Pre-commit hook: 拦截包含内部/敏感标记的文档提交
# 检查范围：文件名 + 文件内容

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

VIOLATIONS=0

# ============================================================
# 1. 敏感文件名模式
# ============================================================
SENSITIVE_NAME_PATTERNS=(
  "*audit*"
  "*revoke*"
  "*checklist*"
  "*internal*"
  "*confidential*"
  "*fix_plan*"
  "*release_test*"
  "*security*"
  "*P0*"
  "*应急*"
  "*安全*"
  "*STABILITY*"
  "*CONFIG_AUTHORITY*"
  "*AGENT_TASK*"
)

# ============================================================
# 2. 敏感内容标记
# ============================================================
SENSITIVE_CONTENT_PATTERNS=(
  "内部文档"
  "内部团队文档"
  "内部安全审计"
  "内部架构决策"
  "内部开发规范"
  "内部设计文档"
  "安全审计报告"
  "P0 风险"
  "P0风险"
  "应急响应手册"
  "应急响应流程"
  "token.*泄露.*应急"
  "安全弱点"
  "未修复的漏洞"
  "撤销流程.*暴露"
  "安全响应机制"
  "CONFIDENTIAL"
  "INTERNAL USE ONLY"
  "DO NOT DISTRIBUTE"
)

# ============================================================
# 3. 允许名单 — 这些文件即使匹配模式也放行
# ============================================================
ALLOWLIST=(
  ".pre-commit-config.yaml"
  "scripts/pre-commit-forbid-internal.sh"
)

# ============================================================
# 获取暂存区文件列表（新增 + 修改，排除删除）
# ============================================================
get_staged_files() {
  git diff --cached --name-only --diff-filter=ACM
}

# 检查文件是否在允许名单中
is_allowlisted() {
  local file="$1"
  for allowed in "${ALLOWLIST[@]}"; do
    if [[ "$file" == "$allowed" ]]; then
      return 0
    fi
  done
  return 1
}

# ============================================================
# 4. 执行检测
# ============================================================
STAGED_FILES=$(get_staged_files)

if [[ -z "$STAGED_FILES" ]]; then
  exit 0
fi

while IFS= read -r file; do
  # 跳过空行
  [[ -z "$file" ]] && continue
  # 跳过允许名单
  is_allowlisted "$file" && continue

  basename=$(basename "$file")

  # --- 文件名检查 ---
  for pattern in "${SENSITIVE_NAME_PATTERNS[@]}"; do
    # 用 case 做 glob 匹配
    # shellcheck disable=SC2251
    case "$basename" in
      $pattern)
        echo -e "${RED}[禁止提交]${NC} 文件名匹配敏感模式: ${YELLOW}$file${NC}"
        echo "           匹配规则: $pattern"
        echo "           原因: 文件名表明这可能是内部/敏感文档"
        VIOLATIONS=$((VIOLATIONS + 1))
        break
        ;;
    esac
  done

  # --- 文件内容检查（仅对文本文件，跳过二进制） ---
  if file "$file" 2>/dev/null | grep -qE 'text|JSON|empty'; then
    while IFS= read -r content_pattern; do
      if git show ":$file" 2>/dev/null | grep -qi "$content_pattern" 2>/dev/null; then
        echo -e "${RED}[禁止提交]${NC} 文件内容包含敏感标记: ${YELLOW}$file${NC}"
        echo "           匹配内容: \"$content_pattern\""
        echo "           原因: 文件内容包含内部/安全敏感信息"
        VIOLATIONS=$((VIOLATIONS + 1))
        break
      fi
    done <<< "$(printf '%s\n' "${SENSITIVE_CONTENT_PATTERNS[@]}")"
  fi

done <<< "$STAGED_FILES"

# ============================================================
# 5. 结果
# ============================================================
if [[ $VIOLATIONS -gt 0 ]]; then
  echo ""
  echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${RED}  ✗ 发现 $VIOLATIONS 处违规，提交已被拦截${NC}"
  echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
  echo "如果确认文件可以提交，请："
  echo "  1. 将它添加到允许名单: 编辑此脚本的 ALLOWLIST 数组"
  echo "  2. 或者将文件加入 .gitignore"
  echo "  3. 或者重命名/删除敏感标记后重新提交"
  exit 1
fi

exit 0
