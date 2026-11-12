# CI と自動マージ

## CI（`.github/workflows/ci.yml`）

PR（master / main 向け）と master / main への push のたびに、GitHub Actions のジョブ `build` が次を行う。

- `python -m unittest discover -s tests -t . -v`（単体テストの実行）
- `docker build -t pocket-dev:ci .`（Docker イメージのビルド確認）

Python は 3.12。アクションはコミットの SHA で固定している（更新するときは SHA ごと書き換える）。

## master / main の保護と自動マージ

- master / main は `build` が通らないとマージできない（ブランチの保護。管理者は対象外にしてあり、緊急時は直接 push できる）
- リポジトリで自動マージを有効にし、マージしたブランチは自動で消える
- PR を作ったら、自動マージを予約する。`build` が通った時点でマージされ、失敗したらマージされない

```bash
gh pr create --base master --title "…" --body "…"
gh pr merge --auto --merge   # build が通ったらマージする（マージコミット）
```

CI が失敗したら、同じブランチに直して push すれば、もう一度 CI が走り、通ればそのままマージされる。

## CD（`.github/workflows/release.yml`）

`v1.2.3` の形のタグを push すると、ジョブ `release` が次を行う（pocket-dev には版の番号のファイルが無いので、タグがそのまま版になる）。

1. 単体テスト
2. Docker イメージを linux/amd64 と linux/arm64（Pocket Linux など）の 2 つで作り、GitHub Container Registry に置く
   - `ghcr.io/kawamonn91/pocket-dev:1.2.3`・`:1.2`・`:latest`
3. GitHub Releases を作る（`docker pull` の書き方を本文に書く）

手で動かす（`gh workflow run release.yml --ref master`）と、イメージを作るまでにして、置かない・リリースも作らない（CD の動作確認用）。
Secrets は使わない（GHCR へは GitHub が渡すトークンで書く）。

最初に置いたとき、GHCR のパッケージが非公開になっていたら、GitHub のパッケージの設定（Package settings → Change visibility）で公開にする。

### 新しい版を出す

```bash
git switch master && git pull --ff-only
git tag v1.2.3 && git push origin v1.2.3
gh run watch "$(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

置いたイメージは、`docker-compose.yml` の `build: .` の代わりに `image: ghcr.io/kawamonn91/pocket-dev:1.2.3` と書けば、ビルドせずに使える。
