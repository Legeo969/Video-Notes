> **Product UI v6 / app v1.4.0** 鈥?A calmer desktop workspace with a new application bar, compact navigation, refined five-panel layouts, and persisted light/dark appearance. See `docs/UI-Architecture-v6.md`.

# video-notes-ai

灏嗚棰戯紙鍦ㄧ嚎閾炬帴鎴栨湰鍦版枃浠讹級鑷姩杞綍涓虹粨鏋勫寲瀛︿範绗旇锛屽綊妗ｅ埌 Obsidian銆?
**妗岄潰搴旂敤**锛歍auri 2 + Svelte 5 + Rust | **寮曟搸**锛歅ython 3.10+ | 褰撳墠妗岄潰鐗堟湰锛?*V1.4.0**

浜у搧绾т换鍔″唴鏍歌鏄庯細[`docs/Product-Architecture-v4.md`](docs/Product-Architecture-v4.md)
鏈疆楠岃瘉缁撴灉锛歔`docs/Validation-Report-v4.md`](docs/Validation-Report-v4.md)

## 浜у搧褰㈡€?
```
瀵煎叆瑙嗛 / 璇剧▼
  鈫?楂樿川閲忚浆褰曪紙faster-whisper锛?  鈫?鍙€夛細OCR 鏂囧瓧璇嗗埆 + 瑙嗚鐞嗚В
  鈫?AI 鐢熸垚缁撴瀯鍖栫瑪璁帮紙甯︽埅鍥惧紩鐢級
  鈫?褰掓。鍒?Obsidian vault
  鈫?鍦?Obsidian 涓畬鎴愮煡璇嗙鐞?```

## 鍔熻兘鐗规€?
- **妗岄潰搴旂敤**锛歍auri 鍘熺敓妗岄潰澹?+ Svelte 5 鍝嶅簲寮忕晫闈?- **CLI 妯″紡**锛氬畬鏁村懡浠よ鍙傛暟锛岄€傚悎鎵归噺澶勭悊鍜岃剼鏈泦鎴?- **鍦ㄧ嚎瑙嗛**锛氭敮鎸?YouTube銆丅ilibili 绛夊钩鍙帮紙yt-dlp锛?- **鏈湴鏂囦欢**锛氭敮鎸?mp4 / mkv / avi / mov / flv / webm 绛夋牸寮?- **AI 杞綍**锛歠aster-whisper锛岃嚜鍔?GPU 鍔犻€燂紝CUDA 澶辫触闄嶇骇 CPU
- **妯℃澘鍖栫瑪璁?*锛? 涓唴缃ā鏉匡紙瀛︿範 / 浼氳 / 鏁欑▼ / 闈㈣瘯绛夛級锛屾敮鎸佽嚜瀹氫箟
- **璇剧▼鍚堥泦**锛氭枃浠跺す/playlist 瀵煎叆锛岃嚜鍔ㄧ粍缁囪棰戯紝鐢熸垚璇剧▼鎬昏
- **鏂偣缁窇**锛氫换鎰忛樁娈典腑鏂悗鍙仮澶嶏紝涓嶉噸澶嶅鐞?- **浠诲姟闃熷垪**锛歟nqueue / status / resume / cancel 瀹屾暣鐢熷懡鍛ㄦ湡
- **澶氫緵搴斿晢閰嶇疆**锛氭敮鎸佸涓?LLM profile锛岀嫭绔嬮厤缃瑪璁扮敓鎴愬拰瑙嗚璇嗗埆
- **瑙嗚璇嗗埆**锛氬彲閫夊叧閿抚瑙嗚鍒嗘瀽 + OCR 鏂囧瓧鎻愬彇
- **瀛楀箷瀵煎嚭**锛歋RT / ASS / 绾枃鏈牸寮?- **Obsidian 褰掓。**锛氳嚜鍔ㄥ皢绗旇澶嶅埗鍒?Obsidian vault

## 蹇€熷紑濮?
### 鐜瑕佹眰
- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html)锛堝繀椤伙紝鐢ㄤ簬闊抽鎻愬彇鍜岃棰戞埅鍥撅級
- Node.js 18+ + Rust锛堜粎寮€鍙戯紝杩愯鏃犻渶锛?- NVIDIA GPU锛堝彲閫夛紝鏈?CUDA 鏃惰嚜鍔ㄥ惎鐢?GPU 鍔犻€燂級

