#!/usr/bin/env bash
# weather-agent 一键提交推送（Git Bash / Linux / macOS）
#
# 用法：
#   bash push.sh       逐项确认（推荐）
#   bash push.sh -y    跳过确认，全自动
#
# 与 push.bat 功能对等，区别只是运行环境。详见 docs/GIT_CHEATSHEET.md
set -euo pipefail
cd "$(dirname "$0")"

AUTO=0
case "${1:-}" in
    -y|--yes) AUTO=1 ;;
esac

# 只在终端里上色，重定向到文件时不输出转义字符
if [ -t 1 ]; then
    RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BOLD=''; NC=''
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s[通过]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%s[提示]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[中止]%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

say "=========================================="
say "  weather-agent  提交并推送到 GitHub"
say "=========================================="
say

# ---------- 关卡 0：仓库检查 ----------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 Git 仓库。"

# ---------- 关卡 1：收集改动 ----------
STATUS=$(git status --porcelain)
if [ -z "$STATUS" ]; then
    warn "工作区没有未提交的改动。"
    say "检查是否有未推送的提交..."
    git push || die "推送失败，可能是远程有新提交或网络问题。"
    say
    say "${GREEN}[完成]${NC} 已是最新状态。"
    exit 0
fi

CHANGE_COUNT=$(printf '%s\n' "$STATUS" | wc -l | tr -d ' ')
say "检测到 ${BOLD}${CHANGE_COUNT}${NC} 项改动："
say "------------------------------------------"
printf '%s\n' "$STATUS"
say "------------------------------------------"
say

# ---------- 关卡 2：敏感文件拦截 ----------
# .env.example 是模板，必须排除，否则每次都会误报
DANGER=$(printf '%s\n' "$STATUS" \
    | grep -Ei '\.env|\.db|\.sqlite3|\.exe|(^|/)logs/' \
    | grep -vi '\.env\.example' || true)

if [ -n "$DANGER" ]; then
    printf '%s\n' "" "${RED}########################################################${NC}"
    printf '%s\n' "${RED}#  [严重] 检测到不该进版本库的敏感/生成文件：${NC}"
    printf '%s\n' "${RED}########################################################${NC}"
    printf '%s\n' "$DANGER"
    say
    say "  .env 含 API 密钥；*.db 是运行时数据；*.exe 是打包产物。"
    say "  一旦推送到 GitHub 就永久留在历史里，删不干净。"
    say
    say "  处理方式：确认 .gitignore 已覆盖这些路径，然后取消暂存："
    say "    git restore --staged <文件>"
    say
    die "已拦截，未推送任何内容。"
fi
ok "未发现敏感文件。"
say

# ---------- 关卡 3：确认 ----------
if [ "$AUTO" -eq 0 ]; then
    read -r -p "确认提交以上全部改动？[y/N] " CONFIRM
    case "$CONFIRM" in
        y|Y|yes|YES) ;;
        *) say; say "[已取消] 未做任何改动。"; exit 0 ;;
    esac
    say
    read -r -p "提交说明（直接回车用默认值）: " MSG
else
    MSG=""
fi

[ -z "${MSG:-}" ] && MSG="chore: 提交本地改动"

# ---------- 执行 ----------
say "[1/3] 暂存全部改动..."
git add -A

say "[2/3] 创建本地提交..."
if ! git commit -m "$MSG"; then
    die "提交失败。若提示 nothing to commit，说明改动已被处理。"
fi

say "[3/3] 推送到远程..."
if ! git push; then
    say
    warn "推送被拒绝，远程存在新提交。尝试变基后重推..."
    if ! git pull --rebase origin main; then
        say
        printf '%s[错误]%s 自动合并失败，存在代码冲突。\n' "$RED" "$NC"
        say "        解决冲突后执行：git rebase --continue"
        say "        想放弃本次合并：git rebase --abort"
        exit 1
    fi
    git push || die "变基后再次推送仍失败。"
fi

say
say "=========================================="
say "  ${GREEN}[完成]${NC} 已推送到 GitHub"
say "=========================================="
git log --oneline -1
say
say "查看 CI 结果：https://github.com/stannardgg/weather-agent/actions"
