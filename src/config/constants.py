"""Default values shared by CLI, GUI, and core pipeline."""

DEFAULT_OUTPUT_DIR = "./output"
DEFAULT_WHISPER_MODEL = "large-v3"
DEFAULT_GPT_MODEL = "mimo-v2.5"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_SUBTITLE_FORMAT = "none"

DEFAULT_SETTINGS_DIRNAME = ".video-notes-ai"
DEFAULT_SETTINGS_FILENAME = "settings.json"

STYLE_MAP = {
    "concise": "简洁",
    "detailed": "详细",
    "tutorial": "教程风格",
    "notes": "以学习笔记形式",
}