# フレーム抽出スタック問題 - 根本原因分析

## 動画情報
- ファイル: ryukyogoku-20260128-1005.mp4
- 長さ: 11時間16分 (約40,560秒)
- fps=1 → 約40,560フレーム
- 各フレーム約120KB → 約4.6GB必要

## 根本原因候補

### 1. ディスク容量不足（最有力）
- 40,560フレーム × 120KB = 約4.6GB
- Workerのディスク容量が不足している可能性が高い
- extract_framesにはディスク事前チェックがあるが、estimated_gb計算が不正確
- 計算: `(expected_frames * 120 * 1024) / (1024 ** 3)` = `(40560 * 120 * 1024) / (1073741824)` = 4.6GB
- 他の動画のフレームデータも残っている場合、容量不足になる

### 2. generate_analysis_videoのタイムアウト（10分）が短すぎる
- 11時間の動画を1fps/1280pxに変換するのに10分では不足
- タイムアウト → RAW動画（おそらく数GB）からフレーム抽出
- RAW動画からのフレーム抽出は10倍遅い

### 3. extract_audio_full/extract_audio_chunksにタイムアウトなし
- subprocess.runにtimeout引数がない
- 11時間の動画の音声抽出が非常に長時間かかる
- PARALLEL実行でaudioが完了しないとframes側も待ち続ける

### 4. ThreadPoolExecutor内のas_completed
- fut_framesとfut_audioの両方が完了するまで待つ
- 一方がスタックすると全体がスタック

## 修正方針
1. generate_analysis_videoのタイムアウトを動画長に比例させる（duration/60分、最低10分、最大60分）
2. extract_audio_full/extract_audio_chunksにタイムアウトを追加
3. ThreadPoolExecutor内にタイムアウトを追加（最大2時間）
4. disk_guardの改善 - 古いフレームデータの自動クリーンアップ
