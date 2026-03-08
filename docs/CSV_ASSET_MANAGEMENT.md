# CSV Asset Management System

## Overview

CSV Asset Management は、動画分析プラットフォームにおけるCSV/Excelファイルの安全な管理を実現するシステムです。CSVファイルの日時がビデオの開始時刻と一致しない場合、すべての分析結果が不正確になるため、このシステムは「安全機能」として位置づけられています。

## Architecture

### Database: `video_upload_assets` テーブル

| Column | Type | Description |
|--------|------|-------------|
| id | BIGINT PK | 自動採番 |
| video_id | VARCHAR(100) | 動画ID |
| asset_type | ENUM | 'video', 'trend_csv', 'product_csv' |
| original_filename | VARCHAR(500) | 元のファイル名 |
| blob_url | TEXT | Azure Blob Storage URL |
| file_size | BIGINT | ファイルサイズ（バイト） |
| uploaded_at | TIMESTAMP | アップロード日時 |
| uploaded_by | INT | アップロードユーザーID |
| is_active | TINYINT(1) | 現在アクティブか（1=はい） |
| version | INT | バージョン番号（1から開始） |
| validation_status | VARCHAR(20) | ok/warning/error/unknown |
| validation_result | JSON | バリデーション結果の詳細 |
| replaced_by_id | BIGINT | 置き換え先のアセットID |

### Versioned Attachment Pattern

CSV差し替え時の動作:

1. 現在アクティブなアセットの `is_active` を `0` に更新
2. 新しいアセットを `version + 1` で挿入（`is_active = 1`）
3. 旧アセットは削除されず、監査証跡として保存

```
v1 (is_active=0) → v2 (is_active=0) → v3 (is_active=1) ← 現在
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/video/{id}/excel-info` | GET | アセット情報 + 履歴を返却 |
| `/video/{id}/csv-preview` | GET | Excel先頭行・カラム情報 |
| `/video/{id}/asset-history` | GET | バージョン履歴 |
| `/video/{id}/update-validation-status` | POST | バリデーション結果を保存 |
| `/video/{id}/replace-excel` | PUT | CSV差し替え + 再処理 |

### Frontend Components

| Component | Role |
|-----------|------|
| `CsvAssetPanel` | 包括的アセット管理パネル（メイン） |
| `CsvReplaceModal` | CSV差し替えモーダル |
| `CsvValidationGate` | 日時バリデーション結果表示 |

## Validation Status Flow

```
Upload/Replace → Validation Gate → Upload to Blob → replace-excel API
                                                          ↓
                                              video_upload_assets INSERT
                                                          ↓
                                              update-validation-status
                                                          ↓
                                              CsvAssetPanel re-fetch
```

### Validation Tiers

| Status | Condition | UI |
|--------|-----------|-----|
| `ok` | 日時差 0-5分 | 緑バッジ、緑ボーダー |
| `warning` | 日時差 5-15分 | 黄バッジ、黄ボーダー |
| `error` | 日時差 15分超 or 日付不一致 | 赤バッジ、赤ボーダー |
| `unknown` | 未検証 or 判定不能 | グレーバッジ |

## CSV Preview

`/video/{id}/csv-preview` APIは以下を返します:

- `columns`: カラム名リスト
- `column_info`: 各カラムのnon-null数とサンプル値
- `datetime_columns`: 日時関連カラムの自動検出
- `preview_rows`: 先頭N行のデータ
- `total_rows`: 総行数
- `sheet_name`: シート名
- `file_size`: ファイルサイズ

## Design Principles

1. **Data operations correctness before AI sophistication** - CSVの正確性がすべての分析の基盤
2. **Never delete, always version** - 旧CSVは監査証跡として保存
3. **Validation status always visible** - パネルヘッダーで即座に状態を確認可能
4. **Recovery enabled** - 差し替えフローで簡単にCSVを更新可能
5. **Full correlation** - フロントエンドのバリデーション結果がバックエンドに保存される
