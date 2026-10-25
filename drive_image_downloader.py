# -*- coding: utf-8 -*-
"""
媒体下载器（支持 Google Drive 和 Facebook Reels）
功能：
  1. 粘贴多个 Google Drive 分享链接（每行一个），批量下载到本地
     （图片和视频都支持，视频同样按链接下载）
  2. 支持 Facebook Reels 链接（https://www.facebook.com/reel/xxx），
     同样按顺序编号下载为 mp4（需要安装或更新下载依赖：python3 -m pip install -r requirements-facebook.txt）
  3. 手动设置起始序号，下载时按顺序自动重命名：
     例如起始序号=1、2位补零  ->  01.jpg, 02.jpg, 03.jpg ...（视频则为 01.mp4, 02.mp4 ...）
     例如起始序号=23         ->  23.jpg, 24.jpg, 25.jpg ...
  4. 文件扩展名（.jpg/.png/.mp4/.mov 等）自动从原文件获取，保持不变
  5. 序号位数可选：不补零 / 2位（01） / 3位（001）

运行环境：Python 3.10+ 和 Tkinter，Windows / Mac / Linux 通用。
          Drive 需安装 requirements-runtime.txt 中的安全证书库；
          Facebook Reels 另需 requirements-facebook.txt（见使用说明）。
使用方法：双击运行本文件，或在命令行执行  python drive_image_downloader.py
"""

import os
import re
import sys
import threading
import tempfile
import errno
import shutil
import stat
import ssl
import urllib.request
import urllib.parse
import urllib.error
from email.message import Message

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
DEFAULT_MAX_BYTES = 2 * 1024 ** 3
_LIMIT_MESSAGE = "文件大小超过单文件下载上限"
_SVG_CANCEL_MESSAGE = "已取消 SVG 下载：未确认文件来源可信"


class _StopRequested(Exception):
    """用户点了停止按钮时用于中断下载的内部异常。"""
    pass


class _DownloadLimitExceeded(Exception):
    """下载达到用户设置的单文件上限。"""
    pass


def _validate_max_bytes(max_bytes):
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("单文件下载上限必须为非负整数，0 表示不限")

CONTENT_TYPE_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
    "video/x-m4v": ".m4v",
}


def extract_file_id(text):
    """从一行文本中提取 Google Drive 文件 ID。支持 /file/d/<id>/ 形式和纯 ID。"""
    text = text.strip()
    if not text:
        return None
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text):
        return text
    return None


def is_facebook_url(text):
    """只接受完整的 Facebook HTTPS 链接，避免把任意 URL 交给提取器。"""
    try:
        url = urllib.parse.urlsplit(text.strip())
        return (url.scheme == "https" and url.hostname in {
            "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"
        } and url.username is None and url.password is None
            and url.port in (None, 443) and bool(url.path.strip("/"))
            and not any(c.isspace() or ord(c) < 32 for c in text))
    except ValueError:
        return False


def _validate_prefix(prefix):
    if (any(c in prefix for c in '/\\:')
            or any(ord(c) < 32 or ord(c) == 127 for c in prefix)):
        raise ValueError("文件名前缀不能包含路径分隔符、冒号或控制字符")