### 寮€鍙戣繍琛?
```bash
# 1. 瀹夎 Python 渚濊禆
pip install -e ".[vision]"

# 2. 瀹夎鍓嶇渚濊禆
cd desktop && npm install

# 3. 鍚姩 Tauri 寮€鍙戞ā寮?npm run tauri dev
```

### CLI 妯″紡锛堟棤闇€ Tauri锛?
```bash
# 澶勭悊瑙嗛
python main.py "https://www.bilibili.com/video/BVxxx"
python main.py "D:\videos\lecture.mp4" --title "璇剧▼绗旇" --template study

# 鍚敤瑙嗚璇嗗埆
python main.py <url> --vision --vision-model qwen-vl-plus

# 浠诲姟绠＄悊
python main.py --job-list
python main.py --job-status 1
python main.py --resume 1
```

## 鏋舵瀯

```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹? Tauri Desktop Shell (Rust)                         鈹?鈹? 鈹溾攢 Svelte 5 UI (Process / Tasks / Notes / ...)    鈹?鈹? 鈹斺攢 Engine Manager 鈥?Python 渚ц溅杩涚▼绠＄悊            鈹?鈹?      鈹?Content-Length framed JSON-RPC 2.0 over stdio鈹?鈹溾攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹? Python Engine                                      鈹?鈹? 鈹溾攢 src/api/     鈥?JSON-RPC 2.0 API 灞?             鈹?鈹? 鈹溾攢 src/application/ 鈥?绠＄嚎缂栨帓 / 鏈嶅姟 / LLM        鈹?鈹? 鈹溾攢 src/domain/  鈥?棰嗗煙妯″瀷 / 鎺ュ彛                   鈹?鈹? 鈹斺攢 src/infrastructure/ 鈥?DB / 杞綍 / 瑙嗛 / Provider鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?```

## 椤圭洰缁撴瀯

