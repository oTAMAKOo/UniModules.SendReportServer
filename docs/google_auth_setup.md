# Google ログインの設定

管理画面のログインを、ユーザー名 + パスワードに加えて **Google アカウント**でも行えるようにする手順。
Google Workspace は不要で、普通の Google アカウント（gmail.com 等）で使える。

## 目次

1. [仕組みの概要](#1-仕組みの概要)
2. [前提条件](#2-前提条件)
3. [GCP 側の設定](#3-gcp-側の設定)
4. [.env の設定](#4-env-の設定)
5. [ユーザーを招待する](#5-ユーザーを招待する)
6. [既存ユーザーの Google 連携・パスワード追加](#6-既存ユーザーの-google-連携パスワード追加)
7. [招待メールの自動送信（任意）](#7-招待メールの自動送信任意)
8. [ロックアウト対策と復旧](#8-ロックアウト対策と復旧)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [セキュリティ上の設計メモ](#10-セキュリティ上の設計メモ)

---

## 1. 仕組みの概要

- **Google ログインでユーザーが勝手に作られることはない。** ログインできるのは、管理者が
  ユーザー管理画面（`/buglog/users`）で **email を登録した Google アカウントだけ**。
- 管理者が email を登録すると**招待リンク**が発行される。本人がリンクを開いてその Google
  アカウントでログインすると、アカウントが有効化され、以後は通常のログイン画面の
  「Google でログイン」から入れる。
- 許可判定は「`AdminUser.email` に一致するレコードが在るか」。招待リンクは案内であって
  防衛線ではないため、リンクが第三者に転送されても、招待先と異なる Google アカウントでは
  有効化できない。
- 従来のパスワード認証は残る。Google 専用ユーザーも、ログイン後に自分でパスワードを
  設定すれば両方で入れるようになる。
- `.env` に Google の設定が無ければ、ログイン画面に Google ボタンは表示されず、
  従来どおりパスワード認証のみで動く（サブモジュールとして取り込んだ他プロジェクトに影響しない）。

## 2. 前提条件

Google はリダイレクト URI に **HTTPS** を要求する（例外は `http://localhost` / `http://127.0.0.1`
のみ）。本番環境は独自ドメイン + HTTPS で稼働している必要がある。

| 環境 | `PUBLIC_BASE_URL` | 登録するリダイレクト URI |
|---|---|---|
| 本番 | `https://buglog.example.com` | `https://buglog.example.com/buglog/auth/google/callback` |
| ローカル | `http://127.0.0.1`（ポートを変えていれば `http://127.0.0.1:8080`） | `http://127.0.0.1/buglog/auth/google/callback` |

`URL_PREFIX` を変えている場合は `/buglog` の部分を合わせる。**後から `URL_PREFIX` や
ドメインを変えると GCP に登録した URI と食い違ってログインできなくなる**ので、変更時は
GCP 側も更新する。

## 3. GCP 側の設定

1. [Google Cloud コンソール](https://console.cloud.google.com/) でプロジェクトを作成（既存でもよい）
2. 左メニュー **「Google Auth Platform」**（旧「API とサービス > OAuth 同意画面」）を開く
3. **ブランディング**（同意画面）を設定する
   - アプリ名: `Log Server` など任意
   - ユーザーサポートメール / デベロッパーの連絡先: 管理者のアドレス
   - **承認済みドメイン**: 本番ドメイン（例 `example.com`）。ローカル用の `127.0.0.1` は登録不要
4. **対象**（ユーザーの種類）は **「外部」** を選ぶ（Workspace を使わず、普通の Google アカウントで入るため）
5. **公開ステータスを「本番環境」にする**（「公開」ボタン）
   - 「テスト」のままだと、テストユーザーとして登録した最大 100 人しかログインできず、
     それ以外は `Error 403: access_denied` になる
   - 要求するスコープは `openid` と `email` のみ（機密スコープではない）なので、
     Google の審査なしで本番環境に切り替えられる
6. **クライアント** → **「クライアントを作成」**
   - アプリケーションの種類: **ウェブ アプリケーション**
   - 名前: `Log Server` など
   - **承認済みのリダイレクト URI**: 上記 2. の表の値を追加（本番とローカルの両方を登録してよい）
7. 作成後に表示される **クライアント ID** と **クライアント シークレット** を控える
   （シークレットは後から再表示できないため、必要なら JSON をダウンロードしておく）

## 4. .env の設定

`.env` は git 管理外。プロジェクト側の `.env`（例: `Dominion/BugLogServer/.env`）に追記する。

```dotenv
PUBLIC_BASE_URL=https://buglog.example.com
GOOGLE_CLIENT_ID=xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 任意: ロックアウト復旧用（8. を参照）
ADMIN_GOOGLE_EMAIL=you@gmail.com
```

3 つ（`PUBLIC_BASE_URL` / `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`）が揃うと Google ログインが有効になる。
`.env` は起動時に読まれるため、変更後はコンテナを再作成する。

```bash
docker compose up -d            # 開発
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d   # 本番
```

ログイン画面（`/buglog/login`）に「Google でログイン」ボタンが出れば設定は反映されている。

## 5. ユーザーを招待する

管理者（superuser）で `/buglog/users` を開く。

1. 「新規ユーザー追加」でユーザー名を入力し、**ログイン方法で「Google」を選ぶ**
2. Email に本人の Google アカウントのアドレスを入力して「追加」
3. 招待リンクが画面に表示される。「コピー」して本人に伝える（Slack 等）
   - `MAIL_MODE` を設定していれば招待メールも自動送信される（7. を参照）
4. 本人がリンクを開くと Google の認証画面に進む。**招待されたアドレスの Google アカウント**で
   ログインすると有効化され、レポート一覧に入る

ユーザー一覧では次のように見える。

| 状態 | ログイン方法 | 意味 |
|---|---|---|
| 招待中 | Google（未連携） | 招待リンクを発行済み。本人の初回ログイン待ち |
| 有効 | Google | 連携済み。Google でログインできる |
| 有効 | パスワード + Google | 両方でログインできる |

招待リンクの有効期限は `INVITE_EXPIRE_HOURS`（既定 72 時間）。期限が切れたら操作列の
**「招待リンク再発行」** で新しいリンクを出す。なお、期限が切れても email は登録済みなので、
本人がログイン画面の「Google でログイン」から入れば同じように有効化される。

## 6. 既存ユーザーの Google 連携・パスワード追加

| やりたいこと | 誰が | どこで |
|---|---|---|
| パスワードユーザーに **Google ログインを追加** | 管理者 | `/buglog/users` の操作列 **「Google連携」** → email を入力して「招待」 |
| Google 専用ユーザーに **パスワードを追加** | **本人** | ログイン後、管理メニュー → **パスワード設定** |
| Google 連携を **解除** | 管理者 | 「Google連携」→「解除」（パスワードを持たないユーザーは解除できない） |
| Google アカウントを **別のアドレスに変更** | 管理者 | 「Google連携」→ アドレスを書き換えて「変更」（旧連携は解除され、新アドレスの持ち主が連携し直す） |

Google の付与だけ管理者経由なのは、`AdminUser.email` が許可リストそのものだから。
本人が自由に設定できると「管理者が招待した人だけ入れる」という前提が崩れる。

自分自身の行にも「Google連携」ボタンはあるので、管理者は自分のアカウントに Google を付けられる。

## 7. 招待メールの自動送信（任意）

既定（`MAIL_MODE=none`）では招待リンクを画面に表示するだけで、メールは送らない。
自動送信したい場合は `.env` で切り替える。**メール送信に失敗しても招待リンクは画面に出る**ので、
ユーザー作成そのものは失敗しない。

### Amazon SES

```dotenv
MAIL_MODE=ses
MAIL_FROM=noreply@buglog.example.com
# 認証情報は S3 と共通の AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION を使う
```

事前に必要な作業:

1. **送信元 ID の検証** — SES コンソールで `MAIL_FROM` のドメイン（またはアドレス）を検証する
2. **サンドボックスの解除** — 初期状態では検証済みアドレスにしか送れず（200 通/24h）、
   本番利用には「本番アクセスのリクエスト」が必要。承認まで通常 24 時間程度
3. **IAM** — S3 用の IAM ユーザーに `ses:SendEmail` / `ses:SendRawEmail` を許可する
4. **DNS** — DKIM の CNAME を追加する。**既存のメール（MX / SPF / DMARC）を壊さないこと。**
   SES の「カスタム MAIL FROM ドメイン」をサブドメイン（例 `mail.buglog.example.com`）に
   すれば、既存ドメインの SPF レコードには触らずに済む

### SMTP

```dotenv
MAIL_MODE=smtp
MAIL_FROM=noreply@example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@example.com
SMTP_PASSWORD=xxxxxxxx
SMTP_STARTTLS=true
```

Gmail の SMTP を使う場合は 2 段階認証を有効にした上で「アプリ パスワード」を発行し、
`SMTP_HOST=smtp.gmail.com` / `SMTP_PORT=587` を指定する。

## 8. ロックアウト対策と復旧

「誰も管理画面に入れない」状態を防ぐため、3 層で守っている。

| 層 | 仕組み | 効く場面 |
|---|---|---|
| 予防 | **最後の有効な管理者**は削除・無効化・権限解除できない | UI 操作による事故 |
| 復旧 | `.env` の **`ADMIN_GOOGLE_EMAIL`** | 管理者が 0 人になった / 権限設定を間違えた / DB を直接いじって壊した |
| 最後の砦 | 緊急管理者の自動作成 + **パスワード認証**が残っている | **Google 認証そのものが死んだ**とき（client secret のローテート、OAuth クライアントの誤削除、Google 側の障害） |

### `ADMIN_GOOGLE_EMAIL` の動き

- 起動のたびに、この email のアカウントが **存在し、管理者権限を持ち、有効である**ことを保証する
  （`ADMIN_USERNAME` のアカウントに紐付ける。無ければ作る）
- `.env` を書き換えられる人 = サーバーに SSH できる人なので、権限の格上げにはならない
- email は秘密情報ではないため、`.env` が漏れても悪用できない（その Google アカウントを
  乗っ取らない限り使えない）。平文の `ADMIN_PASSWORD` より安全な復旧経路になる
- `ADMIN_PASSWORD` は「初回のみ」適用されるが、`ADMIN_GOOGLE_EMAIL` は「毎回」適用される。
  パスワードを毎回強制すると管理画面で変更した値が巻き戻ってしまうため、扱いを分けている

### 復旧手順

```bash
# 1. サーバーに SSH して .env を編集
#    ADMIN_GOOGLE_EMAIL=you@gmail.com   ← 自分の Google アカウント
# 2. コンテナを再作成（.env は起動時に読まれる）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# 3. ログイン画面の「Google でログイン」からそのアカウントで入る
```

Google 認証自体が使えない場合は、パスワードを持つ管理者アカウントで入る。
全員が Google 専用だった場合は、DB に直接パスワードを入れるしかない。

```bash
# コンテナ内で bcrypt ハッシュを作る
docker compose exec app python -c "import bcrypt; print(bcrypt.hashpw(b'新しいパスワード', bcrypt.gensalt()).decode())"
```

```bash
# DB に書き込む（ハッシュは上の出力。$ を含むのでシングルクォートで囲んだ SQL を psql に渡す）
docker compose exec db psql -U logserver -d logserver -c "UPDATE admin_user SET password_hash='ここにハッシュ', is_active=true, is_superuser=true WHERE username='admin';"
```

## 9. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| Google 側で `Error 400: redirect_uri_mismatch` | GCP に登録した URI と `PUBLIC_BASE_URL` + `URL_PREFIX` + `/auth/google/callback` が一致していない。`PUBLIC_BASE_URL` の末尾 `/`、`http`/`https`、ポート番号を確認する |
| Google 側で `Error 403: access_denied` | 同意画面の公開ステータスが「テスト」のまま。「本番環境」に切り替える（3. の手順 5） |
| 「この Google アカウントは招待されていません」 | そのアドレスが `AdminUser.email` に登録されていない。ログインに使った Google アカウントのアドレスと、管理者が登録したアドレスが一致しているか確認する（別アカウントでログインしがち） |
| 「招待されたアドレスと異なる Google アカウントでログインしました」 | 招待リンクから入ったが、別の Google アカウントを選んだ。アカウント選択画面で招待先のアカウントを選び直す |
| 「認証セッションが無効です」 | Google の認証画面で 10 分以上放置した、または Cookie がブロックされている。ログイン画面からやり直す |
| ログイン画面に Google ボタンが出ない | `PUBLIC_BASE_URL` / `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` のいずれかが空。`.env` を直したらコンテナを再作成する |
| 招待メールが届かない | `MAIL_MODE=ses` でサンドボックス未解除 / 送信元 ID 未検証。画面に表示されたリンクを直接伝えれば運用は止まらない |

## 10. セキュリティ上の設計メモ

- **ID トークンの署名検証を行っていない。** 認可コードを Google の token エンドポイントへ
  サーバー間の TLS で直接交換しており、OpenID Connect Core 1.0 §3.1.3.7 により TLS の
  サーバー検証が署名検証の代わりになる。`aud` / `iss` / `exp` / `nonce` / `email_verified` は
  検証している。**フローを implicit / hybrid に変える（ID トークンをブラウザ経由で受け取る）
  場合は JWKS による署名検証が必須になる**（`app/google_auth.py` のコメント参照）。
- **識別子は `sub`、email は照合用。** 初回ログインで `google_sub` を保存し、以後は `sub` で
  ユーザーを特定する。email は Google 側で変わり得るため一意キーにはしない。
- **`hd` によるドメイン制限は使っていない。** 普通の Google アカウントで使えるようにするため。
  そのため許可リスト（`AdminUser.email`）が唯一の防衛線で、Google ログインで
  ユーザーを自動作成する設計は採らない。
- **state / nonce** は既存の `itsdangerous` 署名付き Cookie（10 分有効、`SameSite=Lax`）で
  保持しており、Starlette の `SessionMiddleware` は使っていない。
- **招待トークン**も署名付き（`INVITE_EXPIRE_HOURS` で期限）で、DB には保存しない。
  使用済み管理もしない。防衛線はコールバックでの email 照合であり、リンクは案内に過ぎないため。
- **退職者対応**は運用で行う。Workspace と違い会社側で Google アカウントを止められないので、
  ユーザー管理画面で「無効化」または「削除」する。