def _publish_download(src, dest):
    """同一文件系统内原子发布，不覆盖文件（包括悬空符号链接）。"""
    if os.name == "posix":
        # 硬链接保留源文件权限；yt-dlp 的成品可能受 umask 影响而对其他账号可读。
        # 发布前统一为仅当前用户读写，避免在可遍历的保存目录中意外泄露媒体。
        os.chmod(src, 0o600)
    stem, suffix = os.path.splitext(dest)
    candidate, k = dest, 0
    while True:
        try:
            os.link(src, candidate, follow_symlinks=False)
            break
        except FileExistsError:
            k += 1
            candidate = "%s(%d)%s" % (stem, k, suffix)
        except OSError as e:
            # 某些 Windows 网络盘/同步盘对硬链接返回 WinError 1（函数不正确），
            # 这类文件系统仍可通过排他复制完成发布。
            unsupported_link = getattr(e, "winerror", None) == 1
            if (not unsupported_link and
                    e.errno not in {errno.EPERM, errno.EXDEV, errno.ENOSYS,
                                    errno.ENOTSUP, errno.EOPNOTSUPP}):
                raise
            # exFAT 等不支持硬链接：排他创建仍保证不覆盖，但复制完成前可见。
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                k += 1
                candidate = "%s(%d)%s" % (stem, k, suffix)
                continue
            created = os.fstat(fd)
            try:
                with os.fdopen(fd, "wb") as target, open(src, "rb") as source:
                    shutil.copyfileobj(source, target)
            except Exception:
                try:
                    current = os.stat(candidate, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                        os.unlink(candidate)
                except OSError:
                    pass
                raise
            break
    os.unlink(src)
    return candidate


class _DriveRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or not (host in {"drive.google.com", "drive.usercontent.google.com"}
                        or host.endswith(".googleusercontent.com"))):
            raise urllib.error.URLError("拒绝非 Google HTTPS 下载重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_drive_url(url, timeout):
    if not check_certifi():
        raise urllib.error.URLError("安全证书依赖缺失或过旧，请安装 requirements-runtime.txt")
    import certifi
    # 显式使用已修补的项目 CA 库，不再继承可能仍含失陷根的系统信任库。
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.build_opener(
        _DriveRedirectHandler(), urllib.request.HTTPSHandler(context=context)).open(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)


def check_certifi():
    try:
        import certifi
        value = certifi.__version__
        return bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value)
                    and tuple(map(int, value.split("."))) >= (2026, 10, 19))
    except (ImportError, AttributeError, ValueError):
        return False


def check_yt_dlp():
    """同时拒绝存在已知高风险问题的旧下载依赖。"""
    try:
        from yt_dlp.version import __version__ as ytdlp_version
        import certifi
        import urllib3
        for value, minimum in ((ytdlp_version, (2026, 6, 9)),
                               (certifi.__version__, (2026, 10, 19)),
                               (urllib3.__version__, (2, 8, 0))):
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value):
                return False
            if tuple(map(int, value.split("."))) < minimum:
                return False
        return True
    except (ImportError, AttributeError, ValueError):
        return False