```
video-notes-ai/
鈹溾攢鈹€ main.py                     # CLI / Engine 鍏ュ彛
鈹溾攢鈹€ src/
鈹?  鈹溾攢鈹€ api/                    # JSON-RPC 2.0 寮曟搸 API
鈹?  鈹?  鈹溾攢鈹€ protocol/           # Content-Length 甯у崗璁?/ dispatcher
鈹?  鈹?  鈹溾攢鈹€ dto/                # Pydantic v2 鏁版嵁浼犺緭瀵硅薄
鈹?  鈹?  鈹溾攢鈹€ handlers/           # RPC 鏂规硶澶勭悊鍣?鈹?  鈹?  鈹溾攢鈹€ event_journal.py    # 鎸佷箙鍖栦簨浠舵棩蹇?鈹?  鈹?  鈹斺攢鈹€ server.py           # 鏈嶅姟鍣ㄤ富寰幆
鈹?  鈹溾攢鈹€ engine.py               # Tauri sidecar 鍏ュ彛
鈹?  鈹溾攢鈹€ application/            # 搴旂敤灞?鈹?  鈹?  鈹溾攢鈹€ pipeline/           # 瑙嗛澶勭悊绠＄嚎 + 8 涓樁娈?鈹?  鈹?  鈹溾攢鈹€ services/           # Orchestrator / JobQueue / TaskSupervisor
鈹?  鈹?  鈹溾攢鈹€ llm/                # MAP / REDUCE 绗旇鐢熸垚
鈹?  鈹?  鈹溾攢鈹€ vision/             # 瑙嗚鐞嗚В
鈹?  鈹?  鈹溾攢鈹€ notes/              # 绗旇鐢熸垚缂栨帓
鈹?  鈹?  鈹斺攢鈹€ collections/        # 鍚堥泦绠＄悊
鈹?  鈹溾攢鈹€ domain/                 # 棰嗗煙灞?鈹?  鈹?  鈹溾攢鈹€ models/             # 棰嗗煙瀹炰綋
鈹?  鈹?  鈹溾攢鈹€ interfaces/         # 绾帴鍙ｏ紙Ports锛?鈹?  鈹?  鈹斺攢鈹€ types.py            # PipelineRequest / PipelineResult
鈹?  鈹溾攢鈹€ infrastructure/         # 鍩虹璁炬柦
鈹?  鈹?  鈹溾攢鈹€ db/                 # SQLite repositories
鈹?  鈹?  鈹溾攢鈹€ transcription/      # faster-whisper / whisper.cpp
鈹?  鈹?  鈹溾攢鈹€ video/              # yt-dlp / FFmpeg / OCR
鈹?  鈹?  鈹溾攢鈹€ providers/          # OpenAI / DashScope / Mimo
鈹?  鈹?  鈹溾攢鈹€ artifacts/          # Obsidian 褰掓。
鈹?  鈹?  鈹斺攢鈹€ system/             # Component Manager
鈹?  鈹溾攢鈹€ app/                    # CLI 鍏ュ彛
鈹?  鈹溾攢鈹€ config/                 # 閰嶇疆绠＄悊
鈹?  鈹斺攢鈹€ utils/                  # 宸ュ叿鍑芥暟
鈹溾攢鈹€ desktop/
鈹?  鈹溾攢鈹€ src/                    # Svelte 5 鍓嶇
鈹?  鈹?  鈹溾攢鈹€ pages/              # 5 椤甸潰锛圥rocess/Tasks/Notes/Settings/Collections锛?鈹?  鈹?  鈹斺攢鈹€ lib/                # API 閫傞厤鍣?/ stores / 缁勪欢
鈹?  鈹溾攢鈹€ src-tauri/              # Rust 鍚庣
鈹?  鈹?  鈹溾攢鈹€ src/
鈹?  鈹?  鈹?  鈹溾攢鈹€ engine_manager.rs  # Python 渚ц溅绠＄悊
鈹?  鈹?  鈹?  鈹溾攢鈹€ protocol.rs        # Content-Length 甯у崗璁?鈹?  鈹?  鈹?  鈹斺攢鈹€ process_tree.rs    # Windows Job Object
鈹?  鈹?  鈹斺攢鈹€ tauri.conf.json
鈹?  鈹斺攢鈹€ package.json
鈹溾攢鈹€ runtime/                    # 缁勪欢娓呭崟
鈹溾攢鈹€ src/application/notes/templates/ # 8 濂楀唴缃?YAML 妯℃澘
鈹溾攢鈹€ tests/                      # ~47 涓祴璇曟枃浠?鈹?  鈹斺攢鈹€ test_engine_api_smoke.py  # 寮曟搸 API 鍐掔儫娴嬭瘯
鈹溾攢鈹€ docs/                       # 璁捐鏂囨。
鈹斺攢鈹€ output/                     # 鐢熸垚鐨勭瑪璁?```

## 鎶€鏈爤

| 灞?| 鎶€鏈?|
|------|--------|
| 妗岄潰澹?| Tauri 2 + Rust |
| 鍓嶇 | Svelte 5 + TypeScript + Vite |
| 涓氬姟寮曟搸 | Python 3.10+ |
| API 鍗忚 | JSON-RPC 2.0 over stdin/stdout (Content-Length framed) |
| 杞綍 | faster-whisper (CTranslate2) |
| LLM | OpenAI 鍏煎 API锛坢imo / 闃块噷浜戠櫨鐐?/ 鑷畾涔夛級 |
| 瑙嗚 | 澶氭ā鎬?LLM + PaddleOCR |
| 涓嬭浇 | yt-dlp |
| 瑙嗛澶勭悊 | FFmpeg |
| 鎸佷箙鍖?| SQLite + 鐗堟湰鍖栬縼绉?|
| 杩涚▼绠＄悊 | Windows Job Object |
| 娴嬭瘯 | pytest锛堟椿璺冨悗绔浠?618 passed锛?|

## CLI 鍙傛暟閫熸煡

