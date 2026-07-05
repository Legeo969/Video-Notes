# Video Notes AI 浜у搧绾ф灦鏋?v4

> 鐘舵€侊細鏈疆閲嶆瀯鍚庣殑鍙墽琛屽熀绾?
> 閫傜敤鑼冨洿锛歍auri 2 + Svelte 5 妗岄潰绔€丳ython Sidecar銆佺湡瀹炶棰戝鐞嗕换鍔?
> 鏍稿績鍘熷垯锛氫笉閲嶅啓 Whisper/OCR/LLM 涓氬姟绠＄嚎锛屽彧鏀舵暃浠诲姟鐢熷懡鍛ㄦ湡銆両PC銆佹寔涔呭寲涓庣晫闈㈢姸鎬佽竟鐣?
## 1. 鏈疆瑙ｅ喅鐨勯棶棰?
鏃у疄鐜扮殑涓昏椋庨櫓涓嶆槸绠楁硶鏈韩锛岃€屾槸浠诲姟鎵ц瀛樺湪澶氬叆鍙ｃ€佸浠界姸鎬佸拰涓嶅畬鏁存仮澶嶄俊鎭細

- 鏂板缓銆佺户缁拰閲嶈瘯鍒嗗埆鍒涘缓绾跨▼锛屽鏄撳嚭鐜伴噸澶?worker锛?- SQLite 鍙繚瀛樹换鍔＄粨鏋滃拰闃舵锛屾湭淇濆瓨瀹屾暣闈炲瘑閽ュ弬鏁帮紱
- 缁х画浠诲姟鍙兘閫€鍥炲綋鍓嶉粯璁ゆā鍨嬨€佹ā鏉裤€佽瑙夊弬鏁板拰杈撳嚭璁剧疆锛?- 鍓嶇閮ㄥ垎璋冪敤缁曡繃缁熶竴 `engine_call`锛屾寜閽弬鏁颁笌鍚庣濂戠害涓嶄竴鑷达紱
- Python 杩涘害鏈舰鎴愨€滄寔涔呭寲浜嬩欢 + 瀹炴椂鎺ㄩ€佲€濈殑缁熶竴閾捐矾锛?- Rust 鍦ㄧ瓑寰呴暱 RPC 鏃舵寔鏈夊紩鎿庣敓鍛藉懆鏈熼攣锛屾殏鍋滃拰鏌ヨ鍙兘琚樆濉烇紱
- Python `stderr` 鏈寔缁秷璐规椂瀛樺湪绠￠亾鍐欐弧鍚庝晶杞﹀崱浣忕殑椋庨櫓锛?- 寮曟搸鍚姩闃舵瀵煎叆鍙€夐噸渚濊禆锛岀己灏戞煇涓粍浠跺彲鑳藉鑷存暣涓闈㈠３鏃犳硶鍚姩锛?- 妯℃澘璧勪骇缂哄け锛屾ā鏉垮垪琛ㄣ€佹帹鑽愬拰棰勮鏁存潯鍔熻兘涓嶅彲鐢紱
- 璁剧疆椤典笌 Python API 鐨勪緵搴斿晢銆佹ā鍨嬪拰璇婃柇濂戠害涓嶄竴鑷淬€?
v4 灏嗚繖浜涢棶棰樻敹鏁涗负涓€濂楀彲闀挎湡婕旇繘鐨勪骇鍝侀鏋躲€?
## 2. 鎬讳綋鏋舵瀯

```mermaid
flowchart TB
    UI[Svelte 5 UI\nProcess / Tasks / Notes / Settings]
    STORE[Shared Stores\nJobs / Engine State]
    RUST[Rust Desktop Core\nEngine lifecycle / IPC / Process tree]
    API[Python JSON-RPC API\nHandlers / DTO / Event framing]
    SUP[TaskSupervisor\n鍞竴 worker 鎵€鏈夎€匽
    QUEUE[JobQueue\n鐘舵€佹満 / 鎺у埗浠ょ墝 / 杩涘害]
    PIPE[PipelineOrchestrator\n鐜版湁鐪熷疄澶勭悊绠＄嚎]
    REPO[Repositories\nSQLite v14]
    MANIFEST[Stage Manifest + Artifacts]
    PROVIDERS[Whisper / OCR / Vision / LLM]

    UI <--> STORE
    STORE -->|Tauri invoke| RUST
    RUST -->|Content-Length JSON-RPC| API
    API --> SUP
    SUP --> QUEUE
    SUP --> PIPE
    PIPE --> PROVIDERS
    PIPE --> MANIFEST
    QUEUE --> REPO
    API --> REPO
    API -->|job.progress event| RUST
    RUST -->|Tauri event| STORE
```

