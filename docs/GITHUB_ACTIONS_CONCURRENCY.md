# GitHub Actions Concurrency 設定手順

## 背景

GitHub App にはワークフローファイルの変更権限がないため、以下の変更は GitHub UI から直接行う必要があります。

## 設定内容

3つのワークフローファイルに `concurrency` ブロックを追加してください。

### 1. `.github/workflows/master_aitherhubapi.yml`

`on:` ブロックの直後に追加：

```yaml
concurrency:
  group: deploy-aitherhubapi
  cancel-in-progress: false
```

### 2. `.github/workflows/master_fast-api-kyogoku.yml`

`on:` ブロックの直後に追加：

```yaml
concurrency:
  group: deploy-fast-api
  cancel-in-progress: false
```

### 3. `.github/workflows/githubworkflowsdeploy-swa-frontend.yml`

`on:` ブロックの直後に追加：

```yaml
concurrency:
  group: deploy-frontend
  cancel-in-progress: false
```

## 効果

- 同じグループのデプロイが同時実行されなくなる
- 409 Conflict エラーを防止
- `cancel-in-progress: false` により、実行中のデプロイは完了まで待機

## 設定方法

1. GitHub リポジトリの Code タブを開く
2. 各ワークフローファイルを開く
3. 鉛筆アイコン（Edit）をクリック
4. `on:` ブロックの直後に上記の `concurrency` ブロックを追加
5. "Commit changes" で直接 master にコミット
