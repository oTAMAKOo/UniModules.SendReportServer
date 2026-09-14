# ローカル開発環境セットアップ手順（Claude向け）

このドキュメントは、別PCでClaude（AI）にローカル開発環境を構築させるための手順書です。
Claude Codeに「この手順書に従ってローカル環境をセットアップして」と指示してください。

---

## プロジェクト概要

- **用途**: Unityクライアントからのクラッシュレポート（AES暗号化）を受信・管理するログサーバー
- **技術スタック**: FastAPI + PostgreSQL + Nginx + Docker Compose
- **言語**: Python 3.11
- **管理画面**: Jinja2テンプレート + Bootstrap 5（ダークテーマ対応）

---

## 前提条件

以下がインストールされていること:
- **Docker Desktop**（Windows / Mac）
- **Git**

> Docker Desktop がインストールされていない場合は、https://www.docker.com/products/docker-desktop/ からインストールしてください。
> インストール後、Docker Desktop を起動して Docker Engine が Running 状態であることを確認してください。

---

## セットアップ手順

### Step 1: リポジトリのクローン

```bash
git clone git@github.com:oTAMAKOo/UniModules.SendReportServer.git
cd UniModules.SendReportServer
```

> 他プロジェクトへサブモジュールとして組み込む場合は、リポジトリ直下の `README.md` を参照してください。

### Step 2: .env ファイルの作成

リポジトリ直下に `.env` ファイルを作成してください。
`.env.example` をコピーして作成できます。

```bash
cp .env.example .env
```

`.env` の内容（開発用デフォルト値）:
```env
# Database
DATABASE_URL=postgresql://logserver:logserver@db:5432/logserver

# 最初のプロジェクト（任意）。AES Key/IV は起動後に管理画面のプロジェクト設定で確認・変更する
# INITIAL_PROJECT_SLUG=default
# INITIAL_PROJECT_NAME=Default

# Storage mode: "local" or "s3"
# local は /storage/ 配下を nginx が認証なしで直接配信する（開発用途。画像を保護しない）。
# s3 は期限付きの署名付き URL で配信する（S3_PRESIGN_EXPIRE_SECONDS、既定 30 分）。
STORAGE_MODE=local
LOCAL_STORAGE_PATH=/app/storage

# AWS S3（ローカル開発では空のままでOK）
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_S3_BUCKET_NAME=
AWS_REGION=ap-northeast-1

# Admin credentials（初回起動時に自動作成される管理者アカウント）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=password

# Session secret
SECRET_KEY=change-this-to-a-random-string
```

> **AES Key/IV について**: AES Key/IV は**プロジェクトごと**に DB で管理します。初回起動時に最初のプロジェクト（既定 `default`）が
> ランダムな鍵で作られるので、Unity クライアントと通信する場合は管理画面のプロジェクト設定（`/buglog/p/default/settings`）で
> クライアント側の `PLCryptoAES.KEY`（32文字）と `PLCryptoAES.IV`（16文字）に合わせてください。
> 受信 URL は `POST /buglog/report/<slug>` で、鍵が一致しないと 400 が返ります。

### Step 3: Docker Compose でビルド & 起動

```bash
docker compose build
docker compose up -d
```

> 初回ビルドは数分かかります（Python依存パッケージのインストールのため）。

### Step 4: データベースマイグレーション実行

```bash
docker compose exec app alembic upgrade head
```

以下のように表示されれば成功:
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001, create report_data table
INFO  [alembic.runtime.migration] Running upgrade 001 -> 002, create admin_user table
INFO  [alembic.runtime.migration] Running upgrade 002 -> 003, create system_config table
INFO  [alembic.runtime.migration] Running upgrade 003 -> 004, add last_login_at
INFO  [alembic.runtime.migration] Running upgrade 004 -> 005, add google auth columns to admin_user
INFO  [alembic.runtime.migration] Running upgrade 005 -> 006, create api_token table
INFO  [alembic.runtime.migration] Running upgrade 006 -> 007, create project / project_member and attach reports to a project
INFO  [alembic.runtime.migration] 最初のプロジェクトを作成しました: slug=default name=Default
```

### Step 5: 動作確認

```bash
curl http://127.0.0.1/buglog/login
```

HTMLが返ってくればOK。ブラウザで以下のURLにアクセス:

- **ログイン画面**: http://127.0.0.1/buglog/login
- **API ヘルスチェック**: http://127.0.0.1/

> **重要**: `localhost` ではなく `127.0.0.1` を使用してください（DockerがIPv4のみバインドしている場合があるため）。

### Step 6: ログイン

- ユーザー名: `admin`（.envの `ADMIN_USERNAME`）
- パスワード: `password`（.envの `ADMIN_PASSWORD`）

> これは初回起動時に自動作成されるアカウントです。ログイン後、管理画面からパスワードを変更できます。

---

## ダミーデータの投入（任意）

テスト用のレポートデータを投入したい場合:

```bash
# ダミーデータの元ファイルが ../dummy/ にあることが前提
docker compose exec app python seed_dummy.py
```

> `seed_dummy.py` は MySQL ダンプファイルを読み込んで PostgreSQL にデータを投入するスクリプトです。
> `../dummy/` ディレクトリにダンプファイルとダミー画像が必要です。

---

## サービス構成

```
docker compose ps で確認できるサービス:

