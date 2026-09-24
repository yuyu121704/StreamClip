"""离线 Qt Quick 回归及真实 GPU 窗口缩放测量；不访问生产数据或网络。"""

import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPointF, Qt, QTimer, QUrl, Slot, qInstallMessageHandler
from PySide6.QtGui import QColor, QDesktopServices, QFontMetricsF, QGuiApplication, QImage
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtQml import QQmlEngine, QQmlExpression

import quick_ui as ui
from quick_forms import settings_values


def qml_call(target, expression):
    assert target is not None, "未找到 QML 测试对象：" + expression
    script = QQmlExpression(QQmlEngine.contextForObject(target), target, expression)
    result = script.evaluate()
    assert not script.hasError(), script.error().toString()
    return result


def assert_color_contrast(foreground, background, minimum=4.5):
    luminances = []
    for color in (foreground, background):
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        luminances.append(sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722))))
    low, high = sorted(luminances)
    assert (high + 0.05) / (low + 0.05) >= minimum, (foreground.name(), background.name(), minimum)


def assert_combo_selection(control):
    row = qml_call(control, "popup.contentItem.currentItem")[0]
    assert row is not None and row.property("highlighted"), "下拉菜单缺少选中项"
    foreground, background = qml_call(row, "contentItem.color")[0], qml_call(row, "background.color")[0]
    assert foreground == QColor(qml_call(control, "uiTheme.current.colors.selectionInk")[0])
    assert background in [QColor(qml_call(control, "uiTheme.current.colors." + role)[0]) for role in ("selection", "choicePressed")]
    assert_color_contrast(foreground, background)


def calendar_month(window, prefix, year, month):
    wait_for(QGuiApplication.instance(), lambda: window.findChild(QObject, prefix + "Calendar").property("opened"))
    year_input = window.findChild(QObject, prefix + "CalendarYear")
    qml_call(year_input, "contentItem.forceActiveFocus(); contentItem.selectAll()")
    for digit in str(year):
        QTest.keyClick(window, Qt.Key(int(Qt.Key_0) + int(digit)))
    QTest.keyClick(window, Qt.Key_Return)
    assert year_input.property("value") == year
    month_input = window.findChild(QObject, prefix + "CalendarMonth")
    # 鼠标停在展开列表内时，悬停事件会覆盖刚用键盘定位的月份。
    QTest.mouseMove(window, QPointF(1, 1).toPoint())
    qml_call(month_input, "forceActiveFocus(); popup.open()")
    wait_for(QGuiApplication.instance(), lambda: qml_call(month_input, "popup.opened")[0])
    assert_combo_selection(month_input)
    QTest.keyClick(window, Qt.Key_Home)
    for _ in range(month - 1):
        QTest.keyClick(window, Qt.Key_Down)
    assert_combo_selection(month_input)
    assert month_input.property("highlightedIndex") == month - 1
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(QGuiApplication.instance(), lambda: window.findChild(QObject, prefix + "Calendar").property("viewMonth") == month - 1)


def calendar_day(window, prefix, day):
    grid = window.findChild(QObject, prefix + "CalendarGrid")
    return qml_call(grid, "contentItem.children.find(item => item.dayKey === " + json.dumps(day) + ")")[0]


def choose_calendar_date(window, prefix, day):
    qml_call(window.findChild(QObject, prefix + "DateFilter"), "clicked()")
    if day in {"全部日期", "日期未知"}:
        suffix = "AllDates" if day == "全部日期" else "UnknownDate"
        qml_call(window.findChild(QObject, prefix + suffix), "clicked()")
    else:
        year, month, _ = map(int, day.split("-"))
        calendar_month(window, prefix, year, month)
        item = calendar_day(window, prefix, day)
        assert item is not None and item.property("enabled"), day
        qml_call(item, "forceActiveFocus()")
        QTest.keyClick(window, Qt.Key_Space)
    assert not window.findChild(QObject, prefix + "Calendar").property("visible")


def check_calendar(application, bridge, window, output):
    wait_for(application, lambda: not bridge._refreshing)
    for prefix, page, library in (("recording", 0, bridge.recordings), ("clip", 4, bridge.clips)):
        original = library._all_rows
        base = original[0]
        days = ["2027-01-01", "2026-12-31", "2024-02-29", "日期未知"]
        rows = [dict(base, id=4-i, title="离线日历测试", streamerKey="room:calendar", streamerName="日历测试主播", date=day, started=day) for i, day in enumerate(days)]
        try:
            window.setProperty("page", page)
            library.sync(rows)
            library.setFilter("room:calendar", "")
            button = window.findChild(QObject, prefix + "DateFilter")
            popup = window.findChild(QObject, prefix + "Calendar")
            qml_call(button, "clicked()")
            assert popup.property("viewYear") == 2027 and popup.property("viewMonth") == 0, "应默认打开最近有内容的月份"
            unavailable = calendar_day(window, prefix, "2027-01-02")
            assert not unavailable.property("enabled")
            point = qml_call(unavailable, "mapToItem(null, width / 2, height / 2)")[0]
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
            assert popup.property("visible") and library.filters["date"] == ""
            qml_call(window.findChild(QObject, prefix + "PreviousMonth"), "clicked()")
            assert popup.property("viewYear") == 2026 and popup.property("viewMonth") == 11
            qml_call(window.findChild(QObject, prefix + "NextMonth"), "clicked()")
            assert popup.property("viewYear") == 2027 and popup.property("viewMonth") == 0
            calendar_month(window, prefix, 1, 1)
            assert not window.findChild(QObject, prefix + "PreviousMonth").property("enabled")
            calendar_month(window, prefix, 9999, 12)
            assert not window.findChild(QObject, prefix + "NextMonth").property("enabled")
            qml_call(popup, "close()")
            choose_calendar_date(window, prefix, "2024-02-29")
            assert library.filters["date"] == "2024-02-29" and library.rowCount() == 1
            assert library.filters["streamer"] == "room:calendar"
            qml_call(button, "clicked()")
            assert popup.property("viewYear") == 2024 and popup.property("viewMonth") == 1
            grid = window.findChild(QObject, prefix + "CalendarGrid")
            assert qml_call(grid, "contentItem.children.filter(item => item.dayKey && item.inMonth).length")[0] == 29, "闰年二月应显示 29 天"
            first_day = calendar_day(window, prefix, "2024-02-01")
            wait_for(application, lambda: abs(first_day.property("x") - 3 * (first_day.property("width") + grid.property("spacing"))) < 1)
            assert abs(first_day.property("x") - 3 * (first_day.property("width") + grid.property("spacing"))) < 1, "2024 年 2 月 1 日应位于周四，邻月占位不能被折叠"
            selected = calendar_day(window, prefix, "2024-02-29")
            assert selected.property("highlighted") and "2024年2月29日" in qml_call(selected, "Accessible.name")[0]
            for width, height in ((1280, 820), (1020, 680), (1600, 900), (1020, 680)):
                window.resize(width, height)
                QTest.qWait(150)
                # 检查实际绘制坐标及按钮锚点，而不是仅检查 Popup 的局部 x/y。
                origin = qml_call(popup, "contentItem.mapToItem(null, -leftPadding, -topPadding)")[0]
                anchor = qml_call(button, "mapToItem(null, 0, height)")[0]
                expected_x = max(8, min(anchor.x(), width - popup.property("width") - 8))
                assert abs(origin.x() - expected_x) <= 1, "缩放后日历未跟随日期按钮"
                assert abs(origin.y() - (anchor.y() + 6)) <= 1, "日历应紧贴日期按钮下方"
                assert origin.x() >= 0 and origin.y() >= 0
                assert origin.x() + popup.property("width") <= width
                assert origin.y() + popup.property("height") <= height
                qml_call(popup, "close()")
                qml_call(button, "clicked()")
                QTest.qWait(30)
                reopened = qml_call(popup, "contentItem.mapToItem(null, -leftPadding, -topPadding)")[0]
                assert abs(reopened.x() - expected_x) <= 1 and abs(reopened.y() - (anchor.y() + 6)) <= 1, "重新打开日历位置不正确"
                assert window.grabWindow().save(str(output / f"calendar-{prefix}-{width}.png"))
            calendar_month(window, prefix, 2026, 12)
            QTest.keyClick(window, Qt.Key_Escape)
            assert not popup.property("visible") and library.filters["date"] == "2024-02-29", "浏览月份后取消不应改动筛选"
            assert button.property("activeFocus")
            choose_calendar_date(window, prefix, "全部日期")
            assert library.rowCount() == 4 and library.filters["streamer"] == "room:calendar"
            choose_calendar_date(window, prefix, "日期未知")
            assert library.rowCount() == 1 and library.filters["date"] == "日期未知"
            qml_call(button, "clicked()")
            window.setProperty("page", 2)
            assert not popup.property("visible"), "切页后日历仍覆盖其他页面"
            window.setProperty("page", page)
            library.sync([])
            qml_call(button, "clicked()")
            assert not window.findChild(QObject, prefix + "UnknownDate").property("visible")
            assert qml_call(grid, "contentItem.children.filter(item => item.dayKey && item.enabled).length")[0] == 0
            qml_call(popup, "close()")
        finally:
            library.sync(original)
            library.setFilter("", "")


def check_media_identity():
    rooms = {"101": {"name": "测试主播", "uid": ""}, "202": {"name": "测试主播", "uid": ""}}
    records = [
        {"id": 3, "room_id": "101", "started_at": "2026-09-13", "metadata_json": '{"source_liver_uid":"303","source_name":"测试主播"}'},
        {"id": 2, "room_id": "101", "started_at": "2026-09-09", "metadata_json": "{}"},
        {"id": 1, "room_id": "101", "started_at": "2026-09-08", "metadata_json": '{"source_name":"测试主播"}'},
    ]
    model = ui.MediaRows()
    model.sync([dict(id=r["id"], **ui.media_origin(r, rooms)) for r in records])
    assert model.filters["streamers"] == [{"key": "", "label": "全部主播"}, {"key": "room:101", "label": "测试主播"}], "新录播补齐 UID 后把同一直播间拆成了两个主播"
    model.setFilter("room:101", "")
    assert [r["id"] for r in model.rows] == [3, 2, 1]
    assert model.filters["dates"] == ["全部日期", "2026-09-13", "2026-09-09", "2026-09-08"]
    clip = dict(records[0], metadata_json='{"source_liver_uid":"999"}', recording_metadata_json=records[0]["metadata_json"])
    assert ui.media_origin(clip, rooms)["streamerKey"] == "room:101", "切片必须继承原录播的主播归类"
    rooms["101"]["uid"] = "303"
    model.sync([dict(id=r["id"], **ui.media_origin(r, rooms)) for r in records])
    assert model.filters["streamer"] == "room:101" and model.rowCount() == 3, "配置补齐 UID 不应改变筛选身份"
    replay = dict(records[0], room_id="local", source_type="replay")
    assert ui.media_origin(replay, rooms)["streamerKey"] == "room:101", "已关联 UID 的回放应归入同一主播"
    other = ui.media_origin(dict(records[1], room_id="202"), rooms)
    model.sync([dict(id=4, **other)] + model._all_rows)
    assert len(model.filters["streamers"]) == 3 and model.rowCount() == 3, "不能仅因同名就合并不同直播间"


def check_finished_recordings(application, output):
    with tempfile.TemporaryDirectory(dir=output) as folder:
        settings = ui.core.Settings(base_dir=folder)
        settings.ensure_dirs()
        db = ui.core.Database(settings.data_path / "app.db")
        service = ui.core.RecorderService(settings, db, ui.queue.Queue())
        bridge = ui.Bridge(settings, db, service)
        records = {}
        try:
            db.add_room("901", "离线测试·录制中主播")
            db.add_room("902", "离线测试·已录完主播")
            for status, source in (("starting", "live"), ("recording", "live"), ("stopping", "live"),
                                   ("complete", "live"), ("recorded", "replay"), ("error", "local")):
                active = status in {"starting", "recording", "stopping"}
                rid = db.create_recording("901" if active else "902", status, "离线测试·" + status, "",
                                          "2026-09-16" if active else "2026-09-15", source_type=source)
                records[status] = rid
                if active:
                    db.set_recording_status(rid, status)
                else:
                    db.finish_recording(rid, status, "", "2026-09-15", 60)

            with patch.object(service, "active_room_ids", return_value={"901"}):
                bridge.refresh()
                wait_for(application, lambda: not bridge._refreshing)
                assert [r["id"] for r in bridge.recordings.rows] == [records[s] for s in ("error", "recorded", "complete")], "录播页应隐藏准备、录制和收尾中的记录，保留已结束记录"
                assert bridge.recordings.filters["count"] == bridge.recordings.filters["total"] == 3
                assert bridge.recordings.filters["dates"] == ["全部日期", "2026-09-15"]
                assert [s["key"] for s in bridge.recordings.filters["streamers"]] == ["", "room:902"]
                assert next(r for r in bridge.rooms.rows if r["room_id"] == "901")["status"] == "录制中"
                assert bridge.workspace["activeRooms"] == 1
                for status in ("starting", "stopping", "recording"):
                    bridge.selectRecording(records[status])
                    wait_for(application, lambda: not bridge._refreshing)
                    assert bridge.detail["id"] == 0 and not bridge.detail["canAnalyze"] and not bridge.detail["canDelete"], "隐藏的录制记录不能残留详情或启用操作"

                db.finish_recording(records["recording"], "complete", "", "2026-09-16", 120)
                bridge.refresh()
                wait_for(application, lambda: not bridge._refreshing)
                assert bridge.recordings.filters["total"] == 4, "录制结束后应自动进入录播列表"
                bridge.recordings.setFilter("room:901", "2026-09-16")
                assert [r["id"] for r in bridge.recordings.rows] == [records["recording"]]
                assert bridge.detail["id"] == records["recording"] and bridge.detail["canAnalyze"]

                for rid in records.values():
                    db.set_recording_status(rid, "recording")
                bridge.refresh()
                wait_for(application, lambda: not bridge._refreshing)
                assert not bridge.recordings.rows and bridge.recordings.filters["total"] == 0
                assert bridge.recordings.filters["dates"] == ["全部日期"]
                assert bridge.recordings.filters["streamer"] == bridge.recordings.filters["date"] == ""
                assert bridge.detail["id"] == 0 and not bridge.detail["highlights"]
                assert len(db.list_recordings(-1)) == 6, "页面过滤不能删除数据库记录"
        finally:
            bridge._pool.shutdown(wait=True)
            bridge._network.shutdown(wait=True, cancel_futures=True)
            service.stop()


