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