┌─────────────┬──────────────────────────┬───────────┐
│ サービス     │ 役割                      │ ポート     │
├─────────────┼──────────────────────────┼───────────┤
│ db          │ PostgreSQL 16            │ 5432      │
│ app         │ FastAPI (uvicorn)        │ 8000      │
│ nginx       │ リバースプロキシ + 静的配信 │ 80        │
└─────────────┴──────────────────────────┴───────────┘

ブラウザ → Nginx(:80) → FastAPI(:8000) → PostgreSQL(:5432)
                ↓
          /storage/ (画像ファイル直接配信)
```

---

## 主要URL一覧

| URL | 説明 |
|-----|------|
| `http://127.0.0.1/` | API ヘルスチェック |
| `http://127.0.0.1/buglog/login` | ログイン画面 |
| `http://127.0.0.1/buglog/projects` | プロジェクト選択（所属が 1 つならログイン後は一覧へ直行） |
| `http://127.0.0.1/buglog/p/{slug}/list` | レポート一覧（プロジェクト単位） |
| `http://127.0.0.1/buglog/p/{slug}/detail/{id}` | レポート詳細 |
| `http://127.0.0.1/buglog/p/{slug}/users` | メンバー管理（プロジェクト管理者） |
| `http://127.0.0.1/buglog/p/{slug}/settings` | プロジェクト設定（AES Key/IV、受信 URL） |
| `http://127.0.0.1/buglog/p/{slug}/manage` | レポート管理（一括削除） |
| `http://127.0.0.1/buglog/admin` | 管理メニュー |
| `http://127.0.0.1/buglog/admin/projects` | プロジェクト管理（システム管理者） |
| `http://127.0.0.1/buglog/admin/users` | 全ユーザー管理（システム管理者） |
| `http://127.0.0.1/buglog/system` | システム設定（セッション有効期限） |
| `http://127.0.0.1/buglog/tokens` | API トークン |
| `http://127.0.0.1/docs` | FastAPI Swagger UI |

---

## よく使う操作

### コンテナの起動・停止
```bash
# 起動
docker compose up -d

# 停止
docker compose down

# ログ確認
docker compose logs -f app
```

### ソースコード変更の反映
開発時は `app/` ディレクトリがボリュームマウントされているため、
Pythonファイルを編集すると uvicorn の `--reload` が自動で反映します。

**テンプレート（HTML）やCSS の変更**: 即座に反映（ブラウザリロードのみ）
**Python コードの変更**: uvicorn が自動リロード
**Dockerfile / requirements.txt の変更**: 再ビルドが必要

```bash
docker compose build app
docker compose up -d
```

### Alembic マイグレーション（モデル変更後）
新しいマイグレーションファイルが追加された場合:
```bash
docker compose build app    # マイグレーションファイルをコンテナにコピー
docker compose up -d
docker compose exec app alembic upgrade head
```

### DB直接操作
```bash
# PostgreSQL に接続
docker compose exec db psql -U logserver logserver

# テーブル一覧
\dt

# レポート件数確認
SELECT COUNT(*) FROM report_data;

# 終了
\q
```

### コンテナ内でコマンド実行
```bash
docker compose exec app bash
```

---

## ディレクトリ構成

