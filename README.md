# UniModules.SendReportServer

Unity クライアント（UniModules の `SendReport` モジュール）から送られるクラッシュレポートを受信・閲覧・管理するログサーバー。
FastAPI + PostgreSQL + Nginx を Docker Compose で動かすスタンドアロン構成。

プロジェクト固有の値は `.env` と DB のシステム設定で外に出してあるため、**このリポジトリをサブモジュールとして取り込めば任意のプロジェクトで使える**。

## リポジトリ構成

```
UniModules.SendReportServer/
├── .env.example            # 環境変数テンプレート（.env は git 管理外）
├── Dockerfile
├── docker-compose.yml      # 開発用
├── docker-compose.prod.yml.example  # 本番用 override の雛形（実ファイルは git 管理外）
├── docker-compose.light.yml# 軽量ホスト用の DB 設定（本番では prod.yml の db.command に書く）
├── alembic.ini / alembic/  # DB マイグレーション
├── app/                    # FastAPI アプリ本体
├── nginx/nginx.conf
└── docs/                   # ローカル構築・AWS デプロイ手順・Claude Code 連携
```

## 他プロジェクトへの導入

### 1. サブモジュールとして追加する

導入先プロジェクトのリポジトリルートで実行する。

```bash
git submodule add git@github.com:oTAMAKOo/UniModules.SendReportServer.git UniModules.SendReportServer
git commit -m "UniModules.SendReportServer をサブモジュールとして追加"
```

クローン済みのリポジトリで取得する場合:

```bash
git submodule update --init --recursive
```

### 2. プロジェクト毎の設定を `.env` に書く

`.env` は `.gitignore` 対象なので、導入先プロジェクトのリポジトリには**含めない**（秘匿情報のため）。

```bash
cd UniModules.SendReportServer
cp .env.example .env
```

プロジェクト毎に必ず見直す項目:

| キー | 内容 |
|---|---|
| `INITIAL_PROJECT_SLUG` / `INITIAL_PROJECT_NAME` | 初回起動（マイグレーション 007）で作られる最初のプロジェクトの slug と表示名（任意、既定 `default` / `Default`）。slug は受信 URL と管理画面 URL に使う |
| `URL_PREFIX` | 管理画面・API の URL プレフィックス（既定 `/buglog`）。先頭の `/` は省略可、末尾の `/` は無視される。空にするとルート直下（`/login` 等）にマウントされる |
| `SECRET_KEY` | セッション署名用。プロジェクト毎にランダム文字列を設定する |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 起動時に作られる初期管理者。初回ログイン後に変更する |
| `DATABASE_URL` | 既定の Docker Compose 構成のままなら変更不要 |
| `STORAGE_MODE` | `local`（既定）または `s3`。`s3` の場合は `AWS_*` を設定する。`s3` ではスクリーンショットを期限付きの署名付き URL で配信するのでバケットは非公開のままにする（`local` は `/storage/` を認証なしで配信する開発用途） |
| `S3_PRESIGN_EXPIRE_SECONDS` | `s3` の署名付き URL の有効期限（秒）。既定 `1800`（30 分）、60〜604800 |
| `PUBLIC_BASE_URL` / `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google ログインを使う場合に設定する（任意）。未設定ならパスワード認証のみ |
| `ADMIN_GOOGLE_EMAIL` | ロックアウト復旧用（任意）。起動のたびに管理者権限を保証する Google アカウント |
| `MCP_ENABLED` | Claude Code 向け MCP サーバー（`/buglog/mcp`）を公開するか（既定 `true`） |

AES Key/IV は**プロジェクトごと**に管理画面で設定する（システム管理者は `/buglog/admin/projects`、プロジェクト管理者は `/buglog/p/<slug>/settings`）。
Unity クライアント側の `PLCryptoAES.KEY`（32文字）/ `PLCryptoAES.IV`（16文字）と一致させる。ズレていると受信が 400 になる。
セッション有効期限は全体共通で、システム設定（`/buglog/system`）から変更できる。

管理画面のログインは、ユーザー名 + パスワードに加えて **Google アカウント**でも行える（任意）。
管理者が招待した Google アカウントだけがログインでき、Google ログインからユーザーが勝手に作られることはない。
GCP 側の設定・招待の流れ・ロックアウト時の復旧は [docs/google_auth_setup.md](docs/google_auth_setup.md)。

### 3. 起動する

```bash
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

ブラウザで `http://127.0.0.1/buglog/login` を開く（`localhost` ではなく `127.0.0.1`。Docker の IPv4 問題を避けるため）。

詳細は [docs/local_setup_guide.md](docs/local_setup_guide.md)。

### 4. 本番環境へデプロイする

AWS へのデプロイ手順は構成別に 3 種類ある。常時公開する最安構成は Lightsail 版。

