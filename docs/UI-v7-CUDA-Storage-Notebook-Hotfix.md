# UI v7 鈥?CUDA銆佸瓨鍌ㄧ洰褰曘€佺瑪璁板簱闂幆闆嗕腑淇

鐗堟湰鍙蜂繚鎸?`1.5.0`锛屼笉鍗囩骇銆?
## 淇鑼冨洿

### 1. Whisper CUDA 闂幆

- 璁剧疆椤垫柊澧?`Whisper 杩愯璁惧`锛歚auto` / `cuda` / `cpu`銆?- 璁剧疆椤垫柊澧?`璁＄畻绮惧害`锛歚auto` / `float16` / `int8_float16` / `int8` / `float32`銆?- 鍒涘缓浠诲姟椤靛悓姝ユ樉绀哄苟鍏佽鏈浠诲姟瑕嗙洊璁惧涓庣簿搴︺€?- 浠诲姟蹇収淇濆瓨 `whisper_device` 涓?`whisper_compute_type`銆?- 鍚庣杞綍闃舵灏嗚澶囦笌绮惧害浼犲叆 `faster-whisper` / `CTranslate2`銆?- 鏄惧紡閫夋嫨 `cuda` 鏃讹紝濡傛灉 CUDA 涓嶅彲鐢ㄦ垨 compute type 涓嶆敮鎸侊紝鐩存帴鎶ラ敊锛屼笉鍐嶉潤榛橀檷绾?CPU銆?- `auto` 妯″紡浠嶅厑璁?CUDA 澶辫触鏃惰嚜鍔ㄩ檷绾у埌 CPU/int8銆?- `system.info` 杩斿洖 `cuda_device_count` 涓?`cuda_compute_types` 渚涜瘖鏂睍绀恒€?
### 2. 杈撳嚭鐩綍涓庤繍琛岀洰褰曞垎绂?
- `process.*`銆乣notes.*`銆乣collections.*` 榛樿璇诲彇鐢ㄦ埛璁剧疆涓殑 `output_dir`銆?- 涓嶅啀鍥犱负 Tauri Sidecar 宸ヤ綔鐩綍鍦?AppData 鑰岀敓鎴愮浜屽 `engine-runtime/output/.note_index`銆?- 浠诲姟鏂偣宸ヤ綔鍖烘敼涓虹鏈夌洰褰曪細`%LOCALAPPDATA%\\Video Notes AI\\.jobs`銆?- 鐢ㄦ埛杈撳嚭鐩綍鍙礋璐ｆ渶缁堜骇鐗╀笌绗旇绱㈠紩锛歁arkdown銆乼ranscript銆乫rames銆乣.note_index`銆?- 鏂颁换鍔′笉浼氬啀鍦ㄧ敤鎴疯緭鍑虹洰褰曞垱寤?`.jobs`銆?
### 3. 绗旇搴撻棴鐜?
- 绗旇搴撲娇鐢ㄤ笌浠诲姟浜х墿鐩稿悓鐨勭敤鎴疯緭鍑虹洰褰?`.note_index/video_notes.db`銆?- 浠诲姟瀹屾垚鍚?`IndexProvenanceStage` 鍐欏叆鐨?note 璁板綍鍙互琚?`notes.list` 璇诲彇銆?- 宸茬粡鐢熸垚鍦ㄧ敤鎴疯緭鍑虹洰褰曚腑鐨?`.note_index` 浼氶噸鏂板彉涓哄彲瑙佺瑪璁板簱鏉ユ簮銆?
### 4. 鍒涘缓浠诲姟杈撳叆妗嗗彲鐢ㄦ€?
- 鍏ㄥ眬琛ㄥ崟鏍峰紡鍔犲叆 `input[type="url"]`锛屽叕寮€瑙嗛閾炬帴杈撳叆妗嗘仮澶嶅叏瀹姐€?- 鍒涘缓浠诲姟椤甸摼鎺ヨ緭鍏ユ澧炲姞涓撶敤 `url-input` 鏍峰紡锛岄伩鍏嶅彧鏄剧ず鐭緭鍏ュ尯鍩熴€?
## 楠岃瘉

```text
python -m compileall src锛氶€氳繃
pytest tests/test_api_settings_contract.py tests/test_engine_api_smoke.py tests/test_pipeline_stages.py tests/test_v18_resume_checkpoint.py锛?2 passed
pytest tests/test_job_queue.py tests/test_v14_task_runtime.py tests/test_db_job_repo.py锛?7 passed
Vite production build锛氶€氳繃
svelte-check锛? errors锛? warnings
```

## 鏇存柊娉ㄦ剰

鏈淇敼浜?Python 鍚庣杞綍銆佷换鍔＄洰褰曘€佹湇鍔″惎鍔ㄧ洰褰曡В鏋愬拰璇锋眰蹇収瀛楁锛屽繀椤婚噸鏂版瀯寤?Python Sidecar銆傞娆℃瀯寤轰笉瑕佷娇鐢?`-ReuseSidecar`銆?
