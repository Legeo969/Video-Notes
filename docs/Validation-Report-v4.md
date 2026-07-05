# Video Notes AI v4 楠岃瘉鎶ュ憡

> 楠岃瘉鏃ユ湡锛?026-07-05
> 楠岃瘉鐜锛歀inux 瀹瑰櫒锛孭ython 3.13.5锛孨ode/Vite 鐜鍙敤锛涙湭瀹夎 Rust/Cargo 宸ュ叿閾?
## 1. 楠岃瘉鑼冨洿

鏈姤鍛婇獙璇佹湰杞骇鍝佺骇閲嶆瀯鐨勶細

- Python 浠诲姟鐢熷懡鍛ㄦ湡锛?- 璇锋眰蹇収鍜屽嚟鎹紩鐢紱
- SQLite v14 杩佺Щ锛?- 浜嬩欢鏃ュ織锛?- 鏂偣鎭㈠銆佹殏鍋溿€佸彇娑堝拰閲嶈瘯锛?- 澶勭悊绠＄嚎闃舵琛屼负锛?- 璁剧疆 API锛?- 妯℃澘鍔犺浇銆佹帹鑽愩€侀瑙堝拰鏍￠獙锛?- Svelte 鏋勫缓涓庨潤鎬佹鏌ワ紱
- Rust 婧愮爜闈欐€佸鏌ャ€?
## 2. Python 娲昏穬鍚庣濂椾欢

鎵ц鍛戒护锛?
```bash
python -m pytest -q \
  --ignore=tests/test_collection_delete.py \
  --ignore=tests/test_smart_summary.py \
  --ignore=tests/test_provider_profile_settings.py
```

缁撴灉锛?
```text
618 passed, 52 skipped, 4 xfailed
```

璇存槑锛?
- `skipped` 涓昏涓洪渶瑕佸彲閫夊閮ㄤ緷璧栥€佹ā鍨嬫垨鐗瑰畾杩愯鏉′欢鐨勬祴璇曪紱
- `xfailed` 涓轰粨搴撳凡鏈夌殑鏄惧紡棰勬湡澶辫触锛?- 鏈疆鏂板鐨勪换鍔¤繍琛屾椂涓庤缃绾︽祴璇曞潎閫氳繃銆?
### 2.1 涓轰粈涔堟帓闄?3 涓棫娴嬭瘯鏂囦欢

| 鏂囦欢 | 鍘熷洜 |
|---|---|
| `tests/test_collection_delete.py` | 瀵煎叆宸查€€鍑烘寮忎骇鍝侀摼鐨?`PySide6.QtWidgets` / Qt GUI |
| `tests/test_smart_summary.py` | 瀵煎叆宸查€€鍑烘寮忎骇鍝侀摼鐨?Qt GUI |
| `tests/test_provider_profile_settings.py` | 鍖呭惈閽堝鏃ф簮浠ｇ爜鏂囨湰/鏃?Qt 璁剧疆瀹炵幇鐨勬柇瑷€锛屼笌褰撳墠 Engine API 璁剧疆濂戠害涓嶄竴鑷?|

杩欎簺鏂囦欢娌℃湁琚垹闄わ紝涔熸病鏈夎浼鎴愰€氳繃銆傚畠浠簲鍦ㄥ悗缁縼绉讳腑鏍囪涓?`legacy` 鎴栫Щ鍏ュ巻鍙叉祴璇曠洰褰曪紱姝ｅ紡鍙戝竷娴嬭瘯搴斾互 Tauri/Svelte + Python Engine 涓轰骇鍝佽竟鐣屻€?
## 3. 鑱氱劍鍥炲綊

宸茶鐩栵細

- 璇锋眰蹇収涓嶄繚瀛樺瘑閽ワ紱
- 鎭㈠鏃朵繚鐣欏師妯″瀷銆佹ā鏉裤€佹娊甯у拰瑙嗚鍙傛暟锛?- 褰撳墠婵€娲讳緵搴斿晢鍙樺寲鍚庯紝浠嶆寜鍘熶换鍔＄殑鍛藉悕閰嶇疆鑾峰彇鍑嵁锛?- 鏃у揩鐓у吋瀹瑰洖閫€锛?- 鍚姩鏃跺皢閬楃暀娲诲姩浠诲姟鏍囦负 interrupted锛?- 鍚屼竴浠诲姟涓嶅彲閲嶅鍚姩锛?- 鍚屾椂鍙厑璁镐竴涓噸浠诲姟锛?- resume 娌跨敤 run_id锛?- retry 鐢熸垚鏂?run_id锛屽苟璁板綍 parent_run_id 涓?attempt锛?- 杩涘害鍐欏叆 SQLite锛?- 浜嬩欢鍐欏叆 `job_events`锛?- 璁剧疆渚涘簲鍟嗗鍒犳敼銆佹縺娲汇€佹ā鍨嬫壂鎻忓拰杩炴帴娴嬭瘯濂戠害锛?- 8 濂楀唴缃ā鏉跨殑鍔犺浇銆佹帹鑽愩€侀瑙堝拰鏍￠獙锛?- 鑷姩鎶藉抚涓嶇獊鐮?`max_frames`锛?- OCR 鍏ュ彛鏀舵暃锛?- 瑙嗚 Provider 宸ュ巶璋冪敤淇锛?- Sidecar stdout 涓嶈鏅€氳繘搴﹁緭鍑烘薄鏌撱€?
## 4. Python Sidecar 鍗忚鎻℃墜

