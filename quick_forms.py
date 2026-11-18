"""Qt 表单字段及保存前校验，未显示的业务配置始终沿用已保存值。"""

import os
from dataclasses import replace
from pathlib import Path

import app as core


def field(key, label, kind="text", choices=None):
    result = {"key": key, "label": label, "kind": kind}
    if choices is not None:
        result["choices"] = [{"value": value, "label": text} for value, text in choices.items()]
    return result


SETTINGS_FIELDS = [
    [field("recordings_dir", "录播保存目录", "folder"), field("clips_dir", "切片保存目录", "folder"),
     field("auto_slice", "录播结束后自动转写、总结、切片并投稿", "bool"),
     field("publish_visibility", "手动投稿可见性", "choice", core.PUBLISH_VISIBILITIES)],
    [field("render_font_name", "字体方案 / 字体名称"), field("render_font_path", "导入字体文件", "font"),
     field("subtitle_burn_enabled", "将字幕压入视频画面", "bool"),
     field("subtitle_font_size", "字幕字号（12～240）", "int"), field("subtitle_color", "字幕文字颜色", "color"),
     field("subtitle_outline_color", "字幕描边颜色", "color"), field("subtitle_outline_width", "字幕描边宽度（0～30）", "int"),
     field("subtitle_alignment", "字幕位置", "choice", core.SUBTITLE_ALIGNMENTS),
     field("subtitle_margin_v", "字幕垂直边距（0～1250）", "int"), field("subtitle_margin_l", "字幕左边距（0～1250）", "int"),
     field("subtitle_margin_r", "字幕右边距（0～1250）", "int"), field("cover_text_enabled", "在封面上绘制短文案", "bool"),
     field("cover_font_size", "封面字号（24～180）", "int"), field("cover_primary_color", "封面主色", "color"),
     field("cover_accent_color", "封面强调色", "color"), field("cover_outline_color", "封面描边颜色", "color"),
     field("cover_outline_width", "封面描边宽度（0～20）", "int"), field("cover_shadow_x", "水平阴影（-20～20）", "int"),
     field("cover_shadow_y", "垂直阴影（-20～20）", "int"), field("cover_position", "封面文字位置", "choice", core.COVER_POSITIONS)],
    [field("dashscope_api_key", "阿里云 ASR Key", "secret"), field("dashscope_model", "ASR 识别模型", "asr_model"),
     field("llm_endpoint", "AI API 地址"),
     field("llm_api_key", "AI API Key", "secret"), field("llm_model", "AI 模型", "model")],
    [field("mcp_enabled", "启用联网查词", "bool"), field("brave_api_key", "Brave API Key", "secret"),
     field("tavily_api_key", "Tavily API Key", "secret"), field("mcp_max_tool_rounds", "搜索轮次（1～20）", "int"),
     field("ffmpeg_path", "FFmpeg", "file"), field("ffprobe_path", "FFprobe", "file")],
]
ROOM_FIELDS = [field("name", "显示名称"), field("uid", "主播 UID"), field("replay_source", "回放来源（可选）"),
               field("llm_model", "专用模型（可选）"), field("account_id", "录制与下载账号", "account"),
               field("auto_record", "自动录制", "bool"), field("auto_asr", "转写与回顾", "bool"),
               field("auto_slice", "生成切片", "bool"), field("recap_template", "回顾模板（可选）", "multiline")]
UPLOAD_FIELDS = [field("title", "标题"), field("tags", "标签（逗号分隔）"), field("tid", "投稿分区 ID", "int"), field("description", "简介", "multiline")]


def settings_values(settings):
    return {item["key"]: getattr(settings, item["key"]) for group in SETTINGS_FIELDS for item in group}


