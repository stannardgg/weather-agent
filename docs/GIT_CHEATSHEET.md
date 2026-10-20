# Git 命令行速查（Windows / Git Bash）

> 适用场景：本项目日常提交、撤销误操作、排查「文件莫名显示已修改」。
> 前置约定：仓库使用 `main` 分支，远程 `origin` 指向 GitHub，凭据已存在 Windows 凭据管理器，推送无需输密码。

## 一、每日循环（四条命令，按顺序）

```bash
cd D:/project/python/demo1/weather-agent   # 进项目目录
git status                                  # 看改了什么（必做，这是安全检查点）
git add -A                                  # 全部暂存
git commit -m "feat: 本次改了什么"           # 存档到本地
git push                                    # 上传到 GitHub
```

`git status` 的三种标记：

| 标记 | 含义 | 下一步 |
| --- | --- | --- |
| `M` 红色 | 已跟踪文件被修改，未暂存 | `git add` |
| `??` 红色 | 新文件，Git 完全不知道 | `git add` |
| `M` 绿色 | 已暂存，等提交 | `git commit` |

**看到 `.env`、`*.db`、`*.exe`、`logs/` 出现在任何列表里，立刻停下**——那说明 `.gitignore` 失效了。

提交信息前缀：`feat:` 新功能 / `fix:` 修 bug / `docs:` 文档 / `chore:` 杂务 / `build:` 构建与依赖 / `refactor:` 重构。

## 二、只看某个文件改了什么

```bash
git diff                        # 未暂存的改动（工作区 vs 暂存区）
git diff --staged               # 已暂存的改动（暂存区 vs 上次提交）
git diff HEAD                   # 两者的总和
git diff app/core/config.py     # 只看单个文件
git log --oneline -10           # 最近 10 次提交
git log -p app/core/config.py   # 某文件的完整修改史
```

## 三、改错了怎么撤（按严重程度排序）

**场景 1：改了文件，还没 add，想恢复原样**

```bash
git restore app/core/config.py   # 单个文件
git restore .                    # 全部（危险，会丢掉所有未提交改动）
```

**场景 2：已经 add 了，想撤回暂存（改动保留）**

```bash
git restore --staged app/core/config.py
```

**场景 3：已经 commit，还没 push，想改提交信息**

```bash
git commit --amend -m "新的提交信息"
```

**场景 4：已经 commit，还没 push，想连内容一起改**

```bash
git reset --soft HEAD~1    # 撤销提交，改动全部回到暂存区
# 改文件，然后重新 commit
```

**场景 5：已经 push 了**

不要用 `reset`。本地历史被改写而远程没变，下次 push 会冲突，只能强推（`--force`），会覆盖别人的工作。

```bash
git revert <commit哈希>    # 生成一个新的「反向提交」，历史保持完整
git push
```

**场景 6：彻底搞不清楚状态了**

```bash
git status          # 永远先看这个，它总会告诉你下一步该干什么
git log --oneline --graph --all   # 看整体历史形状
```

## 四、本项目特有的三个坑

**1. 换行符**

`.gitattributes` 规定仓库内存 LF（`* text=auto eol=lf`），并已设 `core.autocrlf=false`。
若某文件 `git status` 显示已修改但 `git diff` 是空的，是文件 stat 缓存过期，不是真改动：

```bash
git update-index --refresh
```

**2. 并发修改**

本项目可能同时有多个会话/编辑器在改同一批文件。两个进程同时 `git add` 会互相覆盖暂存区。
操作前先 `git status` 确认只有你自己的改动，或只暂存具体文件而不是 `git add -A`：

```bash
git add app/core/config.py app/api/v1/chat.py   # 精确暂存，不动别人的半成品
```

**3. 生成物不要进版本库**

以下路径已在 `.gitignore` 中，不要用 `git add -f` 强行加入：
`.venv/`、`data/*.db`、`data/models/`、`logs/`、`build/`、`dist/`、`*.py[cod]`、`.env`、`.workbuddy/`。

## 五、同步远程（多人协作或换机器时）

```bash
git fetch           # 只下载远程信息，不改工作区（安全）
git pull            # 下载并合并到当前分支（已设 pull.ff only，不会产生意外的合并提交）
git push            # 上传
```

`git pull` 报 `diverged` 时，说明本地和远程都有新提交，需要先合并：

```bash
git pull --no-ff    # 本次允许生成合并提交
```

## 六、当前环境事实（2026-10-20 核实）

- Git 版本 `2.55.0.windows.3`
- 远程：`https://github.com/stannardgg/weather-agent.git`（public，`main`）
- 凭据：`credential.helper=manager`，Windows 凭据管理器已存 token
- 未安装 `gh` CLI，查 CI 结果需在浏览器打开仓库的 **Actions** 页
- 配置：`core.autocrlf=false`、`push.default=simple`、`pull.ff=only`、`core.safecrlf=warn`
