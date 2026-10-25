# 媒体下载器：在 GitHub 上打包 Windows 和 Mac

已配置 GitHub Actions 云端构建，带葡萄紫云朵＋柠檬黄下载箭头图标。你只需要上传源码并在网页点击 Run workflow，无需在自己的电脑安装打包依赖。

## 1. 新建仓库

1. 打开 [GitHub 新建仓库页面](https://github.com/new)，填写自己喜欢的仓库名，例如 `media-downloader`。
2. 选择 Public 或 Private；不想公开源码就选择 Private。
3. 点击 Create repository。使用网页上传时，无需安装 Git 或 GitHub CLI。

## 2. 上传这个项目

解压下载的项目包，把 `github-actions-project` 文件夹**里面的内容**上传到仓库根目录，避免多套一层外部文件夹。不要把整个 ZIP 当成一个文件上传。

仓库根目录应能直接看到以下内容：

```text
.github/
  workflows/
    build.yml
assets/
  app.png
  app.icns
  app.ico
scripts/
  package_artifact.py
tests/
drive_image_downloader.py
requirements-runtime.txt
requirements-facebook.txt
requirements-build.txt
媒体下载器.spec
README.md
```

**特别注意 `.github` 是隐藏文件夹。** Mac 在 Finder 按 Command + Shift + . 可显示隐藏文件；上传时也要选中它。上传后，必须在仓库网页上确认存在 `.github/workflows/build.yml`。

如果浏览器上传漏掉 `.github`，就在仓库点击 Add file → Create new file，将文件名写为 `.github/workflows/build.yml`，把本包中同名文件的全部内容粘贴进去，再提交。

把这些文件提交到仓库的默认分支。手动运行按钮要求工作流文件位于默认分支，见 [GitHub 官方说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。

## 3. 在网页点击打包

1. 打开仓库顶部 Actions。
2. 如果首次出现启用提示，启用该仓库的 Actions。
3. 左侧选择 **Build desktop apps**。
4. 点击 **Run workflow**，选择默认分支，再点击绿色的 Run workflow。
5. 打开新产生的运行记录，等待三个任务完成。

这个流程只手动运行；上传文件本身不会立即启动打包，也不需要你填写 Token 或签名密钥。

## 4. 下载成品

打开本次运行页面，在底部 **Artifacts** 下载对应平台的文件：

| Artifact 名称 | 对应电脑 | 内部成品 |
| --- | --- | --- |
| `media-downloader-windows-x64` | Windows 64 位 Intel/AMD 电脑 | ZIP 内的 `媒体下载器.exe`，单文件版 |
| `media-downloader-macos-arm64` | Apple 芯片 Mac（M 系列） | ZIP 内的 `媒体下载器.app` |
| `media-downloader-macos-x64` | Intel Mac | ZIP 内的 `媒体下载器.app` |

Artifacts 下载得到的外层压缩包里有应用 ZIP 和 SHA-256 校验文件；继续解压应用 ZIP 后使用。Windows 成品无需安装 Python。Mac 应用使用原生 ditto 压缩，保留执行权限、符号链接和应用包结构。成品保留 14 天，可以重新运行工作流生成。

使用分开的 Windows/macOS runner 原生构建，因为 [PyInstaller 不提供跨操作系统交叉编译](https://www.pyinstaller.org/en/stable/)。Mac 架构标签依据 [GitHub 官方 runner 列表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)，没有把 Intel 与 Apple 芯片混为一份。

## 构建过程中检查什么

- 使用 Python 3.13，安装固定版本的项目依赖，执行 pip check。
- 检查 Tkinter 模块可导入，然后运行全部现有回归测试。
- Windows 使用 `.ico`，Mac 使用 `.icns`；两者都来自本次选定的同一枚图标。
- Mac 打包后检查架构、图标内容和 ad-hoc 签名完整性；Windows 检查 x64 PE 架构和图标资源。
- 只有构建与检查成功，才上传对应平台的 Artifact。
- Actions 固定到官方仓库的完整提交 SHA，checkout 不保留凭据，仓库权限只有 contents: read。

Windows 的单文件构建会在 `build/spec-windows` 生成临时 spec，不覆盖仓库里用于 Mac 的 `媒体下载器.spec`。

## 验证状态与边界

项目的现有 42 项测试已在本地 macOS 环境通过，选定图标的 macOS 构建也已通过。本次 GitHub 配置在本地完成 YAML 解析、配置检查、图标格式检查，以及 macOS 成品验证/压缩脚本检查。

**仓库尚未创建，因此还没有实际运行过这三个 GitHub 云端任务。** Windows 和 Intel Mac 成品仍须以首次 GitHub 运行结果为准，不将本地 macOS 通过冒充为所有平台已通过。

测试不代表已验证真实下载、系统弹窗和全部 GUI 操作；请在对应电脑上双击成品，确认启动和下载后再分发。当前流程不执行 Windows 代码签名或 Apple Developer ID 签名/公证，也不依赖这些平台的付费证书。

原来的 Mac 本地打包脚本也保留在源码中，供以后需要时使用；GitHub 流程不调用它。

## 固定的官方 Actions

- [actions/checkout v7.0.1](https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1)
- [actions/setup-python v7.1.0](https://github.com/actions/setup-python/commit/6d4fd22b20465ccc0f7354476588e8df7c9ca0e0)
- [actions/upload-artifact v7.0.2](https://github.com/actions/upload-artifact/commit/cf430e030ddbb5b0abf93d22962f4752f3646cd9)

需要更新 Actions 时，应核实官方发布与提交，再更新完整 SHA。

