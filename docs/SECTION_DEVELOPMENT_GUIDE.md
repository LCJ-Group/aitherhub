# AitherHub セクション開発ガイド

## 概要

AitherHub の動画詳細画面は複数の独立したセクションで構成されています。
各セクションは **Frontend Resilience 基盤** に従い、以下の3層で保護されています。

| 層 | コンポーネント | 役割 |
|---|---|---|
| 1 | `SectionErrorBoundary` | コンポーネントクラッシュの局所化 |
| 2 | `useSectionState` | API状態管理の統一（loading/empty/error/success） |
| 3 | `SectionStateUI` | UI表示の統一（エラータイプ別表示） |

## 新セクション追加手順

### 1. テンプレートをコピー

```bash
cp src/components/templates/SectionTemplate.jsx src/components/YourNewSection.jsx
```

### 2. 設定を変更

```javascript
const SECTION_NAME = "YourNewSection";     // 英語。ログ検索に使う
const API_ENDPOINT = "/your-endpoint";     // /video/{id} の後に続くパス
const SECTION_TITLE = "新セクション名";      // 日本語表示名
const SECTION_SUBTITLE = "説明文";          // サブタイトル
```

### 3. fetchFn を実装

```javascript
fetchFn: async (safeFetch) => {
  const baseURL = import.meta.env.VITE_API_BASE_URL;
  return await safeFetch(`${baseURL}/api/v1/video/${videoId}/your-endpoint`);
},
```

### 4. renderContent() にUIを実装

`data` が null でないことが保証された状態で呼ばれます。

### 5. VideoDetail.jsx に配置

```jsx
<SectionErrorBoundary sectionName="YourNewSection">
  <YourNewSection videoId={videoId} />
</SectionErrorBoundary>
```

## PRレビュー基準

### 必須チェック項目

| # | チェック項目 | 理由 |
|---|---|---|
| 1 | `useSectionState` を使用しているか | API状態管理の統一 |
| 2 | `SectionStateUI` で loading/empty/error を表示しているか | UI表示の統一 |
| 3 | `SectionErrorBoundary` で囲まれているか（VideoDetail.jsx側） | クラッシュの局所化 |
| 4 | `SECTION_NAME` が英語で統一されているか | ログ検索の一貫性 |
| 5 | `safeFetch` 経由でAPIコールしているか（useSectionState内蔵） | タイムアウト・リトライ・エラー分類の統一 |
| 6 | 生の `fetch` / `axios` を直接使っていないか | safeFetch を迂回するとエラーハンドリングが漏れる |
| 7 | `try/catch` で個別にエラーハンドリングしていないか | useSectionState が統一管理する |
| 8 | `console.error` でエラーを握りつぶしていないか | `logSectionError` で構造化ログに記録する |

### 推奨チェック項目

| # | チェック項目 | 理由 |
|---|---|---|
| 1 | `fetchOnMount: true/false` が適切か | 自動フェッチ vs ボタン押下の使い分け |
| 2 | セクション固有のUI状態（collapsed等）が独立しているか | 他セクションに影響しない |
| 3 | データが空の場合の `empty` 判定が適切か | useSectionState のデフォルト判定で足りない場合は `isEmpty` オプションを使う |
| 4 | エラー時のリトライボタンが機能するか | `retry` 関数が正しく渡されている |

## エラータイプ一覧

| タイプ | HTTPステータス | ユーザー表示 | アクション |
|---|---|---|---|
| `auth` | 401, 403 | ログインセッションが切れました | 再ログイン導線 |
| `not_found` | 404 | まだ生成されていません | 生成案内 |
| `timeout` | - | サーバーの応答に時間がかかっています | 再試行 |
| `network` | - | ネットワーク接続を確認してください | 再試行 |
| `server` | 500-599 | サーバーで一時的な障害が発生しています | 再試行 |
| `rate_limit` | 429 | リクエスト制限に達しました | 再試行 |
| `parse` | - | データの読み込みに失敗しました | 再試行 |
| `unknown` | - | エラーが発生しました | 再試行 |

## ログ相関

### フロントエンド → バックエンド突合フロー

```
1. フロントエンドでエラー発生
2. logSectionError() が構造化ログを記録
   → video_id / section_name / endpoint / error_type / request_id
3. reportToBackend() が POST /admin/frontend-diagnostics に送信
4. Admin Diagnostics 画面で確認可能
5. Backend ログで同じ request_id を grep して突合
```

### request_id の仕組み

- フロントエンド: `BaseApiService` が全リクエストに `X-Request-Id: fe-{timestamp}-{random}` を自動付与
- バックエンド: `RequestIdMiddleware` が `X-Request-Id` を受け取り、全ログに含める
- レスポンスヘッダーにも `X-Request-Id` が返る

### ログ検索例

```bash
# Backend ログで特定の request_id を検索
grep "fe-1709876543210-abc123" /var/log/aitherhub/*.log

# 特定の video_id のエラーを検索
grep "video_id=VIDEO_ID_HERE" /var/log/aitherhub/*.log
```

## ファイル構成

```
frontend/src/
├── base/
│   ├── api/
│   │   ├── BaseApiService.js    # X-Request-Id 自動付与
│   │   └── safeFetch.js         # タイムアウト・リトライ・エラー分類
│   ├── hooks/
│   │   └── useSectionState.js   # セクション状態管理 hook
│   └── utils/
│       └── runtimeErrorLogger.js # 構造化ログ + Backend送信
├── components/
│   ├── SectionErrorBoundary.jsx # クラッシュ局所化
│   ├── SectionStateUI.jsx       # 4状態統一表示
│   ├── templates/
│   │   └── SectionTemplate.jsx  # 新セクション用テンプレート
│   └── admin/
│       └── AdminDiagnostics.jsx # Admin診断画面
└── ...

backend/app/
├── core/
│   └── request_id_middleware.py  # X-Request-Id + ContextVar + 構造化ログ
└── api/v1/endpoints/
    └── admin.py                  # POST/GET /frontend-diagnostics
```
