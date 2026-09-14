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
管理画面のログインはユーザー名 + パスワードに加えて Google アカウント（招待制、任意）に対応する（`docs/google_auth_setup.md`）。

## 技術スタック

- **バックエンド**: FastAPI (Python 3.11) + SQLAlchemy ORM
- **データベース**: PostgreSQL 16
- **リバースプロキシ**: Nginx
- **コンテナ**: Docker Compose
- **テンプレート**: Jinja2 + Bootstrap 5
- **認証**: bcrypt + itsdangerous セッショントークン。Google OAuth 2.0 / OIDC（Authorization Code、httpx で自前実装・専用ライブラリなし）
- **暗号化**: AES-256-CBC (PyCryptodome) — Unityクライアントと互換
- **画像保存**: ローカル or AWS S3 切替可能
- **招待メール**: `MAIL_MODE` で none（既定・画面にリンク表示のみ）/ SES（boto3）/ SMTP（smtplib）を切替

## ディレクトリ構成

```
UniModules.SendReportServer/
├── README.md               # 他プロジェクトへの導入手順
├── .env.example            # 環境変数テンプレート（.envは.gitignore対象）
├── .dockerignore           # .env / .git 等をイメージに焼かない
├── Dockerfile
├── docker-compose.yml      # 開発用
├── docker-compose.prod.yml.example  # 本番用 override の雛形（実ファイルは git 管理外）
├── docker-compose.light.yml # 軽量ホスト向け DB 設定（本番では prod.yml の db.command に書く。override.yml では効かない）
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/           # DBマイグレーション（001〜005。005 = email / google_sub 追加、password_hash NULL 許容）
├── nginx/nginx.conf
├── app/
│   ├── main.py             # FastAPIエントリーポイント（lifespan: デフォルト管理者作成、ADMIN_GOOGLE_EMAIL の保証）
│   ├── config.py           # pydantic-settings（.envから読み込み。空値・未知キーは無視）
│   ├── database.py         # SQLAlchemy エンジン & セッション（pool_pre_ping）
│   ├── models.py           # ReportData, AdminUser, SystemConfig
│   ├── schemas.py          # Pydantic スキーマ
│   ├── auth.py             # 認証（bcrypt, セッション, CSRF, OAuth state, 招待トークン, 起動時の管理者保証）
│   ├── google_auth.py      # Google OAuth（認可URL, code 交換, ID トークン検証）
│   ├── mail.py             # 招待メール送信（none / SES / SMTP）
│   ├── ratelimit.py        # IP ごとのレート制限（ログイン 10/分, Google 20/分, レポート受信 60/分）
│   ├── crypto.py           # AES復号（DB設定 or .envフォールバック）
│   ├── storage.py          # 画像保存（local/S3対応）
│   ├── routers/
│   │   ├── api.py          # POST /buglog/report — クライアントAPI
│   │   ├── admin.py        # 管理画面（ログイン, 一覧, 詳細, ユーザー管理, システム設定）
│   │   └── google_auth.py  # /auth/google, /auth/google/callback, /invite/{token}
│   ├── templates/          # Jinja2テンプレート（ダーク/ライトテーマ対応）
│   └── static/icons/       # Unity Editor Icons（検索, コンソールアイコン）
└── docs/
    ├── local_setup_guide.md     # ローカル環境構築手順
    ├── google_auth_setup.md     # Google ログインの設定（GCP, .env, 招待, 復旧, トラブルシューティング）
    ├── aws_deployment_guide.md  # AWSデプロイ（構成の選択）
    ├── aws_deploy_lightsail.md  # Lightsail 最小構成（$5 定額、現行の推奨）の全手順と運用
    ├── aws_deploy_budget.md     # EC2 安価版 t4g.nano の全手順
    └── aws_deploy_standard.md   # EC2 通常版 t4g.micro の全手順
```

## DBモデル

### ReportData — クラッシュレポート
- id, title, created_at, user_id, user_name, device_model
- log (Text/JSON), img_name, img_thumbnail_name, extend_info (JSON)

### AdminUser — 管理ユーザー
- id, username, password_hash (bcrypt。Google 専用ユーザーは NULL), is_superuser, is_active
- email (unique, NULL可。Google ログインの許可リスト。小文字で保存), google_sub (unique, NULL可。Google の一意ID。初回 Google ログインで保存)
- created_at, last_login_at
- 「招待中」= email あり・google_sub なし・password_hash なし・is_active=False（明示カラムは無く推論）

### SystemConfig — システム設定（key-value）
- aes_key (32文字), aes_iv (16文字), session_hours (デフォルト24)

## 管理画面の機能

URLプレフィックスは `.env` の `URL_PREFIX` で設定する（既定値 `/buglog`、実装は `app/config.py` の `url_prefix`）。先頭の `/` は省略可、末尾の `/` は無視され、空にするとルート直下にマウントされる。テンプレートは Jinja2 グローバル変数 `{{ PREFIX }}` を参照する。以下のパスは既定値の場合。