def download_facebook_video(url, dest_base, progress_cb=None, chunk_unused=None, stop_check=None,
                            max_bytes=DEFAULT_MAX_BYTES):
    _validate_max_bytes(max_bytes)
    if not is_facebook_url(url):
        return False, "无效的 Facebook HTTPS 链接"
    if not check_yt_dlp():
        return False, "Facebook 下载依赖缺失或版本过旧，请安装 requirements-facebook.txt 中的依赖"
    import yt_dlp
    limit_reached = False

    class _SafeLogger:
        # yt-dlp 在抛出异常前也会记录原文；其中可能有签名 URL、请求头或凭据。
        # 仅由下方的通用错误文案反馈失败，不把提取器消息写入终端或日志。
        def debug(self, message):
            nonlocal limit_reached
            # yt-dlp 可在发出 hook 前按响应长度跳过下载；保留原因但不记录原文。
            if max_bytes and "File is larger than max-filesize" in str(message):
                limit_reached = True

        def warning(self, message):
            pass

        def error(self, message):
            pass

    def hook(d):
        nonlocal limit_reached
        if stop_check and stop_check():
            raise _StopRequested()
        downloaded = d.get("downloaded_bytes") or 0
        if max_bytes and downloaded > max_bytes:
            limit_reached = True
            raise _DownloadLimitExceeded()
        if progress_cb and d.get("status") in {"downloading", "finished"}:
            progress_cb(downloaded,
                        d.get("total_bytes") or d.get("total_bytes_estimate"))

    # 私有随机目录隔离下载、分片和清理，避免匹配或删除用户文件。
    try:
        with tempfile.TemporaryDirectory(prefix=".fbdl-", dir=os.path.dirname(
                os.path.abspath(dest_base))) as temp_dir:
            opts = {
                "format": "b", "outtmpl": os.path.join(temp_dir, "video.%(ext)s"),
                "quiet": True, "no_warnings": True, "noprogress": True,
                "logger": _SafeLogger(),
                "noplaylist": True, "progress_hooks": [hook],
                "allowed_extractors": ["facebook.*"],
                "cachedir": False,
                "max_filesize": max_bytes or None,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url.strip(), download=True)
            if limit_reached:
                return False, _LIMIT_MESSAGE
            if stop_check and stop_check():
                return False, "已停止"
            cands = [os.path.join(temp_dir, n) for n in os.listdir(temp_dir)
                     if os.path.splitext(n)[1].lower() in VIDEO_EXTENSIONS
                     and stat.S_ISREG(os.lstat(os.path.join(temp_dir, n)).st_mode)]
            if len(cands) != 1:
                # 提取器可能提供大小并由 max_filesize 在写入前跳过。
                reported_size = info.get("filesize") if isinstance(info, dict) else None
                if max_bytes and isinstance(reported_size, (int, float)) and reported_size > max_bytes:
                    limit_reached = True
                    return False, _LIMIT_MESSAGE
                return False, "下载完成但未找到唯一的视频文件"
            src = cands[0]
            if max_bytes and os.stat(src, follow_symlinks=False).st_size > max_bytes:
                limit_reached = True
                return False, _LIMIT_MESSAGE
            final = _publish_download(src, dest_base + os.path.splitext(src)[1].lower())
            return True, final
    except Exception:
        if limit_reached:
            return False, _LIMIT_MESSAGE
        if stop_check and stop_check():
            return False, "已停止"
        # 提取器异常可能含带签名 URL、请求头或凭据，不直接回显。
        return False, "Facebook 下载失败：请检查链接、公开访问权限及网络连接"


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
MEDIA_EXTENSIONS = set(CONTENT_TYPE_TO_EXT.values()) | VIDEO_EXTENSIONS | {
    ".jpeg", ".tiff", ".heic", ".heif", ".avif", ".ico", ".3gp", ".mpeg", ".mpg"
}


def _get_ext_from_response(resp, file_id):
    """从响应头推断文件扩展名。"""
    cd = resp.headers.get("Content-Disposition", "")
    if cd:
        msg = Message()
        msg["Content-Disposition"] = cd
        fname = msg.get_filename()
        if fname:
            _, ext = os.path.splitext(fname)
            if ext.lower() in MEDIA_EXTENSIONS:
                return ext.lower()
    ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
    if ctype in CONTENT_TYPE_TO_EXT:
        return CONTENT_TYPE_TO_EXT[ctype]
    return ".bin"  # 未知类型保存为非可执行扩展名，不改变内容


