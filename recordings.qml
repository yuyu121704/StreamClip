import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1280
    height: 820
    minimumWidth: 1020
    minimumHeight: 680
    visible: true
    title: Qt.application.displayName
    color: uiTheme.current.colors.background
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 14
    palette.window: uiTheme.current.colors.background
    palette.active.windowText: uiTheme.current.colors.ink
    palette.inactive.windowText: uiTheme.current.colors.ink
    palette.active.text: uiTheme.current.colors.ink
    palette.inactive.text: uiTheme.current.colors.ink
    palette.active.base: uiTheme.current.colors.surface
    palette.inactive.base: uiTheme.current.colors.surface
    palette.active.button: uiTheme.current.colors.button
    palette.inactive.button: uiTheme.current.colors.button
    palette.active.buttonText: uiTheme.current.colors.primary
    palette.inactive.buttonText: uiTheme.current.colors.primary
    palette.highlight: uiTheme.current.colors.primary
    palette.highlightedText: uiTheme.current.colors.onPrimary
    palette.alternateBase: uiTheme.current.colors.listHover
    palette.light: uiTheme.current.colors.controlHover
    palette.midlight: uiTheme.current.colors.controlPressed
    palette.mid: uiTheme.current.colors.fieldBorder
    palette.dark: uiTheme.current.colors.muted
    palette.shadow: uiTheme.current.colors.shadow
    palette.brightText: uiTheme.current.colors.onPrimary
    palette.accent: uiTheme.current.colors.primary
    palette.link: uiTheme.current.colors.primary
    palette.linkVisited: uiTheme.current.colors.primaryHover
    palette.placeholderText: uiTheme.current.colors.placeholder
    palette.toolTipBase: uiTheme.current.colors.dialog
    palette.toolTipText: uiTheme.current.colors.ink
    palette.disabled.text: uiTheme.current.colors.disabledInk
    palette.disabled.windowText: uiTheme.current.colors.disabledInk
    palette.disabled.buttonText: uiTheme.current.colors.disabledInk
    palette.disabled.base: uiTheme.current.colors.disabled
    palette.disabled.button: uiTheme.current.colors.disabled
    property bool allowClose: false
    property int page: 2
    property var pageNames: ["录播与总结", "任务", "工作台", "直播间", "切片", "投稿", "账号", "设置", "版本"]
    property bool logsOpen: false
    property int selectedTask: 0
    property int confirmationId: 0
    property string confirmationTitle: ""
    property string confirmationUpdateVersion: ""
    property var cleanupSelection: ({ids: []})
    property var cleanupReport: ({message: "", failed: []})
    function confirmCleanup(kind) {
        const selection = bridge.cleanupSelection(kind)
        if (bridge.busy || !selection.ids || !selection.ids.length) return
        cleanupSelection = selection
        cleanupDialog.open()
    }
    readonly property var navigationOrder: [2, 3, 0, 4, 5, 1, 6, 7, 8]
    readonly property var pageIcons: ["clapperboard", "list-checks", "house", "radio", "scissors", "send", "user-round", "settings-2", "refresh-cw"]
    onActiveChanged: if (active) bridge.refreshMotionPreference()
    onPageChanged: {
        if (skinTransition.busy) skinTransition.finish()
        if (bridge.motionEnabled) pageReveal.restart()
        else { pageReveal.stop(); pageStack.opacity = 1 }
    }
    onWidthChanged: { pageReveal.complete(); logMotion.complete(); navigationMotion.complete(); if (skinTransition.busy) skinTransition.finish() }
    onHeightChanged: { pageReveal.complete(); logMotion.complete(); navigationMotion.complete(); if (skinTransition.busy) skinTransition.finish() }
    onLogsOpenChanged: {
        if (!logsOpen && logText.activeFocus) logsButton.forceActiveFocus()
        logMotion.stop()
        logMotion.to = logsOpen ? 120 : 0
        if (bridge.motionEnabled) logMotion.start()
        else logPanel.extent = logMotion.to
    }

    onClosing: function(event) {
        if (skinTransition.busy) skinTransition.finish()
        event.accepted = allowClose
        if (!allowClose && !bridge.busy) closeDialog.open()
    }
    Connections {
        target: bridge
        function onError(message) { errorMessage.text = message; errorDialog.open() }
        function onClosed() { window.allowClose = true; window.close() }
        function onActionDone(action, data) {
            if (action !== "cleanup") return
            if (data.kind === "tasks") window.selectedTask = 0
            window.cleanupReport = data
            cleanupResultDialog.open()
        }
        function onMotionChanged() {
            if (!bridge.motionEnabled) {
                pageReveal.complete()
                logMotion.complete()
                navigationMotion.complete()
            }
        }
    }

    component Action: ActionButton {}

    component Panel: Rectangle {
        color: uiTheme.current.colors.surface
        radius: 16
    }

    NumberAnimation {
        id: pageReveal
        target: pageStack
        property: "opacity"
        from: 0.82; to: 1
        duration: 170
        easing.type: Easing.OutCubic
    }
    NumberAnimation {
        id: navigationMotion
        target: navigationSelection
        property: "y"
        duration: 190
        easing.type: Easing.OutCubic
    }
    NumberAnimation {
        id: logMotion
        target: logPanel
        property: "extent"
        duration: window.logsOpen ? 180 : 140
        easing.type: Easing.OutCubic
    }
    Rectangle {
        id: appSurface
        anchors.fill: parent
        color: window.color
    RowLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 16
        Panel {
            Layout.preferredWidth: 174
            Layout.fillHeight: true
            color: uiTheme.current.colors.rail
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 6
                Image {
                    objectName: "studioIcon"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredHeight: 56
                    Layout.preferredWidth: 56
                    source: artRoot + "app-icon.png"
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
                Label { objectName: "studioName"; textFormat: Text.PlainText; text: window.title; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 4 }
                Label { textFormat: Text.PlainText; text: "创作流程"; font.pixelSize: 11; font.weight: Font.DemiBold; color: uiTheme.current.colors.muted; Layout.topMargin: 16; Layout.leftMargin: 10 }
                Item {
                    Layout.fillWidth: true
                    implicitHeight: navigation.implicitHeight
                    Rectangle {
                        id: navigationSelection
                        objectName: "navigationSelection"
                        readonly property var entry: navigationItems.itemAt(window.navigationOrder.indexOf(window.page))
                        readonly property real destination: entry ? entry.y + (entry.index === 5 ? 28 : 0) : 0
                        onDestinationChanged: {
                            navigationMotion.stop()
                            navigationMotion.to = destination
                            if (bridge.motionEnabled) navigationMotion.start()
                            else y = destination
                        }
                        x: 0
                        width: parent.width
                        height: 40
                        radius: 10
                        color: uiTheme.current.colors.navSelected
                    }
                    ColumnLayout {
                        id: navigation
                        width: parent.width
                        spacing: 4
                        Repeater {
                            id: navigationItems
                            model: window.navigationOrder
                            Item {
                                required property int modelData
                                required property int index
                                Layout.fillWidth: true
                                implicitHeight: index === 5 ? 68 : 40
                                Label { visible: parent.index === 5; text: "管理"; x: 10; y: 7; font.pixelSize: 11; font.weight: Font.DemiBold; color: uiTheme.current.colors.muted }
                                Action {
                                    objectName: "navigation_" + modelData
                                    y: parent.index === 5 ? 28 : 0
                                    width: parent.width
                                    text: window.pageNames[modelData]
                                    flat: true
                                    symbol: window.pageIcons[modelData]
                                    font.weight: window.page === modelData ? Font.DemiBold : Font.Normal
                                    leftPadding: 10
                                    rightPadding: 10
                                    Component.onCompleted: contentItem.alignment = Qt.AlignLeft | Qt.AlignVCenter
                                    onClicked: window.page = modelData
                                    Rectangle {
                                        objectName: "updateBadge"
                                        anchors.right: parent.right
                                        anchors.rightMargin: 9
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: 7; height: 7; radius: 3.5
                                        color: uiTheme.current.colors.primary
                                        visible: modelData === 8 && bridge.updateInfo.hasUpdate
                                    }
                                    Accessible.role: Accessible.PageTab
                                    Accessible.checkable: true
                                    Accessible.checked: window.page === modelData
                                    // Keep the shared moving selection visible below transparent nav buttons.
                                    background: Rectangle {
                                        radius: 10
                                        color: parent.hovered && window.page !== modelData ? uiTheme.current.colors.navHover : "transparent"
                                        border.color: uiTheme.current.colors.primary
                                        border.width: parent.activeFocus ? 2 : 0
                                    }
                                }
                            }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
                Action {
                    id: skinButton
                    objectName: "skinButton"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                    text: "切换皮肤：" + uiTheme.nextName
                    symbol: "palette"
                    display: AbstractButton.IconOnly
                    icon.width: 22
                    icon.height: 22
                    primary: true
                    ToolTip.visible: hovered && !skinTransition.busy
                    Accessible.description: "当前皮肤：" + uiTheme.current.name
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    background: Rectangle {
                        radius: width / 2
                        color: skinButton.down ? uiTheme.current.colors.primaryPressed : skinButton.hovered ? uiTheme.current.colors.primaryHover : uiTheme.current.colors.primary
                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: 3
                            radius: width / 2
                            color: "transparent"
                            border.width: 2
                            border.color: uiTheme.current.colors.onPrimary
                            visible: skinButton.activeFocus
                        }
                    }
                    onClicked: skinTransition.switchFrom(skinButton)
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                RoundedImage { id: headerArtwork; objectName: "headerArtwork"; anchors.fill: parent; source: artRoot + uiTheme.current.header; radius: 16; opacity: 0.6 }
                Label { textFormat: Text.PlainText; anchors.left: parent.left; anchors.leftMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: window.pageNames[window.page]; font.pixelSize: 22; font.weight: Font.DemiBold }
                Label { textFormat: Text.PlainText; anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: (bridge.workspace.stats.clips || 0) + " 个切片  ·  " + (bridge.workspace.stats.tasks || 0) + " 个任务"; color: uiTheme.current.colors.muted; font.pixelSize: 12 }
            }
            StackLayout {
                id: pageStack
                objectName: "pageStack"
                currentIndex: window.page === 8 ? 3 : Math.min(2, window.page)
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Action {
                            objectName: "reanalyzeButton"
                            text: "重新 AI 总结切片"
                            symbol: "refresh-cw"
                            primary: true
                            enabled: !bridge.busy && bridge.detail.canAnalyze
                            onClicked: {
                                window.confirmationId = bridge.detail.id
                                window.confirmationTitle = bridge.detail.title
                                analyzeDialog.open()
                            }
                        }
                        Action {
                            objectName: "deleteRecordingButton"
                            text: "删除录播"
                            symbol: "trash"
                            destructive: true
                            enabled: !bridge.busy && bridge.detail.canDelete
                            onClicked: {
                                window.confirmationId = bridge.detail.id
                                window.confirmationTitle = bridge.detail.title
                                deleteRecordingDialog.open()
                            }
                        }
                        Item { Layout.fillWidth: true }
                        Action {
                            objectName: "clearRecordingsButton"
                            text: "一键删除"
                            symbol: "trash"
                            destructive: true
                            enabled: !bridge.busy && recordingModel.filters.count > 0
                            ToolTip.visible: hovered
                            ToolTip.text: "删除当前筛选范围内的录播及相关文件"
                            onClicked: window.confirmCleanup("recordings")
                        }
                    }
                    MediaFilters { Layout.fillWidth: true; library: recordingModel; namePrefix: "recording" }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 14
                        Panel {
                            Layout.preferredWidth: 420
                            Layout.minimumWidth: 280
                            Layout.fillHeight: true
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                Label { textFormat: Text.PlainText; text: "录播列表"; font.bold: true }
                                ListView {
                                    id: records
                                    objectName: "recordingList"
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    clip: true
                                    model: recordingModel
                                    reuseItems: true
                                    cacheBuffer: 160
                                    spacing: 4
                                    ScrollBar.vertical: ScrollBar {}
                                    delegate: ListEntry {
                                        id: recordRow
                                        required property var rowData
                                        required property int index
                                        width: ListView.view.width
                                        height: 92
                                        highlighted: rowData.id === bridge.detail.id
                                        onClicked: { records.currentIndex = index; bridge.selectRecording(rowData.id) }
                                        Keys.onReturnPressed: clicked()
                                        Keys.onSpacePressed: clicked()
                                        contentItem: ColumnLayout {
                                            spacing: 4
                                            Label { textFormat: Text.PlainText; text: recordRow.rowData.title; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                            Label { textFormat: Text.PlainText; text: "#" + recordRow.rowData.id + "   " + recordRow.rowData.status + "   " + recordRow.rowData.duration + "   " + recordRow.rowData.source; color: uiTheme.current.colors.muted; elide: Text.ElideRight; Layout.fillWidth: true }
                                            Label { textFormat: Text.PlainText; text: recordRow.rowData.streamerName + "  ·  " + recordRow.rowData.started; color: uiTheme.current.colors.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                                        }
                                    }
                                    Label { textFormat: Text.PlainText; anchors.centerIn: parent; width: parent.width - 32; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; text: recordingModel.filters.total ? "没有符合条件的录播\n请选择其他主播或日期，或点击重置。" : "还没有录播\n在直播间下载回放，或在工作台导入本地媒体。"; color: uiTheme.current.colors.muted; visible: records.count === 0 }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.minimumWidth: 330
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            spacing: 10
                            Label { textFormat: Text.PlainText; text: "直播总结"; font.bold: true }
                            Panel {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredHeight: 260
                                ScrollView {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    contentWidth: availableWidth
                                    clip: true
                                    TextArea {
                                        objectName: "summaryText"
                                        text: bridge.detail.summary
                                        readOnly: true
                                        selectByMouse: true
                                        wrapMode: TextEdit.Wrap
                                        textFormat: TextEdit.PlainText
                                        background: null
                                    }
                                }
                            }
                            Label { textFormat: Text.PlainText; text: "高光列表"; font.bold: true }
                            Panel {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredHeight: 180
                                ListView {
                                    id: highlights
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    clip: true
                                    reuseItems: true
                                    model: bridge.detail.highlights
                                    spacing: 16
                                    ScrollBar.vertical: ScrollBar {}
                                    delegate: ColumnLayout {
                                        required property var modelData
                                        width: ListView.view.width - 12
                                        Label { textFormat: Text.PlainText; text: modelData.title || "未命名高光"; font.bold: true; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                        Label { textFormat: Text.PlainText; text: modelData.interval + "  ·  评分 " + Number(modelData.score || 0).toFixed(1) + "  ·  置信度 " + Number(modelData.confidence || 0).toFixed(2); color: uiTheme.current.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                        Label { textFormat: Text.PlainText; text: "风险：" + (modelData.review_status || "candidate") + "\n" + (modelData.reason || ""); wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    }
                                    Label { textFormat: Text.PlainText; anchors.centerIn: parent; text: "暂无高光"; color: uiTheme.current.colors.muted; visible: highlights.count === 0 }
                                }
                            }
                        }
                    }
                }
                ColumnLayout {
                    RowLayout {
                        Action { text: "刷新"; symbol: "refresh-cw"; display: AbstractButton.IconOnly; enabled: !bridge.busy; onClicked: bridge.refresh() }
                        Action { text: "取消选中"; enabled: window.selectedTask > 0 && !bridge.busy; onClicked: bridge.command("cancel", String(window.selectedTask)) }
                        Action { text: "重试选中"; symbol: "rotate-ccw"; enabled: window.selectedTask > 0 && !bridge.busy; onClicked: bridge.command("retry", String(window.selectedTask)) }
                        Action { text: "批量重试失败"; enabled: !bridge.busy; onClicked: bridge.command("retryFailed", "") }
                        Item { Layout.fillWidth: true }
                        Action {
                            objectName: "clearTasksButton"
                            text: "一键清理"
                            symbol: "trash"
                            destructive: true
                            enabled: !bridge.busy && (bridge.workspace.finishedTaskCount || 0) > 0
                            ToolTip.visible: hovered
                            ToolTip.text: "清理已完成、失败和已取消的任务记录"
                            onClicked: window.confirmCleanup("tasks")
                        }
                    }
                    Label { textFormat: Text.PlainText; text: "下载、分析、切片和投稿均保留进度，重启后可恢复。"; color: uiTheme.current.colors.muted }
                    Panel {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        ListView {
                            id: tasks
                            objectName: "taskList"
                            anchors.fill: parent
                            anchors.margins: 12
                            clip: true
                            model: taskModel
                            reuseItems: true
                            spacing: 6
                            ScrollBar.vertical: ScrollBar {}
                            delegate: ListEntry {
                                id: taskRow
                                required property var rowData
                                required property int index
                                width: ListView.view.width
                                height: taskContent.implicitHeight + 24
                                highlighted: rowData.id === window.selectedTask
                                onClicked: { tasks.currentIndex = index; window.selectedTask = rowData.id }
                                contentItem: ColumnLayout {
                                    id: taskContent
                                    Label { textFormat: Text.PlainText; text: "#" + taskRow.rowData.id + "  " + taskRow.rowData.kind + "  ·  " + taskRow.rowData.status + "  " + Number(taskRow.rowData.progress).toFixed(0) + "%"; font.bold: true }
                                    Label { textFormat: Text.PlainText; text: taskRow.rowData.message || ""; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    Label { textFormat: Text.PlainText; text: taskRow.rowData.error || ""; color: uiTheme.current.colors.danger; visible: text.length > 0; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    Label { textFormat: Text.PlainText; text: taskRow.rowData.updated + "  ·  尝试 " + taskRow.rowData.attempts + " 次"; color: uiTheme.current.colors.muted; font.pixelSize: 12 }
                                }
                            }
                            Label { textFormat: Text.PlainText; anchors.centerIn: parent; text: "暂无任务"; visible: tasks.count === 0; color: uiTheme.current.colors.muted }
                        }
                    }
                }
                Item {
                    WorkspacePages {
                        id: workspacePages
                        page: Math.min(7, window.page)
                        // 隐藏的六页保留表单状态，但不随录播/任务页缩放反复布局。
                        property real retainedWidth: 0
                        property real retainedHeight: 0
                        width: window.page >= 2 && window.page < 8 ? parent.width : retainedWidth
                        height: window.page >= 2 && window.page < 8 ? parent.height : retainedHeight
                        onWidthChanged: if (window.page >= 2 && window.page < 8) retainedWidth = width
                        onHeightChanged: if (window.page >= 2 && window.page < 8) retainedHeight = height
                        onNavigate: function(target) { window.page = target }
                        onCleanupRequested: function(kind) { window.confirmCleanup(kind) }
                    }
                }
                VersionPage {
                    onInstallRequested: function(version) {
                        window.confirmationUpdateVersion = version
                        installUpdateDialog.open()
                    }
                }
            }
            Panel {
                id: logPanel
                objectName: "logPanel"
                property real extent: 0
                visible: extent > 0
                Layout.fillWidth: true
                Layout.minimumHeight: extent
                Layout.maximumHeight: extent
                Layout.preferredHeight: extent
                clip: true
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 8
                    contentWidth: availableWidth
                    TextArea { id: logText; text: bridge.workspace.logs.join("\n"); readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; font.family: "Consolas"; font.pixelSize: 12; background: null }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Label { textFormat: Text.PlainText; text: "处理中…"; visible: bridge.busy; color: uiTheme.current.colors.primary }
                Label { textFormat: Text.PlainText; text: bridge.status; elide: Text.ElideRight; Layout.fillWidth: true; color: uiTheme.current.colors.muted }
                Action {
                    objectName: "updateNoticeButton"
                    text: "新版本 " + bridge.updateInfo.latestVersion
                    symbol: "refresh-cw"
                    visible: bridge.updateInfo.hasUpdate && window.page !== 8
                    flat: true
                    implicitHeight: 34
                    onClicked: window.page = 8
                }
                Action { id: logsButton; objectName: "logsButton"; text: window.logsOpen ? "收起日志" : "运行日志"; symbol: "scroll-text"; flat: true; implicitHeight: 34; onClicked: window.logsOpen = !window.logsOpen }
            }
        }
    }
    }
    SkinTransition {
        id: skinTransition
        anchors.fill: parent
        target: appSurface
        artReady: headerArtwork.status === Image.Ready && workspacePages.artReady
    }
    AppDialog {
        id: cleanupDialog
        objectName: "cleanupDialog"
        title: window.cleanupSelection.kind === "tasks" ? "清理已结束任务" : "批量删除" + (window.cleanupSelection.kind === "recordings" ? "录播" : "切片")
        destructive: true
        anchors.centerIn: parent
        width: Math.min(560, window.width - 32)
        modal: true
        standardButtons: Dialog.Yes | Dialog.No
        onOpened: {
            standardButton(Dialog.Yes).text = window.cleanupSelection.kind === "tasks" ? "清理任务记录" : "删除这些文件"
            standardButton(Dialog.No).text = "取消"
            standardButton(Dialog.No).forceActiveFocus()
        }
        onAccepted: {
            if (bridge.busy) return
            if (window.cleanupSelection.kind === "clips") workspacePages.releaseCleanupClip(window.cleanupSelection.ids)
            bridge.perform("cleanup", window.cleanupSelection)
        }
        Label {
            objectName: "cleanupConfirmationText"
            width: parent.width
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            text: "范围：" + (window.cleanupSelection.scope || "") + "\n共 " + window.cleanupSelection.ids.length + " 条。\n\n" +
                  (window.cleanupSelection.kind === "tasks"
                   ? "将从任务列表移除已完成、失败和已取消的记录；移除后不能在此列表重试。\n\n录播、切片、投稿历史和内部去重数据保留。排队、运行中和等待确认的任务不清理。"
                   : (window.cleanupSelection.kind === "recordings"
                      ? "将永久删除这些录播的原视频、弹幕、字幕、总结及相关缓存。\n已有切片和投稿历史保留。"
                      : "将永久删除这些切片的本地视频、封面、字幕和预览文件。\n原录播、投稿历史和已发布的平台稿件保留。") +
                     "\n\n文件删除无法撤销。正在处理、被共用或删除失败的项目将保留，并报告原因。")
        }
    }
    AppDialog {
        id: cleanupResultDialog
        objectName: "cleanupResultDialog"
        title: "清理结果"
        anchors.centerIn: parent
        width: Math.min(560, window.width - 32)
        height: window.cleanupReport.failed.length ? Math.min(420, window.height - 64) : 210
        modal: true
        standardButtons: Dialog.Ok
        onOpened: { standardButton(Dialog.Ok).text = "确定"; standardButton(Dialog.Ok).forceActiveFocus() }
        ColumnLayout {
            anchors.fill: parent
            Label { Layout.fillWidth: true; text: window.cleanupReport.message; textFormat: Text.PlainText; wrapMode: Text.Wrap }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: window.cleanupReport.failed.length > 0
                contentWidth: availableWidth
                clip: true
                TextArea {
                    readOnly: true
                    selectByMouse: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    text: window.cleanupReport.failed.map(item => "#" + item.id + "：" + item.error).join("\n\n")
                    background: null
                }
            }
        }
    }
    AppDialog {
        id: deleteRecordingDialog
        objectName: "deleteRecordingDialog"
        title: "删除录播"
        destructive: true
        anchors.centerIn: parent
        width: 520
        modal: true
        standardButtons: Dialog.Yes | Dialog.No
        onOpened: {
            standardButton(Dialog.Yes).text = "删除录播和文件"
            standardButton(Dialog.No).text = "取消"
            standardButton(Dialog.No).forceActiveFocus()
        }
        onAccepted: bridge.command("deleteRecording", String(window.confirmationId))
        Label { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; text: "确定删除「" + window.confirmationTitle + "」？\n\n将删除录播原视频、弹幕、字幕、总结及相关缓存文件，无法撤销。\n已有切片文件和投稿记录会保留。" }
    }
    AppDialog {
        id: analyzeDialog
        title: "重新 AI 总结切片"
        anchors.centerIn: parent
        width: 520
        modal: true
        standardButtons: Dialog.Yes | Dialog.No
        onOpened: {
            standardButton(Dialog.Yes).text = "是，重新处理"
            standardButton(Dialog.No).text = "取消"
            standardButton(Dialog.No).forceActiveFocus()
        }
        onAccepted: bridge.command("analyze", String(window.confirmationId))
        Label { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; text: "是否重新对「" + window.confirmationTitle + "」进行 AI 总结切片？\n\n将更新总结与高光，并按当前投稿设置处理新切片。已有成片和稿件会保留。\n已有转写会复用；没有转写时先识别语音。" }
    }
    AppDialog {
        id: errorDialog
        title: "操作失败"
        anchors.centerIn: parent
        width: 520
        modal: true
        standardButtons: Dialog.Ok
        onOpened: standardButton(Dialog.Ok).text = "确定"
        Label { textFormat: Text.PlainText; id: errorMessage; width: parent.width; wrapMode: Text.Wrap }
    }
    AppDialog {
        id: installUpdateDialog
        objectName: "installUpdateDialog"
        title: "安装更新并重启"
        anchors.centerIn: parent
        width: 520
        modal: true
        standardButtons: Dialog.Yes | Dialog.No
        onOpened: {
            standardButton(Dialog.Yes).text = "安装并重启"
            standardButton(Dialog.No).text = "取消"
            standardButton(Dialog.No).forceActiveFocus()
        }
        onAccepted: {
            workspacePages.pauseVideo()
            bridge.installUpdate(window.confirmationUpdateVersion)
        }
        Label {
            objectName: "installUpdateMessage"
            width: parent.width
            text: "安装 v" + window.confirmationUpdateVersion + " 后将关闭并重新打开 StreamClip。\n\n只替换程序，保留旧版备份；账号、设置、录播及媒体工具不会被覆盖。更新前请备份数据，不要用旧版程序打开已升级的数据。\n\n" +
                  (workspacePages.hasUnsaved() ? "仍有未保存的表单修改，重启后将丢失。\n\n" : "") +
                  "有录制或处理任务时暂不安装。"
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
    }
    AppDialog {
        id: closeDialog
        title: "确认退出"
        anchors.centerIn: parent
        width: 440
        modal: true
        standardButtons: Dialog.Yes | Dialog.No
        onOpened: {
            standardButton(Dialog.Yes).text = "退出"
            standardButton(Dialog.No).text = "取消"
            standardButton(Dialog.No).forceActiveFocus()
        }
        onAccepted: { workspacePages.pauseVideo(); bridge.close() }
        Label { textFormat: Text.PlainText; width: parent.width; wrapMode: Text.Wrap; text: "退出会停止当前录制和后台服务。" + (workspacePages.hasUnsaved() ? "未保存的表单修改将丢失。" : "") + "确定退出？" }
    }
}