def check_lightweight_clip_rows(application, output):
    with tempfile.TemporaryDirectory(dir=output) as folder:
        settings = ui.core.Settings(base_dir=folder)
        settings.ensure_dirs()
        db = ui.core.Database(settings.data_path / "app.db")
        service = ui.core.RecorderService(settings, db, ui.queue.Queue())
        bridge = ui.Bridge(settings, db, service)
        try:
            db.add_room("901", "离线内存测试主播")
            db.update_room_config("901", uid="77")
            metadata = {"source_liver_uid": "77", "source_name": "离线内存测试主播",
                        "cloud_non_speech_intervals": [{"start": i, "end": i + 0.5, "source": "offline"} for i in range(5000)]}
            rid = db.create_recording("local", "memory", "离线长录播", "", "2026-09-19", metadata=metadata)
            db.finish_recording(rid, "complete", "", "2026-09-19", 7200)
            clips = []
            for source, state in (("ai", "ready"), ("heuristic", "published"), ("heuristic", "rejected")):
                cid = db.create_clip(rid, "离线切片 " + state, 10, 70, str(Path(folder) / (state + ".mp4")), review_status=state,
                                     metadata={"source": source, "source_liver_uid": "999",
                                               "reason": "保留选中切片的完整复核依据", "unused_analysis": metadata})
                db.set_clip_status(cid, "complete")
                clips.append(cid)
            bridge.selectClip(clips[0])
            wait_for(application, lambda: not bridge._refreshing)
            expected_ids = clips[1::-1]
            assert [r["id"] for r in bridge.clips.rows] == expected_ids, "只能隐藏已淘汰的规则草稿，历史成片必须保留"
            for rows in (bridge.clips.rows, bridge.workspace["recentClips"]):
                assert [r["id"] for r in rows] == expected_ids
                assert all(r["streamerKey"] == "room:901" and r["date"] == "2026-09-19" for r in rows), "切片应继承原录播身份，不能误用切片元数据"
                assert all(r["duration"] == "00:01:00" and r["state"] == ui.core.STATUS_LABELS["complete"] for r in rows)
                assert all(set(r) == {"id", "title", "duration", "state", "streamerKey", "streamerName", "date"} for r in rows), "列表把完整录播/切片分析带入了 QML"
                assert len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) < 4096, "列表内存不应随音频分析元数据大小增长"
            assert bridge.workspace["clip"]["evidence"] == "保留选中切片的完整复核依据"
            changes = []
            bridge.clips.dataChanged.connect(lambda *args: changes.append(True))
            bridge.workspaceChanged.connect(lambda: changes.append(True))
            bridge.refresh()
            wait_for(application, lambda: not bridge._refreshing)
            assert not changes, "未变化的数据不应重建切片界面"
            assert json.loads(db.get_recording(rid)["metadata_json"]) == metadata, "优化不能删改原录播分析"
            assert db.clip_review(clips[0])[2]["unused_analysis"] == metadata, "优化不能删改切片复核资料"
        finally:
            bridge._pool.shutdown(wait=True)
            bridge._network.shutdown(wait=True, cancel_futures=True)
            service.stop()


def check_media_filters(application, bridge, window, folder, output):
    db = bridge.db
    original_records = [db.get_recording(i) for i in (1, 2)]
    existing_clips = {c["id"] for c in db.list_clips(-1)}
    original_selected = bridge._selected
    new_record = 0
    db.add_room("90001", "离线测试·主播甲")
    db.update_room_config("90001", uid="9001")
    db.add_room("90002", "离线测试·主播乙")
    db.update_room_config("90002", uid="9002")

    def reload():
        wait_for(application, lambda: not bridge._refreshing)
        bridge.refresh()
        wait_for(application, lambda: not bridge._refreshing)

    def choose(prefix, kind, index):
        if kind == "Date":
            library = bridge.recordings if prefix == "recording" else bridge.clips
            choose_calendar_date(window, prefix, library.filters["dates"][index])
            application.processEvents()
            return
        control = window.findChild(QObject, prefix + kind + "Filter")
        QTest.mouseMove(window, QPointF(1, 1).toPoint())
        qml_call(control, "forceActiveFocus(); popup.open()")
        wait_for(application, lambda: qml_call(control, "popup.opened")[0])
        assert_combo_selection(control)
        QTest.keyClick(window, Qt.Key_Home)
        for _ in range(index):
            QTest.keyClick(window, Qt.Key_Down)
        assert_combo_selection(control)
        QTest.keyClick(window, Qt.Key_Return)
        application.processEvents()
        return control

    try:
        with db._connect() as conn:
            conn.execute("UPDATE recordings SET room_id='90001',started_at='2026-08-01T23:58:00+08:00' WHERE id=1")
            conn.execute("UPDATE recordings SET room_id='90002',started_at='2026-08-02 00:02:00' WHERE id=2")
        new_record = db.create_recording("local", "filter-replay", "离线测试·主播甲的回放", "", "2026-08-03T01:00:00+08:00", source_type="replay", metadata={"source_liver_uid": "9001", "source_name": "旧名称"})
        db.finish_recording(new_record, "complete", "", "2026-08-03T02:00:00+08:00", 3600)
        clip_path = str(Path(folder) / "filter-missing-clip.mp4")
        oldest_clip = db.create_clip(1, "离线测试·旧录播的新切片", 0, 60, clip_path)
        db.set_clip_status(oldest_clip, "complete")
        for _ in range(501):
            db.create_clip(2, "离线测试·主播乙的切片", 0, 45, clip_path)
        reload()
        assert bridge.recordings.rowCount() > 300 and bridge.clips.rowCount() > 500
        assert "summary" not in db.list_recordings(-1, overview=True)[0]
        assert next(r for r in bridge.clips.rows if r["id"] == oldest_clip)["date"] == "2026-08-01", "切片误用了生成日期"
        assert next(r for r in bridge.recordings.rows if r["id"] == new_record)["streamerName"] == "离线测试·主播甲"
        bridge.selectRecording(2)
        wait_for(application, lambda: bridge.detail["id"] == 2 and not bridge._refreshing)
        for prefix, page, library in (("recording", 0, bridge.recordings), ("clip", 4, bridge.clips)):
            window.setProperty("page", page)
            index = next(i for i, choice in enumerate(library.filters["streamers"]) if choice["key"] == "room:90001")
            choose(prefix, "Streamer", index)
            assert library.filters["streamer"] == "room:90001"
            assert library.filters["dates"] == (["全部日期", "2026-08-03", "2026-08-01"] if page == 0 else ["全部日期", "2026-08-01"])
            choose(prefix, "Date", len(library.filters["dates"]) - 1)
            assert library.filters["date"] == "2026-08-01" and library.rowCount() == 1
            assert library.rows[0]["id"] == (1 if page == 0 else oldest_clip), "无法找到超过原列表上限的历史条目"
            if page == 0:
                assert bridge.detail["id"] == 0 and not window.findChild(QObject, "deleteRecordingButton").property("enabled")
                bridge.selectRecording(1)
            else:
                assert not bridge.workspace["clip"] and window.findChild(QMediaPlayer, "clipPlayer").source().isEmpty()
                bridge.selectClip(oldest_clip)
            wait_for(application, lambda: not bridge._refreshing)
            assert (bridge.detail if page == 0 else bridge.workspace["clip"]).get("id") == (1 if page == 0 else oldest_clip)
            reload()
            assert library.filters["date"] == "2026-08-01", "后台刷新清除了筛选"
            if page == 4:
                clip_list = window.findChild(QObject, "clipList")
                qml_call(clip_list, "forceLayout()")
                assert qml_call(clip_list, "itemAtIndex(0).Accessible.name")[0] == "离线测试·旧录播的新切片 · 离线测试·主播甲 · 2026-08-01"
            for width, height in ((1280, 820), (1020, 680)):
                window.resize(width, height)
                QTest.qWait(150)
                for kind in ("Streamer", "Date"):
                    control = window.findChild(QObject, prefix + kind + "Filter")
                    assert control.property("visible")
                    assert qml_call(control, "mapToItem(null, 0, 0).x >= 0 && mapToItem(null, width, height).x <= " + str(width))[0]
                assert window.grabWindow().save(str(output / f"filters-{prefix}-{width}.png"))
                control = window.findChild(QObject, prefix + "StreamerFilter")
                point = control.mapToItem(window.contentItem(), QPointF(control.width() / 2, control.height() / 2))
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
                wait_for(application, lambda: qml_call(control, "popup.opened")[0])
                assert_combo_selection(control)
                assert window.grabWindow().save(str(output / f"selection-{prefix}-{width}.png"))
                QTest.keyClick(window, Qt.Key_Escape)
                assert library.filters["streamer"] == "room:90001" and library.filters["date"] == "2026-08-01"
            index = next(i for i, choice in enumerate(library.filters["streamers"]) if choice["key"] == "room:90002")
            choose(prefix, "Streamer", index)
            assert library.filters["date"] == "" and library.filters["dates"] == ["全部日期", "2026-08-02"]
            if page == 0:
                assert bridge.detail["id"] == 0
                # 不同页面的筛选状态互不覆盖。
                assert bridge.clips.filters["streamer"] == ""
            else:
                assert not bridge.workspace["clip"]
            qml_call(window.findChild(QObject, prefix + "ResetFilters"), "clicked()")
            assert library.filters["streamer"] == library.filters["date"] == ""
            choose(prefix, "Date", library.filters["dates"].index("2026-08-01"))
            assert library.rowCount() == 1 and library.filters["streamer"] == "", "全部主播下无法按日期筛选"
            library.setFilter("", "")

        # 数据刷新与筛选交错，旧详情不得重新显示；筛选必须保留。
        original_list = db.list_recordings
        def slow_list(*args, **kwargs):
            time.sleep(0.1)
            return original_list(*args, **kwargs)
        with patch.object(db, "list_recordings", side_effect=slow_list):
            bridge.selectRecording(1)
            bridge.recordings.setFilter("room:90002", "2026-08-02")
            wait_for(application, lambda: not bridge._refreshing)
        assert bridge.detail["id"] == 0 and bridge.recordings.filters["streamer"] == "room:90002"
        # 工作台的显式跳转能定位此前被筛掉的切片。
        bridge.clips.setFilter("room:90002", "")
        bridge.selectClip(oldest_clip)
        wait_for(application, lambda: not bridge._refreshing)
        assert bridge.workspace["clip"]["id"] == oldest_clip and not bridge.clips.filters["streamer"]
        bridge.clips.setFilter("room:90001", "2026-08-01")
        with db._connect() as conn:
            conn.execute("UPDATE clips SET deleted=1 WHERE id=?", (oldest_clip,))
        reload()
        assert not bridge.clips.filters["streamer"] and not bridge.workspace["clip"], "删除最后一条后遗留失效选项或详情"

        # 缺失/损坏元数据、已移除房间、同名主播仍有明确入口。
        model = ui.MediaRows()
        origins = [ui.media_origin({"room_id": "local", "metadata_json": "[]", "started_at": "invalid"}, {}),
                   ui.media_origin({"room_id": "999", "metadata_json": "{", "started_at": "2026-08-04"}, {}),
                   ui.media_origin({"room_id": "local", "source_type": "replay", "metadata_json": '{"source_liver_uid":"77","source_name":"回放主播"}', "started_at": "2026-08-05"}, {"local": {"name": "本地媒体"}})]
        assert origins[0]["streamerName"] == "未标注主播" and origins[0]["date"] == "日期未知"
        assert origins[1]["streamerName"] == "房间 999" and origins[2]["streamerName"] == "回放主播"
        model.sync([dict(id=i, **origin) for i, origin in zip((3, 2, 1), origins)])
        model.setFilter("unknown", "日期未知")
        assert model.rowCount() == 1
        model.sync([])
        assert model.filters["dates"] == ["全部日期"] and model.filters["streamer"] == ""
        model.sync([dict(id=i, streamerKey=f"uid:{i}", streamerName="同名主播", date="2026-08-01") for i in (2, 1)])
        assert len({item["label"] for item in model.filters["streamers"]}) == 3
    finally:
        bridge.recordings.setFilter("", "")
        bridge.clips.setFilter("", "")
        with db._connect() as conn:
            for record in original_records:
                conn.execute("UPDATE recordings SET room_id=?,started_at=? WHERE id=?", (record["room_id"], record["started_at"], record["id"]))
            conn.execute("UPDATE recordings SET deleted=1 WHERE id=?", (new_record,))
            for clip in db.list_clips(-1):
                if clip["id"] not in existing_clips:
                    conn.execute("UPDATE clips SET deleted=1 WHERE id=?", (clip["id"],))
        db.remove_room("90001")
        db.remove_room("90002")
        bridge.selectRecording(original_selected)
        reload()


