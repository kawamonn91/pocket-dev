# pocket-dev

スマホ・タブレットのブラウザから使う**ターミナル**と**コードエディタ**。
端末の root 化は不要、アプリのインストールも不要（ブラウザだけ）。

---

## なぜ Termux ではなくこの構成なのか

Termux が使いにくいのは設定の問題ではなく、Android 側の構造的な制約です。

| Termux の制約 | 原因 | pocket-dev では |
| --- | --- | --- |
| 普通の Linux バイナリが動かない | Bionic libc・FHS 非準拠（`/usr/bin` が無い） | Debian(glibc) コンテナなので `#!/usr/bin/python3` もそのまま動く |
| pip の wheel や .deb が入らない | manylinux 非対応 | manylinux wheel・apt パッケージがそのまま入る |
| パッケージが少ない | Termux 独自リポジトリ | Debian の全リポジトリが使える |
| バックグラウンドで落ちる | Android 12+ の phantom process killer | 端末外で動くので無関係 |
| Docker が動かない | 非 root では namespace/cgroup を作れない | Docker そのものの中で動く |

proot で Debian を被せても ptrace 由来の遅さ・`mount`/`fuse` 不可は残るため、
**コンテナを端末の外で動かし、スマホ・タブレットは画面だけを担当する**構成にしています。

```text
┌──────────────┐        ┌──────────────┐
│  スマホ       │        │  タブレット    │
│  ターミナル    │        │  IDE          │   ← ブラウザ（PWA）
└──────┬───────┘        └──────┬───────┘
       └────── WebSocket ──────┘
                   │  同じワークスペースを共有
       ┌───────────▼────────────┐
       │  Docker コンテナ (root) │  ← PC / VPS
       │  apt, pip, gcc, git …   │
       └────────────────────────┘
```

タブレットで編集 → スマホのターミナルで実行、が同一ワークスペース上で成立します。

---

## 使い方

```bash
cd /mnt/c/dev/pocket-dev
./scripts/gen-token.sh          # URL を固定するトークンを .env に生成
docker compose up -d --build
docker compose logs | grep http # 接続URLを確認
```

表示された URL をスマホ・タブレットで開きます（初回だけトークン付き URL、
以降は Cookie で認証されます）。ブラウザの「ホーム画面に追加」でアプリのように全画面起動できます。

- ターミナル: `http://<PCのIP>:8765/?token=...`
- IDE: `http://<PCのIP>:8765/ide.html?token=...`

Docker を使わずこの PC で直接動かす場合（動作確認用）:

```bash
./scripts/dev-run.sh ./workspace
```

---

## セキュリティ

コンテナ内は root なので、**接続範囲を絞ることが前提の設計**です。

- 認証トークンは全エンドポイントで必須（`/health` のみ例外）。`secrets.compare_digest` で定数時間比較
- ファイル API は `--root` 配下に封じ込め。`..` とシンボリックリンクによる脱出を拒否（テスト済み）
- 既定の待受は `127.0.0.1`。`docker-compose.yml` のポート公開範囲で外部露出を制御する
  - このPCだけで使う → `"127.0.0.1:8765:8765"`
  - 同一LANのスマホ・タブレットから使う → `"8765:8765"`（既定）
  - 外出先から使う → **インターネットに直接公開しない**。Tailscale などの VPN 経由にする

---

## 構成

```text
pocket-dev/
├── agent/                  # コンテナ内で動くサーバ（標準ライブラリのみ・pip 不要）
│   ├── server.py           #   HTTP + WebSocket、ルーティング
│   ├── websocket.py        #   WebSocket (RFC 6455) 実装
│   ├── pty_session.py      #   擬似端末（forkpty・リサイズ対応）
│   ├── files.py            #   ファイル API（root 封じ込め）
│   └── auth.py             #   トークン認証
├── web/
│   ├── terminal.html       # スマホ用ターミナル（xterm.js）
│   └── ide.html            # タブレット用IDE（Monaco）
├── workspace/              # 作業ファイル（ホストと共有）
├── Dockerfile
├── docker-compose.yml
└── tests/                  # 71 件（デバイス・外部通信なしで実行可能）
```

```bash
python3 -m unittest discover -s tests -t .
```

---

## 実装済み / 未実装

- [x] PTY ターミナル（vim・top・色・ジョブ制御・リサイズ対応）
- [x] スマホ向けキーバー（Esc / Tab / Ctrl / Alt / 矢印 / ^C / ^D）、自動再接続、文字サイズ
- [x] ファイル API（一覧・読み・書き・作成・削除・リネーム・バイナリ配信）
- [x] トークン認証、パス封じ込め
- [x] IDE 本体（Monaco・ファイルツリー・タブ・保存・言語判定・長押しで改名/削除）
- [x] IDE 内ターミナル（「▶ このファイルを実行」で保存して即実行）
- [x] PDF プレビュー（pdf.js）・画像プレビュー
- [ ] 全文検索・Git 操作
- [ ] Google Drive 連携
      ※ `/dev/fuse` が使えないため `rclone mount` ではなく Drive API 方式で実装する
- [ ] オフライン用に CDN 資材をローカルへ取り込み（Service Worker）

## 注意

コンテナ内で `apt install` したパッケージは、コンテナを作り直す
（`docker compose up --build`）と消えます。常用するものは Dockerfile に追記してください。