鍞竴涓氬姟璋冪敤閾句负锛?
```text
Svelte 鈫?Tauri command 鈫?Rust EngineClient 鈫?Python JSON-RPC handler
       鈫?TaskSupervisor 鈫?JobQueue / PipelineOrchestrator
```

鍞竴杩涘害閾句负锛?
```text
Pipeline stage callback
鈫?JobQueue 鎸佷箙鍖?progress / heartbeat
鈫?EventJournal 鍐欏叆 job_events
鈫?Python job.progress 閫氱煡
鈫?Rust 杞彂 params
鈫?Svelte jobs store
鈫?Process / Tasks 椤甸潰鍚屾鏇存柊
```

## 3. 鍒嗗眰鑱岃矗

### 3.1 Svelte 琛ㄧ幇灞?
鑱岃矗锛?
- 鏀堕泦浠诲姟鍙傛暟锛?- 鏄剧ず浠诲姟鍒楄〃銆侀樁娈点€佽繘搴︺€侀敊璇拰浜х墿锛?- 鍙戣捣寮€濮嬨€佹殏鍋溿€佸彇娑堛€佺户缁€佷粠澶撮噸璇曪紱
- 璁㈤槄缁熶竴 `job.progress` 浜嬩欢锛?- 閫氳繃鍏变韩 jobs store 椹卞姩澶氫釜椤甸潰銆?
绂佹锛?
- 鐩存帴璇诲啓 SQLite锛?- 鐩存帴鎿嶄綔 `.jobs` 宸ヤ綔鍖猴紱
- 鑷繁鎺ㄦ柇浠诲姟宸茬粡瀹屾垚锛?- 涓?Process 椤甸潰鍜?Tasks 椤甸潰鍒嗗埆缁存姢鐙珛浠诲姟鐪熺浉锛?- 缁曡繃 `engine_call` 浣跨敤鏁ｈ惤鐨?Tauri invoke銆?
### 3.2 Rust 妗岄潰鏍稿績

鑱岃矗锛?
- 鍚姩銆佹娴嬪拰鍏抽棴 Python Sidecar锛?- 浣跨敤 Content-Length 甯т紶杈?JSON-RPC锛?- 灏嗗搷搴旇矾鐢卞埌瀵瑰簲璇锋眰锛?- 灏嗘棤 `id` 閫氱煡杞彂涓?Tauri 浜嬩欢锛?- 娑堣垂 Python `stderr`锛?- 寮曟搸閫€鍑烘椂绔嬪嵆浣挎墍鏈夊緟澶勭悊 RPC 澶辫触锛?- Windows 涓嬮€氳繃 Job Object 绠＄悊杩涚▼鏍戙€?
鍏抽敭瀹炵幇绾︽潫锛?
- `EngineManager` 鍙鐞嗚繘绋嬬敓鍛藉懆鏈燂紱
- `EngineClient` 鎸佹湁鍙厠闅嗙殑 stdin 涓?pending map锛?- 闀?RPC 绛夊緟鏈熼棿涓嶆寔鏈?`Mutex<EngineManager>`锛?- stdin 鍐欏叆涓茶鍖栵紱
- 鏈€澶у崟甯т负 8 MiB锛?- 寮€鍙戞ā寮忎粠椤圭洰鏍圭洰褰曟墽琛?`python -m src.engine --stdio`锛屼笉渚濊禆鍚姩鏃跺綋鍓嶇洰褰曪紱
- 鍙€氳繃 `VIDEO_NOTES_PYTHON`銆乣VIDEO_NOTES_PROJECT_ROOT`銆乣VIDEO_NOTES_ENGINE` 鍜?`VIDEO_NOTES_ENGINE_CWD` 瑕嗙洊鍚姩鏂瑰紡銆?
### 3.3 Python API 灞?
鑱岃矗锛?
- 鎻愪緵绋冲畾鏂规硶鍚嶅拰鍙傛暟鏍￠獙锛?- 灏嗗紓甯歌浆鎹负 JSON-RPC 閿欒锛?- 鏋勫缓 `PipelineRequest`锛?- 鎻愪緵浠诲姟銆佺瑪璁般€佸悎闆嗐€佽缃拰璇婃柇鎺ュ彛锛?- 鍙戦€佸崗璁簨浠讹紝浣嗕笉鎵ц浠诲姟绾跨▼绠＄悊銆?
浠诲姟鏂规硶锛?
| 鏂规硶 | 璇箟 |
|---|---|
| `process.start` | 鍒涘缓鏂颁换鍔″苟鍚姩 |
| `process.pause` | 璇锋眰鍦ㄥ畨鍏ㄦ鏌ョ偣鏆傚仠 |
| `process.cancel` | 璇锋眰鍙栨秷骞舵寜绛栫暐娓呯悊 |
| `process.resume` | 浣跨敤鍚屼竴 run_id 鍜屽凡鏈夐樁娈典骇鐗╃户缁?|
| `process.retry` | 鏂板缓 run_id锛屼粠澶存墽琛岋紝骞惰褰曟潵婧愪换鍔?|
| `process.list` | 鍒嗛〉鏌ヨ鎸佷箙鍖栦换鍔?|
| `process.get` | 鑾峰彇鍗曚釜浠诲姟 |
| `process.events` | 浠庢寚瀹?event_id 鍚庤ˉ鍙栦簨浠?|
| `process.delete` | 闅愯棌鎴栧垹闄や换鍔¤褰曪紙渚濈幇鏈夌瓥鐣ワ級 |
| `process.permanent_clean` | 娓呯悊宸查殣钘忓巻鍙?|

