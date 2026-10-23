# 07 · 打包与隐私白名单

状态：**FROZEN**（2026-10-23）
背景：v1.1.0 之前的发布包**误带随包商业字体**；更早还误带过使用者数据（下载清单含本机路径/哈希、8 万多行创作者名录）。

## 1. datas 白名单

**只允许**：`data/node_svg`（悬停提示的 SVG 模板）。

已挡：`webtree / manifest / webapi / api_config.json / fonts / *.wbt / *.apientry.json / .env / 私钥`。

原则：白名单里**永远别放"可能有用"的东西**；新需求进来先问"不发会怎样"。

## 2. 发版三步（缺一步不发）

```bash
python -X utf8 tools/check_no_secrets.py --all
python -X utf8 tools/check_no_secrets.py --history
# 第三步：解压发行 zip，逐条核对中央目录（字体那次就是这一步查出来的）
python -X utf8 tools/audit_zip.py dist/PortPanel-<ver>.zip
```

## 3. 验收

1. 发布包内 `data/` 只有 `node_svg/*.svg`；
2. zip 审计脚本 0 命中；
3. `check_no_secrets --history` 0 命中。

## 4. 风险

| 风险 | 处置 |
|---|---|
| 依赖库自带字体/样例数据被打进去 | spec 里显式 `excludes`，发版前看 spec diff |
| 新功能把配置写进仓库 | `.gitignore` + 闸门双保险 |