def download_file(file_id, dest_path, progress_cb=None, chunk=65536, stop_check=None,
                  infer_extension=False, max_bytes=DEFAULT_MAX_BYTES, svg_confirm=None):
    """
    下载单个 Drive 文件到 dest_path。
    progress_cb(downloaded_bytes, total_bytes) 会被反复调用（total 可能为 None）。
    stop_check() 返回 True 时立即中断并删除未下完的文件。
    infer_extension=True 时，按实际下载响应确定扩展名，未知类型使用 .bin。
    max_bytes 为单文件上限，0 表示不限；SVG 需 svg_confirm() 明确确认。
    返回 (成功 True/False, 信息字符串)。
    """
    _validate_max_bytes(max_bytes)
    qid = urllib.parse.quote(file_id, safe="")
    # 新版直连地址（带 confirm=t，大文件也不弹确认页）；旧版地址作为备用
    urls = [
        "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t" % qid,
        "https://drive.google.com/uc?export=download&id=%s&confirm=t" % qid,
    ]

    def open_url(url):
        return _open_drive_url(url, timeout=60)

    tmp_path = None
    temp_dir = None
    resp = None
    last_err = None
    for url in urls:
        try:
            resp = open_url(url)
            ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
            if ctype != "text/html":
                break  # 拿到文件流，直接用
            # 仍是 HTML（旧版确认页），走下面的确认页逻辑
            break
        except Exception as e:
            last_err = e
            resp = None
    if resp is None:
        return False, "网络错误：无法连接 Google Drive"

    try:
        ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        # 大文件会返回一个确认页面（text/html），需要带 confirm 参数再请求一次
        if ctype == "text/html":
            html = resp.read(2 * 1024 * 1024).decode("utf-8", "ignore")
            m = re.search(r"confirm=([0-9A-Za-z_=-]+)", html)
            if not m:
                low = html.lower()
                if ("quota" in low or "too many" in low or "try again" in low
                        or "tente novamente" in low or "很抱歉" in html
                        or "temporarily unavailable" in low):
                    return False, ("谷歌暂时限制了下载（下载太频繁或配额用完），"
                                   "请过几小时再试，不要连续重复下载")
                return False, ("谷歌返回了确认页面但无法继续，可能是大文件限制或临时限制，"
                               "请稍后再试")
            confirm = m.group(1)
            url2 = ("https://drive.google.com/uc?export=download&id=" + qid
                    + "&confirm=" + urllib.parse.quote(confirm))
            try:
                resp.close()
                resp = open_url(url2)
            except Exception as e:
                return False, "网络错误：无法连接 Google Drive"
            ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
            if ctype == "text/html":
                return False, "仍被要求确认，可能是大文件或权限问题"

        response_ext = _get_ext_from_response(resp, file_id)
        if ((ctype == "image/svg+xml" or response_ext == ".svg")
                and (svg_confirm is None or not svg_confirm())):
            return False, _SVG_CANCEL_MESSAGE
        if infer_extension:
            dest_path = os.path.splitext(dest_path)[0] + response_ext
        total = resp.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None
        if max_bytes and total is not None and total > max_bytes:
            return False, _LIMIT_MESSAGE
        downloaded = 0
        # 私有目录阻止其他账号查看内部随机文件名；同 UID 或恶意父目录
        # 仍不属于完整防御边界，发布后的文件也受保存目录权限约束。
        temp_dir = tempfile.mkdtemp(prefix=".drive-", dir=os.path.dirname(
            os.path.abspath(dest_path)))
        fd, tmp_path = tempfile.mkstemp(prefix="media-", dir=temp_dir)
        was_stopped = False
        try:
            with os.fdopen(fd, "wb") as f:
                while True:
                    if stop_check and stop_check():
                        was_stopped = True
                        break
                    data = resp.read(chunk)
                    if not data:
                        break
                    if max_bytes and downloaded + len(data) > max_bytes:
                        raise _DownloadLimitExceeded()
                    f.write(data)
                    downloaded += len(data)
                    if progress_cb:
                        progress_cb(downloaded, total)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if was_stopped:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return False, "已停止"
        if total is not None and downloaded != total:
            raise OSError("下载长度不完整")
        final = _publish_download(tmp_path, dest_path)
        return True, final
    except _DownloadLimitExceeded:
        return False, _LIMIT_MESSAGE
    except Exception as e:
        try:
            if tmp_path is not None and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        # 保留简短、可操作的错误原因，避免所有失败都显示同一条模糊提示。
        detail = str(e).strip().replace("\r", " ").replace("\n", " ")
        if len(detail) > 240:
            detail = detail[:237] + "..."
        return False, ("下载失败：" + detail if detail else
                       "下载失败：请检查网络、磁盘空间与文件夹权限")
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        if temp_dir is not None:
            try:
                shutil.rmtree(temp_dir)
            except OSError:
                # 清理失败不得覆盖已确定的下载结果；私有目录仍保持访问限制。
                pass


