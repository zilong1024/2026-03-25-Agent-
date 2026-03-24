# GitHub 接入与阶段同步说明

## 目标
- 保持当前 `origin`（GitLab）不变。
- 新增一个 `github` 远端用于审阅同步。
- 每个阶段完成后执行一次 `commit + push`。

## 一次性接入步骤

1. 在 GitHub 创建空仓库（建议不勾选 README / .gitignore / License）。
2. 在本地仓库执行：

```bash
cd /home/h3c/zilong1024/260324-Agent笔试题/quant_react_interview
git remote add github <你的GitHub仓库URL>
git remote -v
```

3. 首次推送：

```bash
git push -u github main
```

## 后续阶段同步（固定动作）

```bash
git add <本阶段改动文件>
git commit -m "<清晰描述本阶段目标>"
git push
```

> 说明：完成上面的 `-u` 绑定后，后续在 `main` 分支可直接 `git push`（会推到 `github/main`）。

## 认证方式建议
- 推荐 SSH：仓库 URL 形如 `git@github.com:<user>/<repo>.git`，推送时最稳定。
- 如使用 HTTPS：建议使用 GitHub PAT（Personal Access Token）进行认证。