```
UniModules.SendReportServer/
├── .env                    # 環境変数（git管理外）
├── .env.example            # .env のテンプレート
├── Dockerfile              # Python 3.11 ベースのアプリイメージ
├── docker-compose.yml      # 開発用 Docker Compose 設定
├── requirements.txt        # Python依存パッケージ
├── alembic.ini             # Alembic設定
├── alembic/
│   ├── env.py              # マイグレーション環境設定
│   ├── script.py.mako      # マイグレーションテンプレート
│   └── versions/           # マイグレーションファイル
│       ├── 001_create_report_data.py
│       ├── 002_create_admin_user.py
│       ├── 003_create_system_config.py
│       ├── 004_add_last_login_at.py
│       ├── 005_add_google_auth.py   # email / google_sub 追加、password_hash の NULL 許容
│       ├── 006_create_api_token.py  # 読み取り API / MCP 用の個人トークン
│       └── 007_create_project.py    # プロジェクト・所属の追加、レポートの紐付け、画像キーの書き換え
├── nginx/
│   └── nginx.conf          # Nginx設定（リバースプロキシ）
├── app/
│   ├── main.py             # FastAPIアプリケーション エントリーポイント
│   ├── config.py           # 設定（pydantic-settings、.envから読み込み）
│   ├── database.py         # SQLAlchemy エンジン & セッション
│   ├── models.py           # DBモデル（Project, ProjectMember, ReportData, AdminUser, SystemConfig, ApiToken）
│   ├── schemas.py          # Pydantic スキーマ
│   ├── auth.py             # 認証（bcryptハッシュ、セッショントークン、API トークン）
│   ├── authz.py            # 認可（プロジェクトの所属と役割、ログイン必須の依存関数）
│   ├── templating.py       # Jinja2 環境（全ルーター共通、ナビ用 context processor）
│   ├── crypto.py           # AES-256-CBC 復号処理（鍵はプロジェクトごと）
│   ├── storage.py          # 画像保存（ローカル / S3 対応）
│   ├── ratelimit.py        # IP ごとのレート制限（ログイン / Google 認証 / レポート受信）
│   ├── google_auth.py      # Google OAuth（認可 URL、code 交換、ID トークン検証）
│   ├── mail.py             # 招待メール送信（none / SES / SMTP）
│   ├── routers/
│   │   ├── api.py          # クライアント向けAPI（レポート受信 POST /report/{slug}）
│   │   ├── admin.py        # 管理画面の共通部分（ログイン、プロジェクト選択、管理メニュー、トークン）
│   │   ├── project_pages.py # プロジェクト配下 /p/{slug}/（一覧、詳細、一括削除、メンバー管理、AES 設定）
│   │   ├── system_admin.py # システム管理者専用（プロジェクト管理、全ユーザー管理、システム設定）
│   │   ├── reports_api.py  # 読み取り専用 API（Claude Code 等）
│   │   └── common.py       # ルーター共通ヘルパー
│   ├── report_export.py    # レポートの Markdown / JSON 整形、検索クエリ（画面・API・MCP 共通）
│   ├── mcp_server.py       # Claude Code 向け MCP サーバー（読み取り専用ツール）
│   ├── templates/          # Jinja2 HTMLテンプレート
│   │   ├── base.html       # ベーステンプレート（ダーク/ライトテーマ、プロジェクト切替）
│   │   ├── login.html
│   │   ├── projects.html   # プロジェクト選択
│   │   ├── report_list.html
│   │   ├── report_detail.html
│   │   ├── report_manage.html
│   │   ├── project_members.html   # メンバー管理（プロジェクト管理者）
│   │   ├── project_settings.html  # AES Key/IV・受信 URL（プロジェクト管理者）
│   │   ├── admin_menu.html
│   │   ├── admin_projects.html    # プロジェクト管理（システム管理者）
│   │   ├── user_list.html         # 全ユーザー管理（システム管理者）
│   │   ├── system_config.html     # セッション有効期限
│   │   ├── api_tokens.html
│   │   ├── password_change.html
│   │   ├── error.html
│   │   └── _user_styles.html / _user_scripts.html / _invite_panel.html  # ユーザー系画面の共通部品
│   └── static/
│       └── icons/          # 検索アイコン等
├── docs/
│   ├── local_setup_guide.md     # このファイル
│   ├── google_auth_setup.md     # Google ログインの設定
│   ├── claude_integration.md    # Claude Code 連携（API トークン・MCP）
│   ├── aws_deployment_guide.md  # AWS デプロイ（構成の選択）
│   └── aws_deploy_{lightsail,budget,standard}.md  # 各構成の手順
└── seed_dummy.py           # ダミーデータ投入スクリプト
```

---

## データベースモデル

### ReportData（レポート）
| カラム | 型 | 説明 |
|--------|-----|------|
| id | Integer PK | 自動採番（全プロジェクトで通し番号） |
| project_id | Integer FK NOT NULL | 所属プロジェクト（`project.id`、RESTRICT。レポートが残るプロジェクトは削除不可） |
| title | String(255) | レポートタイトル |
| created_at | DateTime | 作成日時（UTC） |
| user_id | String(255) | ユーザーID |
| user_name | String(255) | ユーザー名 |
| device_model | String(255) | デバイスモデル名 |
| log | Text | ログテキスト（JSON形式） |
| img_name | Text | スクリーンショットのストレージキー（`report/<slug>/images/<file>.png`。007 以前の行は `report/images/...`） |
| img_thumbnail_name | Text | サムネイルのストレージキー（`report/<slug>/thumbnail/thumbnail_<file>.png`） |
| extend_info | Text | 拡張情報（JSON） |

