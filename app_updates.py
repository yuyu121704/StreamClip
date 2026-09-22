"""Public release discovery and verified Windows EXE staging; no account credentials."""

import hashlib
from html.parser import HTMLParser
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


VERSION = "2026.09.22.2"
REPOSITORY = "yuyu121704/StreamClip"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
MAX_PACKAGE_SIZE = 1024 * 1024 * 1024
LOCAL_HISTORY = [
    {"version": VERSION, "date": "2026-09-22", "summary": "竖屏切片改为16:9横向排版：左侧完整人物画面，右侧黑底居中白字绿边字幕；保留字幕时间轴，横屏沿用原样式。"},
    {"version": "2026.09.22.1", "date": "2026-09-22", "summary": "修复竖屏切片字幕过大：按显示宽度缩放字号和描边，保留上下边距比例，识别旋转视频；横屏字幕样式保持不变。"},
    {"version": "2026.09.20.3", "date": "2026-09-20", "summary": "降低媒体处理内存：限制 FFmpeg 解码、滤镜和编码线程，多项处理排队执行；保留分辨率、帧率、画质参数、字幕与严格校验。"},
    {"version": "2026.09.20.2", "date": "2026-09-20", "summary": "修复部分 H.264 录播从中间切片时缺少参考帧导致失败；自动从头解码重试，保留字幕时间轴和严格媒体校验。"},
    {"version": "2026.09.20.1", "date": "2026-09-20", "summary": "录制正常收尾；有限修复末尾损坏视频包并严格校验，避免异常帧率转码；失败保留分段时长。"},
    {"version": "2026.09.19.3", "date": "2026-09-19", "summary": "修复 GitHub API 受限时无法检查版本，保留正式版识别和更新包校验。"},
    {"version": "2026.09.19.2", "date": "2026-09-19", "summary": "新增版本中心：历史版本、更新检查与提醒、校验下载及安装重启。"},
    {"version": "2026.09.19.1", "date": "2026-09-19", "summary": "使用 Windows DPAPI 保护凭据，完善源码启动器和启动异常日志。"},
    {"version": "2026.09.19", "date": "2026-09-19", "summary": "精简切片列表载荷，修复反复刷新引起的界面内存上涨。"},
    {"version": "2026.09.18", "date": "2026-09-18", "summary": "首个公开版本，包含直播录制、AI 切片、成片预览与队列投稿。"},
]


class DownloadCancelled(Exception):
    pass


class UpdateFileError(ValueError):
    pass


class ReleaseUnavailable(ValueError):
    pass


def version_key(value):
    match = re.fullmatch(r"v?(\d{1,6}\.\d{1,6}\.\d{1,6}(?:\.\d{1,6})?)", str(value))
    if not match:
        raise ValueError("不支持的版本号")
    parts = tuple(map(int, match[1].split(".")))
    return parts + (0,) * (4 - len(parts))


def trusted_url(url, asset=False):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment:
        raise ValueError("更新地址不可信")
    host = parsed.hostname
    path = urllib.parse.unquote(parsed.path)
    if ".." in path.split("/") or "\\" in path:
        raise ValueError("更新地址不可信")
    allowed = host == "api.github.com" and path == f"/repos/{REPOSITORY}/releases"
    allowed |= host == "github.com" and path.startswith(f"/{REPOSITORY}/releases/download/")
    allowed |= host == "github.com" and not parsed.query and (
        path in {f"/{REPOSITORY}/releases/latest", f"/{REPOSITORY}/releases.atom"}
        or re.fullmatch(rf"/{re.escape(REPOSITORY)}/releases/tag/v?\d{{1,6}}\.\d{{1,6}}\.\d{{1,6}}(?:\.\d{{1,6}})?", path) is not None)
    allowed |= asset and host == "release-assets.githubusercontent.com"
    if not allowed:
        raise ValueError("更新地址不可信")
    return url


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl, asset=True)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and req.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


def open_release(url, method="GET"):
    trusted_url(url)
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": f"StreamClip/{VERSION}",
        "Accept": "application/vnd.github+json" if urllib.parse.urlsplit(url).hostname == "api.github.com" else "*/*",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        return urllib.request.build_opener(ReleaseRedirect()).open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in {403, 429}:
            raise ReleaseUnavailable("GitHub 请求受限，请稍后重试，或打开发布页面。") from None
        raise ReleaseUnavailable(f"更新服务返回 HTTP {exc.code}，请稍后重试。") from None
    except (OSError, urllib.error.URLError, HTTPException):
        raise ReleaseUnavailable("无法连接 GitHub，请检查网络或代理后重试。") from None


