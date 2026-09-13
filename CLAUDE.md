# SendReport Log Server

# プロジェクト共通ルール

（ここにチーム共通のルールを記述）

## メモリポリシー【重要】
- このファイル（CLAUDE.md）は人間が手動で管理する。Claudeは **人間が明示的に指示した場合を除き、絶対に編集してはならない**。
- プロジェクト構造の分析結果や学習内容は CLAUDE.local.md に記録すること。
- /init の結果も CLAUDE.local.md に出力すること。

## プロジェクト概要

Unityクライアントからのクラッシュレポート（AES-256-CBC暗号化）を受信・管理するログサーバー。
既存のDjango製ログサーバーをFastAPI + PostgreSQLで置き換えたスタンドアロン版。

## 技術スタック

- **バックエンド**: FastAPI (Python 3.11) + SQLAlchemy ORM
- **データベース**: PostgreSQL 16
- **リバースプロキシ**: Nginx
- **コンテナ**: Docker Compose
- **テンプレート**: Jinja2 + Bootstrap 5
- **認証**: bcrypt + itsdangerous セッショントークン
- **暗号化**: AES-256-CBC (PyCryptodome) — Unityクライアントと互換
- **画像保存**: ローカル or AWS S3 切替可能

## ディレクトリ構成

```
log-server/
├── .env.example            # 環境変数テンプレート（.envは.gitignore対象）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/           # DBマイグレーション（001〜004）
├── nginx/nginx.conf
├── app/
│   ├── main.py             # FastAPIエントリーポイント（lifespan: デフォルト管理者作成）
│   ├── config.py           # pydantic-settings（.envから読み込み）
│   ├── database.py         # SQLAlchemy エンジン & セッション
│   ├── models.py           # ReportData, AdminUser, SystemConfig
│   ├── schemas.py          # Pydantic スキーマ
│   ├── auth.py             # 認証（bcrypt, セッション, DB設定連動の有効期限）
│   ├── crypto.py           # AES復号（DB設定 or .envフォールバック）
│   ├── storage.py          # 画像保存（local/S3対応）
│   ├── routers/
│   │   ├── api.py          # POST /mgr/report/report — クライアントAPI
│   │   └── admin.py        # 管理画面（ログイン, 一覧, 詳細, ユーザー管理, システム設定）
│   ├── templates/          # Jinja2テンプレート（ダーク/ライトテーマ対応）
│   └── static/icons/       # Unity Editor Icons（検索, コンソールアイコン）
└── docs/
    ├── local_setup_guide.md     # ローカル環境構築手順
    └── aws_deployment_guide.md  # AWS本番デプロイ手順
```

## DBモデル

### ReportData — クラッシュレポート
- id, title, created_at, user_id, user_name, device_model
- log (Text/JSON), img_name, img_thumbnail_name, extend_info (JSON)

### AdminUser — 管理ユーザー
- id, username, password_hash (bcrypt), is_superuser, is_active
- created_at, last_login_at

### SystemConfig — システム設定（key-value）
- aes_key (32文字), aes_iv (16文字), session_hours (デフォルト24)

## 管理画面の機能

- **レポート一覧** (`/mgr/report/list`): 全文検索（ILIKE）、日付フィルタ、25件/ページ、カード型UI
- **レポート詳細** (`/mgr/report/detail/{id}`): ログ表示（Unity風）、スクリーンショット、削除
- **レポート管理** (`/mgr/report/manage`): 期間指定一括削除
- **ユーザー管理** (`/mgr/users`): CRUD、権限変更ドロップダウン、有効/無効切替、PW変更（展開式）
- **システム設定** (`/mgr/system`): AES Key/IV、セッション有効期限
- **パスワード変更** (`/mgr/password_change`)
- **テーマ切替**: ダーク/ライト（localStorage保存）

## セキュリティ機能

- セッション有効期限: DB設定で変更可能（デフォルト24時間、1〜8760時間）
- 管理者0人時の緊急アカウント自動作成（操作者にのみ認証情報表示）
- 最後の管理者の権限解除防止

## ローカル開発環境の起動

```bash
cd log-server
cp .env.example .env       # 必要に応じて値を変更
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

ブラウザ: http://127.0.0.1/mgr/login（admin / password）

> `localhost` ではなく `127.0.0.1` を使用（Docker IPv4問題の回避）

## コード変更の反映

- **テンプレート/CSS変更**: ブラウザリロードのみ
- **Pythonコード変更**: uvicorn --reload で自動反映
- **マイグレーション追加時**: `docker compose build app && docker compose up -d && docker compose exec app alembic upgrade head`

## 開発ルール

- 常に日本語で返答・コメントすること
- テンプレートはダーク/ライト両テーマ対応（CSS変数使用）
- アイコンは Unity Editor Icons (https://github.com/halak/unity-editor-icons) から取得
- フォント: Inter（Google Fonts）
- 全文検索は PostgreSQL ILIKE でテキスト全フィールド対象