- **ログイン** (`/buglog/login`): ユーザー名 + パスワード。`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `PUBLIC_BASE_URL` が揃っていれば「Google でログイン」ボタンを表示（未設定なら Google 関連ルートは 404、ボタン非表示）
- **Google 認証** (`/buglog/auth/google` → Google → `/buglog/auth/google/callback`、招待リンク `/buglog/invite/{token}`): **Google ログインで AdminUser は作らない**。`AdminUser.email` に一致するユーザーだけログイン可。初回ログインで `google_sub` を保存し、招待中なら有効化。以後は `sub` で特定
- **レポート一覧** (`/buglog/list`): 全文検索（ILIKE）、日付フィルタ、25件/ページ、カード型UI
- **レポート詳細** (`/buglog/detail/{id}`): ログ表示（Unity風）、スクリーンショット、削除
- **レポート管理** (`/buglog/manage`): 期間指定一括削除
- **ユーザー管理** (`/buglog/users`): 作成時にログイン方法を選択（パスワード / Google=招待リンク発行）、権限変更ドロップダウン、有効/無効切替、PW変更・PW設定（展開式）、Google連携（招待・変更・同じアドレスで再連携・解除。展開式）、招待リンク再発行、`ADMIN_GOOGLE_EMAIL` のユーザーに「復旧用」バッジ
- **システム設定** (`/buglog/system`): AES Key/IV、セッション有効期限
- **パスワード変更** (`/buglog/password_change`): Google 専用ユーザーには「パスワード設定」（現在のパスワード不要）として開く
- **テーマ切替**: ダーク/ライト（localStorage保存）

## セキュリティ機能

- セッション有効期限: DB設定で変更可能（デフォルト24時間、1〜8760時間）
- 最後の有効な管理者は削除・無効化・権限解除できない。パスワードを持たない最後の管理者の Google アドレス変更も不可
- 管理者0人時の緊急アカウント自動作成（UI 操作で 0 人になった瞬間のみ。操作者にのみ認証情報表示。Google 障害時には発動しない）
- `ADMIN_GOOGLE_EMAIL`（.env）: 起動のたびに該当アカウントを管理者・有効に保証（ロックアウト復旧用。該当ユーザーが無ければ Google 専用で作成。`ADMIN_PASSWORD` は初回起動のみ）
- Google 認証: state / nonce は itsdangerous 署名 Cookie（10分、HttpOnly、SameSite=Lax）。ID トークンは token エンドポイントと TLS 直結のため署名検証を省略（aud / iss / exp / nonce / email_verified は検証。`verify_claims` をブラウザ経由のトークンに使ってはならない）。`hd` によるドメイン制限は無し（普通の Google アカウント前提）
- 招待リンク: 署名付きトークン（`INVITE_EXPIRE_HOURS`、既定 72 時間）。防衛線はコールバックでの email 照合であり、リンク自体は案内
- 無効化された連携済みユーザーの Google アドレス変更は拒否（「招待中」と区別できず暗黙に有効化されるのを防ぐ）
- Cookie: `PUBLIC_BASE_URL` が https のとき `Secure`。nginx 側の HSTS は手順書の設定に含む
- レート制限: ログイン POST 10回/分、Google 経路 20回/分、レポート受信 60回/分（IP は `X-Forwarded-For` の末尾 = nginx が付けた実IP）
- ユーザー名は JS 文字列に直接埋め込まず data 属性から読む（HTML エスケープでは JS 文字列を守れない）
- パスワード変更・Google 連携解除・降格ではセッションは無効化されない（既存設計。無効化・削除のみ即時に効く）

## ローカル開発環境の起動

```bash
cp .env.example .env       # 必要に応じて値を変更
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

ブラウザ: http://127.0.0.1/buglog/login（admin / password）

> `localhost` ではなく `127.0.0.1` を使用（Docker IPv4問題の回避）

Google ログインをローカルで使う場合は `.env` に `PUBLIC_BASE_URL=http://127.0.0.1`（ポートを変えていればそれも）と GCP のクライアント ID / シークレットを設定し、同じ URL を GCP の承認済みリダイレクト URI に登録する（`docs/google_auth_setup.md`）。

## コード変更の反映

- **テンプレート/CSS変更**: ブラウザリロードのみ
- **Pythonコード変更**: uvicorn --reload で自動反映
- **マイグレーション追加時**: `docker compose build app && docker compose up -d && docker compose exec app alembic upgrade head`

## 本番運用の前提

- 起動は必ず `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`（systemd も同じ）。`-f` を明示するため `docker-compose.override.yml` は読み込まれない。軽量構成（RAM 0.5GB）は `docker-compose.prod.yml` の `db.command` に書く（内容は `docker-compose.light.yml`）
- 更新は `git pull origin main` → `up -d --build`（`docs/aws_deploy_lightsail.md` 12章）。app 再作成中の 30 秒前後はレポート受信が止まる（クライアントは再送しない）
- `.env` はコンテナへ compose の `env_file` で渡す（`.dockerignore` でイメージには入れない）。値の変更後はコンテナの再作成が必要
- `nginx/nginx.conf` は本番で HTTPS 版に書き換えられている（ローカル変更）。上流でこのファイルを変えると本番の `git pull` が止まるので注意
- 秘匿情報（AES キー、`SECRET_KEY`、`GOOGLE_CLIENT_SECRET` 等）は `.env` にだけ置く。`.env.example` やドキュメントには書かない

## 開発ルール

- 常に日本語で返答・コメントすること
- テンプレートはダーク/ライト両テーマ対応（CSS変数使用）
- アイコンは Unity Editor Icons (https://github.com/halak/unity-editor-icons) から取得
- フォント: Inter（Google Fonts）
- 全文検索は PostgreSQL ILIKE でテキスト全フィールド対象
