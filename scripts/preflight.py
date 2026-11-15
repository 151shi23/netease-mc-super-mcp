#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网易 ModSDK 工程静态体检（确定性检查，不替代官方 review_code）。

用法:
    python preflight.py --project /path/to/MyAddon
    python preflight.py --project . --strict

检查项:
    1. behavior_pack / resource_pack 的 manifest.json 是否存在
    2. manifest 中 uuid 是否缺失或重复
    3. Python 文件语法是否符合指定基线（默认 3，传 --python-baseline 2 回退）
    4. 同一文件是否混用 mod.client 与 mod.server
    5. 按基线检查禁用语法（2.7: f-string/注解/u 前缀/print end；3.x: xrange 等 Py2 残留）

退出码: 0 无 critical；1 存在 critical；2 参数/路径错误
"""

import argparse
import json
import os
import re
import sys

FSTRING = re.compile(r"(?<![\w\)\]\}])[fF][\"']")
U_PREFIX = re.compile(r"(?<![\w\)\]\}\"'])[uU][rR]?[\"']")
TYPE_ANNO = re.compile(r"def\s+\w+\s*\([^)]*:[^)=\n]*\)|def\s+\w+\s*\([^)]*\)\s*->")
PRINT_END = re.compile(r"print\s*\([^)]*\bend\s*=")
PY2_ONLY = re.compile(r"\b(xrange|iteritems|iterkeys|itervalues|basestring|raw_input|unicode)\s*\(")
CLIENT_IMPORT = re.compile(r"^\s*(?:import|from)\s+mod\.client", re.M)
SERVER_IMPORT = re.compile(r"^\s*(?:import|from)\s+mod\.server", re.M)
ENCODING = re.compile(r"coding[:=]\s*([-\w.]+)")


class Report(object):
    def __init__(self):
        self.items = []

    def add(self, level, path, message):
        self.items.append((level, path, message))

    def count(self, level):
        return sum(1 for i in self.items if i[0] == level)

    def dump(self):
        order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
        for level, path, message in sorted(self.items, key=lambda x: (order.get(x[0], 9), x[1])):
            print("[%-8s] %s" % (level, message))
            if path:
                print("           %s" % path)


def check_manifest(report, root):
    found = []
    for base, dirs, files in os.walk(root):
        if "manifest.json" in files:
            found.append(os.path.join(base, "manifest.json"))
        # 不深入 .git / 依赖目录
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__")]

    if not found:
        report.add("CRITICAL", root, "未找到任何 manifest.json")
        return

    uuids = {}
    for path in found:
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            report.add("CRITICAL", path, "manifest.json 无法解析: %s" % exc)
            continue

        header = data.get("header") or {}
        hu = header.get("uuid")
        if not hu:
            report.add("CRITICAL", path, "header.uuid 缺失")
        else:
            uuids.setdefault(hu, []).append(path)

        modules = data.get("modules") or []
        if not modules:
            report.add("WARN", path, "modules 为空")
        for idx, mod in enumerate(modules):
            mu = mod.get("uuid")
            if not mu:
                report.add("CRITICAL", path, "modules[%d].uuid 缺失" % idx)
            else:
                uuids.setdefault(mu, []).append("%s#modules[%d]" % (path, idx))
            if mod.get("type") not in ("data", "resources"):
                report.add("WARN", path, "modules[%d].type=%r 非 data/resources" % (idx, mod.get("type")))

    for uuid, paths in uuids.items():
        if len(paths) > 1:
            report.add("CRITICAL", ", ".join(paths), "uuid 重复: %s" % uuid)

    report.add("INFO", "", "发现 %d 个 manifest.json" % len(found))


def check_python(report, root, baseline=2):
    count = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            count += 1
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except UnicodeDecodeError:
                report.add("CRITICAL", path, "文件不是 UTF-8 编码（Py2 需要编码声明）")
                continue

            first_two = "\n".join(text.splitlines()[:2])
            has_encoding = bool(ENCODING.search(first_two))
            has_non_ascii = any(ord(ch) > 127 for ch in text)

            if baseline == 2 and has_non_ascii and not has_encoding:
                report.add("WARN", path, "含非 ASCII 但缺少 # -*- coding: utf-8 -*-")

            for lineno, line in enumerate(text.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                if baseline == 2:
                    if FSTRING.search(line):
                        report.add("CRITICAL", "%s:%d" % (path, lineno), "检测到 f-string（2.7 基线不支持）")
                    if U_PREFIX.search(line):
                        report.add("CRITICAL", "%s:%d" % (path, lineno), "检测到 u/U 字符串前缀（2.7 基线禁止）")
                    if PRINT_END.search(line):
                        report.add("CRITICAL", "%s:%d" % (path, lineno), "print(..., end=) 不适用于 2.7 基线")
                    if TYPE_ANNO.search(line):
                        report.add("CRITICAL", "%s:%d" % (path, lineno), "检测到类型注解（2.7 基线不支持）")
                else:
                    if PY2_ONLY.search(line):
                        report.add("CRITICAL", "%s:%d" % (path, lineno), "检测到 Py2 专属写法（3.x 基线不支持）")

            if CLIENT_IMPORT.search(text) and SERVER_IMPORT.search(text):
                report.add("CRITICAL", path, "同一文件混用 mod.client 与 mod.server")

    report.add("INFO", "", "扫描 %d 个 Python 文件" % count)


def main():
    parser = argparse.ArgumentParser(description="NetEase ModSDK project preflight")
    parser.add_argument("--project", required=True, help="工程根目录")
    parser.add_argument("--strict", action="store_true", help="WARN 也视为失败")
    parser.add_argument("--python-baseline", choices=["2", "3"], default="3",
                        help="运行时 Python 基线，默认 3（游戏内脚本已升级）；实测为 2.7 时传 2")
    args = parser.parse_args()

    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        print("[FATAL] 不是目录: %s" % root, file=sys.stderr)
        return 2

    report = Report()
    check_manifest(report, root)
    check_python(report, root, int(args.python_baseline))

    print("=" * 64)
    print("工程: %s" % root)
    print("Python 基线: %s" % args.python_baseline)
    print("=" * 64)
    report.dump()
    print("-" * 64)
    critical = report.count("CRITICAL")
    warn = report.count("WARN")
    print("CRITICAL=%d  WARN=%d" % (critical, warn))
    print("提醒: 本脚本只做确定性检查，最终仍需通过官方 review_code。")

    if critical:
        return 1
    if args.strict and warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