def release_history(remote=()):
    rows = {version_key(r["version"]): dict(r, notes=r["summary"], url="", published=False) for r in LOCAL_HISTORY}
    for row in remote:
        rows[version_key(row["version"])] = dict(row)
    for key, row in rows.items():
        row["current"] = key == version_key(VERSION)
    return sorted(rows.values(), key=lambda row: version_key(row["version"]), reverse=True)


def parse_releases(payload):
    if not isinstance(payload, list):
        raise ValueError("更新服务返回了无效的版本列表")
    releases = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("更新服务返回了无效的版本记录")
        if item.get("draft") or item.get("prerelease"):
            continue
        tag = str(item.get("tag_name") or "")
        try:
            version_key(tag)
        except ValueError:
            continue
        notes = str(item.get("body") or "此版本未提供更新说明。")[:60000]
        lines = [line.strip().lstrip("-* ").strip() for line in notes.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        summary = "；".join(lines[:3])[:360] or "此版本未提供更新说明。"
        assets = item.get("assets") or []
        if not isinstance(assets, list):
            raise ValueError("更新服务返回了无效的附件列表")
        asset = next((a for a in assets if isinstance(a, dict) and a.get("name") == f"StreamClip-{tag}-windows-x64.zip"), None)
        package = {}
        if asset:
            url = str(asset.get("browser_download_url") or "")
            digest = str(asset.get("digest") or "")
            size = asset.get("size")
            expected_url = f"{RELEASES_URL}/download/{tag}/StreamClip-{tag}-windows-x64.zip"
            if url == expected_url and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) and type(size) is int and 0 < size <= MAX_PACKAGE_SIZE:
                package = {"url": trusted_url(url), "sha256": digest[7:].lower(), "size": size}
        releases.append({"version": tag.removeprefix("v"), "date": str(item.get("published_at") or "")[:10],
                         "summary": summary, "notes": notes, "url": f"{RELEASES_URL}/tag/{tag}",
                         "published": True, "package": package})
    return sorted(releases, key=lambda row: version_key(row["version"]), reverse=True)


def read_release(url, limit=8 * 1024 * 1024):
    try:
        with open_release(url) as response:
            raw = response.read(limit + 1)
    except (OSError, HTTPException):
        raise ReleaseUnavailable("版本信息读取中断，请检查网络后重试。") from None
    if len(raw) > limit:
        raise ValueError("版本列表过大，请打开发布页面查看。")
    return raw


class ReleaseNotes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "li", "br", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        self.handle_starttag(tag, ())

    def handle_data(self, data):
        self.parts.append(data)


def fetch_latest_release():
    # Atom also contains prereleases; only /latest confirms a stable release.
    with open_release(RELEASES_URL + "/latest", method="HEAD") as response:
        url = response.geturl()
    trusted_url(url)
    tag = url.rsplit("/", 1)[-1]
    version_key(tag)
    if url != f"{RELEASES_URL}/tag/{tag}":
        raise ValueError("无法确认最新正式版本，请打开发布页面查看。")
    item = {"tag_name": tag, "body": "完整更新说明请查看发布页面。"}
    try:
        atom = "{http://www.w3.org/2005/Atom}"
        feed = ET.fromstring(read_release(RELEASES_URL + ".atom"))
        for entry in feed.findall(atom + "entry"):
            if any(link.get("rel") == "alternate" and link.get("href") == url for link in entry.findall(atom + "link")):
                notes = ReleaseNotes()
                notes.feed(entry.findtext(atom + "content", ""))
                notes.close()
                item.update(body="\n".join(line.strip() for line in "".join(notes.parts).splitlines() if line.strip()),
                            published_at=entry.findtext(atom + "updated", ""))
                break
    except (ValueError, ET.ParseError):
        pass  # Notes are optional; the stable version was confirmed separately.
    name = f"StreamClip-{tag}-windows-x64.zip"
    asset_url = f"{RELEASES_URL}/download/{tag}/{name}"
    try:
        checksums = read_release(f"{RELEASES_URL}/download/{tag}/SHA256SUMS.txt", 64 * 1024).decode("utf-8-sig")
        digests = [match[1] for line in checksums.splitlines()
                   if (match := re.fullmatch(r"([0-9a-fA-F]{64}) [ *]" + re.escape(name), line))]
        if len(digests) == 1:
            with open_release(asset_url, method="HEAD") as response:
                size = int(response.headers.get("Content-Length", ""))
            item["assets"] = [{"name": name, "browser_download_url": asset_url,
                               "digest": "sha256:" + digests[0], "size": size}]
    except ValueError:
        pass  # Missing/invalid metadata must disable installation, not discovery.
    latest = parse_releases([item])[0]
    latest["source"] = "release-page"
    return [latest]