### 鏍稿績澶勭悊
| 鍙傛暟 | 璇存槑 |
|------|------|
| `input` | 瑙嗛 URL 鎴栨湰鍦版枃浠惰矾寰?|
| `--output` | 杈撳嚭鐩綍锛岄粯璁?`./output` |
| `--title` | 瑙嗛鏍囬锛堢暀绌鸿嚜鍔ㄦ娴嬶級 |
| `--model` | Whisper 妯″瀷锛岄粯璁?`large-v3` |
| `--model-dir` | 鏈湴妯″瀷鐩綍 |
| `--gpt-model` | AI 妯″瀷鍚嶇О |
| `--api-key` | API Key |
| `--base-url` | 鑷畾涔?API 绔偣 |
| `--temperature` | AI 娓╁害 0.0鈥?.0锛岄粯璁?0.3 |
| `--frame-interval` | 鎴浘闂撮殧锛堢锛夛紝0 绂佺敤 |
| `--frame-mode` | 鎴浘妯″紡锛歛uto / fixed / disabled |
| `--max-frames` | 鑷姩鎴浘鏈€澶氫繚鐣欐暟 |

### 瑙嗚璇嗗埆
| 鍙傛暟 | 璇存槑 |
|------|------|
| `--vision` | 鍚敤瑙嗚璇嗗埆 |
| `--vision-model` | 瑙嗚妯″瀷鍚嶇О |
| `--ocr` | 鍚敤 OCR 鏂囧瓧璇嗗埆 |

### 浠诲姟绠＄悊
| 鍙傛暟 | 璇存槑 |
|------|------|
| `--resume <id>` | 鏂偣缁窇 |
| `--job-list` | 鏌ョ湅鎵€鏈変换鍔?|
| `--job-status <id>` | 鏌ョ湅浠诲姟璇︽儏 |

### 妯℃澘
| 鍙傛暟 | 璇存槑 |
|------|------|
| `--template <id/path>` | 鎸囧畾绗旇妯℃澘 |
| `--template-list` | 鍒楀嚭鎵€鏈夊彲鐢ㄦā鏉?|
| `--template-preview <id>` | 棰勮妯℃澘璇︽儏 |
| `--template-recommend <q>` | 鏅鸿兘鎺ㄨ崘妯℃澘 |

### 鍚堥泦
| 鍙傛暟 | 璇存槑 |
|------|------|
| `--collection <id>` | 鍏宠仈鍒版寚瀹氬悎闆?|
| `--collection-create <name>` | 鍒涘缓鍚堥泦 |
| `--collection-list` | 鍒楀嚭鎵€鏈夊悎闆?|
| `--collection-status <id>` | 鍚堥泦鐘舵€?|
| `--collection-export <id>` | 瀵煎嚭鍚堥泦 |
| `--folder <path>` | 浠庢枃浠跺す瀵煎叆 |
| `--playlist <url>` | 浠?playlist 瀵煎叆 |

### 鍏朵粬
| 鍙傛暟 | 璇存槑 |
|------|------|
| `--doctor` | 杩愯鐜璇婃柇 |
| `--issue-bundle` | 鐢熸垚闂鎶ュ憡鍖?|
| `--with-citations` | 绗旇涓檮甯︽潵婧愬紩鐢?|
| `--smart-summary` | 闀挎枃鏅鸿兘鎬荤粨 |

## 鐜鍙橀噺

鍦?`.env` 鏂囦欢涓厤缃細

```bash
MIMO_API_KEY=your-mimo-key
DASHSCOPE_API_KEY=your-dashscope-key
```

## FFmpeg

FFmpeg 鏄繀椤荤殑绯荤粺渚濊禆銆傝嚜鍔ㄦ壂鎻?PATH 鍙婂父瑙佸畨瑁呰矾寰勩€傚鏈畨瑁咃細

```bash
winget install Gyan.FFmpeg
```

## 渚濊禆鎷嗗垎

```bash
pip install -e .               # 鏍稿績锛圕LI + API锛?pip install -e ".[vision]"     # + 瑙嗚澧炲己
pip install -e ".[ocr]"        # + OCR锛圥addleOCR CPU锛?pip install -e ".[ocr-gpu]"    # + OCR锛圥addleOCR GPU锛?pip install -e ".[cuda]"       # + CUDA 鍔犻€?pip install -e ".[dev]"        # + 寮€鍙戝伐鍏?```
