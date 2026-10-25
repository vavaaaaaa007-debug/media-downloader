#!/bin/zsh
# 在自己的虚拟环境中安装项目声明依赖；测试通过后才打包。
set -euo pipefail
cd -- "${0:A:h}"

if [[ "$OSTYPE" != darwin* ]]; then
    print -u2 -- "此脚本用于 macOS；请在 Mac 上打包 .app。"
    exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi

print -r -- "正在安装项目构建依赖……"
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip check

print -r -- "正在运行回归测试……"
.venv/bin/python -m unittest discover -s tests -v

print -r -- "正在生成带图标的应用……"
.venv/bin/python -m PyInstaller --clean --noconfirm 媒体下载器.spec

print -r -- "打包完成：$PWD/dist/媒体下载器.app"
print -r -- "请在正常桌面会话中双击应用，测试启动和下载功能后再分发。"