def check_workbench(application, bridge, window, recording_id, folder, output):
    """使用真实隔离库验证六页写入；网络/投稿边界不触达生产服务。"""
    errors, completed = [], []
    bridge.error.connect(errors.append)
    bridge.actionDone.connect(lambda action, data: completed.append((action, data)))
    def act(action, values):
        errors.clear()
        bridge.perform(action, values)
        wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
        assert not errors, (action, errors)

    db, service = bridge.db, bridge.service
    legacy_settings = ui.core.Settings(base_dir=str(Path(folder) / "legacy"), bili_cookie="SESSDATA=legacy; bili_jct=legacy; DedeUserID=77")
    legacy_settings.ensure_dirs()
    legacy_db = ui.core.Database(legacy_settings.data_path / "app.db")
    legacy = ui.Bridge(legacy_settings, legacy_db, ui.core.RecorderService(legacy_settings, legacy_db, ui.queue.Queue()))
    try:
        with patch.object(legacy.service, "start"):
            legacy._start_service()
            legacy._start_service()
        assert len(legacy_db.list_cookie_accounts()) == 1 and not legacy.settings.bili_cookie
        assert legacy.settings.download_account_id == legacy.settings.publish_account_id != 0
    finally:
        legacy.service.stop()
        legacy._pool.shutdown()
        legacy._network.shutdown()
    act("roomAdd", {"room_id": "https://live.bilibili.com/90001", "name": "Qt 测试主播"})
    act("roomSave", {"room_id": "90001", "name": "Qt 测试主播", "uid": "88", "account_id": 0, "auto_record": False, "auto_asr": True, "auto_slice": True, "recap_template": "测试模板"})
    assert db.get_room("90001")["auto_record"] == 0
    assert window.findChild(QObject, "workspacePages").property("selectedRoom")["uid"] == "88", "保存后主播编辑仍显示旧数据"
    act("roomToggle", {"room_id": "90001"})
    assert not db.get_room("90001")["enabled"]
    with patch.object(service, "start_recording") as start, patch.object(service, "stop_recording") as stop:
        act("recordStart", {"room_id": "90001"})
        act("recordStop", {"room_id": "90001"})
        start.assert_called_once_with("90001")
        stop.assert_called_once_with("90001")

    saved = settings_values(bridge.settings)
    saved["auto_slice"] = True
    act("settingsSave", saved)
    before = bridge.settings_path.read_bytes()
    bad = dict(saved, subtitle_color="not-a-color")
    bridge.perform("settingsSave", bad)
    wait_for(application, lambda: not bridge.busy)
    assert errors and bridge.settings_path.read_bytes() == before and bridge.settings.auto_slice
    QTest.keyClick(window, Qt.Key_Escape)
    check_model_connection(application, bridge, window)
    saved.update(llm_endpoint="https://example.invalid/v1", llm_api_key="offline-key", llm_model="offline-model")
    act("settingsSave", saved)
    assert ui.core.Settings.load(bridge.settings_path).llm_api_key == "offline-key", "保存没有沿用原配置格式"
    saved["llm_api_key"] = "different-key"
    bridge.perform("settingsSave", saved)
    wait_for(application, lambda: not bridge.busy)
    assert errors, "更换 Key 后错误接受旧模型验证"
    QTest.keyClick(window, Qt.Key_Escape)
    check_asr_model_connection(application, bridge, window, output)

    # 取消和过期的二维码事件不能保存登录，也不能进入日志或传给 QML Cookie。
    with patch.object(service, "qr_login_account", side_effect=lambda rid: ui.core.threading.Event()):
        bridge.startQr(0)
        request = bridge._qr_id
        qr = ui.core.Image.new("RGB", (120, 120), "white")
        ready = bridge._handle_qr({"kind": "qr_login", "request_id": request, "state": "ready", "data": {"image": qr}})
        assert ready["image"].startswith("data:image/png;base64,")
        bridge.cancelQr()
        event = {"kind": "qr_login", "request_id": request, "state": "success", "data": {"cookie": "SESSDATA=test; bili_jct=test; DedeUserID=88", "profile": {"mid": 88, "uname": "Qt 测试账号", "isLogin": True}}}
        assert bridge._handle_qr(event) is None and not db.list_cookie_accounts()
        bridge.startQr(0)
        event["request_id"] = bridge._qr_id
        result = bridge._handle_qr(event)
        assert result["state"] == "success" and "cookie" not in result
        account = db.list_cookie_accounts()[0]
        assert bridge.settings.download_account_id == account["id"] == bridge.settings.publish_account_id
        bridge.cancelQr()
        bridge.startQr(0)
        blocked_event = {"kind": "qr_login", "request_id": bridge._qr_id, "state": "success", "data": {"cookie": "SESSDATA=blocked; bili_jct=blocked; DedeUserID=99", "profile": {"mid": 99, "uname": "取消测试", "isLogin": True}}}
        entered = ui.core.threading.Event()
        original_save = db.save_bilibili_account
        def saving(*args, **kwargs):
            entered.set()
            return original_save(*args, **kwargs)
        with db._connect() as locked, patch.object(db, "save_bilibili_account", side_effect=saving):
            locked.execute("BEGIN IMMEDIATE")
            pending = bridge._network.submit(bridge._handle_qr, blocked_event)
            assert entered.wait(2)
            bridge.cancelQr()
            locked.rollback()
        assert pending.result(timeout=5) is None and len(db.list_cookie_accounts()) == 1, "关闭二维码后等待中的保存仍落库"
    act("accountSelect", {"role": "publish", "id": account["id"]})
    with patch.object(service, "check_login") as check:
        act("checkLogin", {"id": account["id"]})
        check.assert_called_once_with(account["id"])
    assert all("cookie" not in a for a in bridge.workspace["accounts"])

    bridge.setGlossary("", "pending", True)
    wait_for(application, lambda: any(c["channel_id"] == "88" for c in bridge.glossary["channels"]) and not bridge._refreshing)
    assert next(c for c in bridge.glossary["channels"] if c["channel_id"] == "88")["room_id"] == "90001", "直播间主播应自动进入下拉列表"
    act("glossaryTerm", {"scope": "", "term": "羽秋", "canonical": "羽啾", "category": "名称", "enabled": True})
    term = db.glossary.entries("")[0]
    assert window.findChild(QObject, "termEditor").property("values").toVariant()["id"] == term["id"], "新增词条保存后缺少 ID，会误创建重复词条"
    act("glossaryChange", {"scope": "88", "ids": [term["id"]], "operation": "disable"})
    assert not db.glossary.entries("88")[0]["enabled"]
    act("glossaryNote", {"scope": "88", "note": "宇宙猫是粉丝称呼。"})
    act("glossaryBind", {"scope": "88", "recording_id": recording_id})
    assert db.get_recording(recording_id)["glossary_channel_id"] == "88"
    export = Path(folder) / "词条导出.json"
    act("glossaryExport", {"scope": "88", "path": str(export)})
    assert "宇宙猫" in export.read_text(encoding="utf-8")
    act("glossaryImport", {"scope": "88", "path": str(export)})
    candidate_id = db.glossary.upsert_candidate("88", str(recording_id), {"term": "宇宙喵", "canonical": "宇宙猫", "category": "粉丝称呼", "confidence": 0.9, "reason": "测试依据"})
    act("glossaryApprove", {"scope": "88", "ids": [candidate_id], "edit": {"term": "宇宙喵", "canonical": "宇宙猫", "category": "粉丝称呼"}})
    assert any(e["canonical"] == "宇宙猫" for e in db.glossary.entries("88"))
    bridge.setGlossary("88", "approved", True)
    wait_for(application, lambda: not bridge._refreshing)
    assert bridge.glossary["note"] and bridge.glossary["candidates"]
    bridge.setGlossary("88", "approved", False)

    # 真实 H.264/AAC 样片验证 Qt Multimedia 的加载、播放、定位及切页暂停。
    ffmpeg = Path(ui.os.environ.get("LIVECLIP_TEST_FFMPEG", str(Path(ui.core.__file__).parent / "tools/ffmpeg.exe")))
    video = Path(folder) / "Qt播放测试.mp4"
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x426e78:s=640x360:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cover = Path(folder) / "Qt封面.jpg"
    ui.core.Image.new("RGB", (1280, 720), "#426e78").save(cover)
    clip_metadata = {"reason": "用于离线媒体验证", "title_candidates": ["候选标题"], "visual_review": {"approved": False, "reason": "标题含有未证实的推测，请核对原片。"}}
    review_warning = ui.core.visual_review_warning(clip_metadata)
    clip_id = db.create_clip(recording_id, "Qt 测试成片", 0, 3, str(video), metadata=clip_metadata, review_status="manual_review")
    db.set_clip_status(clip_id, "complete", thumbnail_path=str(cover))
    legacy_id = db.create_clip(recording_id, "已淘汰的历史候选", 0, 3, str(video), metadata={"source": "heuristic"}, review_status="rejected")
    published_id = db.create_clip(recording_id, "已发布的历史成片", 0, 3, str(video), metadata={"source": "heuristic"}, review_status="published")
    review_id = db.create_clip(recording_id, "需复核的 AI 成片", 0, 3, str(video), metadata={"source": "llm"}, review_status="rejected")
    visible_ids = {clip["id"] for clip in bridge._workspace_snapshot()["clips"]}
    assert {clip_id, published_id, review_id} <= visible_ids and legacy_id not in visible_ids
    assert db.get_clip(legacy_id), "移除显示入口不能删除历史数据"
    assert not hasattr(bridge, "showLegacy")
    assert not any(child.property("text") == "显示旧规则候选" for child in window.findChildren(QObject))
    bridge.selectClip(clip_id)
    wait_for(application, lambda: bridge.workspace["clip"].get("id") == clip_id)
    original_visibility = bridge.settings.publish_visibility
    bridge.settings.publish_visibility = "public"
    detail = bridge._workspace_snapshot()["clip"]
    assert "投稿可见性：私密（仅自己可见）" in detail["info"] and review_warning in detail["info"]
    bridge.settings.publish_visibility = original_visibility
    window.setProperty("page", 4)
    player = window.findChild(QMediaPlayer, "clipPlayer")
    assert player is not None
    wait_for(application, lambda: player.duration() > 0, timeout=10)
    assert player.error() == QMediaPlayer.NoError and player.duration() >= 2900
    player.audioOutput().setVolume(0)
    player.play()
    wait_for(application, lambda: player.position() > 100)
    before_refresh = player.position()
    bridge.refresh()
    wait_for(application, lambda: not bridge._refreshing)
    assert player.position() >= before_refresh and player.playbackState() == QMediaPlayer.PlayingState
    player.setPosition(1500)
    assert player.position() >= 1450
    window.setProperty("page", 0)
    assert player.playbackState() != QMediaPlayer.PlayingState
    upload_id = db.create_upload(clip_id, "Qt 测试投稿", "初始简介", "直播切片", 21, metadata={"visibility": "self", "review_warning": review_warning})
    act("saveUpload", {"id": upload_id, "title": "  Qt 修改投稿  ", "description": "新简介", "tags": "直播切片,虚拟主播", "tid": 21})
    assert db.get_upload(upload_id)["description"] == "新简介"
    assert window.findChild(QObject, "workspacePages").property("selectedUpload")["title"] == db.get_upload(upload_id)["title"], "投稿表单没有回显规范化结果"
    window.setProperty("page", 5)
    warning_label = window.findChild(QObject, "uploadReviewWarning")
    assert warning_label.property("text") == review_warning and warning_label.property("visible")
    assert next(row for row in bridge._workspace_snapshot()["uploads"] if row["id"] == upload_id)["review_warning"] == review_warning
    with patch.object(service, "retry_upload") as upload, patch.object(service, "enqueue_upload", return_value=upload_id) as enqueue:
        act("submitUpload", {"id": upload_id})
        act("enqueueClip", {"id": clip_id})
        upload.assert_called_once_with(upload_id)
        enqueue.assert_called_once_with(clip_id)
    player.stop()
    # Exercise the real confirmation, captured target, media release and empty state.
    pages = window.findChild(QObject, "workspacePages")
    delete_button = window.findChild(QObject, "deleteClipButton")
    delete_dialog = window.findChild(QObject, "workspaceConfirmation")
    window.setProperty("page", 4)
    delete_video = Path(folder) / "删除播放测试.mp4"
    delete_recording_id = db.create_recording("123", "delete-clip-ui", "删除切片测试来源", str(Path(folder) / "删除测试原录播.mp4"), ui.core.now_text())
    delete_video.write_bytes(video.read_bytes())
    delete_id = db.create_clip(delete_recording_id, "删除播放测试", 0, 3, str(delete_video))
    bridge.selectClip(delete_id)
    wait_for(application, lambda: bridge.workspace["clip"].get("id") == delete_id and not bridge._refreshing)
    assert not delete_button.property("enabled")
    db.set_clip_status(delete_id, "complete")
    bridge.refresh()
    wait_for(application, lambda: delete_button.property("enabled") and not bridge._refreshing)
    qml_call(delete_button, "clicked()")
    wait_for(application, lambda: delete_dialog.property("opened"))
    qml_call(delete_dialog, "reject()")
    assert delete_video.exists() and not db.get_clip(delete_id)["deleted"]
    qml_call(delete_button, "clicked()")
    wait_for(application, lambda: delete_dialog.property("opened"))
    bridge.selectClip(clip_id)
    wait_for(application, lambda: bridge.workspace["clip"].get("id") == clip_id and not bridge._refreshing)
    errors.clear()
    qml_call(delete_dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    assert not errors and db.get_clip(delete_id)["deleted"] and not delete_video.exists()
    assert bridge.workspace["clip"]["id"] == clip_id and video.exists()
    delete_video.write_bytes(video.read_bytes())
    delete_id = db.create_clip(delete_recording_id, "删除当前播放", 0, 3, str(delete_video))
    db.set_clip_status(delete_id, "complete")
    bridge.selectClip(delete_id)
    wait_for(application, lambda: bridge.workspace["clip"].get("id") == delete_id and not bridge._refreshing)
    wait_for(application, lambda: player.duration() > 0)
    player.play()
    wait_for(application, lambda: player.position() > 100)
    qml_call(delete_button, "clicked()")
    wait_for(application, lambda: delete_dialog.property("opened"))
    qml_call(delete_dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    assert not errors and not delete_video.exists() and not bridge.workspace["clip"]
    assert player.source().isEmpty() and not delete_button.property("enabled")
    assert delete_id not in {c["id"] for c in bridge.clips.rows}
    bridge.selectClip(clip_id)
    wait_for(application, lambda: bridge.workspace["clip"].get("id") == clip_id and not bridge._refreshing)
    with db._connect() as conn:
        conn.execute("UPDATE recordings SET deleted=1 WHERE id=?", (delete_recording_id,))
    bridge.refresh()
    wait_for(application, lambda: not bridge._refreshing)
    settings_form = window.findChild(QObject, "settingsForm")
    pages = window.findChild(QObject, "workspacePages")
    qml_call(settings_form, "setValue('llm_endpoint', 'https://unsaved.invalid/v1')")
    assert settings_form.property("dirty")
    window.setProperty("page", 0)
    window.setProperty("page", 7)
    assert settings_form.property("values").toVariant()["llm_endpoint"] == "https://unsaved.invalid/v1"
    qml_call(settings_form, "reset(bridge.formSettings)")
    for category in range(4):
        pages.setProperty("settingsCategory", category)
        pages.setProperty("mediaDetails", True)
        QTest.qWait(120)
        if category == 2:
            pending_items = [settings_form]
            endpoint_input = None
            while pending_items:
                item = pending_items.pop()
                if item.objectName() == "input_llm_endpoint":
                    endpoint_input = item
                    break
                pending_items.extend(item.childItems())
            assert qml_call(endpoint_input, "Accessible.name")[0] == "AI API 地址"
        assert window.screen().grabWindow(int(window.winId())).save(str(output / f"settings-{category}.png"))
    glossary = window.findChild(QObject, "glossaryDialog")
    qml_call(glossary, "open()")
    wait_for(application, lambda: bool(glossary.property("opened")) and not bridge._refreshing)
    scope_combo = window.findChild(QObject, "glossaryScope")
    assert qml_call(scope_combo, "model.some(c=>c.value==='88'&&c.label.indexOf('直播间 90001')>=0)")[0]
    assert window.screen().grabWindow(int(window.winId())).save(str(output / "glossary.png"))
    term_form = window.findChild(QObject, "termEditor")
    qml_call(term_form, "setValue('term', 'unsaved-term')")
    qml_call(glossary, "selectScope('88')")
    assert glossary.property("scope") == "", "切换术语范围丢弃了未保存表单"
    qml_call(window.findChild(QObject, "glossaryDiscard"), "reject()")
    qml_call(term_form, "reset({enabled:true})")
    fill = window.findChild(QObject, "fillKnowledge")
    assert not fill.property("enabled"), "全局范围不能检索主播资料"
    qml_call(glossary, "selectScope('88')")
    wait_for(application, lambda: bridge.glossary.get("scope") == "88" and not bridge._refreshing)
    window.findChild(QObject, "glossaryTabs").setProperty("currentIndex", 1)
    editor = window.findChild(QObject, "knowledgeEditor")
    baseline = db.glossary.note("88")
    editor.setProperty("text", baseline + "\n未保存的手写补充")
    from hikami_glossary import KNOWLEDGE_HEADER, KNOWLEDGE_MANUAL
    draft = KNOWLEDGE_HEADER + "\n\n来源：https://zh.moegirl.org.cn/测试主播\n\n测试主播的词条摘要。" + KNOWLEDGE_MANUAL + editor.property("text")
    with patch("hikami_glossary.build_streamer_knowledge", return_value=draft) as generate:
        qml_call(fill, "clicked()")
        wait_for(application, lambda: not bridge.busy and editor.property("text") == draft)
        assert generate.call_args.args[0]["channel_id"] == "88"
        assert "未保存的手写补充" in generate.call_args.args[1]
        assert len(generate.call_args.args) == 4, "一键填写不再依赖联网搜索工具"
    assert editor.property("text").count(KNOWLEDGE_HEADER) == 1
    assert "## 7. 待核实资料" not in editor.property("text")
    assert db.glossary.note("88") == baseline, "草稿不应静默覆盖已保存资料"
    qml_call(glossary, "selectScope('')")
    assert glossary.property("scope") == "88", "生成草稿也应受到未保存保护"
    qml_call(window.findChild(QObject, "glossaryDiscard"), "reject()")
    assert window.screen().grabWindow(int(window.winId())).save(str(output / "knowledge.png"))
    act("glossaryNote", {"scope": "88", "note": draft})
    assert draft in db.glossary.export_prompt("88"), "保存知识库必须进入总结与切片参考"
    with patch("hikami_glossary.build_streamer_knowledge", side_effect=RuntimeError("合成词条读取失败")):
        errors.clear()
        qml_call(fill, "clicked()")
        wait_for(application, lambda: not bridge.busy and bool(errors))
        assert editor.property("text") == draft and db.glossary.note("88") == draft
    qml_call(glossary, "requestClose()")
    wait_for(application, lambda: not glossary.property("opened"))
    pages.setProperty("settingsCategory", 0)
    print("Qt workbench checks passed: rooms, atomic settings, model credentials, QR cancellation, accounts, glossary, uploads and real H.264/AAC playback")


def assert_fixed_columns(window, output):
    original = (window.width(), window.height(), window.property("page"))
    try:
        for width, height in ((1020, 680), (1280, 820)):
            window.resize(width, height)
            for page, name in ((0, "recordingList"), (4, "clipList"), (5, "uploadList")):
                window.setProperty("page", page)
                QTest.qWait(100)
                listing = window.findChild(QObject, name)
                panel = listing.parentItem()
                # 录播列表还有一层内容布局。
                if name == "recordingList":
                    panel = panel.parentItem()
                layout = panel.parentItem()
                before = panel.width()
                assert before >= 240 and panel.height() > 100
                assert before + 14 + 310 <= layout.width() + 1
                if name == "clipList":
                    filters = window.findChild(QObject, "clipDateFilter").parentItem()
                    date_label = next(item for item in filters.childItems() if item.property("text") == "录播日期")
                    edge = panel.mapToScene(QPointF(before, 0)).x()
                    date_start = date_label.mapToScene(QPointF(0, 0)).x()
                    glyph_width = QFontMetricsF(date_label.property("font")).horizontalAdvance("录")
                    assert date_start - 2 <= edge <= date_start + glyph_width + 2, ("切片列表右边界应位于“录”字附近", edge, date_start)
                    assert before + 14 + 350 <= layout.width() + 1, "切片详情被挤出窗口"
                    # 用实际列表字体校验中文标题容量，保留超长标题原有的两行显示。
                    row = qml_call(listing, "itemAtIndex(0)")[0]
                    assert row is not None
                    metrics = QFontMetricsF(row.property("font"))
                    title = "#123  " + "标题" * 10
                    assert metrics.horizontalAdvance(title) <= listing.width() - 16
                start = panel.mapToScene(QPointF(before + 7, panel.height() / 2)).toPoint()
                QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
                QTest.mouseMove(window, start + QPointF(90, 0).toPoint(), 30)
                QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, start + QPointF(90, 0).toPoint())
                QTest.qWait(30)
                assert abs(panel.width() - before) < 1, name + " 分隔仍可拖动"
                assert window.grabWindow().save(str(output / f"fixed-{name}-{width}.png"))
    finally:
        window.resize(original[0], original[1])
        window.setProperty("page", original[2])


def assert_native_resize_edges(window, output):
    """暂停 Qt 布局后扩窗，直接采样 DWM 屏幕，避免 grabWindow 先重绘掩盖黑边。"""
    if ui.sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    from PIL import ImageGrab

    user = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmFlush.argtypes = []
    dwm.DwmFlush.restype = ctypes.c_long
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    for name in ("GetWindowRect", "GetClientRect", "ClientToScreen", "SetWindowPos"):
        getattr(user, name).restype = wintypes.BOOL
    window.resize(1020, 680)
    window.setPosition(40, 40)
    window.raise_()
    QTest.qWait(250)
    handle = int(window.winId())
    rect = wintypes.RECT()
    assert user.GetWindowRect(handle, ctypes.byref(rect))
    background_rgb = window.color().getRgb()[:3]
    samples = []
    try:
        for i, (extra_width, extra_height) in enumerate(((180, 110), (330, 170))):
            assert user.SetWindowPos(handle, -1, rect.left, rect.top, rect.right - rect.left + extra_width, rect.bottom - rect.top + extra_height, 0x0010)
            # Wait for DWM, not Qt: a fixed sleep can capture the previous window bounds.
            assert dwm.DwmFlush() == 0, "DWM 尚未完成扩窗合成"
            client, origin = wintypes.RECT(), wintypes.POINT(0, 0)
            assert user.GetClientRect(handle, ctypes.byref(client))
            assert user.ClientToScreen(handle, ctypes.byref(origin))
            picture = ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + client.right, origin.y + client.bottom), all_screens=True).convert("RGB")
            picture.save(output / f"native-growing-{i}.png")
            w, h = picture.size
            strips = [picture.crop((w - 48, h // 3, w - 4, h * 2 // 3)), picture.crop((w // 3, h - 36, w * 2 // 3, h - 4))]
            black, background = [], []
            for strip in strips:
                pixels = list(zip(*[iter(strip.tobytes())] * 3))
                black.append(sum(max(pixel) < 4 for pixel in pixels) / len(pixels))
                background.append(sum(max(abs(c - expected) for c, expected in zip(pixel, background_rgb)) <= 2 for pixel in pixels) / len(pixels))
            samples.append({"size": [w, h], "black_edge_ratios": black, "theme_background_ratios": background})
        (output / "native-edges.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
        assert all(max(sample["black_edge_ratios"]) < 0.01 for sample in samples), samples
        assert all(min(sample["theme_background_ratios"]) > 0.99 for sample in samples), "扩窗区域透底或颜色错误：" + str(samples)
    finally:
        assert user.SetWindowPos(handle, -2, rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top, 0x0010)
        QTest.qWait(150)


def check_asr_model_connection(application, bridge, window, output):
    qml_call(window, "errorDialog.close()")
    pages = window.findChild(QObject, "workspacePages")
    form = window.findChild(QObject, "settingsForm")
    window.setProperty("page", 7)
    pages.setProperty("settingsCategory", 2)
    application.processEvents()
    link = window.findChild(QObject, "aliyunApiKeyLink")
    assert link is not None and link.property("visible")
    assert "bailian.console.aliyun.com" in link.property("text")
    assert "获取 API Key" in qml_call(link, "Accessible.name")[0]
    opened = []

    class UrlReceiver(QObject):
        @Slot(QUrl)
        def receive(self, url):
            opened.append(url.toString())

    receiver = UrlReceiver()
    QDesktopServices.setUrlHandler("https", receiver, "receive")
    qml_call(form, "setValue('dashscope_api_key', 'offline-link-draft')")
    before_link = (qml_call(form, "JSON.stringify(values)")[0], form.property("dirty"), bridge.settings_path.read_bytes())
    try:
        for width, height in ((1020, 680), (1280, 820)):
            window.resize(width, height)
            QTest.qWait(150)
            point = link.mapToItem(window.contentItem(), QPointF(0, 0))
            assert point.x() >= 0 and point.y() >= 0
            assert point.x() + link.width() <= window.width() and point.y() + link.height() <= window.height()
            assert link.property("implicitContentWidth") <= link.width() - link.property("leftPadding") - link.property("rightPadding")
            center = link.mapToItem(window.contentItem(), QPointF(link.width() / 2, link.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center.toPoint())
            qml_call(link, "forceActiveFocus()")
            QTest.keyClick(window, Qt.Key_Space)
            assert window.grabWindow().save(str(output / f"aliyun-key-link-{width}.png"))
        assert opened == ["https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key"] * 4
        assert before_link == (qml_call(form, "JSON.stringify(values)")[0], form.property("dirty"), bridge.settings_path.read_bytes()), "打开阿里云链接不应修改或保存设置"
        for category in (0, 1, 3):
            pages.setProperty("settingsCategory", category)
            application.processEvents()
            assert not link.property("visible"), "阿里云链接不应出现在其他设置分组"
    finally:
        QDesktopServices.unsetUrlHandler("https")
        pages.setProperty("settingsCategory", 2)
        application.processEvents()
    items = [form]
    for item in items:
        items.extend(item.childItems())
    button = next((item for item in items if item.objectName() == "loadAsrModelsButton"), None)
    choice = next((item for item in items if item.objectName() == "choice_dashscope_model"), None)
    assert button is not None and choice is not None
    original = settings_values(bridge.settings)
    qml_call(form, "reset(" + json.dumps(original) + ")")
    assert choice.property("currentText") == bridge.settings.dashscope_model
    qml_call(form, "setValue('dashscope_api_key', 'offline-asr-key')")
    options = ["fun-asr", "paraformer-v2", ui.core.QWEN3_FILETRANS_MODEL]
    results, errors = [], []
    bridge.asrModelsReady.connect(results.append)
    bridge.error.connect(errors.append)
    before = bridge.settings_path.read_bytes()
    try:
        for failure in (None, RuntimeError("HTTP 401 offline-asr-key"), TimeoutError("连接超时")):
            results.clear()
            with patch.object(ui.core.DashScopeTranscriber, "list_models", return_value=options, side_effect=failure):
                qml_call(button, "clicked()")
                assert pages.property("asrModelsLoading") and not button.property("enabled")
                wait_for(application, lambda: bool(results))
                assert not pages.property("asrModelsLoading") and button.property("enabled")
                assert "offline-asr-key" not in pages.property("asrModelsMessage")
                if failure:
                    assert ("HTTP 401" if isinstance(failure, RuntimeError) else "连接超时") in pages.property("asrModelsMessage")
                else:
                    assert "已加载 3 个" in pages.property("asrModelsMessage")
                    assert qml_call(pages, "asrModelOptions.length")[0] == 3
                    assert choice.property("currentText") == "fun-asr"
                    qml_call(choice, "currentIndex=2; activated(2)")
        assert bridge.settings_path.read_bytes() == before
        assert qml_call(form, "values.dashscope_model")[0] == ui.core.QWEN3_FILETRANS_MODEL
        results.clear()
        with patch.object(ui.core.DashScopeTranscriber, "list_models", return_value=options):
            qml_call(button, "clicked()")
            wait_for(application, lambda: bool(results))
        assert choice.property("currentText") == ui.core.QWEN3_FILETRANS_MODEL, "刷新丢失 ASR 选择"
        values = form.property("values").toVariant()
        bridge.perform("settingsSave", values)
        wait_for(application, lambda: not bridge.busy)
        assert not errors
        loaded = ui.core.Settings.load(bridge.settings_path)
        assert loaded.dashscope_model == ui.core.QWEN3_FILETRANS_MODEL
        body = ui.core.build_dashscope_submit_body(loaded, "https://example.invalid/audio.mp3")
        assert body["model"] == loaded.dashscope_model and "file_url" in body["input"]
        assert "diarization_enabled" not in body["parameters"]

        for width, height in ((1020, 680), (1280, 820)):
            window.resize(width, height)
            QTest.qWait(150)
            for control in (button, choice):
                point = control.mapToItem(window.contentItem(), QPointF(0, 0))
                assert point.x() >= 0 and point.y() >= 0
                assert point.x() + control.width() <= window.width()
                assert point.y() + control.height() <= window.height()
            assert window.grabWindow().save(str(output / f"asr-models-{width}.png"))
        qml_call(choice, "forceActiveFocus(); popup.open()")
        wait_for(application, lambda: qml_call(choice, "popup.opened")[0])
        assert_combo_selection(choice)
        assert window.grabWindow().save(str(output / "asr-models-expanded.png"))
        QTest.keyClick(window, Qt.Key_Home)
        QTest.keyClick(window, Qt.Key_Return)
        wait_for(application, lambda: qml_call(form, "values.dashscope_model")[0] == "fun-asr")

        # Changing a Key, including away and back, invalidates in-flight responses.
        request = bridge._asr_models_request
        qml_call(form, "setValue('dashscope_api_key', 'different-asr-key')")
        qml_call(form, "setValue('dashscope_api_key', 'offline-asr-key')")
        results.clear()
        bridge._receive("asr_models", {"request": request, "credentials": "offline-asr-key", "models": ["stale-model"]})
        assert not results and qml_call(pages, "asrModelOptions.length")[0] == 1
        assert choice.property("currentText") == loaded.dashscope_model
        pages.setProperty("asrModelsLoading", True)
        bridge.asrModelsReady.emit({"credentials": "old-asr-key", "models": ["stale-model"]})
        assert pages.property("asrModelsLoading") and choice.property("currentText") == loaded.dashscope_model
        pages.setProperty("asrModelsLoading", False)
        for bad in (dict(values, dashscope_model="qwen-plus"), dict(values, dashscope_model=""),
                    dict(values, dashscope_model="paraformer-v2", dashscope_api_key="different-asr-key")):
            saved = bridge.settings_path.read_bytes()
            bridge.perform("settingsSave", bad)
            wait_for(application, lambda: not bridge.busy)
            assert errors and bridge.settings_path.read_bytes() == saved
            errors.clear()
            qml_call(window, "errorDialog.close()")
        # Restoring the built-in default needs no network; other settings are retained.
        bridge.perform("settingsSave", dict(values, dashscope_model="fun-asr"))
        wait_for(application, lambda: not bridge.busy)
        assert not errors and bridge.settings.dashscope_model == "fun-asr"
        qml_call(form, "reset(bridge.formSettings)")
    finally:
        pages.setProperty("asrModelsLoading", False)
        bridge.asrModelsReady.disconnect(results.append)
        bridge.error.disconnect(errors.append)


def check_model_connection(application, bridge, window):
    pages = window.findChild(QObject, "workspacePages")
    form = window.findChild(QObject, "settingsForm")
    button = next(child for child in pages.findChildren(QObject) if child.property("text") == "连接并获取模型")
    values = dict(settings_values(bridge.settings), llm_endpoint="https://example.invalid/v1", llm_api_key="offline-key", llm_model="offline-model")
    qml_call(form, "reset(" + json.dumps(values) + ")")
    results = []
    bridge.modelsReady.connect(results.append)
    try:
        for failure in (None, RuntimeError("HTTP 401 offline-key"), TimeoutError("连接超时")):
            results.clear()
            with patch.object(ui.core.LLMClient, "list_models", return_value=["offline-model"], side_effect=failure):
                qml_call(button, "clicked()")
                assert pages.property("modelsLoading") and not button.property("enabled")
                wait_for(application, lambda: bool(results))
                assert not pages.property("modelsLoading"), "模型请求已返回，但 QML 仍停在连接中"
                assert button.property("enabled") and button.property("text") == "连接并获取模型"
                message = pages.property("modelsMessage")
                assert "offline-key" not in message
                if failure:
                    assert ("HTTP 401" if isinstance(failure, RuntimeError) else "连接超时") in message
                else:
                    assert "已获取 1 个模型" in message
                    assert qml_call(pages, "modelOptions[0]")[0] == "offline-model"
                    assert qml_call(form, "values.llm_model")[0] == "offline-model"
        # 旧凭证的响应不能覆盖正在连接的新凭证，也不能提前恢复按钮。
        pages.setProperty("modelsLoading", True)
        bridge.modelsReady.emit({"credentials": ["https://old.invalid/v1", "old-key"], "models": ["stale-model"]})
        assert pages.property("modelsLoading") and qml_call(pages, "modelOptions[0]")[0] == "offline-model"
    finally:
        pages.setProperty("modelsLoading", False)
        bridge.modelsReady.disconnect(results.append)


def check_batch_cleanup(application, bridge, engine, window, folder, output):
    db = bridge.db
    original_record, original_clip = bridge._selected, bridge._selected_clip
    dialog = window.findChild(QObject, "cleanupDialog")
    result_dialog = window.findChild(QObject, "cleanupResultDialog")
    pages = window.findChild(QObject, "workspacePages")
    player = window.findChild(QMediaPlayer, "clipPlayer")
    sample = (Path(folder) / "Qt播放测试.mp4").read_bytes()
    records, clips = [], []
    for index in range(3):
        path = Path(folder) / f"bulk-record-{index}.mp4"
        path.write_bytes(sample)
        rid = db.create_recording("700001" if index < 2 else "700002", f"bulk-{index}",
                                  "批量清理测试录播", str(path), "2026-01-02T20:00:00+08:00", source_type="local")
        db.finish_recording(rid, "complete", str(path), ui.core.now_text(), 3)
        records.append((rid, path))
    for index in range(2):
        path = Path(folder) / f"bulk-clip-{index}.mp4"
        path.write_bytes(sample)
        cid = db.create_clip(records[2][0], "批量清理测试切片", 0, 3, str(path))
        if index == 0:
            db.set_clip_status(cid, "complete")
        clips.append((cid, path))
    timestamp = ui.core.now_text()
    with db._connect() as conn:
        conn.executemany(
            "INSERT INTO tasks(kind,unique_key,status,created_at,updated_at) VALUES('generic',?,'complete',?,?)",
            [(f"bulk-ui-{i}", timestamp, timestamp) for i in range(505)])
    bridge.refresh()
    wait_for(application, lambda: not bridge._refreshing)
    bridge.recordings.setFilter("room:700001", "2026-01-02")
    bridge.clips.setFilter("room:700002", "2026-01-02")
    assert len(bridge.cleanupSelection("tasks")["ids"]) >= 505, "cleanup must not stop at 500 rows"
    record_button = window.findChild(QObject, "clearRecordingsButton")
    clip_button = window.findChild(QObject, "clearClipsButton")
    task_button = window.findChild(QObject, "clearTasksButton")
    for skin in ("day", "night"):
        engine.ui_theme.select(skin)
        for width, height in ((1280, 820), (1020, 680)):
            window.resize(width, height)
            for page, button in ((0, record_button), (4, clip_button), (1, task_button)):
                window.setProperty("page", page)
                QTest.qWait(100)
                assert button.property("visible") and button.property("enabled")
                point = qml_call(button, "mapToItem(null, 0, 0)")[0]
                assert 0 <= point.x() <= width - button.property("width")
                assert 0 <= point.y() <= height - button.property("height")
                assert qml_call(button, "contentItem.implicitWidth <= width - leftPadding - rightPadding")[0]
                assert window.grabWindow().save(str(output / f"cleanup-{skin}-{page}-{width}.png"))
                qml_call(button, "clicked()")
                wait_for(application, lambda: dialog.property("opened"))
                assert qml_call(dialog, "standardButton(Dialog.No).activeFocus")[0]
                assert dialog.property("height") < height
                if width == 1020:
                    assert window.grabWindow().save(str(output / f"cleanup-confirm-{skin}-{page}.png"))
                qml_call(dialog, "reject()")
    assert all(path.exists() for _, path in records + clips), "cancel must not delete files"
    engine.ui_theme.select("day")
    window.setProperty("page", 0)
    bridge.selectRecording(records[0][0])
    wait_for(application, lambda: not bridge._refreshing)
    qml_call(record_button, "clicked()")
    wait_for(application, lambda: dialog.property("opened"))
    captured = window.property("cleanupSelection")
    assert set(captured["ids"]) == {rid for rid, _ in records[:2]}
    # New items and changed filters while the confirmation is open must not widen its scope.
    later_path = Path(folder) / "bulk-record-later.mp4"
    later_path.write_bytes(sample)
    later_id = db.create_recording("700001", "bulk-later", "确认后新增", str(later_path), "2026-01-02T21:00:00+08:00", source_type="local")
    db.finish_recording(later_id, "complete", str(later_path), timestamp, 3)
    bridge.refresh()
    wait_for(application, lambda: not bridge._refreshing)
    bridge.recordings.setFilter("room:700002", "")
    original_unlink = Path.unlink
    def fail_locked(path, *args, **kwargs):
        if path == records[1][1]:
            raise PermissionError("synthetic file lock")
        return original_unlink(path, *args, **kwargs)
    with patch.object(Path, "unlink", fail_locked):
        qml_call(dialog, "accept()")
        assert bridge.busy and not record_button.property("enabled")
        wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    report = window.property("cleanupReport")
    assert report["deleted"] == [records[0][0]] and report["failed"][0]["id"] == records[1][0]
    assert records[1][1].exists() and records[2][1].exists() and later_path.exists()
    assert not bridge.detail["id"] and result_dialog.property("visible")
    assert window.grabWindow().save(str(output / "cleanup-partial-result.png"))
    qml_call(result_dialog, "close()")
    bridge.recordings.setFilter("room:700001", "")
    qml_call(record_button, "clicked()")
    wait_for(application, lambda: dialog.property("opened"))
    qml_call(dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    assert db.get_recording(later_id)["deleted"] and db.get_recording(records[1][0])["deleted"]
    qml_call(result_dialog, "close()")

    window.setProperty("page", 4)
    bridge.selectClip(clips[0][0])
    wait_for(application, lambda: not bridge._refreshing and player.duration() > 0)
    player.audioOutput().setVolume(0)
    player.play()
    wait_for(application, lambda: player.position() > 100)
    qml_call(clip_button, "clicked()")
    wait_for(application, lambda: dialog.property("opened"))
    qml_call(dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    report = window.property("cleanupReport")
    assert report["deleted"] == [clips[0][0]] and report["failed"][0]["id"] == clips[1][0]
    assert player.source().isEmpty() and not bridge.workspace["clip"] and pages.property("deletingClipId") == 0
    assert clips[1][1].exists() and records[2][1].exists()
    qml_call(result_dialog, "close()")
    db.set_clip_status(clips[1][0], "complete")
    qml_call(clip_button, "clicked()")
    wait_for(application, lambda: dialog.property("opened"))
    qml_call(dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    assert db.get_clip(clips[1][0])["deleted"] and not clips[1][1].exists()
    qml_call(result_dialog, "close()")

    window.setProperty("page", 1)
    qml_call(task_button, "clicked()")
    wait_for(application, lambda: dialog.property("opened"))
    captured = window.property("cleanupSelection")["ids"]
    raced = captured[0]
    db.update_task(raced, "running")
    later_task, _ = db.create_task("generic", "task-after-confirmation")
    db.update_task(later_task, "complete")
    qml_call(dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    assert sum(db.get_task(tid)["archived"] for tid in captured) == len(captured) - 1
    assert not db.get_task(raced)["archived"] and not db.get_task(later_task)["archived"]
    assert {r["id"] for r in bridge.tasks.rows}.isdisjoint(set(captured) - {raced})
    assert bridge.workspace["stats"]["tasks"] == bridge.tasks.rowCount()
    qml_call(result_dialog, "close()")
    db.update_task(raced, "complete")
    bridge.refresh()
    wait_for(application, lambda: not bridge._refreshing)
    qml_call(task_button, "clicked()")
    wait_for(application, lambda: dialog.property("opened"))
    qml_call(dialog, "accept()")
    wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
    assert not task_button.property("enabled") and not bridge.cleanupSelection("tasks")["ids"]
    qml_call(result_dialog, "close()")
    for library, button, kind in ((bridge.recordings, record_button, "recordings"), (bridge.clips, clip_button, "clips")):
        saved = library._all_rows
        library.sync([])
        assert not button.property("enabled") and not bridge.cleanupSelection(kind)["ids"]
        library.sync(saved)
        library.setFilter("", "")
    bridge.service.delete_recording(records[2][0])
    bridge.selectRecording(original_record)
    bridge.selectClip(original_clip)
    wait_for(application, lambda: not bridge._refreshing)
    window.setProperty("page", 0)
    print("Qt batch cleanup checks passed: filtered snapshots, cancellation, partial failures, playback release and >500 tasks")


def wait_for(application, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Qt 操作超时"
        application.processEvents()
        time.sleep(0.005)


def check_apple_ui(application, bridge, window, output):
    from PySide6.QtSvg import QSvgRenderer

    icons = Path(ui.__file__).parent / "assets/ui/icons"
    assert len(list(icons.glob("*.svg"))) == 25
    assert all(QSvgRenderer(str(path)).isValid() for path in icons.glob("*.svg")), "图标缺失或不是有效 SVG"
    pages = window.findChild(QObject, "workspacePages")
    form = window.findChild(QObject, "settingsForm")
    page_stack = window.findChild(QObject, "pageStack")
    selection = window.findChild(QObject, "navigationSelection")
    log_panel = window.findChild(QObject, "logPanel")
    dialog = window.findChild(QObject, "deleteRecordingDialog")
    original_page = window.property("page")
    original_form = qml_call(form, "JSON.stringify(values)")[0]
    original_dirty = form.property("dirty")
    try:
        with patch.object(ui, "motion_enabled", return_value=True):
            bridge.refreshMotionPreference()
            assert bridge.motionEnabled
            window.setProperty("page", 2)
            QTest.qWait(250)
            initial_y = selection.property("y")
            nav = qml_call(window, "navigationItems.itemAt(7).children.find(item => item.objectName === 'navigation_7')")[0]
            qml_call(nav, "forceActiveFocus()")
            QTest.keyClick(window, Qt.Key_Space)
            assert window.property("page") == 7, "动效不能延迟导航"
            target_y = qml_call(selection, "entry.y")[0]
            QTest.qWait(40)
            assert initial_y < selection.property("y") < target_y, "侧栏选中态没有移动过渡"
            assert 0.8 <= page_stack.property("opacity") <= 1
            for page in (0, 4, 3, 7):
                window.setProperty("page", page)
                QTest.qWait(20)
            QTest.qWait(250)
            assert abs(selection.property("y") - target_y) < 0.1
            assert page_stack.property("opacity") == 1
            assert nav.property("activeFocus") and qml_call(nav, "Accessible.checked")[0]
            pages.setProperty("settingsCategory", 0)
            application.processEvents()
            save = window.findChild(QObject, "saveSettingsButton")
            QTest.mouseMove(window, QPointF(1, 1).toPoint())
            qml_call(window.findChild(QObject, "restoreDefaultsButton"), "forceActiveFocus()")
            QTest.keyClick(window, Qt.Key_Tab)
            assert save.property("activeFocus") and not save.property("hovered")
            ring = save.findChild(QObject, "primaryFocusRing")
            assert ring.property("visible") and qml_call(ring, "border.width")[0] == 2
            assert qml_call(ring, "border.color")[0] == QColor(qml_call(save, "uiTheme.current.colors.onPrimary")[0])
            assert qml_call(save, "background.color")[0] == QColor(qml_call(save, "uiTheme.current.colors.primary")[0])
            assert window.grabWindow().save(str(output / "apple-keyboard-focus.png"))
            tabs = window.findChild(QObject, "settingsTabs")
            qml_call(tabs, "currentItem.forceActiveFocus()")
            QTest.keyClick(window, Qt.Key_Right)
            assert pages.property("settingsCategory") == 1, "分段页签的键盘选择未切换内容"
            QTest.keyClick(window, Qt.Key_Left)
            assert pages.property("settingsCategory") == 0
            items = [form]
            for item in items:
                items.extend(item.childItems())
            switch = next(item for item in items if item.objectName() == "toggle_auto_slice")
            initial_checked = switch.property("checked")
            knob = qml_call(switch, "indicator.children[0]")[0]
            initial_x = knob.property("x")
            qml_call(switch, "forceActiveFocus()")
            QTest.keyClick(window, Qt.Key_Space)
            assert switch.property("checked") != initial_checked
            assert qml_call(form, "values.auto_slice")[0] == (not initial_checked)
            QTest.qWait(220)
            assert abs(knob.property("x") - initial_x) == 18
            qml_call(form, "reset(" + original_form + ")")
            form.setProperty("dirty", original_dirty)

            window.setProperty("logsOpen", True)
            QTest.qWait(45)
            assert 0 < log_panel.property("extent") < 120, "日志未平滑展开"
            window.setProperty("logsOpen", False)
            QTest.qWait(25)
            window.setProperty("logsOpen", True)
            wait_for(application, lambda: log_panel.property("extent") == 120)
            assert log_panel.property("extent") == 120
            qml_call(window, "logText.forceActiveFocus()")
            window.setProperty("logsOpen", False)
            wait_for(application, lambda: not log_panel.property("visible"))
            assert not log_panel.property("visible")
            assert window.findChild(QObject, "logsButton").property("activeFocus")

            qml_call(dialog, "open()")
            wait_for(application, lambda: dialog.property("opened"))
            assert dialog.property("opacity") == 1 and dialog.property("scale") == 1
            assert qml_call(dialog, "standardButton(Dialog.No).activeFocus")[0], "危险操作的取消按钮应获得焦点"
            assert qml_call(dialog, "background.radius")[0] == 20
            assert window.grabWindow().save(str(output / "apple-dialog.png"))
            QTest.keyClick(window, Qt.Key_Escape)
            wait_for(application, lambda: not dialog.property("visible"))
            # Turning system animations off also settles an in-flight transition.
            window.setProperty("page", 3)
            window.setProperty("logsOpen", True)
            QTest.qWait(30)
        bridge.refreshMotionPreference()
        assert not bridge.motionEnabled
        assert page_stack.property("opacity") == 1 and log_panel.property("extent") == 120, (page_stack.property("opacity"), log_panel.property("extent"))
        for page in range(9):
            window.setProperty("page", page)
            application.processEvents()
            expected = qml_call(selection, "entry.y + (entry.index === 5 ? 28 : 0)")[0]
            assert abs(selection.property("y") - expected) < 0.1
            assert page_stack.property("opacity") == 1
        window.setProperty("logsOpen", False)
        application.processEvents()
        assert log_panel.property("extent") == 0
        window.setProperty("page", 2)
        artwork = window.findChild(QObject, "workbenchArtwork")
        wait_for(application, lambda: qml_call(artwork, "children[0].status === Image.Ready")[0])
        QTest.qWait(100)
        picture = window.grabWindow()
        origin = artwork.mapToScene(QPointF(1, 1))
        ratio = picture.devicePixelRatio()
        assert picture.pixelColor(round(origin.x() * ratio), round(origin.y() * ratio)) == window.color(), "壁纸的圆角没有真正裁切"
        print("Apple-style UI checks passed: icons, navigation, switch, interruptible motion, reduced motion, modal focus and rounded image pixels")
    finally:
        bridge.refreshMotionPreference()
        qml_call(dialog, "close()")
        window.setProperty("logsOpen", False)
        qml_call(form, "reset(" + original_form + ")")
        form.setProperty("dirty", original_dirty)
        window.setProperty("page", original_page)


def check_skin_preferences(output):
    from quick_theme import SKINS, UiTheme

    with tempfile.TemporaryDirectory(dir=output) as folder:
        path = Path(folder) / "appearance.json"
        theme = UiTheme(path)
        assert theme.key == "day" and theme.nextKey == "night"
        assert theme.select("night") and UiTheme(path).key == "night"
        assert theme.current["name"] == "黑夜"
        assert theme.current["scene"] == "wallpaper-moon.png"
        assert theme.current["sceneFit"]
        assert theme.current["header"] == "wallpaper-night-header.png"
        assert theme.nextKey == "day" and len(SKINS) == 2, "应替换旧素材，不增加第三套皮肤"
        saved = path.read_bytes()
        errors = []
        theme.error.connect(errors.append)
        assert not theme.select("../unknown") and path.read_bytes() == saved
        with patch("quick_theme.write_json_atomic", side_effect=OSError("read-only")):
            assert not theme.select("day") and theme.key == "night"
        assert len(errors) == 2 and path.read_bytes() == saved
        for invalid in ('{"skin":[]}', '{"skin":"unknown"}', "[]", "broken"):
            path.write_text(invalid, encoding="utf-8")
            assert UiTheme(path).key == "day"
            assert path.read_text(encoding="utf-8") == invalid, "不能在读取时覆盖坏文件"
        for legacy, current in (("mint", "day"), ("rose", "night")):
            path.write_text(json.dumps({"skin": legacy}), encoding="utf-8")
            saved = path.read_bytes()
            theme = UiTheme(path)
            assert theme.key == current and path.read_bytes() == saved, "读取旧偏好不能擅自改写用户文件"
            assert theme.select(theme.nextKey) and UiTheme(path).key != current
    assert set(SKINS["day"]["colors"]) == set(SKINS["night"]["colors"])
    for skin in SKINS.values():
        for asset in ("scene", "header"):
            assert (Path(ui.__file__).parent / "assets/ui" / skin[asset]).is_file()
        colors = skin["colors"]
        for role in ("ink", "muted"):
            assert_color_contrast(QColor(colors[role]), QColor(skin["sceneBackground"]))
        for foreground, background in (("ink", "surface"), ("ink", "rail"), ("ink", "background"),
                                      ("muted", "rail"), ("muted", "surface"), ("muted", "background"), ("placeholder", "surface"),
                                      ("muted", "selection"), ("muted", "navSelected"), ("muted", "listHover"),
                                      ("ink", "control"), ("ink", "dialog"), ("ink", "navSelected"),
                                      ("ink", "choicePressed"),
                                      ("primary", "surface"), ("danger", "surface"), ("warning", "surface"),
                                      ("selectionInk", "selection"), ("selectionInk", "choicePressed"),
                                      ("onPrimary", "primary"), ("onPrimary", "primaryHover"), ("onPrimary", "primaryPressed")):
            assert_color_contrast(QColor(colors[foreground]), QColor(colors[background]))
        for role in ("background", "surface", "rail"):
            r, g, b, _ = QColor(colors[role]).getRgb()
            assert max(r, g, b) - min(r, g, b) <= 3, "大面积表面必须为中性黑白"
            assert max(r, g, b) < 48 if skin["dark"] else min(r, g, b) > 230
        assert_color_contrast(QColor(colors["switchThumb"]), QColor(colors["switchOff"]), 3)
        assert_color_contrast(QColor(colors["onPrimary"]), QColor(colors["primary"]), 3)


def check_workbench_portrait(application, engine, window, output):
    artwork = window.findChild(QObject, "workbenchArtwork")
    intro = window.findChild(QObject, "workbenchIntro")
    wait_for(application, lambda: artwork.property("status") == 1)
    assert qml_call(artwork, "fillMode === Image.PreserveAspectFit && horizontalAlignment === Image.AlignRight")[0]
    painted_width = qml_call(artwork, "children[0].paintedWidth")[0]
    painted_height = qml_call(artwork, "children[0].paintedHeight")[0]
    assert abs(painted_width - 480) < 0.1 and abs(painted_height - 180) < 0.1, "人物必须完整等比显示，不能裁切头部"
    assert intro.x() + intro.width() <= artwork.width() - painted_width / 2, "文字与人物区域重叠"
    picture = window.grabWindow()
    assert not picture.isNull()
    ratio = picture.devicePixelRatio()
    origin = artwork.mapToScene(QPointF(artwork.width() - painted_width, 0))
    background = qml_call(artwork, "parent.children[0].color")[0]
    # Sample empty areas on both sides of the image edge, allowing only image grain.
    for x in (-12, 12, 120):
        for y in (16, painted_height - 16):
            actual = picture.pixelColor(round((origin.x() + x) * ratio), round((origin.y() + y) * ratio))
            assert max(abs(a - b) for a, b in zip(actual.getRgb(), background.getRgb())) <= 2, (
                "横幅左右背景存在色差", engine.ui_theme.key, x, y, actual.name(), background.name())
    source = QImage(artwork.property("source").toLocalFile()).scaled(
        round(painted_width * ratio), round(painted_height * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # Check the rendered crown, face and costume, not just the QML source URL.
    for x, y in ((340, 20), (350, 72), (400, 120)):
        expected = source.pixelColor(round(x * ratio), round(y * ratio))
        actual = picture.pixelColor(round((origin.x() + x) * ratio), round((origin.y() + y) * ratio))
        assert max(abs(a - b) for a, b in zip(actual.getRgb(), expected.getRgb())) <= 14, "人物像素被裁切、覆盖或丢失"
    assert picture.save(str(output / f"workbench-{engine.ui_theme.key}-{window.width()}.png"))


def check_theme_controls(application, bridge, engine, window, output):
    theme = engine.ui_theme
    colors = theme.current["colors"]
    pages = window.findChild(QObject, "workspacePages")
    form = window.findChild(QObject, "settingsForm")
    original = qml_call(form, "JSON.stringify(values)")[0]
    dirty, page, category = form.property("dirty"), window.property("page"), pages.property("settingsCategory")
    assert not bridge.busy
    try:
        for palette_role, role in (("window", "background"), ("base", "surface"),
                                   ("light", "controlHover"), ("mid", "fieldBorder"),
                                   ("text", "ink"), ("toolTipBase", "dialog"),
                                   ("toolTipText", "ink"), ("disabled.text", "disabledInk")):
            actual = qml_call(window, "palette." + palette_role)[0]
            assert actual == QColor(colors[role]), (theme.key, palette_role, actual.name(), colors[role])
        window.setProperty("page", 0)
        QTest.qWait(220)
        summary = window.findChild(QObject, "summaryText")
        assert summary.property("selectionColor") == QColor(colors["primary"])
        assert summary.property("selectedTextColor") == QColor(colors["onPrimary"])
        combo = window.findChild(QObject, "recordingStreamerFilter")
        assert qml_call(combo, "indicator.color")[0] == QColor(colors["ink"])
        assert not qml_call(combo, "indicator.layer.enabled")[0], "箭头着色不应分配额外渲染图层"
        origin = qml_call(combo, "indicator.mapToItem(null, 0, 0)")[0]
        picture = window.grabWindow()
        scale = picture.devicePixelRatio()
        samples = [picture.pixelColor(round((origin.x() + x) * scale), round((origin.y() + y) * scale)).lightness()
                   for x in range(16) for y in range(16)]
        assert max(samples) > 150 if theme.current["dark"] else min(samples) < 160, "下拉箭头不可见"
        qml_call(combo, "forceActiveFocus(); popup.open()")
        wait_for(application, lambda: qml_call(combo, "popup.opened")[0])
        assert_combo_selection(combo)
        assert qml_call(combo, "contentItem.color")[0] == QColor(colors["ink"])
        assert_color_contrast(qml_call(combo, "contentItem.color")[0], qml_call(combo, "background.color")[0])
        assert window.grabWindow().save(str(output / f"skin-{theme.key}-menu.png"))
        qml_call(combo, "popup.close()")
        wait_for(application, lambda: not qml_call(combo, "popup.visible")[0])
        calendar = window.findChild(QObject, "recordingCalendar")
        qml_call(window.findChild(QObject, "recordingDateFilter"), "clicked()")
        wait_for(application, lambda: calendar.property("opened"))
        year = window.findChild(QObject, "recordingCalendarYear")
        assert qml_call(year, "background.color")[0] == QColor(colors["surface"])
        assert qml_call(year, "contentItem.color")[0] == QColor(colors["ink"])
        assert qml_call(year, "up.indicator.color")[0] == QColor(colors["button"])
        assert window.grabWindow().save(str(output / f"skin-{theme.key}-calendar.png"))
        qml_call(calendar, "close()")

        window.setProperty("page", 7)
        pages.setProperty("settingsCategory", 0)
        QTest.qWait(220)
        items = [form]
        for item in items:
            items.extend(item.childItems())
        toggle = next(item for item in items if item.objectName() == "toggle_auto_slice")
        for checked in (True, False):
            qml_call(form, "setValue('auto_slice', " + str(checked).lower() + ")")
            QTest.qWait(220)
            assert toggle.property("checked") == checked
            thumb = qml_call(toggle, "indicator.children[0].color")[0]
            track = qml_call(toggle, "indicator.color")[0]
            assert_color_contrast(thumb, track, 3)
        save = window.findChild(QObject, "saveSettingsButton")
        QTest.mouseMove(window, QPointF(1, 1).toPoint())
        qml_call(save, "forceActiveFocus()")
        ring = save.findChild(QObject, "primaryFocusRing")
        assert ring.property("visible")
        assert_color_contrast(qml_call(ring, "border.color")[0], qml_call(save, "background.color")[0], 3)
        assert window.grabWindow().save(str(output / f"skin-{theme.key}-focus.png"))
        bridge._busy = True
        bridge.changed.emit()
        QTest.qWait(180)
        assert not toggle.property("enabled") and not save.property("enabled")
        assert qml_call(save, "background.color")[0] == QColor(colors["disabled"])
        assert qml_call(save, "foreground")[0] == QColor(colors["disabledInk"])
        assert window.grabWindow().save(str(output / f"skin-{theme.key}-disabled.png"))
    finally:
        bridge._busy = False
        bridge.changed.emit()
        qml_call(window.findChild(QObject, "recordingCalendar"), "close()")
        qml_call(window.findChild(QObject, "recordingStreamerFilter"), "popup.close()")
        qml_call(form, "reset(" + original + ")")
        form.setProperty("dirty", dirty)
        pages.setProperty("settingsCategory", category)
        window.setProperty("page", page)
        QTest.qWait(220)


def check_skin_switch(application, bridge, engine, window, output):
    theme = engine.ui_theme
    button = window.findChild(QObject, "skinButton")
    transition = window.findChild(QObject, "skinTransition")
    wave = window.findChild(QObject, "skinWave")
    artwork = window.findChild(QObject, "workbenchArtwork")
    header = window.findChild(QObject, "headerArtwork")
    form = window.findChild(QObject, "settingsForm")
    original = qml_call(form, "JSON.stringify(values)")[0]
    dirty = form.property("dirty")
    page = window.property("page")
    saved_config = bridge.settings_path.read_bytes()
    assert button is not None and button.property("width") == button.property("height") == 48
    assert qml_call(button, "background.radius === width / 2")[0]
    assert "切换皮肤" in qml_call(button, "Accessible.name")[0]
    qml_call(form, "setValue('llm_model', 'offline-unsaved-skin-draft')")
    try:
        window.setProperty("page", 2)
        window.resize(1280, 820)
        wait_for(application, lambda: transition.property("artReady"))
        with patch.object(ui, "motion_enabled", return_value=True):
            bridge.refreshMotionPreference()
            QTest.qWait(220)
            before = window.color()
            before_art = artwork.property("source")
            before_header = header.property("source")
            center = button.mapToScene(QPointF(24, 24))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center.toPoint())
            assert transition.property("busy")
            for _ in range(5):
                qml_call(button, "clicked()")
            wait_for(application, lambda: transition.property("phase") == "revealing")
            assert theme.key == "night" and wave.property("running"), "点击应切换一次并产生圆形展开"
            assert not qml_call(button, "ToolTip.visible")[0], "扩散中不应残留旧皮肤的悬停提示"
            QTest.qWait(45)
            assert 0 < transition.property("revealRadius") < transition.property("fullRadius"), "半径未实际随时间增长"
            qml_call(wave, "pause()")
            origin = transition.property("origin")
            assert abs(origin.x() - center.x()) < 1 and abs(origin.y() - center.y()) < 1
            radius = transition.property("fullRadius")
            frames = []
            for progress in (0.18, 0.42, 0.72):
                transition.setProperty("revealRadius", radius * progress)
                QTest.qWait(30)
                picture = window.grabWindow()
                assert picture.save(str(output / f"skin-wave-{int(progress * 100)}.png"))
                frames.append(picture)
            ratio = frames[1].devicePixelRatio()
            assert frames[1].pixelColor(round(8 * ratio), round((window.height() - 8) * ratio)) == window.color(), "圆内未显示新皮肤"
            assert frames[1].pixelColor(round((window.width() - 8) * ratio), round(8 * ratio)) == before, "圆外必须仍是旧皮肤，不能整页淡入"
            qml_call(wave, "resume()")
            wait_for(application, lambda: not transition.property("busy"))
            assert not theme.transitioning
            assert transition.childItems()[0].property("sourceItem") is None, "动画结束仍保留页面快照源"
            assert artwork.property("source") != before_art and header.property("source") != before_header
            assert artwork.property("source").fileName() == theme.current["scene"] == "wallpaper-moon.png"
            assert qml_call(artwork, "fillMode === Image.PreserveAspectFit && horizontalAlignment === Image.AlignRight")[0], "人物壁纸应靠右完整等比显示，不能裁掉头部"
            assert header.property("source").fileName() == theme.current["header"] == "wallpaper-night-header.png"
            assert "黑夜" in qml_call(button, "Accessible.description")[0]
            assert "极昼" in qml_call(button, "ToolTip.text")[0], "完成切换后提示应指向下一套皮肤"
            QTest.mouseMove(window, QPointF(1, 1).toPoint())
            assert QColor(theme.current["colors"]["background"]) == window.color()
            assert qml_call(form, "values.llm_model")[0] == "offline-unsaved-skin-draft" and form.property("dirty")
            assert bridge.settings_path.read_bytes() == saved_config, "换肤不能覆盖业务配置或保存草稿"
            assert ui.UiTheme(theme.path).key == "night", "重启未保留皮肤"

            for width, height in ((1280, 820), (1020, 680)):
                window.resize(width, height)
                QTest.qWait(100)
                point = button.mapToScene(QPointF(0, 0))
                assert point.y() >= 0 and point.y() + 48 <= height - 16
                assert button.property("width") == button.property("height") == 48
                for target in range(9):
                    window.setProperty("page", target)
                    QTest.qWait(190)
                    if target == 2:
                        check_workbench_portrait(application, engine, window, output)
                    assert window.grabWindow().save(str(output / f"skin-night-page-{target}-{width}.png"))
            dialog = window.findChild(QObject, "deleteRecordingDialog")
            qml_call(dialog, "open()")
            wait_for(application, lambda: dialog.property("opened"))
            assert qml_call(dialog, "background.color")[0] == QColor(theme.current["colors"]["dialog"])
            assert window.grabWindow().save(str(output / "skin-night-dialog.png"))
            qml_call(dialog, "close()")
            wait_for(application, lambda: not dialog.property("visible"))
            check_theme_controls(application, bridge, engine, window, output)
            assert_native_resize_edges(window, output / "night-edges")

            qml_call(button, "forceActiveFocus()")
            QTest.keyClick(window, Qt.Key_Space)
            wait_for(application, lambda: transition.property("phase") == "revealing")
            assert theme.key == "day" and button.property("activeFocus")
            qml_call(wave, "pause()")
            window.resize(window.width() + 10, window.height() + 10)
            wait_for(application, lambda: not transition.property("busy"), timeout=0.3)
            assert not transition.property("busy") and not theme.transitioning, "缩放时必须释放旧帧"
            qml_call(button, "clicked()")
            assert transition.property("busy")
        bridge.refreshMotionPreference()
        assert not transition.property("busy") and not theme.transitioning and theme.key == "night"
        qml_call(button, "clicked()")
        assert theme.key == "day" and not transition.property("busy"), "关闭系统动画时应立即切换"
        with patch("quick_theme.write_json_atomic", side_effect=OSError("read-only")):
            qml_call(button, "clicked()")
            assert theme.key == "day" and not transition.property("busy")
        qml_call(window, "errorDialog.close()")
        print("Skin checks passed: circular reveal pixels, all pages, artwork, persistence, drafts, keyboard, reduced motion, resize and save failure")
    finally:
        qml_call(transition, "finish()")
        bridge.refreshMotionPreference()
        theme.select("day")
        qml_call(form, "reset(" + original + ")")
        form.setProperty("dirty", dirty)
        window.setProperty("page", page)


def check_updates_ui(application, bridge, engine, window, output):
    from update_test import release
    import threading

    initial = dict(bridge.updateInfo)
    page = window.property("page")
    form = window.findChild(QObject, "settingsForm")
    original_form = qml_call(form, "JSON.stringify(values)")[0]
    dirty = form.property("dirty")
    version_page = window.findChild(QObject, "versionPage")
    button = window.findChild(QObject, "checkUpdatesButton")
    download = window.findChild(QObject, "downloadUpdateButton")
    dialog = window.findChild(QObject, "installUpdateDialog")
    try:
        nav = qml_call(window, "navigationItems.itemAt(8).children.find(item => item.objectName === 'navigation_8')")[0]
        assert nav is not None and nav.property("text") == "版本"
        qml_call(nav, "clicked()")
        assert window.property("page") == 8 and version_page.property("visible")
        assert bridge.updateInfo["state"] == "idle" and len(bridge.updateInfo["history"]) >= 4
        qml_call(form, 'setValue("llm_endpoint", "https://offline.invalid/unsaved")')
        new_release = ui.app_updates.parse_releases([release()])
        gate = threading.Event()
        def fetch():
            assert gate.wait(5)
            return new_release
        with patch.object(ui.app_updates, "fetch_releases", side_effect=fetch) as fetcher:
            qml_call(button, "clicked()")
            assert bridge.updateInfo["state"] == "checking" and not button.property("enabled")
            bridge.checkUpdates()
            gate.set()
            wait_for(application, lambda: bridge.updateInfo["state"] == "available")
            assert fetcher.call_count == 1
        assert bridge.updateInfo["hasUpdate"] and bridge.updateInfo["latestVersion"] == "2099.01.01"
        assert bridge.updateInfo["canDownload"] is not initial["sourceBuild"]
        assert qml_call(form, "values.llm_endpoint")[0] == "https://offline.invalid/unsaved"
        assert form.property("dirty")
        window.setProperty("page", 2)
        notice = window.findChild(QObject, "updateNoticeButton")
        assert notice.property("visible")
        qml_call(notice, "clicked()")
        assert window.property("page") == 8
        bridge.setAutomaticUpdates(False)
        wait_for(application, lambda: not bridge.updateInfo["autoCheck"])
        assert json.loads(bridge._update_preferences.read_text(encoding="utf-8")) == {"autoCheck": False}
        with patch.object(ui.app_updates, "fetch_releases") as fetcher:
            bridge._automatic_update_check()
            QTest.qWait(30)
            fetcher.assert_not_called()
        bridge.setAutomaticUpdates(True)
        wait_for(application, lambda: bridge.updateInfo["autoCheck"])
        with patch.object(ui.core, "write_json_atomic", side_effect=OSError("offline disk failure")):
            bridge.setAutomaticUpdates(False)
            wait_for(application, lambda: "保存失败" in bridge.updateInfo["error"])
            assert bridge.updateInfo["autoCheck"] and bridge.updateInfo["state"] == "available"
        with patch.object(ui.app_updates, "fetch_releases", side_effect=ValueError("离线测试：无法连接 GitHub")):
            qml_call(button, "clicked()")
            wait_for(application, lambda: bridge.updateInfo["state"] == "error")
            assert len(bridge.updateInfo["history"]) >= 4 and "无法连接" in bridge.updateInfo["error"]
            assert bridge.updateInfo["message"].startswith("版本检查未完成")
            QTest.qWait(50)
            assert window.grabWindow().save(str(output / "version-error.png"))
        bridge._update["sourceBuild"] = False
        with patch.object(ui.app_updates, "fetch_releases", return_value=new_release):
            bridge.checkUpdates()
            wait_for(application, lambda: bridge.updateInfo["state"] == "available")
        assert download.property("visible") and download.property("enabled")
        for skin in ("day", "night"):
            engine.ui_theme.select(skin)
            for width, height in ((1280, 820), (1020, 680)):
                window.resize(width, height)
                QTest.qWait(200)
                for control in (nav, button, download, window.findChild(QObject, "automaticUpdatesSwitch")):
                    position = qml_call(control, "mapToItem(null, 0, 0)")[0]
                    assert control.property("visible") and position.x() >= 0 and position.y() >= 0
                    assert position.x() + control.property("width") <= width
                    assert position.y() + control.property("height") <= height
                    assert not qml_call(control, "contentItem.truncated || false")[0]
                assert window.grabWindow().save(str(output / f"version-{skin}-{width}.png"))
        history = window.findChild(QObject, "releaseHistory")
        assert qml_call(history, "count")[0] == len(ui.app_updates.LOCAL_HISTORY) + 1
        qml_call(history, "itemAtIndex(0).expanded = true")
        QTest.qWait(50)
        assert qml_call(history, "itemAtIndex(0).height")[0] > 60
        qml_call(history, "itemAtIndex(0).expanded = false")
        release_gate = threading.Event()
        def downloading(release_data, root, cancel, progress):
            progress(128, 512)
            assert release_gate.wait(5)
            if cancel.is_set():
                raise ui.app_updates.DownloadCancelled()
            return {"version": release_data["version"], "path": "offline-stage", "sha256": "a" * 64}
        with patch.object(ui.app_updates, "download_release", side_effect=downloading):
            qml_call(download, "clicked()")
            wait_for(application, lambda: bridge.updateInfo["progress"] == 0.25)
            assert not button.property("enabled") and not download.property("enabled")
            qml_call(window.findChild(QObject, "cancelUpdateButton"), "clicked()")
            release_gate.set()
            wait_for(application, lambda: bridge.updateInfo["state"] == "available")
            assert "取消" in bridge.updateInfo["message"]
            qml_call(download, "clicked()")
            wait_for(application, lambda: bridge.updateInfo["state"] == "ready")
        assert download.property("text") == "安装并重启"
        qml_call(download, "clicked()")
        wait_for(application, lambda: dialog.property("opened"))
        assert qml_call(dialog, "standardButton(Dialog.No).activeFocus")[0]
        assert "未保存的表单" in window.findChild(QObject, "installUpdateMessage").property("text"), "安装确认遗漏未保存表单"
        QTest.qWait(50)
        assert window.grabWindow().save(str(output / "version-confirmation.png"))
        with patch.object(ui.app_updates, "launch_installer") as launch:
            qml_call(dialog, "reject()")
            launch.assert_not_called()
        for kind in ("recording", "scheduled", "glossary", "queued"):
            with patch.object(bridge.service, "active_room_ids", return_value={"123"} if kind == "recording" else set()), \
                 patch.object(bridge.service, "_scheduled_tasks", {1} if kind == "scheduled" else set()), \
                 patch.object(bridge.service, "_glossary_jobs", {("test", "job", 1)} if kind == "glossary" else set()), \
                 patch.object(bridge.db, "list_tasks", return_value=[{"id": 1}] if kind == "queued" else []), \
                 patch.object(ui.app_updates, "launch_installer") as launch:
                bridge.installUpdate("2099.01.01")
                wait_for(application, lambda: not bridge.busy)
                assert bridge.updateInfo["state"] == "ready" and "任务" in bridge.updateInfo["error"]
                launch.assert_not_called()
        with patch.object(bridge.service, "active_room_ids", return_value=set()), \
             patch.object(bridge.db, "list_tasks", return_value=[]), \
             patch.object(ui.app_updates, "launch_installer") as launch, patch.object(bridge, "close") as close:
            bridge.installUpdate("2098.01.01")
            launch.assert_not_called()
            bridge.installUpdate("2099.01.01")
            wait_for(application, lambda: not bridge.busy)
            launch.assert_called_once()
            close.assert_called_once()
            assert bridge.service._update_pending
            with patch.object(bridge.service.executor, "submit") as submit:
                bridge.service.start_recording("update-guard")
                bridge.service._schedule_task(999999, lambda: None)
                submit.assert_not_called()
        bridge.service._update_pending = False
        bridge._receive_update("updateInstall", ui.app_updates.UpdateFileError("离线测试：文件被改动，请重新下载"))
        assert bridge._staged_update is None and bridge.updateInfo["state"] == "error"
        assert download.property("text") == "下载更新" and download.property("enabled")
        for version, state in ((ui.app_updates.VERSION, "current"), ("2026.09.18", "ahead")):
            bridge._update["state"] = "idle"
            with patch.object(ui.app_updates, "fetch_releases", return_value=ui.app_updates.parse_releases([release(version)])):
                bridge.checkUpdates()
                wait_for(application, lambda: bridge.updateInfo["state"] == state)
                assert not bridge.updateInfo["hasUpdate"] and not download.property("visible")
        for version, state in ((ui.app_updates.VERSION, "current"), ("2099.01.01", "available"), ("2026.09.18", "ahead")):
            public_release = ui.app_updates.parse_releases([release(version)])
            public_release[0]["source"] = "release-page"
            with patch.object(ui.app_updates, "fetch_releases", return_value=public_release):
                qml_call(button, "clicked()")
                wait_for(application, lambda: bridge.updateInfo["state"] == state)
            assert not bridge.updateInfo["error"] and "发布页面确认" in bridge.updateInfo["message"]
            assert "历史版本列表暂未同步" in bridge.updateInfo["message"]
            assert bridge.updateInfo["canDownload"] is (state == "available")
            public_release[0]["package"] = {}
            with patch.object(ui.app_updates, "fetch_releases", return_value=public_release):
                bridge.checkUpdates()
                wait_for(application, lambda: bridge.updateInfo["state"] == state)
            assert not download.property("visible")
        print("Version UI checks passed: nine-page navigation, drafts, automatic checks, errors, cancellation, task guards, confirmation and both skins/sizes")
    finally:
        bridge._update = initial
        bridge.service._update_pending = False
        bridge._staged_update = bridge._update_installer = bridge._latest_release = None
        bridge.updateChanged.emit()
        engine.ui_theme.select("day")
        qml_call(form, "reset(" + original_form + ")")
        form.setProperty("dirty", dirty)
        window.setProperty("page", page)


@patch.object(ui, "motion_enabled", return_value=False)
def run(motion_preference, updates_only=False):
    check_media_identity()
    output = Path(ui.os.environ.get("LIVECLIP_UI_TEST_OUTPUT", str(Path(tempfile.gettempdir()) / "StreamClip" / "ui-check")))
    output.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
    application = QGuiApplication([])
    warnings = []
    qInstallMessageHandler(lambda mode, context, message: warnings.append(message))
    if not updates_only:
        check_skin_preferences(output)
        check_finished_recordings(application, output)
        check_lightweight_clip_rows(application, output)
    with tempfile.TemporaryDirectory(dir=output) as folder:
        settings = ui.core.Settings(base_dir=folder)
        settings.ensure_dirs()
        db = ui.core.Database(settings.data_path / "app.db")
        service = ui.core.RecorderService(settings, db, ui.queue.Queue())
        bridge = ui.Bridge(settings, db, service)
        errors = []
        bridge.error.connect(errors.append)
        engine = None
        try:
            if updates_only:
                engine = ui.create_engine(bridge)
                window = engine.rootObjects()[0]
                wait_for(application, window.isExposed)
                check_updates_ui(application, bridge, engine, window, output)
                assert not [w for w in warnings if any(s in w for s in ("ReferenceError", "TypeError", "Unable to assign", "Binding loop", "recursive rearrange"))], warnings
                print("Version-only Qt Quick offline checks passed")
                return
            model = ui.Rows()
            resets, changes = [], []
            model.modelReset.connect(lambda: resets.append(True))
            model.dataChanged.connect(lambda *args: changes.append(True))
            model.sync([{"id": 3, "title": "三"}, {"id": 1, "title": "一"}])
            model.sync([{"id": 3, "title": "三"}, {"id": 1, "title": "一"}])
            assert not changes and not resets
            model.sync([{"id": 4, "title": "四"}, {"id": 3, "title": "更新"}, {"id": 2, "title": "二"}])
            assert [r["id"] for r in model.rows] == [4, 3, 2] and len(changes) == 1 and not resets
            model.sync([])
            assert model.rowCount() == 0
            for bad in ([{"id": 1}, {"id": 1}], [{"id": 1}, {"id": 2}]):
                try:
                    model.sync(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError("无效顺序或重复 ID 被接受")

            media = Path(folder) / "recording.mp4"
            media.write_bytes(b"offline command test; never decoded")
            for i in range(300):
                rid = db.create_recording("123", f"live-{i}", f"测试录播 {i} · 歌回之后的日常闲聊", str(media), ui.core.now_text())
                db.finish_recording(rid, "complete", str(media), ui.core.now_text(), 3600)
            transcript = Path(folder) / "transcript.json"
            transcript.write_text(json.dumps({"asr_metadata": {"provider": "DashScope", "model": "fun-asr", "speakers": [1]}}), encoding="utf-8")
            db.update_recording_analysis(rid, str(transcript), "这是一场用于离线检查的录播总结。\n" * 80, [{"start": 12, "end": 75, "title": "猫咪打翻杯子的故事", "reason": "讲述经过与回应", "score": 88, "confidence": 0.9}])
            with patch.object(ui, "recording_detail", side_effect=OSError("synthetic detail read failure")):
                try:
                    bridge._snapshot(rid)
                except OSError:
                    pass
                else:
                    raise AssertionError("读取失败没有传播")
            assert bridge._detail_key is None, "失败读取不能更新缓存标记，否则会回显旧录播"
            engine = ui.create_engine(bridge)
            window = engine.rootObjects()[0]
            assert window.property("page") == 2, "默认入口不是工作台"
            assert ui.core.APP_NAME == "StreamClip"
            assert application.applicationName() == application.applicationDisplayName() == ui.core.APP_NAME
            assert window.title() == ui.core.APP_NAME, "窗口名称不正确"
            studio_name = window.findChild(QObject, "studioName")
            assert studio_name is not None and studio_name.property("text") == window.title(), "侧栏名称应与窗口标题一致"
            studio_icon = window.findChild(QObject, "studioIcon")
            assert studio_icon is not None, "侧栏缺少应用图标"
            assert Path(studio_icon.property("source").toLocalFile()).resolve() == Path(ui.__file__).resolve().parent / "assets/ui/app-icon.png", "侧栏应使用通用应用图标，不能回退到角色头像"
            wait_for(application, lambda: qml_call(studio_icon, "status === Image.Ready")[0])
            # 窗口化 EXE 自检以 Hidden 启动；显式显示本次隔离测试窗口。
            window.hide()
            window.show()
            wait_for(application, window.isExposed)
            bridge.selectRecording(rid)
            wait_for(application, lambda: bridge.detail["canAnalyze"])
            assert bridge.recordings.rowCount() == 300 and "fun-asr" in bridge.detail["summary"]
            assert bridge.detail["highlights"][0]["interval"] == "00:00:12 — 00:01:15"
            notifications = []
            bridge.detailChanged.connect(lambda: notifications.append(True))
            bridge.refresh()
            wait_for(application, lambda: not bridge._refreshing)
            assert not notifications, "未变化的总结触发重新布局"

            # 慢读取与快速选择交错，旧结果不能覆盖新选择。
            original = db.list_recordings
            def slow_records(*args, **kwargs):
                time.sleep(0.15)
                return original(*args, **kwargs)
            with patch.object(db, "list_recordings", side_effect=slow_records):
                bridge.selectRecording(rid - 1)
                bridge.selectRecording(rid)
                wait_for(application, lambda: bridge.detail["id"] == rid and bridge.detail["canAnalyze"])

            with patch.object(service, "analyze_recording", return_value=91) as analyze:
                bridge.command("analyze", str(rid))
                wait_for(application, lambda: not bridge.busy)
                analyze.assert_called_once_with(rid, force=True, reuse_transcript=True, run_pipeline=True)
            with patch.object(service, "download_replay", return_value=92) as download:
                bridge.perform("replayDownload", {"bvid": "BV1xx411c7mD", "title": "直播回放"})
                wait_for(application, lambda: not bridge.busy)
                download.assert_called_once_with("BV1xx411c7mD", "直播回放", source_liver_uid="", source_liver_name="")
            bridge.perform("replayDownload", {"url": "https://example.com/not-bilibili"})
            wait_for(application, lambda: not bridge.busy)
            assert errors
            errors.clear()
            media.unlink()
            with patch.object(service, "analyze_recording") as analyze:
                bridge.command("analyze", str(rid))
                wait_for(application, lambda: not bridge.busy)
                assert errors and "文件不存在" in errors[-1]
                analyze.assert_not_called()
            # 关闭错误弹窗，继续真实窗口检查。
            QTest.keyClick(window, Qt.Key_Escape)
            task_id, _ = db.create_task("analysis", "offline-failed", {"recording_id": rid})
            db.update_task(task_id, status="error", error="离线测试失败提示")
            with patch.object(service, "retry_task") as retry:
                bridge.command("retry", str(task_id))
                wait_for(application, lambda: not bridge.busy)
                retry.assert_called_once_with(task_id)
            db.update_task(task_id, status="running")
            bridge.command("cancel", str(task_id))
            wait_for(application, lambda: not bridge.busy)
            assert db.task_cancel_requested(task_id)
            wait_for(application, lambda: not bridge._refreshing)

            check_workbench(application, bridge, window, rid, folder, output)
            qml_call(window, "errorDialog.close()")
            check_media_filters(application, bridge, window, folder, output)
            check_calendar(application, bridge, window, output)
            check_batch_cleanup(application, bridge, engine, window, folder, output)
            summary = window.findChild(QObject, "summaryText")
            qml_call(summary, "selectAll()")
            assert qml_call(summary, "selectedText.length")[0] > 0
            assert summary.property("selectedTextColor") == QColor(engine.ui_theme.current["colors"]["onPrimary"])
            assert summary.property("selectionColor") == QColor(engine.ui_theme.current["colors"]["primary"]), "下拉配色不应改动文本选择和键盘焦点"
            qml_call(summary, "deselect()")

            # Real confirmation flow: hidden live record, cancel, captured target, file removal and empty detail.
            delete_media = Path(folder) / "delete-ui.mp4"
            delete_media.write_bytes(b"isolated deletion test")
            delete_id = db.create_recording("123", "delete-ui", "删除按钮测试", str(delete_media), ui.core.now_text())
            window.setProperty("page", 0)
            bridge.selectRecording(delete_id)
            wait_for(application, lambda: not bridge._refreshing)
            assert bridge.detail["id"] == 0 and all(r["id"] != delete_id for r in bridge.recordings.rows)
            delete_button = window.findChild(QObject, "deleteRecordingButton")
            delete_dialog = window.findChild(QObject, "deleteRecordingDialog")
            assert delete_button is not None and not delete_button.property("enabled")
            db.finish_recording(delete_id, "complete", str(delete_media), ui.core.now_text(), 5)
            bridge.refresh()
            wait_for(application, lambda: bridge.detail["canDelete"])
            qml_call(delete_button, "clicked()")
            wait_for(application, lambda: delete_dialog.property("opened"))
            qml_call(delete_dialog, "reject()")
            assert delete_media.exists() and not db.get_recording(delete_id)["deleted"]
            qml_call(delete_button, "clicked()")
            wait_for(application, lambda: delete_dialog.property("opened"))
            bridge.selectRecording(rid)
            wait_for(application, lambda: bridge.detail["id"] == rid)
            qml_call(delete_dialog, "accept()")
            wait_for(application, lambda: not bridge.busy and not bridge._refreshing)
            assert not delete_media.exists() and db.get_recording(delete_id)["deleted"]
            assert bridge.recordings.rowCount() == 300 and not db.get_recording(rid)["deleted"]
            bridge.selectRecording(delete_id)
            wait_for(application, lambda: bridge.detail["id"] == 0)
            assert not delete_button.property("enabled") and not bridge.detail["highlights"]
            bridge.selectRecording(rid)
            wait_for(application, lambda: not bridge._refreshing and bridge.detail["id"] == rid and bridge.detail["canAnalyze"])

            for width, height in ((1280, 820), (1020, 680)):
                window.resize(width, height)
                QTest.qWait(200)
                assert studio_name.property("visible") and not studio_name.property("truncated"), "侧栏名称显示不完整"
                assert qml_call(studio_name, "x >= 0 && x + width <= parent.width")[0], "侧栏名称超出可用宽度"
                assert qml_call(studio_icon, "visible && width === 56 && height === 56 && x >= 0 && x + width <= parent.width")[0], "侧栏应用图标不可见或尺寸越界"
                for page in range(9):
                    window.setProperty("page", page)
                    QTest.qWait(150)
                    if page == 0:
                        assert window.findChild(QObject, "replayUrl") is None
                        reanalyze = window.findChild(QObject, "reanalyzeButton")
                        assert reanalyze is not None and reanalyze.property("visible")
                        assert reanalyze.property("text") == "重新 AI 总结切片"
                        assert delete_button.property("visible") and delete_button.property("text") == "删除录播"
                    if page == 2:
                        check_workbench_portrait(application, engine, window, output)
                    # 抓取系统已呈现的窗口，不通过强制重绘掩盖呈现问题。
                    picture = window.screen().grabWindow(int(window.winId())).toImage()
                    assert not picture.isNull()
                    assert picture.save(str(output / f"page-{page}-{width}.png"))
            window.setProperty("page", 0)
            records = window.findChild(QObject, "recordingList")
            assert records is not None
            assert len(records.childItems()[0].childItems()) < 40, "列表实例化了全部 300 行"

            assert_fixed_columns(window, output)
            check_apple_ui(application, bridge, window, output)
            check_theme_controls(application, bridge, engine, window, output)
            (output / "night-edges").mkdir(exist_ok=True)
            check_skin_switch(application, bridge, engine, window, output)
            check_updates_ui(application, bridge, engine, window, output)
            assert_native_resize_edges(window, output)

            # 连续改变真实窗口尺寸，同时让数据库读取每次阻塞 150ms。
            # Settle popup/native-edge checks before measuring the visible window.
            window.raise_()
            window.requestActivate()
            wait_for(application, window.isActive)
            frames, ticks = [], []
            window.frameSwapped.connect(lambda: frames.append(time.perf_counter()), Qt.QueuedConnection)
            timer = QTimer()
            # 使用精确的测试驱动，避免将粗略定时器的调度延迟误计为缩放性能下降。
            timer.setTimerType(Qt.PreciseTimer)
            def resize():
                ticks.append(time.perf_counter())
                step = len(ticks) % 80
                window.resize(1020 + abs(40 - step) * 6, 680 + abs(40 - step) * 3)
            timer.timeout.connect(resize)
            timer.start(16)
            bridge.timer.start(180)
            with patch.object(db, "list_recordings", side_effect=slow_records):
                loop = QEventLoop()
                # Warm the renderer at the same resize/database load, not an idle page.
                QTimer.singleShot(1500, loop.quit)
                loop.exec()
                frames.clear()
                ticks.clear()
                QTimer.singleShot(5000, loop.quit)
                loop.exec()
            timer.stop()
            bridge.timer.stop()
            wait_for(application, lambda: not bridge._refreshing)
            intervals = [(b - a) * 1000 for a, b in zip(frames, frames[1:])]
            assert len(intervals) > 30 and len(ticks) > 50, "后台读取阻塞了界面"
            ordered = sorted(intervals)
            resize_intervals = sorted((b - a) * 1000 for a, b in zip(ticks, ticks[1:]))
            report = {"graphics_api": str(window.rendererInterface().graphicsApi()), "frames": len(frames), "resize_updates": len(ticks), "resize_hz": (len(ticks) - 1) / (ticks[-1] - ticks[0]), "resize_p95_ms": resize_intervals[int(len(resize_intervals) * .95)], "frame_callback_hz": (len(frames) - 1) / (frames[-1] - frames[0]), "median_ms": statistics.median(intervals), "p95_ms": ordered[int(len(ordered) * .95)], "max_ms": max(intervals), "over_33ms": sum(v > 33.34 for v in intervals), "method": "5 秒程序连续缩放，300 条录播，后台数据库读取延迟 150ms；帧回调经队列在 GUI 线程计时，不是显示器实际呈现帧率；不等同于手工拖边框验收", "qml_warnings": warnings}
            report["warmup_ms"] = 1500
            # 静止和忙碌状态不能因禁用 Present 等待而无限重绘。
            QTest.qWait(200)
            frames.clear()
            bridge._busy = True
            bridge.changed.emit()
            QTimer.singleShot(1000, loop.quit)
            loop.exec()
            report["stationary_busy_frames"] = len(frames)
            assert len(frames) < 120, "忙碌状态持续无节制渲染"
            bridge._busy = False
            bridge.changed.emit()
            (output / "performance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            assert not [w for w in warnings if any(s in w for s in ("ReferenceError", "TypeError", "Unable to assign", "Binding loop", "recursive rearrange", "failed to load", "Failed to create", "Failed to resize"))], warnings
            assert report["resize_hz"] >= 45, "消除黑边不能明显牺牲缩放响应：" + str(report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            done = []
            bridge.closed.connect(lambda: done.append(True))
            bridge.close()
            wait_for(application, lambda: bool(done))
            assert not bridge.timer.isActive() and service.stop_event.is_set()
        finally:
            (output / "qml-warnings.json").write_text(json.dumps(warnings, ensure_ascii=False, indent=2), encoding="utf-8")
            bridge.timer.stop()
            bridge._pool.shutdown(wait=True)
            bridge._network.shutdown(wait=True, cancel_futures=True)
            service.stop()
            if engine:
                for player in engine.rootObjects()[0].findChildren(QMediaPlayer):
                    player.stop()
                    player.setSource(QUrl())
                engine.rootObjects()[0].setProperty("allowClose", True)
                engine.rootObjects()[0].close()
                engine.deleteLater()
            application.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    print("Qt Quick offline checks passed")


if __name__ == "__main__":
    run(updates_only="--updates-only" in ui.sys.argv)
