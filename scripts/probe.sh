#!/usr/bin/env bash
# web-search 技能的环境探测脚本。
# 三项独立检查：curl、python3、浏览器打开器。

set -u

CORE_OK=1

echo "web-search environment probe"
echo "----------------------------"

# 1) 检查 curl
printf '[1/3] curl: '
if command -v curl >/dev/null 2>&1; then
    CURL_VERSION=$(curl --version 2>/dev/null | head -n 1)
    echo "OK (${CURL_VERSION:-version unknown})"
else
    echo "MISSING"
    CORE_OK=0
fi

# 2) 检查 python3 及所需标准库模块
printf '[2/3] python3: '
if command -v python3 >/dev/null 2>&1; then
    PY_VERSION=$(python3 -c 'import sys; print(sys.version.split()[0])' 2>/dev/null)
    if python3 -c 'import json, urllib.parse, xml.etree.ElementTree, subprocess, tempfile' >/dev/null 2>&1; then
        echo "OK (${PY_VERSION:-version unknown}, stdlib modules available)"
    else
        echo "BROKEN (${PY_VERSION:-version unknown}, missing standard-library modules)"
        CORE_OK=0
    fi
else
    echo "MISSING"
    CORE_OK=0
fi

# 3) 检查浏览器打开器（可选，仅用于手动打开回退 URL）
printf '[3/3] browser opener: '
if command -v xdg-open >/dev/null 2>&1; then
    echo "OK (xdg-open)"
elif command -v open >/dev/null 2>&1; then
    echo "OK (open)"
else
    echo "MISSING (optional; open fallback URLs manually)"
fi

echo "----------------------------"
if [ "$CORE_OK" -eq 1 ]; then
    echo "core dependencies available"
    exit 0
else
    echo "required dependency missing: install curl and python3" >&2
    exit 1
fi
