# -*- coding: utf-8 -*-
"""敏感内容闸门：入库 / 发布前扫一遍「不该进仓库的东西」——**只读，不改任何文件**。

## 它补的是哪个洞

`deploy.md` 的发布流程里有一步「内容级扫描」（四类特征必须全部为 0），但一直是**人工清单**：
* 2026-10-05 第一次发布：压缩包里真的带上了用户的下载记录与 4.1MB 创作者名录；
* 2026-10-11 拆包路径 bug：程序在包内重建 `data/`，加密 `api_config.json` 被 `git add -A` 收走。
两次都是「人忘了看」。本脚本把那份清单变成**可机械执行、可当闸门**的一步。

## 用法（仓库根，随时可跑）

    python -X utf8 tools/check_no_secrets.py             # 扫已跟踪文件（默认）
    python -X utf8 tools/check_no_secrets.py --all       # 连未跟踪但没被忽略的文件一起扫
    python -X utf8 tools/check_no_secrets.py --staged    # 只扫**已暂存**内容（给 pre-commit 用）
    python -X utf8 tools/check_no_secrets.py --history   # 扫全部历史 blob（审计用，慢一些）

判据：`exit 0` = 干净；`exit 1` = 有 FAIL 项。INFO 项（历史上曾入库、现已移除的敏感路径）
**不算失败** —— 那是事实记录，不是当前泄露。

要当 pre-commit 闸门（本机一次性）：

    python -X utf8 -c "import io,os;p='.git/hooks/pre-commit';io.open(p,'w',encoding='utf-8',newline='\\n').write('#!/bin/sh\\nexec python -X utf8 tools/check_no_secrets.py --staged\\n');os.chmod(p,0o755)"

## ⚠️ 输出纪律

命中项的**值一律打码**（只留前 3 字符 + 长度）。这个脚本自己会跳过自己
（源码里就写着这些正则，扫它会自我命中）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

MAX_BYTES = 2 * 1024 * 1024        # 超过就跳过（不可能是配置/凭据，扫它只浪费时间）

# ---- 路径规则：这些路径**出现即问题**（被跟踪 / 曾入库）----
PATH_RULES = [
    (r'\.wbt$', '图纸（.wbt 里原样存着请求头与 Cookie）'),
    (r'(^|/)api_config\.json$', '加密配置本体（只允许 api_config.default.json 空壳）'),
    (r'\.plain\.backup$', '明文备份'),
    (r'(^|/)secure_session\.json$', '会话盐 / 校验值'),
    (r'(^|/)ui_state\.json(\.tmp)?$', '本机引导「看过」状态'),
    (r'(^|/)ui_prefs\.json(\.tmp)?$', '本机 UI 偏好'),
    (r'(^|/)(webtree|manifest|webAPI)/', '图纸 / 下载清单 / 抓取结果目录'),
    (r'(^|/)logs?/', '运行期日志'),
    (r'(^|/)temp(_images)?/', '运行期临时目录'),
]

# ---- 内容规则：**值**级特征（命中即问题；值会打码）----
CONTENT_RULES = [
    (r'session=eyJ[A-Za-z0-9._\-]{10,}', 'JWT 形式的 session cookie'),
    (r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}', 'JWT'),
    (r'\bLTAI[A-Za-z0-9]{12,}\b', '阿里云 AccessKeyId'),
    (r'(?i)access[_-]?key[_-]?secret["\']?\s*[:=]\s*["\']?[A-Za-z0-9/+]{16,}', '阿里云 AccessKeySecret'),
    (r'[A-Za-z]:\\+Users\\+', '本机绝对路径（含用户名）'),
    (r'(?i)\blocal_path\b["\']?\s*[:=]\s*["\']([^"\']{4,})', '本机存储路径（2026-10-05 事故特征）'),
    (r'(?i)["\']?(?:authorization|cookie|set-cookie)["\']?\s*[:=]\s*["\']([^"\']{20,})["\']',
     '疑似真实 Cookie / Authorization'),
]

# 命中「疑似真实 Cookie」时还要过一道：占位符与短样例放过（否则源码注释会天天报警）
PLACEHOLDER_HINTS = ('${', 'ENV:', '<', 'PLACEHOLDER', 'YOUR_', 'xxx')


def git(*args: str) -> bytes:
    """跑一条 git 命令，返回 stdout（bytes）。失败返回 b''。"""
    try:
        out = subprocess.run(['git', *args], capture_output=True, check=False)
        return out.stdout if out.returncode == 0 else b''
    except OSError:
        return b''


def list_paths(*args: str) -> list:
    """`git ls-files -z` 风格：NUL 分隔，路径里的空格 / 中文都安全。"""
    return [p.decode('utf-8', 'replace') for p in git(*args).split(b'\0') if p]


def read_worktree(path: str):
    try:
        with open(path, 'rb') as f:
            return f.read(MAX_BYTES + 1)
    except OSError:
        return None


def looks_binary(blob: bytes) -> bool:
    return b'\0' in blob[:8192]


def mask(value: str) -> str:
    """打码：只留前 3 字符 + 长度。**绝不回显原值**。"""
    head = value[:3]
    return '%s…(len=%d)' % (head, len(value))


def scan_text(path: str, text: str, findings: list):
    """按 CONTENT_RULES 扫一段文本，命中就追加 (path, line_no, label, 打码值)。"""
    for i, line in enumerate(text.splitlines(), 1):
        if 'check_no_secrets' in line:          # 自引用（文档 / 注释）放过
            continue
        for pattern, label in CONTENT_RULES:
            m = re.search(pattern, line)
            if not m:
                continue
            value = m.group(1) if m.groups() else m.group(0)
            if 'Cookie' in label or 'Authorization' in label:
                low = value.lower()
                if any(h.lower() in low for h in PLACEHOLDER_HINTS):
                    continue                    # 占位符 / 样例
                if '=' not in value and not low.startswith(('bearer', 'basic')):
                    continue                    # 不像真 cookie
            findings.append((path, i, label, mask(value)))


def scan_paths(paths, findings):
    """路径规则：这些路径**出现即问题**。"""
    for p in paths:
        for pattern, label in PATH_RULES:
            if re.search(pattern, p):
                findings.append((p, 0, label, '（按路径判定）'))
                break


def check_gitignore_guard(problems):
    """`.gitignore` 里那几条 `**/` 加固必须还在（2026-10-11 事故的防线）。"""
    want = ('**/api_config.json', '**/secure_session.json', '**/ui_state.json', '**/webtree/')
    try:
        with open('.gitignore', encoding='utf-8') as f:
            body = f.read()
    except OSError:
        problems.append('.gitignore 读不到 —— 忽略规则可能被改坏')
        return
    for w in want:
        if w not in body:
            problems.append('.gitignore 缺了加固规则 `%s`（2026-10-11 事故防线）' % w)


def _scan_blobs(pairs, findings, scanned):
    """pairs = [(显示路径, 内容 bytes)]；二进制 / 超大 / 自己跳过。"""
    me = os.path.abspath(__file__)
    for path, blob in pairs:
        try:
            if os.path.abspath(path) == me:
                continue                     # 自己跳过自己（源码里就是那些正则）
        except (OSError, ValueError):
            pass
        if not blob or len(blob) > MAX_BYTES or looks_binary(blob):
            continue
        scanned[0] += 1
        scan_text(path, blob.decode('utf-8', 'replace'), findings)


def scan_tree(all_files, findings, scanned):
    """工作区：默认只扫已跟踪文件；`--all` 连未跟踪（未被忽略）的一起扫。"""
    args = ('ls-files', '--cached', '--others', '--exclude-standard', '-z') if all_files \
        else ('ls-files', '-z')
    paths = list_paths(*args)
    scan_paths(paths, findings)
    _scan_blobs([(p, read_worktree(p)) for p in paths], findings, scanned)


def scan_staged(findings, scanned):
    """只扫**已暂存**内容（pre-commit 闸门：读 index，不读工作区）。"""
    paths = list_paths('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')
    scan_paths(paths, findings)
    _scan_blobs([(p, git('show', ':' + p)) for p in paths], findings, scanned)


def scan_history(findings, info, scanned):
    """审计模式：扫全部历史 blob，并列出「曾入库、现已移除」的敏感路径。"""
    raw = git('rev-list', '--all', '--objects').decode('utf-8', 'replace')
    ever = {}
    for line in raw.splitlines():
        parts = line.split(' ', 1)
        if len(parts) == 2 and parts[1]:
            ever.setdefault(parts[1], []).append(parts[0])
    tip = set(list_paths('ls-tree', '-r', '--name-only', '-z', 'HEAD'))
    seen = set()
    pairs = []
    for path, shas in ever.items():
        for pattern, label in PATH_RULES:
            if re.search(pattern, path):
                info.append((path, label, '仍在 tip' if path in tip else '已从 tip 移除'))
                break
        for sha in shas:
            if sha in seen:
                continue
            seen.add(sha)
            pairs.append((path, git('cat-file', 'blob', sha)))
    _scan_blobs(pairs, findings, scanned)


def main() -> int:
    ap = argparse.ArgumentParser(description='敏感内容闸门（只读，不改任何文件）')
    ap.add_argument('--all', action='store_true', help='连未跟踪（非忽略）文件一起扫')
    ap.add_argument('--staged', action='store_true', help='只扫已暂存内容（pre-commit 用）')
    ap.add_argument('--history', action='store_true', help='扫全部历史 blob（审计用）')
    args = ap.parse_args()

    findings, info, scanned, problems = [], [], [0], []
    where = '工作区'

    if args.history:
        where = '历史'
        scan_history(findings, info, scanned)
    elif args.staged:
        where = '暂存区'
        scan_staged(findings, scanned)
    else:
        scan_tree(args.all, findings, scanned)

    if not args.history:
        check_gitignore_guard(problems)      # 历史模式不查忽略规则（规则本身也随版本变）

    print('扫描 %d 个文本内容（%s）' % (scanned[0], where))

    if info:
        print('\n[INFO] 历史里曾入库、现已从 tip 移除的敏感路径（事实记录，**不算失败**）：')
        for path, label, where_at in info:
            print('  - %s  ← %s（%s）' % (path, label, where_at))

    if problems:
        print('\n[WARN] 忽略规则：')
        for p in problems:
            print('  - %s' % p)

    if findings:
        print('\n[FAIL] 命中 %d 处（应为 0）：' % len(findings))
        for path, line, label, preview in findings:
            print('  - %s:%s  %s  值=%s' % (path, line or '-', label, preview))
        print('\n建议：把这些内容从工作区 / 暂存区拿掉（必要时 `git rm --cached`），再跑一次。')
        return 1

    if problems:
        return 1

    print('\nNO_SECRETS_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
