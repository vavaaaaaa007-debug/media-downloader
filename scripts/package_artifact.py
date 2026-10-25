"""Verify the platform build and create a downloadable archive for Actions."""
import argparse
import hashlib
from pathlib import Path
import plistlib
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("windows-x64", "macos-arm64", "macos-x64")


def verify_windows(binary):
    if sys.platform != "win32":
        raise RuntimeError("The Windows artifact must be built on Windows")
    import pefile
    with pefile.PE(str(binary)) as executable:
        if executable.FILE_HEADER.Machine != 0x8664:
            raise RuntimeError("Expected an x64 Windows executable")
        resources = getattr(executable, "DIRECTORY_ENTRY_RESOURCE", None)
        if resources is None or not any(entry.id == 14 for entry in resources.entries):
            raise RuntimeError("The executable does not contain an icon group")


def verify_macos(app, target):
    if sys.platform != "darwin":
        raise RuntimeError("The macOS artifact must be built on macOS")
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    icon_name = info["CFBundleIconFile"]
    if not icon_name.endswith(".icns"):
        icon_name += ".icns"
    bundled_icon = app / "Contents/Resources" / icon_name
    if bundled_icon.read_bytes() != (ROOT / "assets/app.icns").read_bytes():
        raise RuntimeError("The app icon does not match the selected project icon")
    binary = app / "Contents/MacOS" / info["CFBundleExecutable"]
    architectures = subprocess.check_output(
        ["/usr/bin/lipo", "-archs", str(binary)], text=True).split()
    expected = "arm64" if target == "macos-arm64" else "x86_64"
    if expected not in architectures:
        raise RuntimeError("Unexpected macOS executable architecture")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    archive = args.out_dir / ("media-downloader-" + args.target + ".zip")
    if archive.exists():
        raise FileExistsError("Remove the old generated archive before packaging again")

    if args.target == "windows-x64":
        binary = args.dist_dir / "媒体下载器.exe"
        verify_windows(binary)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            output.write(binary, binary.name)
            output.writestr(
                "运行说明.txt",
                "解压后双击 媒体下载器.exe，无需安装 Python。\n"
                "仅下载有权访问的公开文件，下载后请确认内容来源可信。\n"
                "图标为葡萄紫云朵与柠檬黄下载箭头。\n",
            )
    else:
        app = args.dist_dir / "媒体下载器.app"
        verify_macos(app, args.target)
        # ditto preserves the bundle's executable bits, symlinks and signatures.
        subprocess.run(
            ["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
             str(app), str(archive)],
            check=True,
        )

    with zipfile.ZipFile(archive) as output:
        bad_member = output.testzip()
        if bad_member is not None:
            raise RuntimeError("Archive integrity check failed")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = archive.with_suffix(".sha256")
    checksum.write_text(digest + "  " + archive.name + "\n", encoding="ascii")
    print("Created:", archive)
    print("SHA-256:", digest)


if __name__ == "__main__":
    main()
