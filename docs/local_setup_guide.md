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
git clone git@github.com:oTAMAKOo/BugLogServer.git
cd BugLogServer
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

# AES Encryption（Unityクライアントと一致させること）
REPORT_AES_KEY=0123456789abcdef
REPORT_AES_IV=abcdef0123456789

# Storage mode: "local" or "s3"
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

> **AES Key/IV について**: 本番のUnityクライアントと通信する場合は、クライアント側の `PLCryptoAES.KEY`（32文字）と `PLCryptoAES.IV`（16文字）に合わせてください。
> AES Key/IV はシステム設定画面（/buglog/system）からもDB経由で変更可能です。

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
| `http://127.0.0.1/buglog/list` | レポート一覧 |
| `http://127.0.0.1/buglog/detail/{id}` | レポート詳細 |
| `http://127.0.0.1/buglog/admin` | 管理メニュー |
| `http://127.0.0.1/buglog/system` | システム設定（AES Key/IV、セッション有効期限） |
| `http://127.0.0.1/buglog/users` | ユーザー管理 |
| `http://127.0.0.1/buglog/manage` | レポート管理（一括削除） |
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
BugLogServer/
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
│       └── 004_add_last_login_at.py
├── nginx/
│   └── nginx.conf          # Nginx設定（リバースプロキシ）
├── app/
│   ├── main.py             # FastAPIアプリケーション エントリーポイント
│   ├── config.py           # 設定（pydantic-settings、.envから読み込み）
│   ├── database.py         # SQLAlchemy エンジン & セッション
│   ├── models.py           # DBモデル（ReportData, AdminUser, SystemConfig）
│   ├── schemas.py          # Pydantic スキーマ
│   ├── auth.py             # 認証（bcryptハッシュ、セッショントークン）
│   ├── crypto.py           # AES-256-CBC 復号処理
│   ├── storage.py          # 画像保存（ローカル / S3 対応）
│   ├── routers/
│   │   ├── api.py          # クライアント向けAPI（レポート受信）
│   │   └── admin.py        # 管理画面（ログイン、一覧、詳細、ユーザー管理等）
│   ├── templates/          # Jinja2 HTMLテンプレート
│   │   ├── base.html       # ベーステンプレート（ダーク/ライトテーマ）
│   │   ├── login.html
│   │   ├── report_list.html
│   │   ├── report_detail.html
│   │   ├── report_manage.html
│   │   ├── admin_menu.html
│   │   ├── password_change.html
│   │   ├── user_list.html
│   │   └── system_config.html
│   └── static/
│       └── icons/          # 検索アイコン等
├── docs/
│   ├── aws_deployment_guide.md  # AWS本番デプロイ手順
│   └── local_setup_guide.md     # このファイル
└── seed_dummy.py           # ダミーデータ投入スクリプト
```

---

## データベースモデル

### ReportData（レポート）
| カラム | 型 | 説明 |
|--------|-----|------|
| id | Integer PK | 自動採番 |
| title | String(255) | レポートタイトル |
| created_at | DateTime | 作成日時 |
| user_id | String(255) | ユーザーID |
| user_name | String(255) | ユーザー名 |
| device_model | String(255) | デバイスモデル名 |
| log | Text | ログテキスト（JSON形式） |
| img_name | Text | スクリーンショット画像ファイル名 |
| img_thumbnail_name | Text | サムネイル画像ファイル名 |
| extend_info | Text | 拡張情報（JSON） |

### AdminUser（管理ユーザー）
| カラム | 型 | 説明 |
|--------|-----|------|
| id | Integer PK | 自動採番 |
| username | String(100) UNIQUE | ユーザー名 |
| password_hash | String(255) | bcryptハッシュ |
| is_superuser | Boolean | 管理者権限 |
| is_active | Boolean | 有効/無効 |
| created_at | DateTime | 作成日時 |
| last_login_at | DateTime | 最終ログイン日時 |

### SystemConfig（システム設定）
| キー | 説明 | デフォルト |
|------|------|-----------|
| aes_key | AES暗号化キー（32文字） | .envの値 |
| aes_iv | AES初期化ベクトル（16文字） | .envの値 |
| session_hours | セッション有効期限（時間） | 24 |

---

## 管理画面の機能一覧

- **レポート一覧**: 全文検索（ILIKE）、日付フィルタ、25件/ページのページング
- **レポート詳細**: ログ表示（Unityコンソール風）、スクリーンショットライトボックス、削除
- **レポート管理**: 期間指定での一括削除
- **パスワード変更**: 自分のパスワードを変更
- **ユーザー管理**: ユーザーCRUD、権限変更、有効/無効切替、PW変更（スーパーユーザーのみ）
- **システム設定**: AES Key/IV、セッション有効期限（スーパーユーザーのみ）
- **テーマ切替**: ダーク/ライトモード（localStorageに保存）

### セキュリティ機能
- bcryptパスワードハッシュ
- itsdangerousセッショントークン（有効期限はDB設定で変更可能）
- 管理者0人時の緊急アカウント自動作成
- 最後の管理者の権限解除防止

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
