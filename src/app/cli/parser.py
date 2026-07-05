import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 视频笔记生成工具")
    parser.add_argument("input", nargs="?", default=None, help="视频 URL 或本地视频文件路径（批处理模式下可省略）")
    parser.add_argument("--batch", "--file-list", dest="batch_file", default=None,
                        help="批处理模式：指定一个文本文件，每行一个视频 URL 或本地文件路径")
    parser.add_argument("--model", default="large-v3", help="faster-whisper 模型大小 (tiny/base/small/medium/large-v2/large-v3)，默认 large-v3")
    parser.add_argument("--model-dir", help="Whisper 模型目录路径，默认 %%LOCALAPPDATA%%\\VideoCaptioner\\AppData\\models")
    parser.add_argument("--output", default="./output", help="输出目录")
    parser.add_argument("--title", help="视频标题（可选）")
    parser.add_argument("--lang", help="转录语言代码 (zh/en/ja 等)，默认自动检测")
    parser.add_argument("--gpt-model", default="mimo-v2.5", help="AI 模型名称，默认 mimo-v2.5")
    parser.add_argument("--api-key", help="API Key，覆盖 .env 中的 MIMO_API_KEY / DASHSCOPE_API_KEY")
    parser.add_argument("--base-url", help="API Base URL，覆盖默认端点")
    parser.add_argument("--template", help="笔记模板 ID（auto/default/study/meeting/...）或 Markdown 模板文件路径")
    parser.add_argument("--template-list", dest="template_list", action="store_true",
                        help="列出所有可用模板")
    parser.add_argument("--template-preview", dest="template_preview",
                        help="预览模板详情和 prompt（传入模板 ID 或 YAML 文件路径）")
    parser.add_argument("--template-validate", dest="template_validate",
                        help="校验模板 YAML 文件的合法性（传入 YAML 文件路径）")
    parser.add_argument("--template-recommend", dest="template_recommend",
                        help="根据标题或描述推荐最佳模板（传入描述文本）")
    parser.add_argument("--collection", dest="collection_id",
                        help="将任务归入指定集合（批处理模式下自动传入每个任务）")
    parser.add_argument("--collection-create", dest="collection_create",
                        help="创建新集合（传入标题）")
    parser.add_argument("--collection-type", default="course",
                        help="集合类型 (course|playlist|folder|project)，默认 course")
    parser.add_argument("--collection-list", dest="collection_list", action="store_true",
                        help="列出所有集合")
    parser.add_argument("--collection-status", dest="collection_status",
                        help="查看指定集合的状态（传入 collection_id 或标题）")
    parser.add_argument("--collection-add-job", nargs=2, dest="collection_add_job",
                        metavar=("COLLECTION_ID", "RUN_ID"),
                        help="将已有任务加入集合")
    parser.add_argument("--collection-overview", dest="collection_overview",
                        help="生成集合总览 Markdown（传入 collection_id 或标题）")
    parser.add_argument("--folder", dest="folder_path",
                        help="从本地文件夹批量导入音视频文件")
    parser.add_argument("--playlist", dest="playlist_url",
                        help="从播放列表 URL 批量导入（YouTube/B站 playlist）")
    parser.add_argument("--recursive", action="store_true",
                        help="递归扫描子文件夹（配合 --folder 使用）")
    parser.add_argument("--sort", choices=["name", "mtime", "natural"], default="natural",
                        help="文件排序方式 (name|mtime|natural)，默认 natural（配合 --folder 使用）")
    parser.add_argument("--collection-export", dest="collection_export",
                        help="导出集合到规范目录结构（传入 collection_id 或标题）")
    parser.add_argument("--frame-interval", type=int, default=30, help="视频帧提取保底间隔（秒），0 禁用截图")
    parser.add_argument("--frame-mode", choices=["auto", "fixed", "disabled"], default="auto",
                        help="截图模式: auto=场景/字幕智能抽帧, fixed=固定间隔, disabled=禁用截图")
    parser.add_argument("--max-frames", type=int, default=30, help="自动截图模式下最多保留的截图数量，默认 30")
    parser.add_argument("--obsidian-vault", help="Obsidian vault 目录路径，笔记将自动归档到此 vault 的 video-notes/ 子目录中")
    parser.add_argument("--subtitle-format", choices=["srt", "ass", "txt", "none"],
                        default="none", help="字幕导出格式: srt / ass / txt / none（默认 none）")
    parser.add_argument("--temperature", type=float, default=0.3,
                        help="AI 生成温度（0.0-2.0），默认 0.3")
    parser.add_argument("--detail-level", choices=["concise", "standard", "detailed"],
                        help="内容详细度: concise=精简, standard=标准, detailed=详细")
    parser.add_argument("--style", choices=["concise", "detailed", "tutorial", "notes"],
                        help=argparse.SUPPRESS)  # 旧参数，保留兼容性
    parser.add_argument("--smart-summary", dest="smart_summary", action="store_true",
                        help="启用长文智能总结（仅在转录文本分为多段时生效）")
    parser.add_argument("--ocr", dest="ocr_enabled", action="store_true",
                        help="启用关键帧 OCR 文字识别")
    parser.add_argument("--check-ocr", dest="check_ocr", action="store_true",
                        help="检查 PaddleOCR 运行时是否可导入")
    parser.add_argument("--doctor", dest="doctor", action="store_true",
                        help="运行环境诊断，检查依赖、API 连接和配置")
    parser.add_argument("--issue-bundle", dest="issue_bundle", action="store_true",
                        help="生成问题报告包 (issue bundle)，收集诊断信息用于反馈 bug")
    parser.add_argument("--no-plugins", action="store_true", help="禁用插件加载")
    parser.add_argument("--with-citations", dest="with_citations", action="store_true",
                        help="在笔记中生成来源引用（时间戳、转写片段、截图）")
    parser.add_argument("--reindex-job", dest="reindex_job", type=int, default=None,
                        help="为指定任务 ID 重建 provenance 索引")
    parser.add_argument("--reindex-all", dest="reindex_all", action="store_true",
                        help="为所有已完成任务重建 provenance 索引")
    parser.add_argument("--citation-preview", dest="citation_preview", type=int, default=None,
                        help="预览指定任务的来源引用（不写入文件）")
    parser.add_argument("--resume", dest="resume", type=int, default=None,
                        help="断点续跑：指定任务 ID 从失败点恢复（用 --job-list 查看任务列表）")
    parser.add_argument("--job-list", dest="job_list", action="store_true",
                        help="列出所有历史任务")
    parser.add_argument("--job-status", dest="job_status", default=None,
                        help="查看指定任务 ID 的详细状态")
    parser.add_argument("--bilibili-cookies", dest="bilibili_cookies",
                        help="Bilibili cookies.txt file path for videos that require login cookies")
    return parser
