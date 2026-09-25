# StreamClip

Windows 桌面端 Bilibili 直播切片工作台，使用 Python、PySide6 / Qt Quick 和 FFmpeg。

## 下载

Windows x64 版本见 [GitHub Releases](https://github.com/yuyu121704/StreamClip/releases/latest)。解压后运行 `StreamClip.exe`，无需安装 Python；FFmpeg、FFprobe 和 yt-dlp 需自行配置，见下方运行要求。

升级前关闭旧程序，将新程序与已有 `data/` 放在同一目录，并保留原媒体工具及配置；不要用空数据目录覆盖已有数据。账号、录播、任务及业务设置沿用。

## 功能

- 监控直播间、自动录播、采集实时弹幕，下载历史回放或导入本地媒体。
- 通过阿里云 DashScope 转写语音，通过 OpenAI 兼容接口生成回顾、选题、标题和封面文案。
- 管理全局和主播术语表、主播知识库及待审核词条。
- 使用 FFmpeg 制作字幕、封面和视频切片，在软件内预览成片。
- 通过 Bilibili 扫码登录，按队列投稿并查询平台处理状态。
- 任务重试、未保存表单保护和运行日志。
- 版本历史、更新说明、后台新版本提醒及校验下载、确认安装重启。

## 版本与更新

`v2026.09.25.1` 修复直播启动和混合编码录播：

- 直播刚开始时，即使房间已显示开播，流地址仍可能短暂返回 `403`、`404` 或 `EOF`；程序现在在没有录到有效分段前保留独立的启动重试窗口，避免瞬时地址故障直接把录制标为失败。
- 混合 H.264/HEVC 分段需要重新编码视频时，如果音频编码参数一致则直接复制 AAC 音轨，保留长录播的完整音频时间线；只有严格校验发现复制后的音频时间异常时才回退重新编码，失败仍保留原始分段。
- 两条路径都继续保留完整音视频校验；已丢失的直播内容和已经损坏的源文件不会因升级自动恢复。

`v2026.09.24.2` 新增一键清理，并包含直播断线重连修复：

- 录播与切片页新增「一键删除」，只删除点击时当前主播、日期筛选下的项目；确认窗口列明范围、数量和删除内容，默认聚焦取消，确认期间新增的项目不会被带入。
- 逐项检查并删除本地文件，正在处理、文件共用或占用等失败项保留，结束后显示成功数量及逐项原因。删除切片前释放内嵌播放器；已有单条删除入口保留。
- 任务页新增「一键清理」，只移除已完成、失败和已取消的任务记录；保留排队、运行中和等待平台确认的任务，以及内部去重键、回执和关联数据。清理后的任务不再参与批量重试失败，重新从原业务入口发起处理时恢复显示；任务列表不再截断为500条。
- 删除录播时保留已有切片，删除切片时保留原录播；清理任务不删除媒体文件。三种操作均保留投稿历史，不删除平台已发布的稿件。文件删除无法撤销，请先确认筛选范围。
- 直播断线后重新获取地址并保存为新的独立分段，不再把新的FLV流续接到旧流中；沿用严格音视频校验，失败时保留原始文件。升级不会自动修复已损坏的录播，请先修复或重新获取源文件再重试。

`v2026.09.22.2` 为竖屏切片采用新的横向排版：

- 自动识别竖屏素材，生成1920×1080的16:9视频：原画面等比完整放在左侧，右侧黑底显示居中的白字绿边字幕，长句自动换行。
- 保留字幕文字、时间轴和原帧率，识别旋转信息及非方形像素；横屏和方形素材保持原输出和字幕样式。关闭字幕时，竖屏仍使用横向画布，右侧留黑。
- 竖屏字体、字号、描边宽度和边距沿用设置，字幕颜色及对齐使用上述参考样式。现有录播可按新布局重新生成切片；升级不会自动覆盖旧成片，也不会替换B站已经发布的稿件。

`v2026.09.20.3` 合并录制收尾、H.264 切片和内存优化：

- 停止录制时让 FFmpeg 正常写完文件；仅对一个末尾损坏视频包尝试有限修复，随后完整严格解码。中途损坏仍报错并保留原始分段，收尾失败不再把可探测时长清零；避免将 TS 的 90 kHz 时钟误作转码帧率。
- 部分完整的 H.264 开放 GOP 录播在中途定位时会缺少参考帧。识别到对应 POC/MMCO 错误后从头解码重试一次，保持音视频和字幕时间轴；真正损坏的源文件仍拒绝处理，保留原片和已有成品。较长录播的重试可能耗时更久。
- 媒体处理的输入解码与输出编码各限制为2线程，普通/复杂滤镜各1线程；多项切片与收尾媒体命令共享队列执行，FFprobe 探测和直播采集不占该队列。保留画质参数、分辨率、帧率、字幕及严格校验，以处理速度换取更低峰值内存。

`v2026.09.20.3` 的本机隔离源码长测完成120次页面/真实媒体切换、1080p60和1440p60字幕切片，以及2项/4项同时提交后的排队处理。约740秒内，包含 FFmpeg/FFprobe 的进程树工作集采样峰值1124.43 MB，私有提交峰值1072.57 MB，9个成片均通过完整解码。按 `1 MB = 1,000,000 bytes`、每250ms采样，未强制回收或清空工作集；该结果不是任意素材、全天运行、多实例或无限直播路数下的1500 MB硬上限，GPU显存不在统计范围内。

从 `v2026.09.19.2` 开始，侧栏「管理 → 版本」提供当前版本、历史版本简介和可展开的完整更新说明。默认启动10秒后检查 GitHub 正式版，此后每4小时检查；发现新版本时显示侧栏提示点和底栏入口。「自动检查并提醒」可以关闭，手动检查仍然可用。

`v2026.09.19.3` 修复匿名 GitHub API 受限时无法检查版本的问题：API 限流或传输失败时，沿用系统代理，通过同仓库的最新正式版发布入口确认版本，并读取对应发布说明。备用路径只同步最新正式版，页面会提示历史列表暂未同步；不会把订阅中的预发布版本当成正式版。两个入口均失败时明确显示检查错误，不会误报为最新版。不需要 GitHub Token，也不修改代理设置。

EXE 版支持「下载更新」、进度和取消；仅接受带 SHA-256 校验信息的 Windows x64 ZIP。校验信息优先来自 GitHub API；API 不可用时，读取同一正式版附件 `SHA256SUMS.txt` 中与 ZIP 文件名精确匹配的唯一记录，并以 HEAD 请求确认包大小，检查时不下载整个 ZIP。缺少有效校验信息时仍显示新版本，但不开放下载和自动安装。校验通过后需主动点击「安装并重启」并确认，不会自动下载、重启或降级。录制、处理、术语或排队任务未结束时暂不安装；未保存的表单会在确认前提示。源码版只提供发布页面下载入口。

更新仅原子替换 `StreamClip.exe`，不会覆盖账号、配置、数据库、录播或媒体工具。旧版备份和更新结果保存在程序旁的 `data/updates/`；替换失败或无法启动新进程时保留/恢复原程序，不自动回退已经启动后发生的故障，以免旧程序打开已迁移的数据。更新前仍应备份数据；GitHub SHA-256 校验依赖 GitHub HTTPS 和仓库发布权限，不等同于独立代码签名。

早于 `v2026.09.19.2` 的版本没有应用内更新入口；`v2026.09.19.2` 如果已经提示请求受限，也需从 Releases 手动下载本修复版。先关闭旧程序并备份数据，再只替换 EXE，保留已有数据和媒体工具。升级后可在版本页检查后续更新。版本号与离线历史由 `app_updates.py` 维护；正式标签使用 `v<版本号>`，附件名称为 `StreamClip-v<版本号>-windows-x64.zip`，内含唯一的 `StreamClip.exe`，并上传以 `SHA256  文件名` 格式记录 ZIP 校验值的 `SHA256SUMS.txt`。API 正常时页面同步最近100个正式版本，忽略草稿、预发布和不支持的标签，更早历史可从发布页面查看。

## 运行要求

- Windows 10/11，x64。仅源码版需要 64 位 Python 3.11～3.13，启动脚本可自动安装。
- 完整版 FFmpeg 和 FFprobe，需要支持 H.264/AAC、字幕、绘字以及 MP4 输出。
- 下载 Bilibili 回放需要 yt-dlp。
- 语音识别和 AI 分析需要用户自己的服务账号、API Key、模型权限及额度。
- 默认封面字体依赖 Windows 的等线粗体和微软雅黑粗体；也可在设置中导入有使用授权的字体。本仓库不分发字体文件。

FFmpeg、FFprobe、yt-dlp 的可执行文件不放入 Git 仓库。从 [FFmpeg 官方下载页的 Windows 构建入口](https://ffmpeg.org/download.html#build-windows)获取含字幕功能的完整构建，从 [yt-dlp 官方 Releases](https://github.com/yt-dlp/yt-dlp/releases)获取 `yt-dlp.exe`。将对应 EXE 放到 `tools/ffmpeg.exe`、`tools/ffprobe.exe`、`tools/yt-dlp.exe`，或在「设置 → 高级设置 → 媒体工具」填写路径并点击「检查媒体工具」。这三个媒体工具不由启动脚本自动下载；缺失不影响打开设置，但录制、转码或回放下载会受影响。

## 源码启动

**首次使用源码版：完整解压到可写目录，双击 `启动Qt试运行.cmd`，首次安装时保持联网。** 不要直接在 ZIP 内运行，也不要放在需要管理员权限才能写入的目录。

启动器检查 Python、pip、Tcl/Tk、SQLite、Pillow、Qt 和其他固定版本依赖；没有合适的 Python 时从 python.org 下载官方 3.13.15 x64 安装器，验证固定 SHA-256 和 Python Software Foundation 签名后按当前用户安装，不修改 PATH。随后在项目旁创建独立 `.venv`，从 PyPI 安装依赖，不改动系统 Python 的包。后续依赖完整时不重复下载；移动目录造成虚拟环境失效时保留旧环境备份再重建。

检查和应用错误记录在 `data/logs/startup-日期-进程号.log`，失败会保留控制台，不再静默退出。运行期间保持控制台打开；不要同时运行 EXE 和源码版。没有网络或安装失败时不会继续启动，按日志排查网络或手动安装 [Python](https://www.python.org/downloads/windows/)（勾选 pip 与 Tcl/Tk）后重试。`.runtime` 为下载缓存，不要提交。

只检查并补齐环境、不启动录制等服务：

```powershell
.\启动Qt试运行.cmd -CheckOnly
```

已安装受支持 Python 的开发者也可手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

默认启动完整 Qt Quick 界面。`启动Qt试运行.cmd` 与 `--qml` 是兼容入口；无需运行任何独立服务器。

首次使用：

1. 在「设置」的「语音与 AI」分组填写阿里云 ASR Key、AI API 地址和 Key，获取并选择模型。OpenAI 兼容地址填写到 `/v1`。
2. 在「账号」添加账号，用 Bilibili App 扫码并确认登录。
3. 在「直播间」添加房间号并启用监控，或从工作台导入本地媒体。
4. 在「基础与自动化」确认保存目录、自动处理和投稿可见性后再开始。

术语管理入口位于「高级设置」顶部。联网查词可选，默认关闭；需要时自行配置 Brave 或 Tavily。

## Key 获取

**不是必须配置三条 Key。** 打开软件、扫码登录、监控录播不需要 AI Key；语音转写需要 ASR Key，自动总结与选题需要 AI Key；搜索 Key 只在启用联网查词时需要。

| 用途 | 填写位置 | 获取与验证 |
| --- | --- | --- |
| 语音转写 | 设置 → 语音与 AI → 阿里云 ASR Key | 按[阿里云官方说明](https://help.aliyun.com/zh/model-studio/get-api-key)登录百炼，在北京地域创建并复制 Key（默认 ASR 地址是北京地域），填写后点「加载 ASR 模型」，选择模型并保存。界面也有「获取 API Key」入口。 |
| 总结、选题和画面复核 | 同页的 AI API 地址、AI API Key | 在自己所用模型服务商的控制台创建 API Key，复制该服务商提供的 OpenAI 兼容 Base URL（一般到 `/v1`），点「连接并获取模型」，选择支持图片输入的模型后保存。Key、地址与模型须属于同一服务商和对应地域。 |
| 可选联网查词 | 设置 → 高级设置 → 联网查词 | [Brave](https://api-dashboard.search.brave.com/documentation/guides/authentication)：在控制台开通相应计划，在 API Keys 创建 Key；或 [Tavily](https://docs.tavily.com/documentation/quickstart)：登录控制台后从 API Keys 获取。二选一即可，不用搜索就保持关闭。 |

模型列表加载成功仅表示接口可访问，实际调用还需模型权限和可用额度。不要把 Bilibili Cookie 当成 API Key，Bilibili 只需在「账号」扫码。不要把真实 Key 放在截图、命令行、Issue 或仓库中。

**自动处理默认开启，投稿默认私密。** AI 画面复核不通过的切片会强制私密投稿并保留复核提醒。请自行核对账号、素材使用许可、标题和可见性，不要将自动检查当作人工审片或平台审核通过的保证。

## 数据与隐私

运行数据默认在程序旁的 `data/`，包括配置、数据库、登录凭据和日志；录播与切片可设置独立目录。不要同时启动多个实例处理同一数据目录。

API Key 与账号凭据使用 Windows 当前用户的 DPAPI 加密，不再以用户名、机器名或程序路径充当密钥。旧配置中的明文 API Key 在成功读取时原子迁移；能解密的旧 Cookie 在数据库打开时迁移，失败不清空原凭据。新格式不依赖程序目录；更换 Windows 用户或电脑后需要重新配置 Key、扫码登录。API Key 无法解密时会停止读取并保留原文件，按错误提示备份配置后清空对应字段再填写，不能降级为明文保存。升级后不要让不支持新格式的旧程序打开同一数据目录。

账号凭据、API Key、数据库、录播、字幕和日志仍不应提交到 Git，也不要放进 Issue。DPAPI 不能抵御已经控制当前 Windows 账号的程序，历史配置副本、数据库备份中的旧凭据不会自动消失；曾泄露过的 Key 应在服务商控制台撤销重建，账号应重新登录，不能仅靠升级撤回泄露。

转写会将音频上传到配置的 ASR 服务；AI 分析可能向配置的模型服务发送转写、弹幕、主播知识库、标题及画面抽帧。只处理有权使用并可发送至相应服务的内容，费用由所用服务收取。

## 测试

测试使用隔离数据，不执行真实录制、收费 ASR / AI 调用或平台投稿。界面检查需要 Windows 桌面会话和可用的图形驱动。

```powershell
$testRoot = Join-Path $env:LOCALAPPDATA 'StreamClip\tests'
$env:TEMP = Join-Path $testRoot 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:LIVECLIP_UI_TEST_OUTPUT = Join-Path $testRoot 'ui-check'
$env:LIVECLIP_TEST_FFMPEG = (Resolve-Path .\tools\ffmpeg.exe).Path
.\.venv\Scripts\python.exe -X utf8 -B release_check.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\startup_test.ps1
.\.venv\Scripts\python.exe -X utf8 -B security_test.py
.\.venv\Scripts\python.exe -X utf8 -B self_test.py
.\.venv\Scripts\python.exe -X utf8 -B quick_ui_test.py
```

路径可以替换为自己的隔离测试目录。修改后先暂存已审查的文件，再运行 `release_check.py`。发布检查扫描 Git 将跟踪的文件，包括二进制中的可见字符串和 UTF-16 字符串，验证文档、许可证和资源完整性；它不解压压缩包，不扫描 Git 历史，也不能替代完整的安全审计。启动器离线测试模拟缺 Python、缺包、安装失败、旧环境失效、重复启动和应用异常，不实际安装软件。

## 构建

将虚拟环境的 Python 放到当前 PowerShell 的 PATH 后执行：

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
$env:LIVECLIP_TEST_FFMPEG = (Resolve-Path .\tools\ffmpeg.exe).Path
.\build.ps1 -BuildRoot (Join-Path $env:LOCALAPPDATA 'StreamClip\release')
```

`-BuildRoot` 可指定其他有写入权限的构建目录；不要指向源码或数据目录。脚本先构建、逐字节验证包内 QML 和图片资源，再运行打包后的业务和界面自检；全部成功且当前程序未运行时，才备份并替换项目根目录的 `StreamClip.exe`。它不会停止正在录制的进程。请勿直接分发未通过检查的 EXE。

预编译程序作为 GitHub Release 附件分发，不放入 Git 源码历史。下载包包含许可证和第三方声明；Qt、FFmpeg 等随包组件的上游来源见 `THIRD_PARTY_NOTICES.md`。对应版本的完整应用源码可从同一 Release 的源码归档获取。

## 许可证

项目自有源码与 Hikami-Go 派生代码按 GNU GPL v3 分发，全文见 [LICENSE](LICENSE)。Hikami-Go 的来源提交、移植范围，以及 Qt、FFmpeg、Lucide 的声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。第三方声明与原许可证均保留。

素材来源见 [ASSET_RIGHTS.md](assets/ui/ASSET_RIGHTS.md)。本项目不代表 Bilibili、阿里云或任何主播；请遵守平台规则和相关内容权利要求。
