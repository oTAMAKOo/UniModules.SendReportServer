# UniModules.SendReportServer

# プロジェクト共通ルール

（ここにチーム共通のルールを記述）

## メモリポリシー【重要】
- このファイル（CLAUDE.md）は人間が手動で管理する。Claudeは **人間が明示的に指示した場合を除き、絶対に編集してはならない**。
- プロジェクト構造の分析結果や学習内容は CLAUDE.local.md に記録すること。
- /init の結果も CLAUDE.local.md に出力すること。

## プロジェクト概要

Unityクライアントからのクラッシュレポート（AES-256-CBC暗号化）を受信・管理するログサーバー。
既存のDjango製ログサーバーをFastAPI + PostgreSQLで置き換えたスタンドアロン版。
各プロジェクトへはサブモジュールとして取り込んで使う（導入手順は README.md）。
送信側は Unity の UniModules にある `Modules/Devkit/Diagnosis/SendReport/`。

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
UniModules.SendReportServer/
├── README.md               # 他プロジェクトへの導入手順
├── .env.example            # 環境変数テンプレート（.envは.gitignore対象）
├── Dockerfile
├── docker-compose.yml
├── docker-compose.light.yml # 軽量ホスト向けoverride素材（0.5GB RAM）
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
│   │   ├── api.py          # POST /buglog/report — クライアントAPI
│   │   └── admin.py        # 管理画面（ログイン, 一覧, 詳細, ユーザー管理, システム設定）
│   ├── templates/          # Jinja2テンプレート（ダーク/ライトテーマ対応）
│   └── static/icons/       # Unity Editor Icons（検索, コンソールアイコン）
└── docs/
    ├── local_setup_guide.md     # ローカル環境構築手順
    ├── aws_deployment_guide.md  # AWSデプロイ（構成の選択）
    ├── aws_deploy_budget.md     # 安価版 t4g.nano の全手順
    └── aws_deploy_standard.md   # 通常版 t4g.micro の全手順
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

URLプレフィックスは `.env` の `URL_PREFIX` で設定する（既定値 `/buglog`、実装は `app/config.py` の `url_prefix`）。先頭の `/` は省略可、末尾の `/` は無視され、空にするとルート直下にマウントされる。テンプレートは Jinja2 グローバル変数 `{{ PREFIX }}` を参照する。以下のパスは既定値の場合。

- **レポート一覧** (`/buglog/list`): 全文検索（ILIKE）、日付フィルタ、25件/ページ、カード型UI
- **レポート詳細** (`/buglog/detail/{id}`): ログ表示（Unity風）、スクリーンショット、削除
- **レポート管理** (`/buglog/manage`): 期間指定一括削除
- **ユーザー管理** (`/buglog/users`): CRUD、権限変更ドロップダウン、有効/無効切替、PW変更（展開式）
- **システム設定** (`/buglog/system`): AES Key/IV、セッション有効期限
- **パスワード変更** (`/buglog/password_change`)
- **テーマ切替**: ダーク/ライト（localStorage保存）

## セキュリティ機能

- セッション有効期限: DB設定で変更可能（デフォルト24時間、1〜8760時間）
- 管理者0人時の緊急アカウント自動作成（操作者にのみ認証情報表示）
- 最後の管理者の権限解除防止

## ローカル開発環境の起動

```bash
cp .env.example .env       # 必要に応じて値を変更
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

ブラウザ: http://127.0.0.1/buglog/login（admin / password）

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