鏂板瀛愯繘绋嬬骇娴嬭瘯浼氭樉寮忛樆姝?`yt_dlp`銆乣faster_whisper` 鍜?`ctranslate2` 瀵煎叆锛屽啀鍚姩锛?
```bash
python -m src.engine --stdio
```

楠岃瘉缁撴灉锛?
- 鏀跺埌 `engine.hello`锛?- `system.info` 姝ｅ父鍝嶅簲锛?- `system.shutdown` 姝ｅ父鍝嶅簲锛?- 杩涚▼閫€鍑虹爜涓?0锛?- stderr 鏃?Traceback銆?
杩欒瘉鏄庤缃〉銆佽瘖鏂〉鍜屽熀纭€寮曟搸鎻℃墜涓嶅啀琚彲閫変笅杞?妯″瀷渚濊禆闃绘柇銆?
## 5. Svelte 鍓嶇

鎵ц鍛戒护锛?
```bash
cd desktop
npm run build
npx svelte-check --tsconfig ./tsconfig.json
```

缁撴灉锛?
```text
Vite production build: success
svelte-check: 0 errors, 0 warnings
```

楠岃瘉鐐癸細

- 鎵€鏈変笟鍔¤皟鐢ㄧ粺涓€缁?`engine_call`锛?- Tauri 浜嬩欢 payload 鑷姩瑙ｅ寘锛?- Process 涓?Tasks 椤甸潰浣跨敤鍏变韩 jobs store锛?- 寮€濮嬨€佹殏鍋溿€佸彇娑堛€佺户缁拰閲嶈瘯浣跨敤缁熶竴 job id锛?- 璁剧疆椤典笌 Python API 瀛楁涓€鑷达紱
- Mock API 涓庢寮忓绾﹀悓姝ャ€?
## 6. Rust/Tauri 鐘舵€?
瀹屾垚鐨勬簮鐮佺骇淇锛?
- 闀?RPC 涓嶅啀鍗犵敤 `EngineManager` 鐢熷懡鍛ㄦ湡閿侊紱
- stdin 鍐欏叆涓茶鍖栵紱
- stderr 鎸佺画娑堣垂锛?- Sidecar 鏂紑鍚庣珛鍗冲敜閱?pending RPC锛?- 闃诲 stdout 璇诲彇鏀惧叆 blocking pool锛?- 鍗忚甯ф渶澶?8 MiB锛?- 浜嬩欢鍙浆鍙?Python notification 鐨?`params`锛?- 寮€鍙戞ā寮忓畾浣嶉」鐩牴骞惰繍琛?`python -m src.engine --stdio`锛?- Windows Job Object 缁х画绠＄悊 Sidecar 瀛愯繘绋嬫爲銆?
闄愬埗锛氬綋鍓嶉獙璇佸鍣ㄦ病鏈?`cargo` 鍜?`rustfmt`锛屽洜姝?*娌℃湁澹扮О Rust 宸茬紪璇戦€氳繃**銆傛寮忓彂甯冨墠蹇呴』鍦?Windows 寮€鍙戞満鎵ц锛?
```powershell
cd desktop\src-tauri
cargo fmt --check
cargo check
cargo test
cd ..
npm run tauri build
```

## 7. 灏氭湭鎵ц鐨勭湡瀹炵幆澧冩祴璇?
鏈疆娌℃湁鍦ㄦ瀹瑰櫒涓畬鎴愶細

- Windows 瀹夎鍖呮瀯寤猴紱
- 鎵撳寘鍚庣殑 `python-engine.exe` 鍚姩锛?- CUDA Whisper锛?- PaddleOCR GPU锛?- 鐪熷疄 LLM/Vision API 璋冪敤锛?- 澶у瀷鏈湴瑙嗛瀹屾暣澶勭悊锛?- yt-dlp 鍦ㄧ嚎 URL 涓嬭浇锛?- 搴旂敤鍦ㄨ浆褰?鎶藉抚/LLM 闃舵琚己鏉€鍚庣殑鐪熷疄鎭㈠锛?- 鑷姩鏇存柊銆佺鍚嶅拰鍥炴粴銆?
杩欎簺椤圭洰灞炰簬鍙戝竷闂ㄧ锛屼笉搴斾互鍗曞厓娴嬭瘯鏇夸唬銆?
## 8. 缁撹

鏈疆宸插畬鎴愨€滀骇鍝佸唴鏍搁噸鏋勫熀绾库€濓細浠诲姟鎵ц鍏ュ彛銆佷换鍔＄湡鐩搞€佸弬鏁版仮澶嶃€佷簨浠堕摼鍜屽墠绔姸鎬佸潎宸叉敹鏁涳紝鍙互缁х画杩涘叆 Windows Sidecar 鎵撳寘涓庣湡瀹炲獟浣撶鍒扮楠岃瘉銆?
褰撳墠鍑嗙‘鐘舵€佹槸锛?
- Python 浜у搧鍐呮牳锛氶€氳繃娲昏穬娴嬭瘯濂椾欢锛?- Svelte 鍓嶇锛氱敓浜ф瀯寤轰笌闈欐€佹鏌ラ€氳繃锛?- Rust/Tauri锛氬畬鎴愭簮鐮佷慨澶嶏紝寰?Windows 宸ュ叿閾剧紪璇戦獙璇侊紱
- 瀹夎鍙戝竷锛氬皻鏈畬鎴愶紝涓嶅簲绉颁负鏈€缁堝畨瑁呭寘銆?
## v4.1 Windows settings-isolation regression

The three Windows failures in `test_api_settings_contract.py` shared one root
cause: platform-specific home-directory expansion bypassed pytest's temporary
`HOME`. The settings path now supports an explicit cross-platform override and
the fixture uses it. Post-fix active backend result:

```text
618 passed, 52 skipped, 4 xfailed
```
