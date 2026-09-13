# BugLogServer

Unity クライアント（UniModules の `SendReport` モジュール）から送られるクラッシュレポートを受信・閲覧・管理するログサーバー。
FastAPI + PostgreSQL + Nginx を Docker Compose で動かすスタンドアロン構成。

プロジェクト固有の値は `.env` と DB のシステム設定で外に出してあるため、**このリポジトリをサブモジュールとして取り込めば任意のプロジェクトで使える**（URL プレフィックスのみ現状ハードコード。下表を参照）。

## リポジトリ構成

```
BugLogServer/
├── .env.example            # 環境変数テンプレート（.env は git 管理外）
├── Dockerfile
├── docker-compose.yml      # 開発用
├── docker-compose.light.yml# 軽量ホスト用の override 素材（t3.nano 等）
├── alembic.ini / alembic/  # DB マイグレーション
├── app/                    # FastAPI アプリ本体
├── nginx/nginx.conf
└── docs/                   # ローカル構築・AWS デプロイ手順
```

## 他プロジェクトへの導入

### 1. サブモジュールとして追加する

導入先プロジェクトのリポジトリルートで実行する。

```bash
git submodule add git@github.com:oTAMAKOo/BugLogServer.git BugLogServer
git commit -m "BugLogServer をサブモジュールとして追加"
```

クローン済みのリポジトリで取得する場合:

```bash
git submodule update --init --recursive
```

### 2. プロジェクト毎の設定を `.env` に書く

`.env` は `.gitignore` 対象なので、導入先プロジェクトのリポジトリには**含めない**（秘匿情報のため）。

```bash
cd BugLogServer
cp .env.example .env
```

プロジェクト毎に必ず見直す項目:

| キー | 内容 |
|---|---|
| `REPORT_AES_KEY` / `REPORT_AES_IV` | Unity クライアント側の `PLCryptoAES.KEY`（32文字）/ `PLCryptoAES.IV`（16文字）と一致させる。ここがズレるとレポートを復号できない |
| `URL_PREFIX` | 管理画面・API の URL プレフィックス。**現状この値はアプリから読まれておらず、`/buglog` が `app/main.py` と `app/routers/admin.py` にハードコードされている** |
| `SECRET_KEY` | セッション署名用。プロジェクト毎にランダム文字列を設定する |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 起動時に作られる初期管理者。初回ログイン後に変更する |
| `DATABASE_URL` | 既定の Docker Compose 構成のままなら変更不要 |
| `STORAGE_MODE` | `local`（既定）または `s3`。`s3` の場合は `AWS_*` を設定する |

AES Key/IV とセッション有効期限は、起動後に管理画面のシステム設定（`/buglog/system`）からも変更できる。

### 3. 起動する

```bash
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

ブラウザで `http://127.0.0.1/buglog/login` を開く（`localhost` ではなく `127.0.0.1`。Docker の IPv4 問題を避けるため）。

詳細は [docs/local_setup_guide.md](docs/local_setup_guide.md)。

### 4. 本番環境へデプロイする

AWS EC2 へのデプロイ手順は用途別に 2 種類ある。

- [docs/aws_deploy_budget.md](docs/aws_deploy_budget.md) — 低コスト構成（t3.nano 等）
- [docs/aws_deploy_standard.md](docs/aws_deploy_standard.md) — 標準構成（t3.micro 以上）
- [docs/aws_deployment_guide.md](docs/aws_deployment_guide.md) — 共通のデプロイガイド

デプロイ先のドメイン・EC2 インスタンス ID・SSH 鍵のパスは**プロジェクト毎に異なる**ため、このリポジトリには持たせていない。導入先プロジェクト側（例: `.claude/commands/` の運用コマンド、CI の設定）で管理する。

## クライアント側

送信側は Unity の `UniModules` リポジトリにある
`Assets/UniModules/Scripts/Modules/Devkit/Diagnosis/SendReport/` が担当する。
サーバーの受信エンドポイントは `POST /buglog/report`。

## 開発ルール

- 常に日本語で返答・コメントする
- テンプレートはダーク / ライト両テーマ対応（CSS 変数を使用）
- アイコンは [Unity Editor Icons](https://github.com/halak/unity-editor-icons) から取得
- フォント: Inter（Google Fonts）