### AdminUser（管理ユーザー）
| カラム | 型 | 説明 |
|--------|-----|------|
| id | Integer PK | 自動採番 |
| username | String(100) UNIQUE | ユーザー名 |
| password_hash | String(255) NULL可 | bcryptハッシュ。Google 専用ユーザーは NULL |
| email | String(255) UNIQUE NULL可 | Google ログインの許可リスト（小文字で保存） |
| google_sub | String(255) UNIQUE NULL可 | Google アカウントの一意 ID。初回 Google ログインで保存 |
| is_superuser | Boolean | システム管理者（全プロジェクトの管理、プロジェクトの作成・削除、全ユーザー管理） |
| is_active | Boolean | 有効/無効 |
| created_at | DateTime | 作成日時 |
| last_login_at | DateTime | 最終ログイン日時 |

### Project（プロジェクト）
| カラム | 型 | 説明 |
|--------|-----|------|
| id | Integer | 主キー |
| slug | String(40) | URL 用の識別子（一意。小文字英数字とハイフン） |
| name | String(100) | 表示名 |
| aes_key / aes_iv | String(32) / String(16) | このプロジェクトの受信を復号する AES Key / IV |
| is_active | Boolean | False にすると受信 URL が 404 を返す（閲覧は可） |

### ProjectMember（所属）
| カラム | 型 | 説明 |
|--------|-----|------|
| project_id / user_id | Integer | プロジェクトとユーザー（組で一意） |
| role | String(16) | `admin`（プロジェクト管理者）/ `member` |

### SystemConfig（システム設定）
| キー | 説明 | デフォルト |
|------|------|-----------|
| session_hours | セッション有効期限（時間） | 24 |

---

## 管理画面の機能一覧

- **プロジェクト選択**: ログイン後、所属が複数ならプロジェクトを選ぶ（1 つなら一覧へ直行）。ナビバーで切替
- **レポート一覧**（プロジェクト単位）: 全文検索（ILIKE）、日付フィルタ、25件/ページのページング
- **レポート詳細**: ログ表示（Unityコンソール風）、スクリーンショットライトボックス、Markdown コピー、削除
- **レポート管理**: 期間指定での一括削除（プロジェクト管理者）
- **メンバー管理**: 既存ユーザーの追加、新規ユーザー作成（パスワード / Google 招待）、役割変更、除外（プロジェクト管理者）
- **プロジェクト設定**: AES Key/IV、受信 URL の表示（プロジェクト管理者）
- **パスワード変更**: 自分のパスワードを変更
- **API トークン**: Claude Code（MCP）/ 読み取り API 用の個人トークン
- **プロジェクト管理**: プロジェクトの作成・表示名や slug の変更・受信停止・削除（システム管理者）
- **全ユーザー管理**: アカウントの作成・削除、有効/無効、システム管理者権限、PW変更、Google 連携（システム管理者）
- **システム設定**: セッション有効期限（システム管理者）
- **テーマ切替**: ダーク/ライトモード（localStorageに保存）

### セキュリティ機能
- bcryptパスワードハッシュ
- itsdangerousセッショントークン（有効期限はDB設定で変更可能）
- プロジェクトの所属と役割による認可（所属外のレポートは存在しないものとして 404）
- システム管理者0人時の緊急アカウント自動作成
- 最後のシステム管理者の権限解除防止、最後のプロジェクト管理者の降格・除外防止（システム管理者は例外）

---

## トラブルシューティング

### `curl localhost` で応答がない
→ `curl http://127.0.0.1/` を使用してください（IPv6の問題）

### マイグレーションが適用されない
→ `docker compose build app` で再ビルドしてから `alembic upgrade head`
（alembicファイルはビルド時にコンテナにコピーされるため）

### DB接続エラー
→ `docker compose ps` で db コンテナが healthy か確認
→ `docker compose logs db` でエラーログを確認

### ポート80が既に使用中
→ 他のWebサーバー（IIS、Apache等）が動作していないか確認
→ `docker-compose.yml` の nginx ポートを変更（例: `"8080:80"`）

### Docker Desktop が起動しない / WSL2エラー
→ Windows の場合、BIOS で仮想化を有効にする必要があります
→ `wsl --update` を実行してWSL2を最新に更新