### 3.4 TaskSupervisor

`TaskSupervisor` 鏄敮涓€鍏佽鍒涘缓澶勭悊绾跨▼鐨勭粍浠躲€?
鑱岃矗锛?
- 缁熶竴 `start / resume / retry`锛?- 闃叉鍚屼竴涓换鍔¤閲嶅鍚姩锛?- 棣栫増闄愬埗涓€涓噸浠诲姟鍚屾椂鎵ц锛岄伩鍏?Whisper銆丱CR銆佽瑙夋ā鍨嬪拰 GPU 鏄惧瓨浜夋姠锛?- worker 缁撴潫鍚庢竻鐞嗙嚎绋嬭〃锛?- 灏嗘垚鍔熴€佸彇娑堝拰寮傚父鏀舵暃鍒?`JobQueue`锛?- 浠庤姹傚揩鐓ч噸寤哄師浠诲姟銆?
璇箟锛?
- **缁х画浠诲姟锛坮esume锛?*锛氭部鐢ㄥ師 `run_id`銆佸伐浣滃尯鍜屾湁鏁?stage manifest锛屽彧琛ュ仛鏈畬鎴愰樁娈碉紱
- **浠庡ご閲嶈瘯锛坮etry锛?*锛氬垱寤烘柊 `run_id`锛宍attempt + 1`锛屽啓鍏?`parent_run_id`锛屽苟浠?`force=True` 浠庡ご鎵ц锛?- **鏂板缓浠诲姟锛坰tart锛?*锛氬厛鎸佷箙鍖栬姹傚揩鐓э紝鍐嶅惎鍔?worker銆?
### 3.5 JobQueue 涓庣姸鎬佹満

鎸佷箙鍖栫姸鎬侊細

```text
pending
  鈫?running / resolving / downloading / transcribing
  鈫?extracting_frames / generating_notes / indexing
  鈫?completed