- [docs/aws_deploy_lightsail.md](docs/aws_deploy_lightsail.md) — Lightsail 最小バンドル（$5 定額・固定 IP 込み。現行の推奨）
- [docs/aws_deploy_budget.md](docs/aws_deploy_budget.md) — EC2 低コスト構成（t4g.nano 等）
- [docs/aws_deploy_standard.md](docs/aws_deploy_standard.md) — EC2 標準構成（t4g.micro 以上）
- [docs/aws_deployment_guide.md](docs/aws_deployment_guide.md) — 構成の選び方と共通事項

更新手順（`git pull` → `up -d --build`）と、ファイルコピーで配置した環境を git 管理に切り替える手順は
`aws_deploy_lightsail.md` の 12 章にある。

デプロイ先のドメイン・EC2 インスタンス ID・SSH 鍵のパスは**プロジェクト毎に異なる**ため、このリポジトリには持たせていない。導入先プロジェクト側（例: `.claude/commands/` の運用コマンド、CI の設定）で管理する。

## 複数プロジェクトを 1 台で扱う

1 つのサーバーで複数の Unity プロジェクトのレポートを受けられる。レポートは**プロジェクト**（URL 用の `slug` と表示名、AES Key/IV を持つ）に属し、
ユーザーは所属するプロジェクトのレポートだけが見える。ユーザーアカウントは全体で 1 つで、プロジェクトごとの**所属と役割**で見える範囲が決まる。

| 役割 | 判定 | できること |
|---|---|---|
| システム管理者 | ユーザーの「システム管理者」フラグ | プロジェクトの作成・削除（`/buglog/admin/projects`）、全ユーザー管理（`/buglog/admin/users`）、全プロジェクトの管理 |
| プロジェクト管理者 | 所属の役割 = 管理者 | メンバーの追加・新規ユーザー作成・役割変更・除外、AES Key/IV の設定、期間指定の一括削除 |
| メンバー | 所属の役割 = メンバー | レポートの閲覧・削除、Markdown コピー、自分の API トークン |

- 受信 URL はプロジェクトごとに `POST /buglog/report/<slug>`。管理画面は `/buglog/p/<slug>/list` のようにプロジェクト配下に分かれる
- ログイン後は所属プロジェクトが 1 つならその一覧へ、複数ならプロジェクト選択画面（`/buglog/projects`）へ進む。ナビバーで切り替えられる
- プロジェクトの追加はシステム管理者が `/buglog/admin/projects` で行う（AES Key/IV はランダム生成されるので、Unity 側の鍵をそれに合わせるか、後からプロジェクト設定で書き換える）
- 旧 URL（`/buglog/detail/<id>` など）は所属を確認したうえで新しい URL へ転送される

## Claude Code からレポートを読む

管理画面はログイン必須のため、レポートの URL をそのまま Claude に渡しても中身は読めない。
代わりに **MCP サーバー**（`/buglog/mcp`）と**読み取り専用 API**（`/buglog/api/reports`）を用意しており、
管理画面の「API トークン」で発行した個人トークンで認証する。

1. 管理画面 → 管理メニュー → **API トークン** でトークンを発行する（各エンジニアが自分の分を発行）
2. 発行したトークンを環境変数 `BUGLOG_TOKEN` に設定する
3. 導入先プロジェクトの `.mcp.json` に MCP サーバーを登録する（トークンは `${BUGLOG_TOKEN}` で参照し、ファイルには書かない）
4. レポートの URL（`/buglog/p/<slug>/detail/<id>`）を Claude Code に貼ると、`get_report` ツールでログ全文を読んで調査できる

トークン 1 つで所属する全プロジェクトのレポートを読める。検索（`search_reports` / `list_recent_reports`）は
所属が 2 つ以上あるときプロジェクトの `slug` を指定する（1 つなら省略可。`list_projects` で確認できる）。
設定手順・ツール一覧・トラブルシューティングは [docs/claude_integration.md](docs/claude_integration.md)。
MCP サーバーは `.env` の `MCP_ENABLED=false` で無効化できる（読み取り API は常に有効）。

## クライアント側

送信側は Unity の `UniModules` リポジトリにある
`Assets/UniModules/Scripts/Modules/Devkit/Diagnosis/SendReport/` が担当する。
サーバーの受信エンドポイントは `POST /buglog/report/<slug>`（`<slug>` は管理画面で作ったプロジェクトの識別子。
プロジェクト設定画面に送信先 URL がそのまま表示される）。AES Key/IV はそのプロジェクトの設定と一致させる。

## 開発ルール

- 常に日本語で返答・コメントする
- テンプレートはダーク / ライト両テーマ対応（CSS 変数を使用）
- アイコンは [Unity Editor Icons](https://github.com/halak/unity-editor-icons) から取得
- フォント: Inter（Google Fonts）

## ライセンス

[MIT License](LICENSE)。

`app/static/icons/` のアイコン画像は Unity Editor のアイコン（Unity Technologies の著作物）を
[halak/unity-editor-icons](https://github.com/halak/unity-editor-icons) から取得したもので、
本リポジトリのライセンスの対象外。