def fetch_releases():
    try:
        raw = read_release(API_URL)
    except ReleaseUnavailable:
        return fetch_latest_release()
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("版本列表无法解析，请稍后重试。") from None
    # The UI shows the latest 100 public releases; older entries remain on GitHub.
    return parse_releases(payload)


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download_release(release, root, cancel, progress):
    if version_key(release["version"]) <= version_key(VERSION):
        raise ValueError("只能安装比当前版本更新的正式版。")
    package = release.get("package") or {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(package.get("sha256") or "")):
        raise ValueError("此版本没有可信的 SHA-256 校验信息，请在发布页面核实。")
    size = package.get("size")
    if type(size) is not int or not 0 < size <= MAX_PACKAGE_SIZE:
        raise ValueError("更新包大小无效。")
    url = trusted_url(package["url"])
    root = Path(root).resolve()
    cache = root / "data" / "updates"
    cache.mkdir(parents=True, exist_ok=True)
    if not cache.resolve().is_relative_to(root):
        raise ValueError("更新缓存不能位于程序目录以外。")
    folder = Path(tempfile.mkdtemp(prefix="download-", dir=cache))
    archive = folder / "package.zip"
    staged = folder / "StreamClip.exe"
    started, last_progress = time.monotonic(), 0.0
    try:
        digest, received = hashlib.sha256(), 0
        with open_release(url) as response, archive.open("xb") as target:
            while True:
                if cancel.is_set():
                    raise DownloadCancelled()
                if time.monotonic() - started > 1800:
                    raise ValueError("下载超时，请检查网络后重试。")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > size:
                    raise ValueError("更新包大小与发布信息不一致。")
                target.write(chunk)
                digest.update(chunk)
                if time.monotonic() - last_progress >= 0.15:
                    progress(received, size)
                    last_progress = time.monotonic()
        if received != size or digest.hexdigest() != package["sha256"]:
            raise ValueError("更新包 SHA-256 或大小校验失败，文件已丢弃。")
        if cancel.is_set():
            raise DownloadCancelled()
        with zipfile.ZipFile(archive) as bundle:
            candidates = [entry for entry in bundle.infolist() if PurePosixPath(entry.filename).name == "StreamClip.exe"]
            if len(candidates) != 1:
                raise ValueError("更新包必须包含唯一的 StreamClip.exe。")
            entry = candidates[0]
            parts = PurePosixPath(entry.filename)
            if parts.is_absolute() or ".." in parts.parts or "\\" in entry.filename or len(parts.parts) > 2 or not 0 < entry.file_size <= MAX_PACKAGE_SIZE:
                raise ValueError("更新包的程序路径或大小无效。")
            with bundle.open(entry) as source, staged.open("xb") as target:
                count = 0
                while chunk := source.read(256 * 1024):
                    if cancel.is_set():
                        raise DownloadCancelled()
                    count += len(chunk)
                    if count > MAX_PACKAGE_SIZE:
                        raise ValueError("解压后的程序过大。")
                    target.write(chunk)
            if count != entry.file_size:
                raise ValueError("更新程序未完整解压。")
        with staged.open("rb") as source:
            header = source.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise ValueError("更新包不是有效的 Windows 程序。")
            offset = struct.unpack_from("<I", header, 60)[0]
            if offset > staged.stat().st_size - 6:
                raise ValueError("更新程序头损坏。")
            source.seek(offset)
            if source.read(6) != b"PE\0\0\x64\x86":
                raise ValueError("更新程序不是 Windows x64 版本。")
        progress(size, size)
        if cancel.is_set():
            raise DownloadCancelled()
        return {"path": str(staged), "sha256": file_hash(staged), "version": release["version"]}
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    finally:
        archive.unlink(missing_ok=True)
        if not staged.exists():
            folder.rmdir()


def launch_installer(staged):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise ValueError("源码版请从发布页面下载程序；不会自动覆盖源码。")
    target = Path(sys.executable).resolve()
    source = Path(staged["path"]).resolve()
    cache = target.parent / "data" / "updates"
    if target.name != "StreamClip.exe" or source.name != target.name or source.parent.parent != cache or not source.is_file():
        raise UpdateFileError("更新文件不在预期目录，请重新下载。")
    if file_hash(source) != staged["sha256"]:
        raise UpdateFileError("下载后的程序校验失败，请重新下载。")
    helper = source.parent / "install-update.ps1"
    shutil.copyfile(Path(__file__).with_name("install-update.ps1"), helper)
    plan = source.parent / "plan.json"
    plan.write_text(json.dumps({"target": str(target), "staged": str(source), "sha256": staged["sha256"],
                               "pid": os.getpid(), "version": staged["version"]}), encoding="utf-8")
    (source.parent / "ready").unlink(missing_ok=True)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    process = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper), "-Plan", str(plan)],
                               cwd=target.parent, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if (source.parent / "ready").is_file():
            return process
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    raise ValueError("更新助手未能就绪，程序保持运行；请检查目录权限后重试。")