```

鎺у埗鐘舵€侊細

```text
running 鈫?pausing 鈫?paused
running 鈫?cancelling 鈫?cancelled
```

寮傚父鐘舵€侊細

```text
running 鈫?failed
寮曟搸杩涚▼娑堝け鍚庯紝閬楃暀 running/pausing/cancelling 鈫?interrupted
```

鍙户缁姸鎬侊細

- `pending`
- `running`锛堝吋瀹规棫璁板綍锛涘紩鎿庡惎鍔ㄦ椂浼氬厛褰掍竴涓?interrupted锛?- `paused`
- `failed`
- `interrupted`

涓嶅彲缁х画鐘舵€侊細

- `completed`
- `cancelled`锛堝彧鍏佽浠庡ご閲嶈瘯锛?
鏁版嵁搴撳悓鏃朵繚瀛橈細

- `progress`锛?- `progress_message`锛?- `heartbeat_at`锛?- `last_active_stage`锛?- `interrupted_at`锛?- `request_json`锛?- `attempt`锛?- `parent_run_id`銆?
## 4. 鏂偣鎭㈠璁捐

### 4.1 涓ゅ眰鎭㈠渚濇嵁

鎭㈠涓嶈兘鍙湅鏁版嵁搴撶姸鎬侊紝涔熶笉鑳藉彧鐪嬫枃浠舵槸鍚﹀瓨鍦ㄣ€倂4 浣跨敤涓ゅ眰渚濇嵁锛?
1. **浠诲姟璇锋眰蹇収**锛氬喅瀹氭仮澶嶆椂浣跨敤浠€涔堝弬鏁帮紱
2. **闃舵 manifest**锛氬喅瀹氬摢浜涢樁娈典骇鐗╃粡杩囧畬鏁存€ч獙璇侊紝鍙互璺宠繃銆?
### 4.2 璇锋眰蹇収

姣忎釜浠诲姟鍦ㄥ叆闃熷墠淇濆瓨鐗堟湰鍖?JSON锛?
```json
{
  "schema_version": 1,
  "request": {
    "input": "...",
    "whisper_model": "...",
    "provider": "...",
    "gpt_model": "...",
    "template_id": "...",
    "frame_mode": "...",
    "max_frames": 80,
    "vision_enabled": true
  },
  "credential_refs": {
    "llm_profile": "鐢熶骇妯″瀷",
    "vision_profile": "瑙嗚妯″瀷"
  },
  "secret_policy": "named_profile_or_environment"
}
```

鏄庣‘涓嶄繚瀛橈細

- LLM API Key锛?- Vision API Key锛?- Bilibili cookies锛?- 鍏朵粬鍘熷鍑嵁銆?
鎭㈠鏃讹細

1. 闈炲瘑閽ュ弬鏁颁互鍘嗗彶蹇収涓哄噯锛?2. 鍑嵁浼樺厛浠庡師浠诲姟璁板綍鐨勨€滀緵搴斿晢閰嶇疆鍚嶇О鈥濊В鏋愶紱
3. 鏃у揩鐓ф棤鍚嶇О鏃讹紝鍥為€€鍒板綋鍓嶇粦瀹氶厤缃紱
4. 閰嶇疆鍐呮棤瀵嗛挜鏃跺啀璇诲彇鐜鍙橀噺锛?5. 瀵嗛挜鍙疆鎹紝涓嶉渶瑕佷慨鏀瑰巻鍙蹭换鍔¤銆?
### 4.3 闃舵 manifest

姣忎釜闃舵瀹屾垚鍚庡啓鍏ワ細

```text
.jobs/{stable_job_id}/artifacts/_manifest/_manifest_{stage}.json
```

manifest 鍖呭惈锛?
- stage锛?- completed / partial锛?- 杈撳嚭鏂囦欢鍒楄〃锛?- 杈撳叆鍝堝笇锛?- 鏍煎紡鐗堟湰锛?- 鏃堕棿锛?- partial 閿欒銆?
鍙湁鐘舵€佷负 completed銆佽緭鍑哄瓨鍦ㄤ笖闈炵┖銆佸苟婊¤冻闃舵鏍￠獙鏃讹紝鎭㈠鎵嶈烦杩囪闃舵銆傛棫 manifest 璺緞浠嶅吋瀹硅鍙栥€?
### 4.4 杩涚▼宕╂簝鎭㈠

Python 寮曟搸鍚姩鍚庢墽琛?`reconcile_interrupted_jobs()`锛?
- 灏嗕笂涓€杩涚▼閬楃暀鐨勬椿鍔ㄧ姸鎬佹爣璁颁负 `interrupted`锛?- 淇濈暀宸ヤ綔鍖恒€佸揩鐓с€侀樁娈?manifest 鍜屼簨浠跺巻鍙诧紱
- 鍓嶇灞曠ず鈥滅户缁换鍔♀€濓紱
- 缁х画鏃朵娇鐢ㄥ悓涓€浠诲姟鍙婂師閰嶇疆锛屼笉閲嶆柊鎻愬彇宸茬粡楠岃瘉瀹屾垚鐨勯煶棰戞垨杞綍銆?
## 5. 浜嬩欢妯″瀷

SQLite v14 澧炲姞 `job_events`锛?
```text
id, run_id, job_id, event_type, data, created_at
```

鏍囧噯浜嬩欢 payload锛?
```json
{
  "event_id": 125,
  "job_id": 42,
  "stable_job_id": "uuid",
  "status": "running",
  "stage": "transcribing",
  "progress": 36.5,
  "message": "姝ｅ湪杞綍鈥?,
  "timestamp": "2026-07-05T..."
}
```

瀹炴椂璁㈤槄缁熶竴浣跨敤 `job.progress`銆傜粓绔簨浠朵粛浠?`event_type` 鎸佷箙鍖栦负 completed銆乫ailed銆乮nterrupted銆乸aused 鎴?cancelled锛屽墠绔笉闇€瑕佸缓绔嬪濂楃洃鍚€?
`process.events(after_id)` 鐢ㄤ簬锛?
- 椤甸潰閲嶆柊杩涘叆鍚庤ˉ浜嬩欢锛?- Tauri 鐭殏鏂繛鍚庤拷璧讹紱
- 璇婃柇浠诲姟鐘舵€佹祦杞紱
- 鍚庣画鏀寔瀹¤鏃堕棿绾裤€?
## 6. 鏁版嵁涓庢枃浠跺竷灞€

```text
output/
鈹溾攢鈹€ video_notes.db
鈹溾攢鈹€ .jobs/
鈹?  鈹斺攢鈹€ {stable_job_id}/
鈹?      鈹溾攢鈹€ artifacts/
鈹?      鈹?  鈹溾攢鈹€ transcript.json
鈹?      鈹?  鈹溾攢鈹€ notes.md
鈹?      鈹?  鈹溾攢鈹€ frames/
鈹?      鈹?  鈹斺攢鈹€ _manifest/
鈹?      鈹斺攢鈹€ temp/
鈹斺攢鈹€ 鏈€缁堝鍑虹洰褰?```

鍘熷垯锛?
- 鍙仮澶嶄骇鐗╂斁 `artifacts`锛?- 鍙噸鏂扮敓鎴愮殑涓棿鐗╂斁 `temp`锛?- 鎶藉抚宸ヤ綔鐩綍涓轰换鍔＄鏈夛紝绂佹澶氫釜浠诲姟鍏变韩鍏ㄥ眬涓存椂鐩綍锛?- 瀹屾垚鍚庢寜鐜版湁淇濈暀绛栫暐娓呯悊涓存椂鏂囦欢锛?- 鍙栨秷涓庡け璐ヤ笉搴旇鍒犲彲鐢ㄤ簬璇婃柇鎴栨仮澶嶇殑闃舵浜х墿銆?
## 7. 鍓嶇鐘舵€佽璁?
鍏变韩 jobs store 璐熻矗锛?
- 棣栨鍔犺浇 `process.list`锛?- 鏍规嵁 `job.progress` upsert 浠诲姟锛?- 寮€濮嬨€佹殏鍋溿€佸彇娑堛€佺户缁拰閲嶈瘯鍚庝富鍔ㄥ埛鏂帮紱
- Process 椤甸潰鏄剧ず褰撳墠杩愯浠诲姟锛?- Tasks 椤甸潰鏄剧ず瀹屾暣鍘嗗彶涓庡彲鐢ㄦ搷浣滐紱
- 椤甸潰鍒囨崲涓嶄涪澶辨鍦ㄨ繍琛岀殑鐪熷疄鐘舵€併€?
鐣岄潰鎿嶄綔瑙勫垯锛?
| 鐘舵€?| 鎿嶄綔 |
|---|---|
| pending | 缁х画 / 鍙栨秷 |
| running | 鏆傚仠 / 鍙栨秷 |
| pausing | 绛夊緟瀹夊叏鏆傚仠 |
| paused | 缁х画 / 鍙栨秷 |
| interrupted | 缁х画 / 浠庡ご閲嶈瘯 |
| failed | 缁х画 / 浠庡ご閲嶈瘯 |
| cancelled | 浠庡ご閲嶈瘯 |
| completed | 鎵撳紑浜х墿 / 鍒犻櫎璁板綍 |

## 8. 璁剧疆銆佹ā鏉夸笌璇婃柇

### 8.1 璁剧疆 API

璁剧疆鍐欏叆浣跨敤鍘熷瓙鏇存柊锛屾敮鎸佹柊 `patches` 缁撴瀯骞跺吋瀹规棫缁撴瀯銆備緵搴斿晢濂戠害缁熶竴鏀寔锛?
- list锛?- create锛?- update锛?- delete锛?- set_active锛?- scan_models锛?- test_connection銆?
杩炴帴娴嬭瘯鍙繘琛岃交閲忓彲杈炬€ф垨璁よ瘉妫€鏌ワ紝涓嶄富鍔ㄧ敓鎴愬唴瀹广€?
### 8.2 妯℃澘璧勪骇

姝ｅ紡鍐呯疆 8 濂?YAML锛?
- 閫氱敤绗旇锛?- 瀛︿範绗旇锛?- 浼氳绾锛?- 缂栫▼鏁欑▼锛?- 璇剧▼璁插骇锛?- 璁胯皥鏁寸悊锛?- 浜у搧婕旂ず锛?- 璁烘枃鐮旇銆?
妯℃澘鐢?Python registry 缁熶竴鍔犺浇锛屽墠绔笉鍐嶇‖缂栫爜涓€濂楀钩琛屽垪琛ㄣ€?
### 8.3 璇婃柇

璇婃柇妯″潡閲囩敤寤惰繜瀵煎叆锛?
- 缂哄皯 yt-dlp銆乄hisper銆丱CR 鎴栧叾浠栧彲閫夌粍浠舵椂锛屾闈㈠３浠嶈兘鍚姩锛?- doctor 杩斿洖 pass / warn / fail锛?- 闂鎶ュ憡鍙鍑哄畨鍏ㄨ瘖鏂俊鎭紱
- 涓嶅鍑烘暣涓幆澧冨彉閲忔垨鏄庢枃 API Key銆?
## 9. 瀹夊叏杈圭晫

- stdout 鍙厑璁?Content-Length JSON-RPC 甯э紱
- Python 鏅€氭棩蹇楀啓 stderr 鎴栨枃浠讹紱
- 鎺у埗鍙扮洿璺戞ā寮忓彲鏄惧紡寮€鍚繘搴︽墦鍗帮紝Sidecar 妯″紡鍏抽棴锛?- 璇锋眰蹇収鍜屼簨浠舵棩蹇椾笉寰楀啓瀵嗛挜锛?- 鍓嶇姘歌繙鍙緱鍒拌劚鏁忓悗鐨勪緵搴斿晢淇℃伅锛?- 璇婃柇鍖呬笉寰楀寘鍚畬鏁寸幆澧冨彉閲忥紱
- Rust 瀵瑰崗璁抚璁剧疆澶у皬涓婇檺锛?- 澶栭儴鍛戒护鐨勫弬鏁颁粛搴斾娇鐢ㄥ弬鏁版暟缁勶紝涓嶆嫾鎺?shell 瀛楃涓层€?
## 10. 褰撳墠浜у搧鑳藉姏杈圭晫

鏈疆宸茬粡褰㈡垚鐨勭ǔ瀹氳竟鐣岋細

- 涓€濂楃湡瀹炲鐞嗙绾匡紱
- 涓€涓换鍔＄洃鐫ｅ櫒锛?- 涓€浠?SQLite 浠诲姟鐪熺浉锛?- 涓€濂楅樁娈电骇鎭㈠璇箟锛?- 涓€鏉″疄鏃朵笌鎸佷箙鍖栦簨浠堕摼锛?- 涓€濂楀墠鍚庣璁剧疆濂戠害锛?- 涓€濂楀叡浜墠绔换鍔＄姸鎬侊紱
- 涓€濂楀彲閫変緷璧栭檷绾ф満鍒躲€?
褰撳墠鍒绘剰闄愬埗锛?
- 鍚屾椂鍙繍琛屼竴涓噸浠诲姟锛?- Python worker 浠嶄负杩涚▼鍐呯嚎绋嬶紝涓嶆槸鐙珛 worker 杩涚▼姹狅紱
- 缁勪欢涓嬭浇銆佹ā鍨嬬鐞嗐€佽嚜鍔ㄦ洿鏂板拰绛惧悕鍙戝竷灏氭湭绾冲叆鏈疆锛?- 姝ｅ紡鐢熶骇浠ｇ爜宸蹭笉鍐嶅鍏?`src.core`锛涗粨搴撲粛淇濈暀灏戦噺鍘嗗彶娉ㄩ噴鍜屾棫 Qt 娴嬭瘯锛岄渶鍦ㄥ悗缁綊妗ｏ紱
- 鏃?Qt 娴嬭瘯浠嶅瓨鍦ㄤ簬浠撳簱锛屼絾瀵瑰簲 GUI 宸查€€鍑烘寮忎骇鍝侀摼銆?
## 11. 鍚庣画婕旇繘璺嚎

### 闃舵 1锛氬綋鍓?v4 鍩虹嚎

- TaskSupervisor 鍗曚竴鍏ュ彛锛?- SQLite v14锛?- 鍙傛暟蹇収涓庡嚟鎹紩鐢紱
- 鎸佷箙鍖栦簨浠讹紱
- Tauri IPC 瑙ｉ攣锛?- Svelte 鍏变韩浠诲姟 Store锛?- 璁剧疆銆佹ā鏉垮拰璇婃柇濂戠害缁熶竴銆?
### 闃舵 2锛氬彂甯冨伐绋嬪寲

- Windows 涓婂畬鎴?`cargo check`銆乣cargo test`銆乣tauri build`锛?- 浣跨敤 PyInstaller/Nuitka 鐢熸垚 `python-engine.exe`锛?- 楠岃瘉 Tauri `externalBin` 鍛藉悕涓?target triple锛?- 瀹夎鍖呴獙璇?CPU-only 涓?CUDA 涓ゅ缁勪欢绛栫暐锛?- 瀵圭湡瀹炴湰鍦拌棰戙€乁RL銆佹殏鍋溿€佸己鏉€銆佺户缁€侀噸璇曟墽琛岀鍒扮娴嬭瘯锛?- 寤虹珛鏃ュ織杞浆鍜屼竴閿瘖鏂寘銆?
### 闃舵 3锛氬浠诲姟涓庣嫭绔?worker

鍦ㄧ‘璁ゆā鍨嬬敓鍛藉懆鏈熷拰 GPU 璧勬簮绛栫暐鍚庯細

- Supervisor 璋冨害闃熷垪锛?- 姣忎釜閲嶄换鍔＄嫭绔?worker 杩涚▼锛?- 骞跺彂妲戒綅涓?GPU 璧勬簮绉熺害锛?- worker 蹇冭烦涓庣绾﹁秴鏃讹紱
- 涓诲紩鎿庨噸鍚悗鑷姩閲嶈繛鎴栨樉寮忔仮澶嶃€?
### 闃舵 4锛氱粍浠跺寲鍙戝竷

- Shell 鑷姩鏇存柊锛?- Engine 鐙珛鐗堟湰锛?- Whisper/OCR/CUDA 缁勪欢娓呭崟锛?- 鍝堝笇銆佺鍚嶃€佸洖婊氬拰绂荤嚎缂撳瓨锛?- 鏁版嵁搴撹縼绉诲浠戒笌鍥炴粴绛栫暐銆?
## 12. 姝ｅ紡鍙戝竷闂ㄧ

浠ヤ笅椤圭洰鍏ㄩ儴閫氳繃鍚庯紝鎵嶅彲绉颁负鈥滃彲鍏紑鍙戝竷鐗堟湰鈥濓細

1. Python 娲昏穬鍚庣娴嬭瘯閫氳繃锛?2. Svelte production build 涓庣被鍨嬫鏌ラ€氳繃锛?3. Windows Rust `cargo check` 涓?`tauri build` 閫氳繃锛?4. Sidecar 鑳戒粠瀹夎鐩綍鍚姩锛屼笉渚濊禆绯荤粺 Python锛?5. 鑷冲皯瀹屾垚鏈湴鏂囦欢鍜?URL 涓ょ被鐪熷疄浠诲姟锛?6. 鍦ㄨ浆褰曘€佹娊甯с€丩LM 涓変釜闃舵鍒嗗埆寮烘潃搴旂敤骞舵垚鍔熺户缁紱
7. 鏀瑰彉褰撳墠婵€娲讳緵搴斿晢鍚庯紝鏃т换鍔′粛鑳芥寜鍘熷懡鍚嶉厤缃仮澶嶏紱
8. API Key 涓嶅嚭鐜板湪 SQLite銆佷簨浠舵棩蹇椼€佸墠绔搷搴斿拰璇婃柇鍖咃紱
9. 瀹夎銆佸崌绾с€佸嵏杞藉悗鐢ㄦ埛浜х墿涓庢暟鎹簱绗﹀悎淇濈暀绛栫暐锛?10. 鏃犳棫 Qt GUI 杩涘叆姝ｅ紡鏋勫缓浜х墿銆?
