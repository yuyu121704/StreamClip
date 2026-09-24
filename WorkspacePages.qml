import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia

Item {
    id: pages
    objectName: "workspacePages"
    enabled: !bridge.busy
    property int page: 2
    property var selectedRoom: ({})
    property var selectedUpload: ({})
    property int settingsCategory: 0
    property var modelOptions: bridge.formSettings.llm_model ? [bridge.formSettings.llm_model] : []
    property bool modelsLoading: false
    property var asrModelOptions: [bridge.formSettings.dashscope_model]
    property bool asrModelsLoading: false
    property string asrModelsMessage: ""
    property int deletingClipId: 0
    property string modelsMessage: "填好地址和 Key 后，连接并获取模型。"
    property bool mediaDetails: false
    property bool showVideo: false
    readonly property bool artReady: workbenchArtwork.status === Image.Ready
    property int qrAccount: 0
    property string pendingAction: ""
    property var pendingValues: ({})
    signal navigate(int page)
    signal cleanupRequested(string kind)
    function confirm(title, message, action, values) {
        confirmation.title=title; confirmationText.text=message
        pendingAction=action; pendingValues=values; confirmation.open()
    }
    function addRoom() { roomId.text=""; roomName.text=""; addRoomDialog.open() }
    function importMedia() { importForm.reset({source:"",danmaku:"",title:""}); importDialog.open() }
    function startLogin(id) { qrAccount=id; qrDialog.open(); bridge.startQr(id) }
    function timeText(milliseconds) { const s=Math.floor(milliseconds/1000); return Math.floor(s/60)+":"+String(s%60).padStart(2,"0") }
    function hasUnsaved() { return settingsForm.dirty || uploadForm.dirty || (roomDialog.opened && roomForm.dirty) || (importDialog.opened && importForm.dirty) || glossaryPanel.hasUnsaved() }
    function pauseVideo() { player.pause() }
    function releaseCleanupClip(ids) {
        if (ids.indexOf(bridge.workspace.clip.id) >= 0) pages.deletingClipId = bridge.workspace.clip.id
    }
    onPageChanged: if (page !== 4) player.pause()
    onShowVideoChanged: if (!showVideo) player.pause()

    Connections {
        target: bridge
        function onError(message) { pages.deletingClipId=0 }
        function onSettingsChanged() { settingsForm.reset(bridge.formSettings) }
        function onModelsReady(result) {
            if (result.credentials[0] !== String(settingsForm.values.llm_endpoint).trim() || result.credentials[1] !== String(settingsForm.values.llm_api_key).trim()) return
            pages.modelsLoading=false
            pages.modelsMessage=result.error || ("已获取 "+result.models.length+" 个模型，选择后保存。")
            if (!result.error) {
                pages.modelOptions=result.models
                if (result.models.indexOf(settingsForm.values.llm_model)<0) settingsForm.setValue("llm_model",result.models.length===1?result.models[0]:"")
            }
        }
        function onAsrModelsReady(result) {
            if (result.credentials !== String(settingsForm.values.dashscope_api_key).trim()) return
            pages.asrModelsLoading=false
            pages.asrModelsMessage=result.error || ("已加载 "+result.models.length+" 个录音文件 ASR 模型")
            if (!result.error) {
                pages.asrModelOptions=result.models
                if (result.models.indexOf(settingsForm.values.dashscope_model)<0)
                    settingsForm.setValue("dashscope_model",result.models.length===1?result.models[0]:"")
            }
        }
        function onActionDone(action, data) {
            if (action === "deleteClip" || action === "cleanup") pages.deletingClipId=0
            if (action === "roomAdd") addRoomDialog.close()
            if (action === "roomSave") { pages.selectedRoom=data.room; roomForm.reset(data.room); roomDialog.close() }
            if (action === "mediaImport") { importDialog.close(); pages.navigate(1) }
            if (action === "replayDownload") { replayDialog.close(); pages.navigate(1) }
            if (action === "enqueueClip") pages.navigate(5)
            if (action === "saveUpload") { pages.selectedUpload=data.upload; uploadForm.reset(data.upload) }
            if (action === "importFont") { settingsForm.setValue("render_font_name",data.render_font_name); settingsForm.setValue("render_font_path",data.render_font_path) }
            if (action === "replays") { if (data.items.length) replayDialog.open(); else { noticeText.text="没有找到可用回放。"; notice.open() } }
            if (action === "tools") { noticeText.text=data.message; notice.open() }
        }
        function onQrChanged() { if (bridge.qr.state === "success") qrDialog.close() }
    }
    component Panel: Rectangle { color:uiTheme.current.colors.surface; radius:16 }
    component Empty: Label { textFormat: Text.PlainText; anchors.centerIn:parent; width:parent.width-30; horizontalAlignment:Text.AlignHCenter; wrapMode:Text.Wrap; color:uiTheme.current.colors.muted }

    StackLayout {
        anchors.fill: parent
        // 外层切到录播/任务时仍保留本页状态，不能给原生 StackLayout 传 -2。
        currentIndex: Math.max(0, pages.page-2)
        // 工作台
        ColumnLayout {
            spacing:12
            Item {
                Layout.fillWidth:true; Layout.preferredHeight:180
                Rectangle { anchors.fill:parent; radius:18; color:uiTheme.current.sceneFit?uiTheme.current.sceneBackground:"transparent" }
                RoundedImage { id:workbenchArtwork; objectName:"workbenchArtwork"; anchors.fill:parent; source:artRoot+uiTheme.current.scene; radius:18; fillMode:uiTheme.current.sceneFit?Image.PreserveAspectFit:Image.PreserveAspectCrop; horizontalAlignment:uiTheme.current.sceneFit?Image.AlignRight:Image.AlignHCenter }
                ColumnLayout {
                    objectName:"workbenchIntro"
                    anchors.left:parent.left; anchors.leftMargin:22; anchors.verticalCenter:parent.verticalCenter; width:parent.width*0.65
                    Label { textFormat: Text.PlainText; text:"把今天的直播，留下精彩片段"; font.pixelSize:22; font.weight:Font.DemiBold; wrapMode:Text.Wrap; Layout.fillWidth:true }
                    Label { textFormat: Text.PlainText; text:bridge.workspace.activeRooms+" 个房间录制中 · "+(bridge.workspace.stats.tasks||0)+" 个任务 · "+(bridge.workspace.stats.clips||0)+" 个切片"; color:uiTheme.current.colors.muted; wrapMode:Text.Wrap; Layout.fillWidth:true }
                    RowLayout {
                        ActionButton { text:"添加直播间"; symbol:"plus"; primary:true; onClicked:pages.addRoom() }
                        ActionButton { text:"导入本地媒体"; symbol:"folder-open"; onClicked:pages.importMedia() }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth:true
                Repeater {
                    model:[{text:"01 录制监控",page:3,symbol:"radio"},{text:"02 AI 回顾",page:0,symbol:"clapperboard"},{text:"03 选片与封面",page:4,symbol:"scissors"},{text:"04 投稿队列",page:5,symbol:"send"}]
                    ActionButton { required property var modelData; text:modelData.text; symbol:modelData.symbol; flat:true; Layout.fillWidth:true; onClicked:pages.navigate(modelData.page) }
                }
            }
            RowLayout {
                ActionButton { text:"发现历史回放"; symbol:"search"; onClicked:{ discoverRoom.text=pages.selectedRoom.room_id||""; discoverDialog.open() } }
                ActionButton { text:"查看任务"; symbol:"list-checks"; flat:true; onClicked:pages.navigate(1) }
                Item { Layout.fillWidth:true }
            }
            RowLayout {
                Layout.fillWidth:true; Layout.fillHeight:true
                Panel {
                Layout.fillWidth:true; Layout.fillHeight:true
                ColumnLayout {
                    anchors.fill:parent; anchors.margins:14
                    Label { textFormat: Text.PlainText; text:"最近切片"; font.bold:true }
                    ListView {
                        Layout.fillWidth:true; Layout.fillHeight:true; clip:true; model:bridge.workspace.recentClips; reuseItems:true
                        ScrollBar.vertical:ScrollBar {}
                        delegate:ListEntry {
                            required property var modelData
                            width:ListView.view.width; height:72
                            text:"#"+modelData.id+"  "+modelData.title+"\n"+modelData.duration+" · "+modelData.state
                            onClicked:{ bridge.selectClip(modelData.id); pages.navigate(4) }
                        }
                        Empty { visible:parent.count===0; text:"还没有切片，从录制或导入一场直播开始。" }
                    }
                }
                }
                Panel {
                    Layout.fillWidth:true; Layout.fillHeight:true
                    ColumnLayout {
                        anchors.fill:parent; anchors.margins:14
                        Label { textFormat:Text.PlainText; text:"最近任务"; font.bold:true }
                        ListView {
                            Layout.fillWidth:true; Layout.fillHeight:true; clip:true; model:bridge.workspace.recentTasks; reuseItems:true
                            ScrollBar.vertical:ScrollBar {}
                            delegate:ListEntry { required property var modelData; width:ListView.view.width; height:66; text:modelData.kind+" · "+modelData.status+" "+Number(modelData.progress).toFixed(0)+"%\n"+modelData.message; onClicked:pages.navigate(1) }
                            Empty { visible:parent.count===0; text:"暂无任务" }
                        }
                    }
                }
            }
        }
        // 直播间
        ColumnLayout {
            RowLayout {
                ActionButton { text:"添加直播间"; symbol:"plus"; primary:true; onClicked:pages.addRoom() }
                ActionButton { text:"主播配置"; enabled:!!pages.selectedRoom.room_id&&!bridge.busy; onClicked:{ roomForm.reset(pages.selectedRoom); roomDialog.open() } }
                ActionButton { text:"启用 / 停用"; enabled:!!pages.selectedRoom.room_id&&!bridge.busy; onClicked:bridge.perform("roomToggle",pages.selectedRoom) }
                ActionButton { text:"移除"; symbol:"trash"; destructive:true; enabled:!!pages.selectedRoom.room_id&&!bridge.busy; onClicked:pages.confirm("移除直播间","移除房间 "+pages.selectedRoom.room_id+"？历史录播会保留。","roomRemove",pages.selectedRoom) }
            }
            RowLayout {
                ActionButton { text:"开始录制"; enabled:!!pages.selectedRoom.room_id&&!bridge.busy; onClicked:bridge.perform("recordStart",pages.selectedRoom) }
                ActionButton { text:"停止录制"; enabled:!!pages.selectedRoom.room_id&&!bridge.busy; onClicked:bridge.perform("recordStop",pages.selectedRoom) }
                ActionButton { text:"立即检查"; enabled:!bridge.busy; onClicked:bridge.perform("checkRooms",{}) }
                ActionButton { text:"发现回放"; onClicked:{ discoverRoom.text=pages.selectedRoom.room_id||""; discoverDialog.open() } }
            }
            Panel {
                Layout.fillWidth:true; Layout.fillHeight:true
                ListView {
                    id:rooms
                    objectName:"roomList"
                    anchors.fill:parent; anchors.margins:12; clip:true; reuseItems:true; model:roomModel
                    ScrollBar.vertical:ScrollBar {}
                    delegate:ListEntry {
                        required property var rowData
                        width:ListView.view.width; height:84
                        highlighted:rowData.room_id===pages.selectedRoom.room_id
                        onClicked:pages.selectedRoom=Object.assign({},rowData)
                        contentItem:ColumnLayout {
                            Label { textFormat: Text.PlainText; text:rowData.name+" · "+rowData.room_id; font.bold:true; Layout.fillWidth:true; elide:Text.ElideRight }
                            Label { textFormat: Text.PlainText; text:rowData.status+" · 监控"+(rowData.enabled?"已启用":"已停用")+" · "+(rowData.last_title||"暂无直播标题"); Layout.fillWidth:true; elide:Text.ElideRight }
                            Label { textFormat: Text.PlainText; text:rowData.last_checked||"尚未检查"; color:uiTheme.current.colors.muted; font.pixelSize:12 }
                        }
                    }
                    Empty { visible:rooms.count===0; text:"还没有直播间，点击上方添加。" }
                }
            }
        }
        // 切片
        ColumnLayout {
            RowLayout {
                ActionButton {
                    objectName:"deleteClipButton"; text:"删除切片"
                    symbol:"trash"; destructive:true
                    enabled:!!bridge.workspace.clip.canDelete&&!bridge.busy
                    onClicked:pages.confirm("删除切片","删除「"+bridge.workspace.clip.title+"」及其本地视频、封面、字幕和预览文件？此操作无法撤销。\n原录播和投稿历史会保留，已发布的平台稿件不会删除。","deleteClip",{id:bridge.workspace.clip.id})
                }
                ActionButton {
                    objectName:"clearClipsButton"; text:"一键删除"
                    symbol:"trash"; destructive:true
                    enabled:clipModel.filters.count>0&&!bridge.busy
                    ToolTip.visible:hovered
                    ToolTip.text:"删除当前筛选范围内的切片及相关文件"
                    onClicked:pages.cleanupRequested("clips")
                }
                Item { Layout.fillWidth:true }
                ActionButton { text:"加入投稿队列"; symbol:"send"; primary:true; enabled:!!bridge.workspace.clip.id&&!bridge.busy; onClicked:bridge.perform("enqueueClip",{id:bridge.workspace.clip.id}) }
            }
            MediaFilters { Layout.fillWidth:true; library:clipModel; namePrefix:"clip" }
            RowLayout {
                Layout.fillWidth:true; Layout.fillHeight:true
                spacing:14
                Panel {
                    Layout.preferredWidth:380; Layout.minimumWidth:320; Layout.fillHeight:true
                    ListView {
                        id:clips
                        objectName:"clipList"
                        anchors.fill:parent; anchors.margins:12; clip:true; model:clipModel; reuseItems:true
                        ScrollBar.vertical:ScrollBar {}
                        delegate:ListEntry {
                            required property var rowData
                            id:clipRow
                            Accessible.name:rowData.title+" · "+rowData.streamerName+" · "+rowData.date
                            width:ListView.view.width; height:96
                            highlighted:rowData.id===bridge.workspace.clip.id
                            contentItem:ColumnLayout {
                                spacing:4
                                Label { textFormat:Text.PlainText; text:"#"+clipRow.rowData.id+"  "+clipRow.rowData.title; wrapMode:Text.Wrap; maximumLineCount:2; elide:Text.ElideRight; Layout.fillWidth:true }
                                Label { textFormat:Text.PlainText; text:clipRow.rowData.duration+" · "+clipRow.rowData.state; color:uiTheme.current.colors.muted; elide:Text.ElideRight; Layout.fillWidth:true }
                                Label { textFormat:Text.PlainText; text:clipRow.rowData.streamerName+" · "+clipRow.rowData.date; color:uiTheme.current.colors.muted; font.pixelSize:12; elide:Text.ElideRight; Layout.fillWidth:true }
                            }
                            onClicked:bridge.selectClip(rowData.id)
                        }
                        Empty { visible:clips.count===0; text:clipModel.filters.total ? "没有符合条件的切片\n请选择其他主播或日期，或点击重置。" : "还没有生成切片" }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth:true; Layout.minimumWidth:350; Layout.fillHeight:true
                    SegmentedBar {
                        Layout.fillWidth:true; currentIndex:pages.showVideo?1:0
                        onCurrentIndexChanged: if (currentIndex >= 0) pages.showVideo=currentIndex===1
                        PageTab { text:"成品预览" }
                        PageTab { text:"视频播放" }
                    }
                    StackLayout {
                        Layout.fillWidth:true; Layout.fillHeight:true; currentIndex:pages.showVideo?1:0
                        ScrollView {
                            contentWidth:availableWidth; clip:true
                            ColumnLayout {
                                width:parent.width; spacing:10
                                Image { source:bridge.workspace.clip.cover||""; fillMode:Image.PreserveAspectFit; asynchronous:true; Layout.fillWidth:true; Layout.preferredHeight:200; visible:source.toString().length>0 }
                                Label { textFormat: Text.PlainText; text:bridge.workspace.clip.title||"选择左侧切片"; font.bold:true; wrapMode:Text.Wrap; Layout.fillWidth:true }
                                Label { textFormat: Text.PlainText; text:bridge.workspace.clip.info||"查看封面、选题依据与视频。"; wrapMode:Text.Wrap; Layout.fillWidth:true }
                                RowLayout {
                                    ActionButton { text:"播放视频"; symbol:"play"; enabled:!!bridge.workspace.clip.video; onClicked:{ pages.showVideo=true; player.play() } }
                                    ActionButton { text:"候选画面"; enabled:!!bridge.workspace.clip.id; onClicked:{ if (bridge.workspace.clip.candidates) coverDialog.open(); else { noticeText.text="这条切片还没有候选画面拼图。"; notice.open() } } }
                                }
                                TextArea { text:bridge.workspace.clip.evidence||""; readOnly:true; selectByMouse:true; wrapMode:TextEdit.Wrap; Layout.fillWidth:true; background:null }
                            }
                        }
                        ColumnLayout {
                            Rectangle {
                                color:"#10151E"; Layout.fillWidth:true; Layout.fillHeight:true
                                VideoOutput { id:video; anchors.fill:parent; fillMode:VideoOutput.PreserveAspectFit }
                                Label { textFormat: Text.PlainText; anchors.centerIn:parent; width:parent.width-24; horizontalAlignment:Text.AlignHCenter; wrapMode:Text.Wrap; color:"white"; text:player.error!==MediaPlayer.NoError ? "视频无法播放："+player.errorString : !player.source.toString()?"切片文件不存在或尚未选择":"" }
                            }
                            Slider { id:seek; Layout.fillWidth:true; from:0; to:Math.max(1,player.duration); value:player.position; enabled:player.seekable; onMoved:player.setPosition(value) }
                            RowLayout {
                                ActionButton { text:player.playbackState===MediaPlayer.PlayingState?"暂停":"播放"; symbol:player.playbackState===MediaPlayer.PlayingState?"pause":"play"; display:AbstractButton.IconOnly; enabled:!!player.source.toString(); onClicked:{ if(player.playbackState===MediaPlayer.PlayingState) player.pause(); else { if(player.mediaStatus===MediaPlayer.EndOfMedia) player.setPosition(0); player.play() } } }
                                Label { textFormat: Text.PlainText; text:pages.timeText(player.position)+" / "+pages.timeText(player.duration) }
                                Item { Layout.fillWidth:true }
                                Label { textFormat: Text.PlainText; text:"音量" }
                                Slider { Layout.preferredWidth:100; from:0; to:1; value:0.8; onMoved:audio.volume=value }
                            }
                        }
                    }
                }
            }
        }
        // 投稿
        ColumnLayout {
            RowLayout {
                ActionButton { text:"提交 / 重试选中"; symbol:"upload"; primary:true; enabled:!!pages.selectedUpload.id&&!bridge.busy; onClicked:bridge.perform("submitUpload",{id:pages.selectedUpload.id}) }
                ActionButton { text:"检查投稿账号"; enabled:!bridge.busy; onClicked:bridge.perform("checkLogin",{}) }
                Label { textFormat: Text.PlainText; text:"稿件收到 BV 号后仍需等待平台确认。"; color:uiTheme.current.colors.muted; Layout.fillWidth:true; wrapMode:Text.Wrap }
            }
            RowLayout {
                Layout.fillWidth:true; Layout.fillHeight:true
                spacing:14
                Panel {
                    Layout.preferredWidth:380; Layout.minimumWidth:260; Layout.fillHeight:true
                    ListView {
                        id:uploads
                        objectName:"uploadList"
                        anchors.fill:parent; anchors.margins:12; clip:true; model:uploadModel; reuseItems:true
                        ScrollBar.vertical:ScrollBar {}
                        delegate:ListEntry {
                            required property var rowData
                            width:ListView.view.width; height:98
                            highlighted:rowData.id===pages.selectedUpload.id
                            text:"#"+rowData.id+"  "+rowData.title+"\n"+rowData.state+" · "+(rowData.bvid||"等待回执")+" · 切片 #"+rowData.clip_id+" · "+rowData.attempts+" 次\n"+(rowData.error||rowData.review_warning||"")
                            onClicked:{
                                if (uploadForm.dirty && rowData.id!==pages.selectedUpload.id) { pages.confirm("尚未保存","放弃当前稿件字段的修改？","selectUpload",rowData); return }
                                pages.selectedUpload=Object.assign({},rowData); uploadForm.reset(rowData)
                            }
                        }
                        Empty { visible:uploads.count===0; text:"投稿队列为空，在切片页选择成片加入队列。" }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth:true; Layout.minimumWidth:310; Layout.fillHeight:true
                    ScrollView {
                        Layout.fillWidth:true; Layout.fillHeight:true; contentWidth:availableWidth; clip:true
                        Column {
                            width:parent.width; spacing:12
                            Label { objectName:"uploadReviewWarning"; width:parent.width; visible:!!text; text:pages.selectedUpload.review_warning||""; textFormat:Text.PlainText; wrapMode:Text.Wrap; color:uiTheme.current.colors.warning }
                            FormFields { id:uploadForm; width:parent.width; fields:bridge.forms.upload; enabled:!!pages.selectedUpload.id }
                        }
                    }
                    ActionButton { text:"保存投稿字段"; symbol:"save"; primary:true; enabled:!!pages.selectedUpload.id&&!bridge.busy; Layout.alignment:Qt.AlignRight; onClicked:bridge.perform("saveUpload",uploadForm.values) }
                }
            }
        }
        // 账号
        ColumnLayout {
            RowLayout {
                ActionButton { text:"添加账号"; symbol:"plus"; primary:true; onClicked:pages.startLogin(0) }
                Label { textFormat: Text.PlainText; text:"用 Bilibili App 扫码，登录信息仅在本机加密保存。"; color:uiTheme.current.colors.muted; wrapMode:Text.Wrap; Layout.fillWidth:true }
            }
            GridLayout {
                columns:2; Layout.fillWidth:true
                Repeater {
                    model:[{role:"download",label:"录制与下载账号"},{role:"publish",label:"投稿账号"}]
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth:true
                        Label { textFormat: Text.PlainText; text:modelData.label }
                        ChoiceBox {
                            Layout.fillWidth:true; textRole:"label"; valueRole:"value"
                            model:[{value:0,label:"未选择"}].concat(bridge.workspace.accounts.filter(a=>a.enabled&&["both",modelData.role].indexOf(a.role)>=0).map(a=>({value:a.id,label:a.name})))
                            currentIndex:{ const selected=modelData.role==="download"?bridge.workspace.downloadAccount:bridge.workspace.publishAccount; for(let i=0;i<model.length;i++) if(model[i].value===selected)return i; return 0 }
                            onActivated:bridge.perform("accountSelect",{role:modelData.role,id:currentValue})
                        }
                    }
                }
            }
            Panel {
                Layout.fillWidth:true; Layout.fillHeight:true
                ListView {
                    id:accounts
                    objectName:"accountList"
                    anchors.fill:parent; anchors.margins:16; clip:true; model:bridge.workspace.accounts; reuseItems:true
                    ScrollBar.vertical:ScrollBar {}
                    delegate:RowLayout {
                        required property var modelData
                        width:ListView.view.width; height:100; spacing:14
                        Rectangle {
                            width:64; height:64; radius:32; color:uiTheme.current.colors.selection
                            RoundedImage { anchors.fill:parent; radius:32; source:modelData.avatar||""; fillMode:Image.PreserveAspectFit; visible:source.toString().length>0 }
                            Label { textFormat: Text.PlainText; anchors.centerIn:parent; text:modelData.name.slice(0,1); font.pixelSize:26; visible:!modelData.avatar }
                        }
                        ColumnLayout {
                            Layout.fillWidth:true
                            Label { textFormat: Text.PlainText; text:modelData.name; font.bold:true; Layout.fillWidth:true; elide:Text.ElideRight }
                            Label { textFormat: Text.PlainText; text:"UID "+(modelData.uid||"待重新登录")+" · "+(modelData.last_status||"尚未检查"); color:uiTheme.current.colors.muted; wrapMode:Text.Wrap; Layout.fillWidth:true }
                        }
                        ActionButton { text:"检查登录"; enabled:!bridge.busy; onClicked:bridge.perform("checkLogin",{id:modelData.id}) }
                        ActionButton { text:"重新扫码"; onClicked:pages.startLogin(modelData.id) }
                        ActionButton { text:"移除"; symbol:"trash"; destructive:true; display:AbstractButton.IconOnly; enabled:!bridge.busy; onClicked:pages.confirm("移除账号","移除「"+modelData.name+"」的本机登录状态？","accountRemove",{id:modelData.id}) }
                    }
                    Empty { visible:accounts.count===0; text:"还没有登录账号，点击上方添加账号。" }
                }
            }
        }
        // 设置
        ColumnLayout {
            SegmentedBar {
                objectName:"settingsTabs"
                Layout.fillWidth:true
                currentIndex:pages.settingsCategory
                onCurrentIndexChanged: if (currentIndex >= 0) pages.settingsCategory=currentIndex
                Repeater {
                    model:["基础与自动化","字幕与封面","语音与 AI","高级设置"]
                    PageTab { required property string modelData; text:modelData }
                }
            }
            Panel {
                Layout.fillWidth:true; Layout.fillHeight:true
                ScrollView {
                    anchors.fill:parent; anchors.margins:18; clip:true; contentWidth:availableWidth
                    ColumnLayout {
                        width:parent.width; spacing:12
                        RowLayout {
                            Layout.fillWidth:true; spacing:8
                            Label { textFormat: Text.PlainText; text:pages.settingsCategory===0?"选择保存位置并启用自动处理；设置保存后用于新任务。":pages.settingsCategory===1?"默认沿用参考封面和字幕样式，日常使用无需调整排版。":pages.settingsCategory===2?"阿里云语音识别与 AI":"术语、联网查词与媒体工具"; color:uiTheme.current.colors.muted; Layout.fillWidth:pages.settingsCategory!==2; wrapMode:Text.Wrap }
                            ActionButton {
                                objectName:"aliyunApiKeyLink"
                                visible:pages.settingsCategory===2
                                text:"获取 API Key（bailian.console.aliyun.com）"
                                flat:true; font.pixelSize:12; font.underline:true
                                implicitHeight:28; padding:4; leftPadding:6; rightPadding:6
                                ToolTip.visible:hovered
                                ToolTip.text:"在浏览器中打开阿里云百炼 API Key 管理页面"
                                HoverHandler { cursorShape:Qt.PointingHandCursor }
                                onClicked:Qt.openUrlExternally("https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key")
                            }
                            Item { visible:pages.settingsCategory===2; Layout.fillWidth:true }
                        }
                        ToggleSwitch { text:"自定义字体与排版"; visible:pages.settingsCategory===1; checked:pages.mediaDetails; onToggled:pages.mediaDetails=checked }
                        RowLayout {
                            visible:pages.settingsCategory===3
                            ActionButton { text:"管理术语与候选"; primary:true; onClicked:glossaryPanel.open() }
                        }
                        FormFields {
                            id:settingsForm
                            objectName:"settingsForm"
                            Layout.fillWidth:true
                            fields:bridge.forms.settings[pages.settingsCategory]
                            visible:pages.settingsCategory!==1||pages.mediaDetails
                            models:pages.modelOptions
                            asrModels:pages.asrModelOptions
                            asrModelsLoading:pages.asrModelsLoading
                            asrModelsMessage:pages.asrModelsMessage
                            onLoadAsrModels:{ pages.asrModelsLoading=true; bridge.fetchAsrModels(values) }
                            Component.onCompleted:reset(bridge.formSettings)
                            onEdited:function(key) {
                                if(key==="llm_endpoint"||key==="llm_api_key") { pages.modelOptions=[]; pages.modelsLoading=false; pages.modelsMessage="地址或 Key 已改变，请重新获取模型。"; setValue("llm_model","") }
                                if(key==="dashscope_api_key") {
                                    bridge.invalidateAsrModels()
                                    pages.asrModelsLoading=false
                                    pages.asrModelsMessage="ASR Key 已改变，模型列表待重新加载。"
                                    pages.asrModelOptions=[bridge.formSettings.dashscope_model]
                                    setValue("dashscope_model",bridge.formSettings.dashscope_model)
                                }
                            }
                        }
                        ActionButton { text:pages.modelsLoading?"连接中…":"连接并获取模型"; visible:pages.settingsCategory===2; enabled:!pages.modelsLoading; primary:true; onClicked:{ pages.modelsLoading=true; bridge.fetchModels(settingsForm.values) } }
                        Label { textFormat: Text.PlainText; text:pages.modelsMessage; visible:pages.settingsCategory===2; color:uiTheme.current.colors.muted; wrapMode:Text.Wrap; Layout.fillWidth:true }
                        RowLayout {
                            visible:pages.settingsCategory===1&&pages.mediaDetails
                            Label { textFormat: Text.PlainText; text:"系统字体" }
                            ChoiceBox { Layout.fillWidth:true; model:bridge.forms.fonts; editable:true; onActivated:settingsForm.setValue("render_font_name",currentText) }
                            ActionButton { text:"使用系统字体"; onClicked:settingsForm.setValue("render_font_path","") }
                        }
                        ActionButton { text:"检查媒体工具"; visible:pages.settingsCategory===3; enabled:!bridge.busy; onClicked:bridge.perform("checkTools",settingsForm.values) }
                    }
                }
            }
            RowLayout {
                Item { Layout.fillWidth:true }
                Label { textFormat: Text.PlainText; text:settingsForm.dirty?"有未保存的修改":"设置已同步"; color:uiTheme.current.colors.muted }
                ActionButton { objectName:"restoreDefaultsButton"; text:"恢复默认"; symbol:"rotate-ccw"; enabled:!bridge.busy; onClicked:pages.confirm("恢复默认设置","恢复表单的安全默认值，保存后生效；不会删除录播和任务。","defaults",{}) }
                ActionButton { objectName:"saveSettingsButton"; text:"保存设置"; symbol:"save"; primary:true; enabled:!bridge.busy&&!pages.modelsLoading&&!pages.asrModelsLoading; onClicked:bridge.perform("settingsSave",settingsForm.values) }
            }
        }
    }
    MediaPlayer { id:player; objectName:"clipPlayer"; source:pages.deletingClipId===bridge.workspace.clip.id ? "" : bridge.workspace.clip.video||""; videoOutput:video; audioOutput:AudioOutput { id:audio; volume:0.8 } onSourceChanged:stop() }
    AppDialog {
        id:confirmation; objectName:"workspaceConfirmation"; anchors.centerIn:parent; width:Math.min(510,pages.width-24); modal:true; standardButtons:Dialog.Yes|Dialog.No
        destructive:["deleteClip","roomRemove","accountRemove"].indexOf(pages.pendingAction)>=0
        onOpened:{ standardButton(Dialog.Yes).text="确认"; standardButton(Dialog.No).text="取消"; standardButton(Dialog.No).forceActiveFocus() }
        onAccepted:{
            if(pages.pendingAction==="selectUpload") { pages.selectedUpload=pages.pendingValues; uploadForm.reset(pages.pendingValues) }
            else if(pages.pendingAction==="defaults") {
                bridge.invalidateAsrModels()
                settingsForm.reset(bridge.defaultSettings); settingsForm.dirty=true
                pages.modelOptions=[]; settingsForm.setValue("llm_model","")
                pages.asrModelOptions=[bridge.defaultSettings.dashscope_model]
                pages.asrModelsLoading=false; pages.asrModelsMessage=""
            }
            else if(pages.pendingAction==="deleteClip") { pages.deletingClipId=pages.pendingValues.id; bridge.perform(pages.pendingAction,pages.pendingValues) }
            else bridge.perform(pages.pendingAction,pages.pendingValues)
        }
        Label { textFormat: Text.PlainText; id:confirmationText; width:parent.width; wrapMode:Text.Wrap }
    }
    AppDialog {
        id:addRoomDialog; title:"添加直播间"; anchors.centerIn:parent; width:440; modal:true
        ColumnLayout {
            width:parent.width
            Label { textFormat: Text.PlainText; text:"Bilibili 房间号或直播间链接" }
            RoundedField { id:roomId; Layout.fillWidth:true; selectByMouse:true; Accessible.name:"Bilibili 房间号或直播间链接" }
            Label { textFormat: Text.PlainText; text:"显示名称（可选）" }
            RoundedField { id:roomName; Layout.fillWidth:true; selectByMouse:true; Accessible.name:"显示名称" }
            ActionButton { text:"添加"; primary:true; enabled:!bridge.busy&&roomId.text.trim().length>0; Layout.alignment:Qt.AlignRight; onClicked:bridge.perform("roomAdd",{room_id:roomId.text,name:roomName.text}) }
        }
    }
    AppDialog {
        id:roomDialog; title:"主播配置"; anchors.centerIn:parent; width:Math.min(590,pages.width-20); height:Math.min(620,pages.height-20); modal:true
        ColumnLayout {
            anchors.fill:parent
            ScrollView { Layout.fillWidth:true; Layout.fillHeight:true; contentWidth:availableWidth; clip:true; FormFields { id:roomForm; width:parent.width; fields:bridge.forms.room; accounts:bridge.workspace.accounts } }
            ActionButton { text:"保存主播配置"; primary:true; enabled:!bridge.busy; Layout.alignment:Qt.AlignRight; onClicked:bridge.perform("roomSave",roomForm.values) }
        }
    }
    AppDialog {
        id:importDialog; title:"导入本地媒体"; anchors.centerIn:parent; width:Math.min(600,pages.width-20); modal:true
        ColumnLayout {
            width:parent.width
            FormFields { id:importForm; Layout.fillWidth:true; fields:[{key:"source",label:"媒体文件",kind:"file"},{key:"danmaku",label:"弹幕文件（可选）",kind:"file"},{key:"title",label:"标题（可留空）",kind:"text"}] }
            ActionButton { text:"导入"; primary:true; enabled:!bridge.busy; Layout.alignment:Qt.AlignRight; onClicked:bridge.perform("mediaImport",importForm.values) }
        }
    }
    AppDialog {
        id:discoverDialog; title:"发现历史回放"; anchors.centerIn:parent; width:430; modal:true
        ColumnLayout { width:parent.width; Label { textFormat: Text.PlainText; text:"直播间号" } RoundedField { id:discoverRoom; Layout.fillWidth:true; Accessible.name:"直播间号" } ActionButton { text:"查找回放"; symbol:"search"; primary:true; enabled:!bridge.busy&&discoverRoom.text.trim().length>0; onClicked:{ bridge.perform("discover",{room_id:discoverRoom.text}); discoverDialog.close() } } }
    }
    AppDialog {
        id:replayDialog; title:"选择回放"; anchors.centerIn:parent; width:Math.min(650,pages.width-20); height:Math.min(500,pages.height-20); modal:true
        ListView { anchors.fill:parent; model:bridge.workspace.replays; clip:true; ScrollBar.vertical:ScrollBar {} delegate:ListEntry { required property var modelData; width:ListView.view.width; height:62; text:(modelData.title||modelData.id)+" · "+(modelData.length||"未知时长"); enabled:!bridge.busy; onClicked:bridge.perform("replayDownload",modelData) } }
    }
    AppDialog {
        id:qrDialog; title:pages.qrAccount?"重新扫码登录":"添加账号"; anchors.centerIn:parent; width:360; modal:true; onClosed:bridge.cancelQr()
        ColumnLayout {
            width:parent.width
            Label { textFormat: Text.PlainText; text:"使用 Bilibili App 扫码"; Layout.alignment:Qt.AlignHCenter }
            Image { source:bridge.qr.image; Layout.preferredWidth:270; Layout.preferredHeight:270; Layout.alignment:Qt.AlignHCenter; fillMode:Image.PreserveAspectFit; smooth:false; cache:false }
            Label { textFormat: Text.PlainText; text:bridge.qr.message; Layout.fillWidth:true; wrapMode:Text.Wrap; horizontalAlignment:Text.AlignHCenter }
            RowLayout { Layout.alignment:Qt.AlignHCenter; ActionButton { text:"刷新二维码"; enabled:bridge.qr.state!=="loading"; onClicked:bridge.startQr(pages.qrAccount) } ActionButton { text:"取消"; onClicked:qrDialog.close() } }
        }
    }
    AppDialog { id:coverDialog; title:"候选画面"; anchors.centerIn:parent; width:Math.min(pages.width-20,900); height:Math.min(pages.height-20,620); modal:true; Image { anchors.fill:parent; source:bridge.workspace.clip.candidates||""; fillMode:Image.PreserveAspectFit; asynchronous:true } }
    AppDialog { id:notice; title:"提示"; anchors.centerIn:parent; width:440; modal:true; standardButtons:Dialog.Ok; onOpened:standardButton(Dialog.Ok).text="确定"; Label { textFormat: Text.PlainText; id:noticeText; width:parent.width; wrapMode:Text.Wrap } }
    GlossaryDialog { id:glossaryPanel; parent:pages; anchors.centerIn:parent; width:Math.min(pages.width-10,1040); height:Math.min(pages.height-10,710) }
}