def download_batch(links, start_number, out_dir, prefix="", zero_pad=0,
                   status_cb=None, file_progress_cb=None, overall_progress_cb=None,
                   stop_flag=None, max_bytes=DEFAULT_MAX_BYTES, svg_confirm=None):
    """
    批量下载（支持 Google Drive 链接和 Facebook Reels 链接混排）。
    start_number 必须为整数；不能把外部文本直接用作文件名序号。
    返回 (成功数, [(序号, 原始链接, 结果信息), ...])。
    """
    if isinstance(start_number, bool) or not isinstance(start_number, int):
        raise ValueError("起始序号必须是整数")
    _validate_prefix(prefix)
    _validate_max_bytes(max_bytes)
    out_dir = os.path.abspath(out_dir)
    results = []
    items = []  # (link, kind, file_id)
    for link in links:
        link = link.strip()
        if not link:
            continue
        # 注意：重复的链接不跳过，每一行都会下载并占用一个序号
        if is_facebook_url(link):
            items.append((link, "facebook", None))
            continue
        fid = extract_file_id(link)
        if fid:
            items.append((link, "drive", fid))
        else:
            results.append((None, link, "无法识别的链接"))

    total = len(items)
    ok_count = 0
    n = start_number
    for i, (link, kind, fid) in enumerate(items):
        if stop_flag and stop_flag():
            results.append((None, link, "已停止"))
            break
        if status_cb:
            status_cb("正在下载第 %d/%d 个 ..." % (i + 1, total))

        num_str = str(n)
        if zero_pad and zero_pad > 0:
            num_str = num_str.zfill(zero_pad)
        base_name = ("%s_%s" % (prefix, num_str)) if prefix else num_str

        def _cb(d, t, _i=i, _t=total):
            if file_progress_cb:
                file_progress_cb(_i, _t, d, t)

        if kind == "drive":
            # 后缀和正文取自同一次最终响应，避免探测失败或两次响应变化时误标。
            dest = os.path.join(out_dir, base_name + ".bin")
            success, info = download_file(fid, dest, progress_cb=_cb, stop_check=stop_flag,
                                          infer_extension=True, max_bytes=max_bytes,
                                          svg_confirm=svg_confirm)
        else:  # facebook
            tmp_base = os.path.join(out_dir, base_name)
            success, info = download_facebook_video(link, tmp_base, progress_cb=_cb,
                                                    stop_check=stop_flag, max_bytes=max_bytes)

        if info == "已停止":
            results.append((n, link, "已停止"))
            break

        if kind == "drive":
            if success:
                ok_count += 1
                results.append((n, link, os.path.basename(info)))
            else:
                results.append((n, link, "失败: " + info))
        else:  # facebook：已在隔离下载目录完成原子发布
            if success:
                ok_count += 1
                results.append((n, link, os.path.basename(info)))
            else:
                results.append((n, link, "失败: " + info))
        if overall_progress_cb:
            overall_progress_cb(i + 1, total)
        n += 1
    return ok_count, results


def _avoid_overwrite(dest):
    """如果目标文件已存在，自动改名，不覆盖。"""
    k = 1
    stem, suffix = os.path.splitext(dest)
    while os.path.lexists(dest):
        dest = "%s(%d)%s" % (stem, k, suffix)
        k += 1
    return dest


def probe_ext(file_id):
    """探测文件的扩展名（只读响应头，不下载正文）。"""
    qid = urllib.parse.quote(file_id, safe="")
    urls = [
        "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t" % qid,
        "https://drive.google.com/uc?export=download&id=%s" % qid,
    ]
    for url in urls:
        try:
            resp = _open_drive_url(url, timeout=30)
            try:
                ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
                if ctype == "text/html":
                    continue  # 确认页，换备用地址
                return _get_ext_from_response(resp, file_id)
            finally:
                resp.close()
        except Exception:
            continue
    return ".bin"


