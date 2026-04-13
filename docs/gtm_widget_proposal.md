# AitherHub GTMウィジェット＋「3つの悪魔的ハック」実装提案書

## 1. 概要と目的

先方のECサイトに「GTM経由で1行のタグを追加するだけ」で、既存デザインを一切破壊せずにフローティングUIとフルスクリーン縦型動画プレイヤーを導入する仕組みを構築します。
さらに、将来の完全自動化とCVR極大化を見据えた「3つの悪魔的ハック」を初期アーキテクチャから組み込みます。

## 2. システムアーキテクチャ

本機能は、既存のAitherHubのバックエンド/フロントエンドとは独立して動作する「ウィジェット配信システム」として実装します。

### 2.1. コンポーネント構成

1. **Widget Loader (JavaScript)**
   - GTM経由で配信される軽量なエントリーポイント（`loader.js`）
   - 顧客のサイト上で実行され、必要なUIコンポーネントを動的に生成・注入します。
2. **Floating UI & Video Player (React/Preact or Vanilla JS)**
   - 既存サイトのCSSと干渉しないよう、Shadow DOMまたはIframe内で動作するUIコンポーネント。
   - 画面右下に追従する「丸いアイコン」と、タップ時に展開される「フルスクリーン縦型動画プレイヤー」。
3. **Widget API (Backend)**
   - `backend/app/api/v1/endpoints/widget.py` を新設。
   - ウィジェットの設定情報（表示する動画、顧客IDなど）の提供、DOM解析データの受信、トラッキングイベントの受信を担当します。

## 3. 「3つの悪魔的ハック」の実装方針

### Hack 1: DOM自動解析ロジック（商品手動紐付けの撲滅）

**要件:** GTMタグ発火時に、ページのURL、商品名（h1/og:title）、画像（og:image）をスクレイピングしてサーバーへ送信。

**実装案:**
- `loader.js` の初期化処理内で、`document.querySelector` を用いてメタデータ（canonical URL, og:title, og:image, h1等）を抽出。
- 抽出したデータを非同期（`fetch` または `navigator.sendBeacon`）でバックエンドの新規エンドポイント（例: `POST /api/v1/widget/page-context`）へ送信。
- バックエンド側では、このデータを顧客ID（GTMタグに埋め込まれたトークン）と紐付けてデータベース（新規テーブル `widget_page_contexts`）に蓄積。

### Hack 2: In-Video Action（動画内完結型）ボタンの配置

**要件:** フルスクリーン動画下部に「購入する」ボタンをオーバーレイ配置し、可能なら裏側でカート追加や商品ページ遷移を行う。

**実装案:**
- 動画プレイヤーUIの最前面（`z-index: 9999`）にCTAボタンを配置。
- ボタンクリック時のアクションとして、以下の2段階のアプローチを用意：
  1. **スムーズな遷移:** 抽出したcanonical URLまたは設定された商品URLへ `window.location.href` で遷移。
  2. **DOM操作（将来拡張）:** 先方サイトのカートボタンのセレクタを事前に設定しておき、`document.querySelector(cartSelector).click()` を発火させる（クロスドメイン制約がない同一ページ内での動作を想定）。

### Hack 3: Shadow Tracking（影の計測）

**要件:** localStorageとsessionStorageにセッションIDとタイムスタンプを書き込み、サンクスページで回収して送信。

**実装案:**
- `loader.js` 初回ロード時に、一意のセッションID（UUID）を生成し、`localStorage.setItem('lcj_sid', uuid)` および `sessionStorage.setItem('lcj_sid', uuid)` で保存。
- 同時にタイムスタンプも保存。
- サンクスページ（CV地点）でGTMタグが発火した際、これらのStorageからIDを読み取り、CVイベントとしてバックエンド（例: `POST /api/v1/widget/track-cv`）へ送信。
- Cookie（ITPの影響を受けやすい）に依存しないファーストパーティのStorageを利用することで、トラッキング精度を向上。

## 4. データベース設計（追加・変更）

既存のデータベースに以下のテーブルを追加することを提案します。

1. **`widget_configs`**
   - 顧客ごとのウィジェット設定（有効/無効、テーマカラー、表示位置など）
2. **`widget_page_contexts`**
   - Hack 1で収集したDOM解析データの蓄積用
3. **`widget_tracking_events`**
   - Hack 3で収集したトラッキングデータ（PV、動画再生、CVなど）の蓄積用

## 5. 開発・デプロイ手順

1. **バックエンド実装:**
   - 新規APIエンドポイントの作成（`widget.py`）。
   - データベースマイグレーション（`main.py` の `ensure_tables_exist` または Alembic）。
2. **フロントエンド（ウィジェット）実装:**
   - `frontend/public/widget/` ディレクトリ等に、GTMから読み込まれる静的JSファイル（`loader.js`）とCSSを作成。
   - （※React等のビルドプロセスに組み込むか、Vanilla JSで軽量に実装するかは要検討。初期はVanilla JSで軽量・高速に実装することを推奨。）
3. **テスト:**
   - ローカル環境での動作確認。
   - ダミーのHTMLページを作成し、GTMタグ相当のスクリプトを埋め込んで動作検証。
4. **デプロイ:**
   - GitHubの `master` ブランチへプッシュし、Railway（またはAzure/SWA）の自動デプロイをトリガー。

## 6. 確認事項（ユーザーへの質問）

実装を進めるにあたり、以下の点についてご意見・ご指示をお願いいたします。

1. **ウィジェットの実装技術:**
   - 既存サイトへの影響を最小限にするため、Vanilla JS（フレームワークなし）で軽量な `loader.js` を作成し、UIはShadow DOMでカプセル化するアプローチでよろしいでしょうか？
2. **データベースの追加:**
   - 上記提案の3つの新規テーブル（設定、DOMデータ、トラッキング）を追加する方針で進めてよろしいでしょうか？
3. **GTMタグの形式:**
   - 先方に渡すタグは、以下のようなシンプルな形式を想定しています。
     ```html
     <script src="https://www.aitherhub.com/widget/loader.js" data-client-id="YOUR_CLIENT_ID" async></script>
     ```
     この形式で問題ないでしょうか？

以上、ご確認のほどよろしくお願いいたします。
