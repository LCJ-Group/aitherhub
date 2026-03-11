# デプロイワークフロー改善案

## 背景

2026-03-11にコミット `b959117` がサーバーをクラッシュさせた。
CIにスモークテストがなかったため、壊れたコードがそのままデプロイされた。

## 変更内容

`master_aitherhubapi.yml` に以下を追加する必要がある：

### 1. `pre_deploy_check` ジョブの追加

`build_and_deploy` の前に実行され、`app.main` が正常にimportできるか検証する。

```yaml
  pre_deploy_check:
    runs-on: ubuntu-latest
    name: Pre-deploy smoke test
    steps:
      - name: Checkout source
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        working-directory: backend
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run startup smoke test
        run: python tests/test_startup_smoke.py

      - name: Run import boundary tests
        run: python tests/test_import_boundaries.py
```

### 2. `build_and_deploy` に `needs: pre_deploy_check` を追加

```yaml
  build_and_deploy:
    needs: pre_deploy_check
    ...
```

### 3. `verify_deploy` の改善

- wait 時間を 60s → 90s に増加
- リトライを 3回 → 5回に増加
- **失敗時に `exit 1` でワークフローを FAIL にする**（現在は FAIL でも success になる）

## 適用方法

GitHub App の権限制限で `.github/workflows/` の変更は自動プッシュできないため、
GitHub Web UI で直接編集するか、Personal Access Token でプッシュする必要がある。
