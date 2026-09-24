"""Qt Quick 创作工作台；复用业务服务，界面线程不执行业务 I/O。"""

import base64
import io
import json
import logging
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle

import app as core
import app_updates
from quick_forms import ROOM_FIELDS, SETTINGS_FIELDS, UPLOAD_FIELDS, settings_values, validated_settings
from character_theme import motion_enabled
from quick_theme import UiTheme


def image_data(raw):
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii") if raw else ""


def local_url(path):
    return QUrl.fromLocalFile(str(Path(path).resolve())).toString() if path else ""


def upload_row(upload):
    try:
        metadata = json.loads(upload.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        metadata = {}
    return dict(upload, state=core.STATUS_LABELS.get(upload["status"], upload["status"]),
                review_warning=str(metadata.get("review_warning") or "") if isinstance(metadata, dict) else "")


class Rows(QAbstractListModel):
    """按 ID 合并倒序列表；只通知新增、移除和真正变化的行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return {Qt.UserRole: b"rowData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and 0 <= index.row() < len(self.rows) and role == Qt.UserRole:
            return self.rows[index.row()]
        return None

    def sync(self, rows):
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)) or ids != sorted(ids, reverse=True):
            raise ValueError("列表必须按唯一 ID 倒序排列")
        wanted = set(ids)
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i]["id"] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                self.rows.pop(i)
                self.endRemoveRows()
        for i, row in enumerate(rows):
            if i == len(self.rows) or self.rows[i]["id"] != row["id"]:
                self.beginInsertRows(QModelIndex(), i, i)
                self.rows.insert(i, row)
                self.endInsertRows()
            elif self.rows[i] != row:
                self.rows[i] = row
                self.dataChanged.emit(self.index(i), self.index(i), [Qt.UserRole])


def media_origin(record, rooms):
    try:
        metadata = json.loads(record.get("recording_metadata_json", record.get("metadata_json")) or "{}")
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    room_id = str(record.get("room_id") or "")
    room = rooms.get(room_id, {}) if room_id.isdecimal() and int(room_id) > 0 else {}
    uid = str(metadata.get("source_liver_uid") or room.get("uid") or "").strip()
    if uid and not (room_id.isdecimal() and int(room_id) > 0):
        room_id, room = next(((key, r) for key, r in rooms.items() if str(r.get("uid") or "") == uid), (room_id, room))
    name = str(room.get("name") or metadata.get("source_name") or "").strip()
    # 房间号是新旧录播共有的身份，不能因后来补齐 UID 就拆成两个主播。
    if room_id.isdecimal() and int(room_id) > 0:
        key = "room:" + room_id
        name = name if name and name != room_id else "房间 " + room_id
    elif uid:
        key, name = "uid:" + uid, name or "主播 UID " + uid
    elif metadata.get("source_name"):
        name = str(metadata["source_name"]).strip()
        key = "name:" + name
    else:
        key, name = "unknown", "未标注主播"
    # 日期沿用录播列表的开始时间；导入媒体没有原始直播时间时就是导入日期。
    try:
        day = datetime.fromisoformat(str(record.get("started_at") or "")).date().isoformat()
    except ValueError:
        day = "日期未知"
    return {"streamerKey": key, "streamerName": name, "date": day}


class MediaRows(Rows):
    """全量轻量列表在本地筛选；万级以上历史库可升级为数据库分页。"""

    filtersChanged = Signal()
    visibleChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_rows = []
        self._streamer = self._date = ""
        self._filters = {}
        self.sync([])

    @Property("QVariantMap", notify=filtersChanged)
    def filters(self):
        return self._filters

    @Slot(str, str)
    def setFilter(self, streamer, day):
        self._streamer, self._date = streamer, day
        self.sync(self._all_rows)

    def sync(self, rows):
        self._all_rows = rows
        names = {r["streamerKey"]: r["streamerName"] for r in reversed(rows)}
        if self._streamer and self._streamer not in names:
            self._streamer = self._date = ""
        candidates = [r for r in rows if not self._streamer or r["streamerKey"] == self._streamer]
        days = sorted({r["date"] for r in candidates}, reverse=True)
        if "日期未知" in days:
            days.remove("日期未知")
            days.append("日期未知")
        if self._date not in days:
            self._date = ""
        visible = [r for r in candidates if not self._date or r["date"] == self._date]
        super().sync(visible)
        name_counts = Counter(names.values())
        choices = [{"key": key, "label": name + ("（" + key.split(":", 1)[1] + "）" if name_counts[name] > 1 else "")} for key, name in sorted(names.items(), key=lambda item: (item[1], item[0]))]
        filters = {"streamers": [{"key": "", "label": "全部主播"}] + choices,
                   "dates": ["全部日期"] + days, "streamer": self._streamer, "date": self._date,
                   "count": len(visible), "total": len(rows)}
        if filters != self._filters:
            self._filters = filters
            self.filtersChanged.emit()
        self.visibleChanged.emit()


def recording_detail(record):
    if not record or record.get("deleted"):
        return {"id": 0, "title": "", "summary": "选择左侧录播，查看整场总结与高光。", "highlights": [], "canAnalyze": False, "canDelete": False}
    summary = record.get("summary") or "尚未生成总结。导入完成后自动处理；也可点击“重新 AI 总结切片”开始。"
    transcript = Path(str(record.get("transcript_path") or ""))
    if transcript.is_file():
        try:
            package = json.loads(transcript.read_text(encoding="utf-8"))
            metadata = package.get("asr_metadata", {}) if isinstance(package, dict) else {}
            if isinstance(metadata, dict) and metadata:
                summary += "\n\nASR：" + str(metadata.get("provider") or "未知")
                summary += "；模型 " + str(metadata.get("model") or "未知")
                summary += f"；说话人 {len(metadata.get('speakers') or [])} 个"
                labels = {"speech": "讲话", "music": "歌曲/音乐", "noise": "噪声/掌声", "noEnergy": "静音", "nonSpeech": "云端拒识的非语音"}
                for key, label in (("languages", "语言"), ("segment_audio_types", "音频类型")):
                    if metadata.get(key):
                        summary += "；" + label + " " + ", ".join(labels.get(str(v), str(v)) if key == "segment_audio_types" else str(v) for v in metadata[key])
                metrics = metadata.get("cloud_audio_metrics") if isinstance(metadata.get("cloud_audio_metrics"), dict) else {}
                if metrics.get("rejected_non_speech_seconds") is not None:
                    summary += f"；云端拒识非语音约 {float(metrics['rejected_non_speech_seconds']):.1f} 秒"
                if metadata.get("audio_detector"):
                    summary += "；分类器 " + str(metadata["audio_detector"])
        except (OSError, ValueError, TypeError):
            summary += "\n\n转写元信息读取失败，原文件已保留。"
    if record.get("error"):
        summary += "\n\n错误：" + str(record["error"])
    try:
        highlights = json.loads(record.get("highlights_json") or "[]")
        if not isinstance(highlights, list):
            raise ValueError("高光数据不是列表")
        highlights = [dict(item, interval=core.format_seconds(item.get("render_start", item.get("start"))) + " — " + core.format_seconds(item.get("render_end", item.get("end")))) for item in highlights if isinstance(item, dict)]
    except (ValueError, TypeError):
        highlights = []
        summary += "\n\n高光数据无法解析，原数据已保留。"
    return {"id": record["id"], "title": str(record["title"]), "summary": summary, "highlights": highlights, "canAnalyze": record.get("status") not in {"starting", "recording", "stopping"}, "canDelete": record.get("status") not in {"starting", "recording", "stopping"}}


class Bridge(QObject):
    motionChanged = Signal()
    changed = Signal()
    detailChanged = Signal()
    workspaceChanged = Signal()
    settingsChanged = Signal()
    glossaryChanged = Signal()
    qrChanged = Signal()
    actionDone = Signal(str, "QVariantMap")
    modelsReady = Signal("QVariantMap")
    asrModelsReady = Signal("QVariantMap")
    error = Signal(str)
    completed = Signal(str)
    updateChanged = Signal()
    closed = Signal()
    _result = Signal(str, object)

    def __init__(self, settings, db, service, parent=None, settings_path=None):
        super().__init__(parent)
        self._motion_enabled = motion_enabled()
        self.settings, self.db, self.service = settings, db, service
        self.recordings = MediaRows(self)
        self.tasks = Rows(self)
        self.rooms = Rows(self)
        self.clips = MediaRows(self)
        self.uploads = Rows(self)
        self.settings_path = Path(settings_path) if settings_path else db.path.parent / "config.json"
        self._workspace = {"stats": {}, "accounts": [], "replays": [], "clip": {}, "logs": [], "activeRooms": 0, "recentClips": [], "recentTasks": []}
        self._selected_clip = 0
        self._glossary_scope = ""
        self._glossary_filter = "pending"
        self._glossary_open = False
        self._glossary = {"channels": [], "entries": [], "candidates": [], "note": "", "recordings": []}
        self._qr = {"state": "closed", "message": "", "image": ""}
        self._qr_id = 0
        self._qr_account = None
        self._qr_cancel = None
        self._models = [settings.llm_model] if settings.llm_model else []
        self._models_request = 0
        self._model_credentials = (settings.llm_endpoint, settings.llm_api_key)
        self._asr_models = [settings.dashscope_model]
        self._asr_models_request = 0
        self._asr_model_credentials = settings.dashscope_api_key
        self._network = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quick-network")
        self._detail = recording_detail(None)
        self._selected = 0
        self._status = "正在读取录播…"
        self._busy = False
        self._closing = False
        self._refreshing = False
        self._detail_key = None
        self._detail_cache = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quick-data")
        self._result.connect(self._receive, Qt.QueuedConnection)
        self._update_cancel = threading.Event()
        self._staged_update = None
        self._update_installer = None
        self._latest_release = None
        self._update_preferences = self.db.path.parent / "updates.json"
        preference_error = ""
        automatic = True
        if self._update_preferences.exists():
            try:
                saved = json.loads(self._update_preferences.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or type(saved.get("autoCheck")) is not bool:
                    raise ValueError("无效更新偏好")
                automatic = saved["autoCheck"]
            except (OSError, ValueError):
                automatic = False
                preference_error = "更新偏好读取失败，已暂停自动检查；可手动检查或重新设置。"
        self._update = {
            "currentVersion": app_updates.VERSION, "state": "idle", "message": "尚未检查新版本",
            "error": preference_error, "lastChecked": "", "autoCheck": automatic,
            "hasUpdate": False, "latestVersion": "", "canDownload": False,
            "sourceBuild": not getattr(sys, "frozen", False), "progress": 0, "received": 0, "total": 0,
            "history": app_updates.release_history(), "releaseUrl": app_updates.RELEASES_URL,
        }
        result_path = core.runtime_root() / "data/updates/last-result.json"
        if getattr(sys, "frozen", False) and result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8-sig"))
                if result.get("success") is False:
                    self._update["error"] = "上次更新未完成，原程序已保留。详情：" + str(result.get("error") or "")
            except (ValueError, OSError, AttributeError):
                self._update["error"] = "上次更新结果无法读取，请检查 data/updates/last-result.json。"
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(4 * 60 * 60 * 1000)
        self.update_timer.timeout.connect(self._automatic_update_check)
        self.update_startup = QTimer(self)
        self.update_startup.setSingleShot(True)
        self.update_startup.timeout.connect(self._automatic_update_check)
        self.timer = QTimer(self)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self.refresh)
        self.recordings.visibleChanged.connect(self._clear_hidden_recording)
        self.clips.visibleChanged.connect(self._clear_hidden_clip)

    @Property(bool, notify=motionChanged)
    def motionEnabled(self):
        return self._motion_enabled

    @Slot()
    def refreshMotionPreference(self):
        enabled = motion_enabled()
        if enabled != self._motion_enabled:
            self._motion_enabled = enabled
            self.motionChanged.emit()

    def _clear_hidden_recording(self):
        if any(r["id"] == self._selected for r in self.recordings._all_rows) and not any(r["id"] == self._selected for r in self.recordings.rows):
            self._selected = 0
            self._detail = recording_detail(None)
            self.detailChanged.emit()

    def _clear_hidden_clip(self):
        if any(r["id"] == self._selected_clip for r in self.clips._all_rows) and not any(r["id"] == self._selected_clip for r in self.clips.rows):
            self._selected_clip = 0
            self._workspace["clip"] = {}
            self.workspaceChanged.emit()

    @Property("QVariantMap", notify=workspaceChanged)
    def workspace(self):
        return self._workspace

    @Property("QVariantMap", notify=settingsChanged)
    def formSettings(self):
        return settings_values(self.settings)

    @Property("QVariantMap", constant=True)
    def defaultSettings(self):
        return settings_values(core.Settings(base_dir=self.settings.base_dir))

    @Property("QVariantMap", notify=glossaryChanged)
    def glossary(self):
        return self._glossary

    @Property("QVariantMap", notify=qrChanged)
    def qr(self):
        return self._qr

    @Property("QVariantMap", constant=True)
    def forms(self):
        return {"settings": SETTINGS_FIELDS, "room": ROOM_FIELDS, "upload": UPLOAD_FIELDS,
                "fonts": [core.REFERENCE_FONT_PRESET] + QFontDatabase.families()}

    @Slot(str, result=str)
    def filePath(self, url):
        value = QUrl(url)
        return value.toLocalFile() if value.isLocalFile() else url

    @Slot(int)
    def selectClip(self, clip_id):
        if any(r["id"] == clip_id for r in self.clips._all_rows) and not any(r["id"] == clip_id for r in self.clips.rows):
            self.clips.setFilter("", "")
        self._selected_clip = clip_id
        self.refresh()

    @Slot(str, str, bool)
    def setGlossary(self, scope, status, opened):
        self._glossary_scope, self._glossary_filter, self._glossary_open = scope, status, opened
        self.refresh()

    @Property("QVariantMap", notify=detailChanged)
    def detail(self):
        return self._detail

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy or self._closing

    def _submit(self, kind, work, network=False):
        def execute():
            try:
                result = work()
            except Exception as exc:
                if not isinstance(exc, app_updates.DownloadCancelled):
                    logging.exception("Qt Quick %s failed", kind)
                result = exc
            if not self._closing or kind == "close":
                self._result.emit(kind, result)
        (self._network if network else self._pool).submit(execute)

    def start(self):
        self._submit("start", self._start_service)
        self.timer.start()
        self.update_timer.start()
        self.update_startup.start(10000)
        self.refresh()

    @Property("QVariantMap", notify=updateChanged)
    def updateInfo(self):
        return self._update

    def _automatic_update_check(self):
        if self._update["autoCheck"]:
            self.checkUpdates()

    @Slot()
    def checkUpdates(self):
        if self._closing or self._update["state"] in {"checking", "downloading", "installing"}:
            return
        self._update.update(state="checking", message="正在检查 GitHub 正式版本…", error="")
        self.updateChanged.emit()
        self._submit("updateCheck", app_updates.fetch_releases, network=True)

    @Slot(bool)
    def setAutomaticUpdates(self, enabled):
        if self._closing:
            return
        self._submit("updatePreference", lambda: (core.write_json_atomic(self._update_preferences, {"autoCheck": enabled}), enabled)[1])

    @Slot()
    def downloadUpdate(self):
        if self._closing or self._update["state"] in {"checking", "downloading", "installing", "ready"}:
            return
        if not self._latest_release or not self._update["canDownload"]:
            return
        self._update_cancel.clear()
        release = dict(self._latest_release)
        self._update.update(state="downloading", message="正在下载并校验更新包…", error="", progress=0, received=0, total=release["package"]["size"])
        self.updateChanged.emit()
        self._submit("updateDownload", lambda: app_updates.download_release(
            release, core.runtime_root(), self._update_cancel,
            lambda received, total: self._result.emit("updateProgress", (received, total))), network=True)

    @Slot()
    def cancelUpdateDownload(self):
        self._update_cancel.set()

    @Slot(str)
    def installUpdate(self, version):
        if self.busy or self._update["state"] != "ready" or not self._staged_update:
            return
        if version != self._staged_update["version"]:
            return
        self._busy = True
        self._update.update(state="installing", message="正在准备安装…", error="")
        self.changed.emit()
        self.updateChanged.emit()

        def prepare():
            # Freeze new work under the same locks as recording/task admission.
            with self.service._active_lock, self.service._scheduled_lock, self.service._glossary_jobs_lock:
                if self.service.active_room_ids() or self.service._scheduled_tasks or self.service._glossary_jobs or self.db.list_tasks(statuses=["running", "queued", "retry"]):
                    raise ValueError("仍有录制、处理或排队任务，请在任务结束后安装更新。")
                process = app_updates.launch_installer(self._staged_update)
                self.service._update_pending = True
                return process
        self._submit("updateInstall", prepare)

    def _receive_update(self, kind, result):
        if isinstance(result, app_updates.UpdateFileError):
            self._staged_update = None
        if kind == "updateInstall":
            self._busy = False
            self.changed.emit()
        if kind == "updatePreference" and isinstance(result, Exception):
            self._update["error"] = "更新偏好保存失败：" + str(result)
        elif isinstance(result, app_updates.DownloadCancelled):
            self._update.update(state="available", message="下载已取消，可重新下载。", error="", progress=0)
        elif isinstance(result, Exception):
            message = "版本检查未完成，可重试或打开发布页面。" if kind == "updateCheck" else "更新未完成，可重试或打开发布页面。"
            self._update.update(state="ready" if self._staged_update else "error", error=str(result), message=message)
        elif kind == "updatePreference":
            self._update.update(autoCheck=result, error="")
        elif kind == "updateCheck":
            latest = result[0] if result else None
            newer = bool(latest and app_updates.version_key(latest["version"]) > app_updates.version_key(app_updates.VERSION))
            self._latest_release = latest
            ready = bool(newer and self._staged_update and self._staged_update["version"] == latest["version"])
            if not ready:
                self._staged_update = None
            state = "ready" if ready else "available" if newer else "current" if latest and app_updates.version_key(latest["version"]) == app_updates.version_key(app_updates.VERSION) else "ahead" if latest else "idle"
            message = {"ready": "更新已下载并通过校验，可以安装。", "available": "发现新版本 " + (latest["version"] if latest else ""),
                       "current": "当前已是最新正式版。", "ahead": "当前为较新的本地版本，尚无可升级的正式版。", "idle": "暂未发现正式发布的版本。"}[state]
            if latest and latest.get("source") == "release-page":
                message += " 已通过发布页面确认；历史版本列表暂未同步。"
            self._update.update(state=state, message=message, error="", lastChecked=datetime.now().strftime("%Y-%m-%d %H:%M"),
                                hasUpdate=newer, latestVersion=latest["version"] if latest else "",
                                canDownload=bool(newer and latest.get("package") and not self._update["sourceBuild"]),
                                history=app_updates.release_history(result))
        elif kind == "updateProgress":
            if self._update["state"] != "downloading":
                return
            received, total = result
            self._update.update(progress=received / total, received=received, total=total)
        elif kind == "updateDownload":
            self._staged_update = result
            self._update.update(state="ready", message="更新已下载并通过校验，可以安装。", error="", progress=1)
        elif kind == "updateInstall":
            self._update_installer = result
            self.close()
        self.updateChanged.emit()

    def _start_service(self):
        if self.settings.bili_cookie.strip():
            accounts = self.db.list_cookie_accounts()
            uid = core.parse_cookie(self.settings.bili_cookie).get("DedeUserID")
            existing = next((a for a in accounts if a["cookie"] == self.settings.bili_cookie or (uid and core.parse_cookie(a["cookie"]).get("DedeUserID") == uid)), None)
            name = "默认账号"
            while any(a["name"] == name for a in accounts):
                name += "·"
            account_id = existing["id"] if existing else self.db.upsert_cookie_account(name, self.settings.bili_cookie)
            migrated = replace(self.settings, bili_cookie="", bili_cookie_ciphertext="")
            migrated.download_account_id = migrated.download_account_id or account_id
            migrated.publish_account_id = migrated.publish_account_id or account_id
            account = self.db.get_cookie_account(migrated.publish_account_id)
            migrated.uploader_uid = core.parse_cookie(account["cookie"]).get("DedeUserID", "") if account else ""
            migrated.save(self.settings_path)
            self.settings.__dict__.update(migrated.__dict__)
        self.service.start()

    def _snapshot(self, selected):
        records = [r for r in self.db.list_recordings(-1, overview=True) if r["status"] not in {"starting", "recording", "stopping"}]
        rooms = {str(r["room_id"]): r for r in self.db.list_rooms()}
        rows = [{"id": r["id"], "title": r["title"], "status": core.STATUS_LABELS.get(r["status"], r["status"]), "duration": core.format_seconds(r["duration"]), "source": {"live": "直播", "replay": "回放", "local": "本地"}.get(r.get("source_type"), "直播"), "room": r["room_id"], "started": r["started_at"], **media_origin(r, rooms)} for r in records]
        labels = {"analysis": "分析", "clip": "切片", "upload": "投稿", "replay_download": "回放下载", "media_import": "媒体导入"}
        tasks = [{"id": t["id"], "kind": labels.get(t["kind"], t["kind"]), "state": t["status"], "status": core.STATUS_LABELS.get(t["status"], t["status"]), "progress": t["progress"], "message": t["message"], "error": t["error"], "updated": t["updated_at"], "attempts": t["attempts"]} for t in self.db.list_tasks(-1)]
        record = self.db.get_recording(selected) if any(r["id"] == selected for r in records) else None
        transcript = Path(str((record or {}).get("transcript_path") or ""))
        try:
            stamp = transcript.stat().st_mtime_ns if transcript.is_file() else None
        except OSError:
            stamp = None
        key = (record, stamp)
        if key != self._detail_key:
            self._detail_cache = recording_detail(record)
            self._detail_key = key
        message = ""
        messages = []
        # 消费有界批次，防止日志洪峰饿死命令；不为每条事件重建界面。
        for _ in range(128):
            try:
                event = self.service.events.get_nowait()
            except queue.Empty:
                break
            if event.get("kind") == "qr_login":
                self._result.emit("qr", self._handle_qr(event))
                continue
            if event.get("kind") == "replays":
                self._result.emit("replays", (event.get("data") or {}).get("items") or [])
                continue
            message = str(event.get("message") or message)
            if event.get("message"):
                messages.append(str(event["message"]))
            logging.info("%s", event.get("message", ""))
        workspace = self._workspace_snapshot(rooms)
        workspace["messages"] = messages
        workspace["recentTasks"] = sorted(tasks, key=lambda row: row["id"], reverse=True)[:8]
        workspace["finishedTaskCount"] = sum(t["state"] in {"complete", "success", "error", "cancelled"} for t in tasks)
        workspace["recentClips"] = workspace["clips"][:8]
        return rows, sorted(tasks, key=lambda t: t["id"], reverse=True), selected, self._detail_cache, message, workspace

    def _workspace_snapshot(self, room_map=None):
        if room_map is None:
            room_map = {str(r["room_id"]): r for r in self.db.list_rooms()}
        active = self.service.active_room_ids()
        rooms = [dict(r, id=str(r["room_id"]), status="录制中" if str(r["room_id"]) in active else "直播中" if r["live_status"] else "未开播") for r in room_map.values()]
        # QML property reads copy the entire map; keep audio timelines out of list rows.
        clips = [{"id": c["id"], "title": c["title"], "duration": core.format_seconds(c["end_time"] - c["start_time"]),
                  "state": core.STATUS_LABELS.get(c["status"], c["status"]), **media_origin(c, room_map)}
                 for c in self.db.list_clips(-1) if not core.is_legacy_heuristic_clip(c)]
        uploads = [upload_row(u) for u in self.db.list_uploads()]
        accounts = [{k: a.get(k) for k in ("id", "name", "role", "enabled", "last_status")} | {"uid": core.parse_cookie(a["cookie"]).get("DedeUserID", ""), "avatar": image_data(a.get("avatar_png") or b"")} for a in self.db.list_cookie_accounts()]
        selected_clip = self._selected_clip
        clip = self.db.get_clip(selected_clip) if selected_clip else None
        detail = {}
        if clip and not clip.get("deleted"):
            state, flags, metadata = self.db.clip_review(clip["id"])
            warning = core.visual_review_warning(metadata)
            visibility = "self" if warning else self.settings.publish_visibility
            evidence = str(metadata.get("reason") or metadata.get("selection_reason") or "")
            for key, title in (("editorial_review", "生成时的选题记录"), ("visual_review", "画面复核")):
                if isinstance(metadata.get(key), dict):
                    evidence += "\n\n" + title + "：" + str(metadata[key].get("reason") or "")
            if metadata.get("title_candidates"):
                evidence += "\n\n备选标题：\n" + "\n".join(map(str, metadata["title_candidates"]))
            detail = {"id": clip["id"], "canDelete": clip["status"] != "processing", "title": clip["title"], "path": clip["path"], "video": local_url(clip["path"]) if Path(clip["path"]).is_file() else "",
                      "cover": local_url(clip.get("thumbnail_path")) if clip.get("thumbnail_path") and Path(clip["thumbnail_path"]).is_file() else "",
                      "candidates": local_url(Path(clip["path"]).with_suffix(".cover-candidates.jpg")) if Path(clip["path"]).with_suffix(".cover-candidates.jpg").is_file() else "",
                      "info": f"{core.format_seconds(clip['start_time'])} — {core.format_seconds(clip['end_time'])} · {core.STATUS_LABELS.get(state, state)}\n投稿可见性：{core.PUBLISH_VISIBILITIES[visibility]}\n" + (warning or "、".join(core.REVIEW_FLAG_LABELS.get(f, f) for f in flags) or "未发现阻断项"), "evidence": evidence or "暂无选题依据。"}
        glossary = None
        if self._glossary_open:
            store = self.db.glossary
            store.sync_rooms(rooms)
            scope = self._glossary_scope
            merged = {e["term"]: e for e in store.entries("")} if scope else {}
            merged.update({e["term"]: e for e in store.entries(scope)})
            channels = store.channels()
            records = [{"id": r["id"], "title": r["title"]} for r in self.db.list_recordings(1000) if r.get("transcript_path")]
            glossary = {"scope": scope, "filter": self._glossary_filter, "channels": channels, "entries": list(merged.values()), "candidates": store.candidates(scope, self._glossary_filter), "note": store.note(scope), "recordings": records}
        return {"rooms": sorted(rooms, key=lambda r: r["id"], reverse=True), "clips": sorted(clips, key=lambda r: r["id"], reverse=True),
                "uploads": sorted(uploads, key=lambda r: r["id"], reverse=True), "accounts": accounts, "stats": self.db.stats(), "activeRooms": len(active),
                "clip": detail, "clipSelection": selected_clip, "downloadAccount": self.settings.download_account_id, "publishAccount": self.settings.publish_account_id, "glossary": glossary}

    @Slot()
    def refresh(self):
        if self._closing or self._refreshing:
            return
        self._refreshing = True
        selected = self._selected
        self._submit("refresh", lambda: self._snapshot(selected))

    @Slot(int)
    def selectRecording(self, recording_id):
        if any(r["id"] == recording_id for r in self.recordings._all_rows) and not any(r["id"] == recording_id for r in self.recordings.rows):
            self.recordings.setFilter("", "")
        if recording_id == self._selected:
            return
        self._selected = recording_id
        self._detail = dict(recording_detail(None), id=recording_id, summary="正在读取总结…")
        self.detailChanged.emit()
        self.refresh()

    def _save_account_defaults(self, **changes):
        settings = replace(self.settings, **changes)
        account = self.db.get_cookie_account(settings.publish_account_id) if settings.publish_account_id else None
        settings.uploader_uid = core.parse_cookie(account["cookie"]).get("DedeUserID", "") if account else ""
        settings.save(self.settings_path)
        self.settings.__dict__.update(settings.__dict__)

    @Slot(int)
    def startQr(self, account_id=0):
        self.cancelQr()
        self._qr_account = account_id or None
        self._qr_id += 1
        self._qr = {"state": "loading", "image": "", "message": "正在连接 Bilibili…"}
        self._qr_cancel = self.service.qr_login_account(self._qr_id)
        self.qrChanged.emit()

    @Slot()
    def cancelQr(self):
        if self._qr_cancel:
            self._qr_cancel.set()
        self._qr_id += 1
        self._qr = {"state": "closed", "image": "", "message": ""}
        self.qrChanged.emit()

    def _handle_qr(self, event):
        if event.get("request_id") != self._qr_id or not self._qr_cancel or self._qr_cancel.is_set():
            return None
        state, data = event.get("state"), event.get("data") or {}
        result = {"request_id": self._qr_id, "state": state, "message": str(event.get("message") or ""), "image": ""}
        if state == "ready":
            buffer = io.BytesIO()
            data["image"].save(buffer, format="PNG")
            result["image"] = image_data(buffer.getvalue())
        elif state == "success":
            try:
                request, cancel = event["request_id"], self._qr_cancel
                account_id = self.db.save_bilibili_account(data["cookie"], data["profile"], data.get("avatar_png", b""), self._qr_account, cancelled=lambda: cancel.is_set() or request != self._qr_id)
                account = self.db.get_cookie_account(account_id)
                changes = {f"{role}_account_id": account_id for role in ("download", "publish") if not getattr(self.settings, f"{role}_account_id") and account["role"] in {role, "both"}}
                self._save_account_defaults(**changes)
                result["message"] = "登录成功：" + account["name"]
            except core.TaskCancelled:
                return None
            except (ValueError, OSError, core.sqlite3.Error) as exc:
                result.update(state="error", message=str(exc) if isinstance(exc, ValueError) else "登录信息保存失败，请检查数据目录权限后重试。")
        return result

    @Slot("QVariantMap")
    def fetchModels(self, values):
        self._models_request += 1
        request = self._models_request
        credentials = [str(values.get("llm_endpoint", "")).strip(), str(values.get("llm_api_key", "")).strip()]
        def fetch():
            settings = replace(self.settings, llm_provider="openai", llm_endpoint=credentials[0], llm_api_key=credentials[1])
            try:
                models = core.LLMClient(settings).list_models()
                return {"credentials": credentials, "models": models, "request": request}
            except Exception as exc:
                return {"credentials": credentials, "models": [], "error": core._redact_secret(exc, settings.llm_api_key), "request": request}
        self._submit("models", fetch, network=True)

    @Slot()
    def invalidateAsrModels(self):
        self._asr_models_request += 1

    @Slot("QVariantMap")
    def fetchAsrModels(self, values):
        self._asr_models_request += 1
        request = self._asr_models_request
        credentials = str(values.get("dashscope_api_key", "")).strip()
        settings = replace(self.settings, dashscope_api_key=credentials)
        def fetch():
            client = core.DashScopeTranscriber(settings)
            try:
                return {"credentials": credentials, "models": client.list_models(), "request": request}
            except Exception as exc:
                return {"credentials": credentials, "models": [], "error": core._redact_secret(exc, client._api_key()), "request": request}
        self._submit("asr_models", fetch, network=True)

    @Slot(str, result="QVariantMap")
    def cleanupSelection(self, kind):
        if kind == "tasks":
            ids = [t["id"] for t in self.tasks.rows if t["state"] in {"complete", "success", "error", "cancelled"}]
            return {"kind": kind, "ids": ids, "scope": "全部已结束任务"}
        if kind not in {"recordings", "clips"}:
            return {}
        model = self.recordings if kind == "recordings" else self.clips
        filters = model.filters
        streamer = next(item["label"] for item in filters["streamers"] if item["key"] == filters["streamer"])
        return {"kind": kind, "ids": [r["id"] for r in model.rows],
                "scope": streamer + " · " + (filters["date"] or "全部日期")}

    @Slot(str, "QVariantMap")
    def perform(self, action, values):
        if self.busy:
            return
        self._busy = True
        self.changed.emit()

        def execute():
            message = "操作已完成"
            data = {}
            if action == "roomAdd":
                room_id = str(values.get("room_id", "")).strip()
                match = core.re.search(r"live\.bilibili\.com/([0-9]+)", room_id, core.re.I)
                room_id = match.group(1) if match else room_id
                if not core.re.fullmatch(r"\d+", room_id):
                    raise ValueError("请填写数字房间号或 Bilibili 直播间链接")
                self.db.add_room(room_id, str(values.get("name") or room_id))
            elif action == "roomSave":
                room_id = str(values["room_id"])
                if not self.db.get_room(room_id):
                    raise ValueError("直播间已不存在")
                uid = str(values.get("uid", "")).strip()
                if uid and not uid.isdigit():
                    raise ValueError("主播 UID 应为数字")
                account_id = int(values.get("account_id") or 0)
                if account_id and not self.db.cookie_for_account(account_id, "download"):
                    raise ValueError("录制与下载账号不可用")
                config = {k: str(values.get(k) or "").strip() for k in ("name", "replay_source", "llm_model", "recap_template")}
                self.db.update_room_config(room_id, **config, uid=uid, account_id=account_id, **{k: int(bool(values.get(k))) for k in ("auto_record", "auto_asr", "auto_slice")})
                data = {"room": self.db.get_room(room_id)}
            elif action in {"roomRemove", "roomToggle", "recordStart", "recordStop", "discover"}:
                room_id = str(values.get("room_id") or "").strip()
                room = self.db.get_room(room_id)
                if not room and action != "discover":
                    raise ValueError("请先选择直播间")
                if action == "roomRemove":
                    self.service.stop_recording(room_id)
                    self.db.remove_room(room_id)
                elif action == "roomToggle":
                    self.db.set_room_enabled(room_id, not bool(room["enabled"]))
                elif action == "recordStart":
                    self.service.start_recording(room_id)
                elif action == "recordStop":
                    self.service.stop_recording(room_id)
                elif room_id:
                    self.service.discover_replays(room_id)
            elif action == "checkRooms":
                self.service.executor.submit(self.service.check_now)
                message = "正在检查直播间…"
            elif action == "mediaImport":
                source = Path(self.filePath(str(values.get("source") or "")))
                if not source.is_file():
                    raise ValueError("请选择存在的媒体文件")
                chat = self.filePath(str(values.get("danmaku") or ""))
                task = self.service.import_media(source, Path(chat) if chat else None, str(values.get("title") or source.stem))
                message = f"媒体导入任务 #{task} 已加入"
            elif action == "replayDownload":
                task = self.service.download_replay(str(values.get("url") or values.get("bvid") or ""), str(values.get("title") or "回放"), source_liver_uid=str(values.get("source_liver_uid") or ""), source_liver_name=str(values.get("source_liver_name") or ""))
                message = f"回放下载任务 #{task} 已加入"
            elif action == "cleanup":
                kind, ids = values.get("kind"), values.get("ids")
                if kind == "tasks":
                    count = self.db.clear_finished_tasks(ids)
                    data = {"kind": kind, "message": f"已清理 {count} 条已结束任务。", "failed": []}
                    if count < len(set(ids)):
                        data["message"] += f"\n另有 {len(set(ids)) - count} 条已变化或已清理，未作修改。"
                else:
                    data = dict(self.service.delete_media_batch(kind, ids), kind=kind)
                    label = "录播" if kind == "recordings" else "切片"
                    data["message"] = f"已删除 {len(data['deleted'])} 条{label}，未删除 {len(data['failed'])} 条。"
                message = data["message"]
            elif action == "deleteClip":
                clip_id = int(values["id"])
                self.service.delete_clip(clip_id)
                data = {"id": clip_id}
                message = "切片及相关本地文件已删除，原录播和投稿历史已保留"
            elif action == "enqueueClip":
                upload_id = self.service.enqueue_upload(int(values["id"]))
                message = f"投稿 #{upload_id} 已加入队列"
            elif action == "saveUpload":
                self.db.update_upload_fields(int(values["id"]), str(values.get("title") or ""), str(values.get("description") or ""), str(values.get("tags") or ""), int(values.get("tid") or core.DEFAULT_PUBLISH_TID))
                data = {"upload": upload_row(self.db.get_upload(int(values["id"])))}
                message = "投稿字段已保存"
            elif action == "submitUpload":
                self.service.retry_upload(int(values["id"]))
                message = "投稿任务已提交；结果不明的稿件会先核对平台回执"
            elif action == "accountSelect":
                role = str(values.get("role"))
                if role not in {"download", "publish"}:
                    raise ValueError("无效账号用途")
                account_id = int(values.get("id") or 0)
                if account_id and not self.db.cookie_for_account(account_id, role):
                    raise ValueError("该账号不可用于所选用途，请重新登录或选择账号")
                self._save_account_defaults(**{f"{role}_account_id": account_id})
                message = "账号选择已保存"
            elif action == "accountRemove":
                account_id = int(values["id"])
                self._save_account_defaults(**{f"{role}_account_id": 0 for role in ("download", "publish") if getattr(self.settings, f"{role}_account_id") == account_id})
                self.db.remove_cookie_account(account_id)
            elif action == "checkLogin":
                self.service.check_login(int(values.get("id") or 0) or None)
                message = "正在检查账号登录…"
            elif action == "settingsSave":
                settings = validated_settings(self.settings, values, self._model_credentials, self._models,
                                              self._asr_model_credentials, self._asr_models)
                settings.save(self.settings_path)
                self.settings.__dict__.update(settings.__dict__)
                self._model_credentials = (settings.llm_endpoint, settings.llm_api_key)
                message = "设置已保存，新任务立即生效"
            elif action == "checkTools":
                settings = replace(self.settings, ffmpeg_path=str(values.get("ffmpeg_path") or "ffmpeg"), ffprobe_path=str(values.get("ffprobe_path") or "ffprobe"))
                def check():
                    try:
                        core.FFmpeg(settings).ensure_tools()
                        return "媒体工具检查通过"
                    except Exception as exc:
                        return "媒体工具检查失败：" + str(exc)
                self._submit("tools", check, network=True)
                message = "正在后台检查媒体工具…"
            elif action == "importFont":
                source = Path(self.filePath(str(values.get("path") or "")))
                if not source.is_file() or source.suffix.lower() not in core.FONT_FILE_SUFFIXES:
                    raise ValueError("请选择有效的字体文件")
                family = core.font_family_name(source)
                if not family:
                    raise ValueError("无法识别字体家族")
                destination = self.settings.data_path / "fonts" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.resolve() != destination.resolve():
                    core.shutil.copy2(source, destination)
                data = {"render_font_name": family, "render_font_path": str(destination)}
                message = "字体已导入，保存设置后生效"
            elif action == "glossaryTerm":
                store = self.db.glossary
                scope = str(values.get("scope") or "")
                entry = next((e for e in store.entries(scope) if e["id"] == int(values.get("id") or 0)), None)
                entry_id = store.upsert(scope, str(values.get("term") or ""), str(values.get("canonical") or ""), str(values.get("category") or ""), bool(values.get("enabled", True)), entry_id=entry["id"] if entry else None)
                data = {"entry": next(e for e in store.entries(scope) if e["id"] == entry_id)}
                message = "词条已保存"
            elif action == "glossaryKnowledge":
                from hikami_glossary import build_streamer_knowledge
                scope = str(values.get("scope") or "")
                channel = next((item for item in self.db.glossary.channels() if item["channel_id"] == scope), {})
                settings = replace(self.settings)
                room = self.db.get_room(str(channel.get("room_id") or "")) or {}
                settings.llm_model = str(room.get("llm_model") or settings.llm_model)
                if not settings.llm_model.strip():
                    raise ValueError("请先在“语音与 AI”配置 AI 模型。")
                def progress(text):
                    self.service.emit("info", text)
                client = core.LLMClient(replace(settings, mcp_enabled=False), progress)
                note = build_streamer_knowledge(channel, str(values.get("note") or ""), client.chat, progress)
                data = {"scope": scope, "note": note}
                message = "主播知识库已填写，检查内容后点击保存知识库"
            elif action.startswith("glossary"):
                message = self._glossary_action(action, values)
            else:
                raise ValueError("未知操作")
            return {"action": action, "message": message, "data": data}
        self._submit("perform", execute)

    def _glossary_action(self, action, values):
        store = self.db.glossary
        scope = str(values.get("scope") or "")
        if action == "glossaryChange":
            ids = [int(i) for i in values.get("ids", [])]
            selected = {e["id"]: e for e in store.entries("") + store.entries(scope)}
            operation = str(values.get("operation"))
            chosen = [selected[i] for i in ids if i in selected]
            if operation not in {"enable", "disable", "delete"}:
                raise ValueError("无效词条操作")
            if operation == "delete" and any(e["channel_id"] != scope for e in chosen):
                raise ValueError("全局词条请切换到全局术语删除；主播范围可以停用它")
            for entry in chosen:
                if entry["channel_id"] == scope:
                    store.change_entries(scope, [entry["id"]], operation)
                else:
                    store.upsert(scope, entry["term"], entry["canonical"], entry["category"], operation == "enable")
        elif action == "glossaryNote":
            store.set_note(scope, str(values.get("note") or ""))
        elif action == "glossaryBind":
            store.bind_recording(int(values["recording_id"]), scope)
        elif action == "glossaryImport":
            path = Path(self.filePath(str(values.get("path") or "")))
            if path.stat().st_size > 2_000_000:
                raise ValueError("导入文件不能超过 2 MB")
            count = store.import_text(scope, path.read_text(encoding="utf-8-sig"), "json" if path.suffix.lower() == ".json" else "markdown")
            return f"已导入 {count} 条术语"
        elif action == "glossaryExport":
            core.write_json_atomic(Path(self.filePath(str(values["path"]))), store.export_json(scope))
        elif action == "glossaryApprove":
            ids = [int(i) for i in values.get("ids", [])]
            store.approve(scope, ids, values.get("edit") if len(ids) == 1 else None)
        elif action == "glossaryReject":
            store.reject(scope, [int(i) for i in values.get("ids", [])])
        elif action == "glossaryJob":
            recording_id = int(values.get("recording_id") or 0) or None
            mode = str(values.get("mode"))
            if mode not in {"discover", "review"}:
                raise ValueError("无效术语任务")
            settings = replace(self.settings)
            if recording_id:
                record = self.db.get_recording(recording_id) or {}
                room = self.db.get_room(str(record.get("room_id") or "")) or {}
                settings.llm_model = str(room.get("llm_model") or settings.llm_model)
            started = self.service._start_glossary_job(scope, recording_id, mode, settings)
            return "术语任务已开始，可继续使用软件" if started else "相同术语任务正在运行"
        else:
            raise ValueError("未知术语操作")
        return "术语修改已保存"

    @Slot(str, str)
    def command(self, action, value):
        if self.busy:
            return
        if action not in {"analyze", "deleteRecording", "cancel", "retry", "retryFailed"}:
            self.error.emit("未知操作")
            return
        self._busy = True
        self.changed.emit()

        def execute():
            if action == "retryFailed":
                return f"已重新排队 {self.service.retry_failed_tasks()} 个失败/取消任务。"
            target = int(value)
            if action == "deleteRecording":
                self.service.delete_recording(target)
                return "录播及相关文件已删除，已有切片和投稿记录已保留。"
            if action == "analyze":
                record = self.db.get_recording(target)
                if not record:
                    raise ValueError("录播已不存在，请刷新后重试。")
                if record["status"] in {"recording", "starting"}:
                    raise ValueError("录播仍在写入，请结束录制后再操作。")
                if not Path(record["path"]).is_file():
                    raise ValueError("录播文件不存在，请先补导入录播。")
                task_id = self.service.analyze_recording(target, force=True, reuse_transcript=core.recording_transcript_path(record).is_file(), run_pipeline=True)
                return f"AI 总结切片任务 #{task_id} 已加入；进行中的任务不会重复启动。"
            task = self.db.get_task(target)
            if not task:
                raise ValueError("任务已不存在，请刷新后重试。")
            if action == "retry" and task["status"] not in {"error", "cancelled"}:
                raise ValueError("只能重试失败或已取消的任务。")
            if action == "cancel" and task["status"] not in {"queued", "retry", "running"}:
                raise ValueError("该任务当前不能取消。")
            getattr(self.service, action + "_task")(target)
            return "任务操作已提交。"

        self._submit("command", execute)

    @Slot(str, object)
    def _receive(self, kind, result):
        if kind == "refresh":
            self._refreshing = False
        if kind in {"command", "perform"}:
            self._busy = False
        if kind == "close":
            if isinstance(result, Exception):
                self.service._update_pending = False
                if self._update_installer is not None and self._update_installer.poll() is None:
                    self._update_installer.terminate()
                    self._update_installer = None
                    self._update.update(state="ready", error="退出失败，更新已停止；可以重试。")
                    self.updateChanged.emit()
                self._closing = False
                self.timer.start()
                self.update_timer.start()
                self.error.emit(str(result))
                self.changed.emit()
                return
            self._pool.shutdown(wait=False)
            self._network.shutdown(wait=False, cancel_futures=True)
            self.closed.emit()
            return
        if self._closing:
            return
        if kind.startswith("update"):
            self._receive_update(kind, result)
            return
        if kind == "qr":
            if result and result.get("request_id") == self._qr_id and self._qr_cancel and not self._qr_cancel.is_set():
                previous_image = self._qr["image"]
                self._qr = result
                if result["state"] in {"waiting", "confirming"}:
                    self._qr["image"] = previous_image
                self.qrChanged.emit()
            return
        if kind == "models":
            if result.get("request") == self._models_request:
                if not result.get("error"):
                    self._model_credentials, self._models = tuple(result["credentials"]), result["models"]
                self.modelsReady.emit(result)
            return
        if kind == "asr_models":
            if result.get("request") == self._asr_models_request:
                if not result.get("error"):
                    self._asr_model_credentials, self._asr_models = result["credentials"], result["models"]
                self.asrModelsReady.emit(result)
            return
        if kind == "replays":
            self._workspace["replays"] = result
            self.workspaceChanged.emit()
            self.actionDone.emit("replays", {"items": result})
            return
        if isinstance(result, Exception):
            self._status = "操作失败：" + str(result)
            if kind != "refresh":
                self.error.emit(str(result))
        elif kind == "refresh":
            records, tasks, selected, detail, message, workspace = result
            self.recordings.sync(records)
            self.tasks.sync(tasks)
            self.rooms.sync(workspace.pop("rooms"))
            self.clips.sync(workspace.pop("clips"))
            self.uploads.sync(workspace.pop("uploads"))
            glossary = workspace.pop("glossary")
            if glossary and glossary["scope"] == self._glossary_scope and glossary["filter"] == self._glossary_filter and glossary != self._glossary:
                self._glossary = glossary
                self.glossaryChanged.emit()
            if workspace.pop("clipSelection") != self._selected_clip:
                workspace["clip"] = self._workspace.get("clip", {})
                self.refresh()
            workspace["replays"] = self._workspace["replays"]
            workspace["logs"] = (self._workspace["logs"] + workspace.pop("messages"))[-300:]
            if workspace != self._workspace:
                self._workspace = workspace
                self.workspaceChanged.emit()
            if selected == self._selected:
                if self._detail != detail:
                    self._detail = detail
                    self.detailChanged.emit()
            else:
                self.refresh()  # 过期选择结果不覆盖用户的新选择。
            if message:
                self._status = message
            elif self._status == "正在读取录播…":
                self._status = "就绪"
        elif kind == "command":
            self._status = str(result)
            self.completed.emit(str(result))
            self.refresh()
        elif kind == "perform":
            self._status = result["message"]
            data = result["data"]
            deleted_clips = [data["id"]] if result["action"] == "deleteClip" else data.get("deleted", []) if data.get("kind") == "clips" else []
            if self._selected_clip in deleted_clips:
                self._selected_clip = 0
                self._workspace["clip"] = {}
                self.workspaceChanged.emit()
            if result["action"] == "cleanup" and data.get("kind") == "recordings" and self._selected in data["deleted"]:
                self._selected = 0
                self._detail = recording_detail(None)
                self.detailChanged.emit()
            if result["action"] == "settingsSave":
                self.settingsChanged.emit()
            self.actionDone.emit(result["action"], result["data"])
            self.refresh()
        elif kind == "tools":
            self._status = str(result)
            self.actionDone.emit("tools", {"message": str(result)})
        self.changed.emit()

    @Slot()
    def close(self):
        if self._closing:
            return
        self._closing = True
        self.update_timer.stop()
        self.update_startup.stop()
        self._update_cancel.set()
        self.cancelQr()
        self._status = "正在停止后台服务…"
        self.timer.stop()
        self.changed.emit()
        self._submit("close", self.service.stop)


def create_engine(bridge):
    QGuiApplication.setApplicationName(core.APP_NAME)
    QGuiApplication.setApplicationDisplayName(core.APP_NAME)
    QGuiApplication.setApplicationVersion(app_updates.VERSION)
    if sys.platform == "win32":
        # 本机 D3D11 flip 交换链会在快扩窗时补黑；OpenGL 可使用原生类背景刷补边。
        # basic 避免 OpenGL 调整尺寸时 GUI/渲染线程互等，后台 I/O 仍在工作线程。
        os.environ.setdefault("QSG_RENDER_LOOP", "basic")
        QQuickWindow.setGraphicsApi(QSGRendererInterface.OpenGL)
        # 由 DWM 合成，避免 Present 再次等待；不使用持续渲染线程动画。
        surface = QSurfaceFormat.defaultFormat()
        surface.setSwapInterval(0)
        QSurfaceFormat.setDefaultFormat(surface)
    engine = QQmlApplicationEngine()
    engine.ui_theme = UiTheme(bridge.db.path.parent / "appearance.json", engine)
    engine.ui_theme.error.connect(bridge.error)
    context = engine.rootContext()
    context.setContextProperty("uiTheme", engine.ui_theme)
    context.setContextProperty("bridge", bridge)
    context.setContextProperty("recordingModel", bridge.recordings)
    context.setContextProperty("taskModel", bridge.tasks)
    context.setContextProperty("roomModel", bridge.rooms)
    context.setContextProperty("clipModel", bridge.clips)
    context.setContextProperty("uploadModel", bridge.uploads)
    context.setContextProperty("artRoot", QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "assets" / "ui") + "/"))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("recordings.qml"))))
    if not engine.rootObjects():
        raise RuntimeError("Qt Quick 界面加载失败")
    if sys.platform == "win32":
        import ctypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        user.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        user.SetClassLongPtrW.restype = ctypes.c_void_p
        gdi.CreateSolidBrush.argtypes = [ctypes.c_uint32]
        gdi.CreateSolidBrush.restype = ctypes.c_void_p
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi.DeleteObject.restype = ctypes.c_int
        window = engine.rootObjects()[0]
        brushes = []

        def update_background():
            color = window.color()
            brush = gdi.CreateSolidBrush(color.red() | color.green() << 8 | color.blue() << 16)
            if not brush:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.set_last_error(0)
            previous = user.SetClassLongPtrW(int(window.winId()), -10, brush)  # GCLP_HBRBACKGROUND
            error = ctypes.get_last_error()
            if not previous and error:
                gdi.DeleteObject(brush)
                raise ctypes.WinError(error)
            # Only delete our replaced brush; Windows owns the currently installed one.
            if brushes and previous == brushes[0]:
                gdi.DeleteObject(previous)
            brushes[:] = [brush]

        update_background()
        window.colorChanged.connect(update_background)
    return engine


def run():
    QQuickStyle.setStyle("Basic")
    application = QGuiApplication(sys.argv)
    application.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets/ui/app-icon.png")))
    settings = core.Settings.load(core.runtime_root() / "data/config.json")
    settings.ensure_dirs()
    logging.basicConfig(filename=str(settings.data_path / "logs/quick-ui.log"), level=logging.INFO, encoding="utf-8")
    db = core.Database(settings.data_path / "app.db")
    bridge = Bridge(settings, db, core.RecorderService(settings, db, queue.Queue()), settings_path=core.runtime_root() / "data/config.json")
    engine = create_engine(bridge)
    bridge.closed.connect(application.quit)
    bridge.start()
    try:
        application.exec()
    finally:
        bridge.timer.stop()
        bridge.update_timer.stop()
        bridge.update_startup.stop()
        bridge._update_cancel.set()
        if not bridge._closing:
            bridge.service.stop()
            bridge._pool.shutdown(wait=True)
            bridge._network.shutdown(wait=False, cancel_futures=True)
        engine.deleteLater()
        logging.shutdown()


if __name__ == "__main__":
    run()