def validated_settings(current, values, model_credentials, models, asr_credentials, asr_models):
    settings = replace(current)
    for group in SETTINGS_FIELDS:
        for item in group:
            key, kind = item["key"], item["kind"]
            if key not in values:
                raise ValueError("缺少设置：" + item["label"])
            value = values[key]
            if kind == "bool":
                if not isinstance(value, bool):
                    raise ValueError(item["label"] + "必须为开关值")
            elif kind == "int":
                try:
                    value = int(str(value).strip())
                except ValueError as exc:
                    raise ValueError(item["label"] + "必须为整数") from exc
            elif kind == "choice":
                match = next((choice["value"] for choice in item["choices"] if str(choice["value"]) == str(value)), None)
                if match is None:
                    raise ValueError(item["label"] + "无效")
                value = match
            else:
                value = str(value).strip()
            if kind == "color":
                value = core.normalize_hex_color(value, "")
                if not value:
                    raise ValueError(item["label"] + "必须是 #RRGGBB 格式")
            setattr(settings, key, value)
    for key, low, high in (("subtitle_font_size", 12, 240), ("subtitle_outline_width", 0, 30),
                           ("subtitle_margin_v", 0, 1250), ("subtitle_margin_l", 0, 1250), ("subtitle_margin_r", 0, 1250),
                           ("cover_font_size", 24, 180), ("cover_outline_width", 0, 20), ("cover_shadow_x", -20, 20),
                           ("cover_shadow_y", -20, 20), ("mcp_max_tool_rounds", 1, 20)):
        if not low <= getattr(settings, key) <= high:
            raise ValueError(f"{key} 应在 {low}～{high} 之间")
    credentials = (settings.llm_endpoint, settings.llm_api_key)
    if settings.llm_api_key and (credentials != model_credentials or not settings.llm_model or settings.llm_model not in models):
        raise ValueError("请先连接当前 AI 接口，并从返回列表选择模型。")
    if not settings.dashscope_model:
        raise ValueError("请选择 ASR 识别模型。")
    settings.dashscope_model = core.normalize_dashscope_model(settings.dashscope_model)
    if settings.dashscope_model not in {core.normalize_dashscope_model(current.dashscope_model), core.DEFAULT_DASHSCOPE_MODEL}:
        if (settings.dashscope_api_key != asr_credentials or settings.dashscope_model not in asr_models
                or not core.is_dashscope_filetrans_model(settings.dashscope_model)):
            raise ValueError("请先加载当前 Key 的 ASR 模型，并从返回列表选择识别模型。")
    if settings.llm_endpoint:
        settings.llm_endpoint = core.normalize_llm_endpoint(settings.llm_endpoint)
    if settings.llm_api_key:
        settings.llm_provider = "openai"
    for key in ("brave_api_key", "tavily_api_key"):
        if any(char.isspace() or ord(char) < 32 for char in getattr(settings, key)):
            raise ValueError("搜索 API Key 不能包含空白或控制字符")
    font_name = core.normalize_font_name(settings.render_font_name, "")
    if not font_name:
        raise ValueError("字体名称为空或包含无效字符")
    if settings.render_font_path:
        font_path = Path(os.path.expandvars(settings.render_font_path)).expanduser()
        if not font_path.is_absolute():
            font_path = settings.data_path / font_path
        if not font_path.is_file() or font_path.suffix.lower() not in core.FONT_FILE_SUFFIXES:
            raise ValueError("字体文件不存在或格式不受支持")
        font_name = core.font_family_name(font_path)
        if not font_name:
            raise ValueError("无法读取字体的家族名称")
    settings.render_font_name = font_name
    settings.transcription_provider = "dashscope"
    settings.settings_version = 11
    settings.ffmpeg_path = settings.ffmpeg_path or "ffmpeg"
    settings.ffprobe_path = settings.ffprobe_path or "ffprobe"
    for key, fallback in (("recordings_dir", "recordings"), ("clips_dir", "clips")):
        folder = core._resolve_storage_dir(settings.base_dir, getattr(settings, key), fallback)
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            raise ValueError("保存位置必须是文件夹")
        setattr(settings, key, str(folder))
    return settings