# ---------------------------------------------------------------- GUI
def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("媒体下载器")
    root.geometry("640x560")
    root.minsize(560, 480)

    # ---- 链接输入
    ttk.Label(root, text="1. 粘贴链接（每行一个，按粘贴顺序下载）：\n"
                         "   • Google Drive 分享链接（图片/视频）\n"
                         "   • Facebook Reels 链接（需安装 yt-dlp，见使用说明）").pack(anchor="w", padx=12, pady=(12, 4))
    txt = scrolledtext.ScrolledText(root, height=9, wrap="none", font=("Consolas", 10))
    txt.pack(fill="both", expand=False, padx=12)

    # ---- 参数区
    frm = ttk.Frame(root)
    frm.pack(fill="x", padx=12, pady=10)

    ttk.Label(frm, text="2. 起始序号：").grid(row=0, column=0, sticky="w")
    start_var = tk.StringVar(value="1")
    ttk.Entry(frm, textvariable=start_var, width=8).grid(row=0, column=1, sticky="w", padx=(0, 16))

    ttk.Label(frm, text="序号位数：").grid(row=0, column=2, sticky="w")
    pad_var = tk.StringVar(value="2位（01）")
    pad_box = ttk.Combobox(frm, textvariable=pad_var, width=10, state="readonly",
                           values=["不补零", "2位（01）", "3位（001）"])
    pad_box.grid(row=0, column=3, sticky="w")

    ttk.Label(frm, text="3. 保存文件夹：").grid(row=1, column=0, sticky="w", pady=(10, 0))
    out_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads", "drive_images"))
    ttk.Entry(frm, textvariable=out_var, width=52).grid(row=1, column=1, columnspan=2, sticky="we", pady=(10, 0))
    ttk.Button(frm, text="选择…", command=lambda: _choose_dir(out_var)).grid(row=1, column=3, sticky="w", pady=(10, 0))
    frm.columnconfigure(1, weight=1)

    ttk.Label(frm, text="单文件上限（GB）：").grid(row=2, column=0, sticky="w", pady=(10, 0))
    max_size_var = tk.StringVar(value="2")
    ttk.Entry(frm, textvariable=max_size_var, width=8).grid(
        row=2, column=1, sticky="w", pady=(10, 0))
    ttk.Label(frm, text="可调大；填 0 表示不限（1 GB = 1024³ 字节）").grid(
        row=2, column=2, columnspan=2, sticky="w", pady=(10, 0))

    # ---- 进度
    status_var = tk.StringVar(value="就绪")
    ttk.Label(root, textvariable=status_var).pack(anchor="w", padx=12)
    bar = ttk.Progressbar(root, mode="determinate", maximum=100)
    bar.pack(fill="x", padx=12, pady=4)

    # ---- 日志
    log = scrolledtext.ScrolledText(root, height=8, state="disabled", font=("Consolas", 9))
    log.pack(fill="both", expand=True, padx=12, pady=(4, 8))

    def log_msg(s):
        log.configure(state="normal")
        log.insert("end", s + "\n")
        log.see("end")
        log.configure(state="disabled")

    # ---- 按钮
    btn_frm = ttk.Frame(root)
    btn_frm.pack(fill="x", padx=12, pady=(0, 12))
    dl_btn = ttk.Button(btn_frm, text="开始下载")
    dl_btn.pack(side="left")
    stop_btn = ttk.Button(btn_frm, text="停止", state="disabled")
    stop_btn.pack(side="left", padx=8)
    open_btn = ttk.Button(btn_frm, text="打开保存文件夹",
                          command=lambda: _open_folder(out_var.get()))
    open_btn.pack(side="right")

    stop_requested = {"v": False}

    def close():
        stop_requested["v"] = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)

    def confirm_svg():
        # 网络线程等待结果；所有 Tk 调用仍由主线程执行。
        answered = threading.Event()
        decision = {"allow": False}

        def ask():
            try:
                if not stop_requested["v"]:
                    decision["allow"] = messagebox.askyesno(
                        "SVG 文件安全提醒",
                        "SVG 图片可能包含脚本，之后用浏览器打开时可能执行。\n\n"
                        "请仅下载和打开可信来源的文件。是否继续保存这个 SVG？",
                        parent=root, default="no")
            finally:
                answered.set()

        try:
            root.after(0, ask)
        except (RuntimeError, tk.TclError):
            return False
        while not answered.wait(0.1):
            if stop_requested["v"]:
                return False
        return decision["allow"] and not stop_requested["v"]

    def _choose_dir(var):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def run():
        raw = txt.get("1.0", "end").strip().splitlines()
        links = [l for l in (x.strip() for x in raw) if l]
        if not links:
            messagebox.showwarning("提示", "请先粘贴至少一个链接。")
            return
        try:
            start = int(start_var.get().strip())
        except ValueError:
            messagebox.showwarning("提示", "起始序号必须是整数。")
            return
        try:
            from decimal import Decimal, InvalidOperation
            size_gb = Decimal(max_size_var.get().strip())
            if not size_gb.is_finite() or size_gb < 0 or size_gb > 1000000:
                raise ValueError
            max_bytes = int(size_gb * 1024 ** 3)
            if size_gb > 0 and max_bytes == 0:
                raise ValueError
        except (InvalidOperation, ValueError, OverflowError):
            messagebox.showwarning("提示", "单文件上限须为 0 到 1000000 之间的 GB 数；0 表示不限。")
            return
        if not check_certifi():
            messagebox.showwarning(
                "需要安全证书依赖",
                "请先安装或更新项目安全证书库，再重新打开软件：\n\n"
                "python3 -m pip install -r requirements-runtime.txt\n"
                "Windows 可将 python3 改为 python。")
            return
        # 有 Facebook 链接时，提前检查 yt-dlp
        if any(is_facebook_url(l) for l in links) and not check_yt_dlp():
            messagebox.showwarning(
                "需要安装或更新下载依赖",
                "检测到 Facebook 链接，但下载依赖缺失或版本过旧，无法下载。\n\n"
                "请先安装（只需一次）：\n"
                "Mac / Linux（终端执行）：python3 -m pip install -r requirements-facebook.txt\n"
                "Windows（cmd 执行）：python -m pip install -r requirements-facebook.txt\n\n"
                "安装后重新打开本软件再下载。")
            return
        out_dir = out_var.get().strip() or os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", "无法创建保存文件夹：%s" % e)
            return
        pad_choice = pad_var.get()
        zero_pad = {"2位（01）": 2, "3位（001）": 3}.get(pad_choice, 0)

        dl_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        stop_requested["v"] = False
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

        def status_cb(s):
            root.after(0, lambda: status_var.set(s))

        def file_cb(i, total, d, t):
            def _u():
                if t:
                    bar["value"] = (i + d / t) / total * 100
                    status_var.set("正在下载第 %d/%d 个：%s / %s" % (i + 1, total, _fmt(d), _fmt(t)))
                else:
                    status_var.set("正在下载第 %d/%d 个：%s" % (i + 1, total, _fmt(d)))
            root.after(0, _u)

        def overall_cb(i, total):
            root.after(0, lambda: bar.configure(value=i / total * 100))

        def worker():
            ok, results = download_batch(
                links, start, out_dir, zero_pad=zero_pad,
                status_cb=status_cb, file_progress_cb=file_cb,
                overall_progress_cb=overall_cb,
                stop_flag=lambda: stop_requested["v"],
                max_bytes=max_bytes, svg_confirm=confirm_svg)
            def done():
                for num, link, info in results:
                    if num is None:
                        log_msg("[跳过] 无法识别的链接 -> %s" % info)
                    elif info == "已停止":
                        log_msg("[停止] 序号 %s -> 已停止" % num)
                    elif info.startswith("失败"):
                        log_msg("[失败] 序号 %s -> %s" % (num, info))
                    else:
                        log_msg("[成功] 序号 %s -> %s" % (num, info))
                log_msg("完成：成功 %d 个。" % ok)
                status_var.set("完成：成功 %d 个，保存到 %s" % (ok, out_dir))
                bar["value"] = 100
                dl_btn.configure(state="normal")
                stop_btn.configure(state="disabled")
            root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _fmt(n):
        if n >= 1048576:
            return "%.1f MB" % (n / 1048576)
        if n >= 1024:
            return "%.0f KB" % (n / 1024)
        return "%d B" % n

    dl_btn.configure(command=run)
    stop_btn.configure(command=lambda: stop_requested.update(v=True))

    root.mainloop()


def _open_folder(path):
    import subprocess
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", "--", os.path.abspath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.abspath(path)])
    except Exception:
        pass


if __name__ == "__main__":
    main()
