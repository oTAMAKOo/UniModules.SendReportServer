# AWS デプロイ手順書 — 安価版（t4g.nano）

個人開発・小規模プロジェクト向けの最小コスト構成です。

### この構成のスペック

| 項目 | 値 |
|------|-----|
| インスタンス | t4g.nano（ARM Graviton） |
| RAM | 0.5GB + swap 1GB |
| ディスク | 8GB gp3 |
| 画像保存 | S3 |
| 月額 | ~$8.4（~1,250円）＝ インスタンス $3.94 + パブリックIPv4 $3.65 + EBS 8GB $0.77 |

---

## 目次

1. [事前準備（ローカルPC）](#1-事前準備ローカルpc)
2. [AWSアカウントの作成](#2-awsアカウントの作成)
3. [S3バケットの作成（画像保存用）](#3-s3バケットの作成画像保存用)
4. [EC2インスタンスの作成](#4-ec2インスタンスの作成)
5. [EC2にSSH接続](#5-ec2にssh接続)
6. [サーバーの初期設定](#6-サーバーの初期設定)
7. [アプリケーションの配置と起動](#7-アプリケーションの配置と起動)
8. [動作確認](#8-動作確認)
9. [ドメインとHTTPS設定（推奨）](#9-ドメインとhttps設定推奨)
10. [自動起動と自動バックアップ](#10-自動起動と自動バックアップ)
11. [セキュリティチェックリスト](#11-セキュリティチェックリスト)
12. [運用・メンテナンス](#12-運用メンテナンス)
13. [コスト削減（リザーブドインスタンス）](#13-コスト削減リザーブドインスタンス)
14. [トラブルシューティング](#14-トラブルシューティング)
15. [通常版へのスケールアップ](#15-通常版へのスケールアップ)

---

## 1. 事前準備（ローカルPC）

### 1-1. AWS CLIのインストール

AWS CLIは、AWSをコマンドで操作するためのツールです。

**Windows の場合:**
1. https://awscli.amazonaws.com/AWSCLIV2.msi をダウンロード
2. ダウンロードした .msi ファイルを実行してインストール
3. コマンドプロンプトを**新しく**開いて確認:
   ```
   aws --version
   ```
   `aws-cli/2.x.x` のような表示が出ればOK

**Mac の場合:**
```bash
brew install awscli
aws --version
```

### 1-2. SSHクライアントの確認

Windows 10以降 / Mac には標準でSSHが入っています。

```bash
ssh -V
# OpenSSH_x.x のような表示が出ればOK
```

---

## 2. AWSアカウントの作成

### 2-1. AWSアカウント作成

> すでにAWSアカウントがある場合は 2-2 へ進んでください。

1. https://aws.amazon.com/ にアクセス
2. 「AWSアカウントを作成」をクリック
3. メールアドレス、パスワード、アカウント名を入力
4. クレジットカード情報を入力（従量課金のため）
5. 電話番号認証を完了
6. サポートプラン: 「ベーシック（無料）」を選択

### 2-2. IAMユーザーの作成（セキュリティのため必須）

ルートアカウント（メールアドレスでログインするアカウント）を直接使うのは危険です。
操作用のIAMユーザーを作成します。

1. AWSコンソール（https://console.aws.amazon.com/）にログイン
2. 上部の検索バーで「IAM」と検索 → IAMを開く
3. 左メニューの「ユーザー」→「ユーザーを作成」
4. 設定:
   - ユーザー名: `deploy-user`（任意の名前）
   - 「AWS マネジメントコンソールへのユーザーアクセスを提供する」にチェック
   - 「IAMユーザーを作成します」を選択
   - パスワードを設定
5. 許可の設定:
   - 「ポリシーを直接アタッチする」を選択
   - 検索バーで `AdministratorAccess` を検索してチェック
6. 「ユーザーの作成」をクリック

### 2-3. アクセスキーの発行

1. 作成したユーザーの詳細ページを開く
2. 「セキュリティ認証情報」タブ → 「アクセスキーを作成」
3. ユースケース: 「コマンドラインインターフェイス (CLI)」を選択
4. **表示されるアクセスキーIDとシークレットアクセスキーをメモ**

> このシークレットアクセスキーは二度と表示されません。必ずこの画面でメモしてください。

### 2-4. AWS CLIの認証設定

ローカルPCのターミナルで実行:

```bash
aws configure
```

以下を入力:
```
AWS Access Key ID [None]: （メモしたアクセスキーID）
AWS Secret Access Key [None]: （メモしたシークレットアクセスキー）
Default region name [None]: ap-northeast-1
Default output format [None]: json
```

確認:
```bash
aws sts get-caller-identity
```
アカウント情報が表示されればOK。

---

## 3. S3バケットの作成（画像保存用）

S3はAWSのファイル保存サービスです。クラッシュレポートのスクリーンショットを保存します。

### 3-1. バケットの作成

```bash
# バケット名は世界中で一意にする必要があります
# プロジェクト名を含めた名前にしてください
aws s3 mb s3://your-project-logserver --region ap-northeast-1
```

> 例: `myapp-crash-reports`, `unity-logserver-2026` など

### 3-2. バケットを非公開に保つ

スクリーンショットは、アプリが発行する期限付きの署名付き URL（`.env` の `S3_PRESIGN_EXPIRE_SECONDS`、既定 1800 秒 = 30 分）で配信します。管理画面の `<img>` も、MCP / 読み取り API が返す Markdown の画像 URL も、この署名付き URL です。バケットに公開読み取りのポリシーは付けず、パブリックアクセスブロックは新規バケットの既定値（4 項目とも有効）のままにします。

```bash
# 既定値のまま。明示する場合は 4 項目とも true
aws s3api put-public-access-block \
  --bucket your-project-logserver \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# バケットポリシーが付いていないことを確認（NoSuchBucketPolicy と返れば正常）
aws s3api get-bucket-policy --bucket your-project-logserver
```

> `your-project-logserver` はすべて Step 3-1 で作成したバケット名に置き換えてください。

> 既存のバケットを流用する場合、匿名読み取り（`Principal: "*"` の `s3:GetObject`）を許可するバケットポリシーが付いていたら外してから、上記のパブリックアクセスブロックを設定します。
>
> ```bash
> aws s3api get-bucket-policy --bucket your-project-logserver --query Policy --output text > bucket-policy.backup.json
> aws s3api delete-bucket-policy --bucket your-project-logserver
> ```
>
> 他の用途のステートメントが同じポリシーに含まれる場合は、`delete-bucket-policy` ではなく該当ステートメントだけを除いた JSON を `put-bucket-policy` で書き戻します。切り替えの順序は「アプリを署名付き URL 対応の版に更新してから、ポリシーを外す」です（更新前にポリシーを外すと管理画面の画像が表示されなくなります）。

署名付き URL の注意点:

- 期限内なら URL を知る誰でも画像を開けます。チャットやメモには画像 URL ではなくレポートの詳細ページ URL（`/buglog/p/<slug>/detail/<id>`）を貼ってください
- 管理画面を期限より長く開いたままにすると画像が表示されなくなります。ページを再読み込みすれば新しい URL で表示されます
- 署名はサーバーの時計を使います。時刻が数分ずれると S3 が `RequestTimeTooSkewed` を返すので、インスタンスで時刻同期（chrony 等）が動いていることを確認してください

### 3-3. S3アクセス用のIAMユーザーを作成

```bash
# IAMユーザー作成
aws iam create-user --user-name logserver-s3

# S3アクセスポリシーの作成
aws iam create-policy --policy-name LogServerS3Access --policy-document '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::your-project-logserver/*"
    }
  ]
}'
```

上のコマンドの出力に `"Arn": "arn:aws:iam::123456789012:policy/LogServerS3Access"` のような行が表示されます。

```bash
# ポリシーをユーザーに割り当て（Arnの値を使用）
aws iam attach-user-policy \
  --user-name logserver-s3 \
  --policy-arn arn:aws:iam::<your-account-id>:policy/LogServerS3Access

# アクセスキーを発行
aws iam create-access-key --user-name logserver-s3
```

**出力される `AccessKeyId` と `SecretAccessKey` をメモしてください。**

---

## 4. EC2インスタンスの作成

### 4-1. キーペアの作成（SSH接続用の鍵）

```bash
aws ec2 create-key-pair \
  --key-name logserver-key \
  --query 'KeyMaterial' \
  --output text > logserver-key.pem
```

**Windows の場合（PowerShellで実行）:**
```powershell
icacls logserver-key.pem /inheritance:r /grant:r "${env:USERNAME}:(R)"
```

**Mac/Linux の場合:**
```bash
chmod 400 logserver-key.pem
```

> **重要: この `logserver-key.pem` はサーバーにログインする唯一の鍵です。絶対に失くさないでください。**

### 4-2. セキュリティグループの作成（ファイアウォール）

```bash
# 作成
aws ec2 create-security-group \
  --group-name logserver-sg \
  --description "Log Server Security Group"
```

出力される `GroupId`（例: `sg-0123456789abcdef0`）をメモ。

```bash
# 自分のIPアドレスを確認
curl -s https://checkip.amazonaws.com

# SSH（ポート22）を自分のIPからのみ許可
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp --port 22 \
  --cidr <YOUR_IP>/32

# HTTP（ポート80）を全世界に開放
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp --port 80 \
  --cidr 0.0.0.0/0

# HTTPS（ポート443）を全世界に開放
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp --port 443 \
  --cidr 0.0.0.0/0
```

> `sg-xxxxxxxxx` と `<YOUR_IP>` を実際の値に置き換えてください。

### 4-3. EC2インスタンスの作成

```bash
# Amazon Linux 2023 (ARM64) の最新AMI IDを取得
aws ssm get-parameters \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query 'Parameters[0].Value' --output text
```

出力されるAMI ID（例: `ami-0abcdef1234567890`）を使って:

```bash
aws ec2 run-instances \
  --image-id ami-0abcdef1234567890 \
  --instance-type t4g.nano \
  --key-name logserver-key \
  --security-group-ids sg-xxxxxxxxx \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":8,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=LogServer}]' \
  --count 1
```

出力される `InstanceId`（例: `i-0123456789abcdef0`）をメモ。

> **安価版のポイント:**
> - `t4g.nano`: ARM Graviton、RAM 0.5GB、月額~470円
> - `VolumeSize:8`: 8GBディスク（最小限に抑える。画像はS3に保存するため十分）

### 4-4. Elastic IP（固定IP）の割り当て

```bash
# Elastic IP の確保
aws ec2 allocate-address --domain vpc
```

出力される `AllocationId` と `PublicIp` をメモ。

```bash
# EC2インスタンスに割り当て
aws ec2 associate-address \
  --instance-id i-xxxxxxxxx \
  --allocation-id eipalloc-xxxxxxxxx
```

> この `PublicIp` がサーバーのIPアドレスです。以降 `<SERVER_IP>` と表記します。

---

## 5. EC2にSSH接続

### 5-1. 接続

```bash
ssh -i logserver-key.pem ec2-user@<SERVER_IP>
```

初回は `Are you sure you want to continue connecting?` と聞かれるので `yes` と入力。

> **接続できない場合:**
> - EC2が「実行中」になるまで1〜2分待つ
> - セキュリティグループのポート22が自分のIPに開放されているか確認
> - `.pem` ファイルのパスが正しいか確認

### 5-2. swap領域の設定 ★安価版で必須

t4g.nano は RAM 0.5GB しかないため、swap（ディスクをメモリ代わりに使う仕組み）を設定します。

```bash
# 1GBのswapファイルを作成
sudo dd if=/dev/zero of=/swapfile bs=128M count=8
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 再起動後も有効にする
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab

# 確認
free -h
```

期待される出力:
```
              total        used        free
Mem:          474Mi        ...         ...
Swap:         1.0Gi        0B        1.0Gi
```

Swap の行に `1.0Gi` が表示されればOK。

---

## 6. サーバーの初期設定

引き続きSSH接続した状態で実行します。

### 6-1. パッケージの更新

```bash
sudo dnf update -y
```

### 6-2. Docker のインストール

```bash
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
```

### 6-3. Docker Compose のインストール

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

> ARM(Graviton)なので `linux-aarch64` を指定しています。

### 6-4. Git のインストール

```bash
sudo dnf install -y git
```

### 6-5. 一度ログアウトして再接続

```bash
exit
ssh -i logserver-key.pem ec2-user@<SERVER_IP>
```

### 6-6. インストール確認

```bash
docker --version        # Docker Engine が表示される
docker compose version  # Docker Compose が表示される
git --version           # git が表示される
```

---

## 7. アプリケーションの配置と起動

### 7-1. ソースコードの取得

**方法A: Gitからクローン（推奨）**
```bash
cd /home/ec2-user
git clone git@github.com:oTAMAKOo/UniModules.SendReportServer.git log-server
cd log-server
```

> リポジトリは public なので `https://github.com/oTAMAKOo/UniModules.SendReportServer.git` でも取得できる。
> fork して private で運用している場合は、`git clone` の前にサーバーで Deploy Key（読み取り専用の SSH 鍵）を作り、
> GitHub の Settings → Deploy keys に登録しておく。手順は [aws_deploy_lightsail.md](aws_deploy_lightsail.md) の 7-1。

**方法B: ローカルPCからSCPで転送**

ローカルPCの別のターミナルで、リポジトリの**親ディレクトリ**から実行:
```bash
rsync -av -e "ssh -i logserver-key.pem" --exclude .git ./UniModules.SendReportServer/ ec2-user@<SERVER_IP>:/home/ec2-user/log-server/
```

> 転送先 `/home/ec2-user/log-server` が既に存在する場合は、中身だけを転送する（ディレクトリ指定だと `log-server/UniModules.SendReportServer` と二重にネストするため）:
```bash
rsync -av -e "ssh -i logserver-key.pem" --exclude .git ./UniModules.SendReportServer/ ec2-user@<SERVER_IP>:/home/ec2-user/log-server/
```

サーバー側で:
```bash
cd /home/ec2-user/log-server
```

> **方法 B で置いた配置先は git 管理されていないため、以後 `git pull` で更新できない。**
> 更新の前に [aws_deploy_lightsail.md](aws_deploy_lightsail.md) 12 章「配置先が git 管理されていない場合」の
> 手順で git 管理に切り替えること。`scp -r dir/*` はドットファイル（`.env.example` / `.gitignore`）を
> 転送しないため rsync（末尾の `/` でディレクトリの中身を同期）を使う。


### 7-2. 本番用 .env ファイルの作成

```bash
cp .env.example .env
nano .env
```

> `nano` はテキストエディタです。編集後は `Ctrl+O` → `Enter` で保存、`Ctrl+X` で終了。

以下のように書き換えてください:

```env
# Database
DATABASE_URL=postgresql://logserver:<DB_PASSWORD>@db:5432/logserver

# 最初のプロジェクト（任意）。AES Key/IV はプロジェクトごとに DB で持ち、起動後に管理画面の
# プロジェクト設定（/buglog/p/<slug>/settings）で Unity 側の鍵に合わせる
INITIAL_PROJECT_SLUG=<プロジェクトの slug（小文字英数字とハイフン）>
INITIAL_PROJECT_NAME=<表示名>

# Storage mode
STORAGE_MODE=s3

# AWS S3（Step 3-3 で作成したキー）
AWS_ACCESS_KEY_ID=<S3用アクセスキーID>
AWS_SECRET_ACCESS_KEY=<S3用シークレットアクセスキー>
AWS_S3_BUCKET_NAME=<Step 3-1 で作成したバケット名>
AWS_REGION=ap-northeast-1
# スクリーンショット URL（署名付き）の有効期限（秒）。任意。既定 1800 = 30 分、60〜604800
# S3_PRESIGN_EXPIRE_SECONDS=1800

# Admin credentials（初回起動時に作成される管理者アカウント）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<強力なパスワード>

# Session secret
SECRET_KEY=<ランダム文字列>

# URL prefix
URL_PREFIX=/buglog
```

**パスワード・シークレットのランダム生成:**
```bash
# SECRET_KEY 用
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# DB_PASSWORD 用
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

### 7-3. docker-compose.prod.yml の作成（本番用設定）

リポジトリに雛形があるので、コピーして書き換えます。

```bash
cp docker-compose.prod.yml.example docker-compose.prod.yml
vi docker-compose.prod.yml
```

書き換えるのは 3 箇所です。

- `POSTGRES_PASSWORD: CHANGE_ME` → `.env` の `DB_PASSWORD` と同じ値
- `--workers 2` → **`--workers 1`**（RAM 0.5GBなのでワーカーは1つに制限します）
- db の `command:`（軽量構成）の**コメントを外す**（RAM 0.5GB のため。内容は `docker-compose.light.yml` と同じ）

> 雛形の `ports: !override []` と `volumes: !override` は、開発用 compose の 5432 公開と
> `./app` バインドマウントを打ち消すためのものです。`!override` を外すと compose が
> マージしてしまい、どちらも残ります（`!override` は Docker Compose v2.24.0 以降）。

### 7-4. 軽量構成について ★安価版で必須

軽量構成（PostgreSQL の `shared_buffers=32MB` 等）は、7-3 のとおり `docker-compose.prod.yml` の
`db.command` で有効にします。

> **`cp docker-compose.light.yml docker-compose.override.yml` では効きません。**
> `-f` でファイルを明示して起動する（7-5 と 10-1 の systemd）と、`docker-compose.override.yml` は
> 自動では読み込まれません。起動後に `SHOW shared_buffers` が `32MB` になっていることを確認してください:
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db psql -U logserver -d logserver -c "SHOW shared_buffers"
> ```

### 7-5. ビルドと起動

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

初回は5〜10分程度かかります。

### 7-6. 起動確認

```bash
# 全コンテナが Up になっているか確認
docker compose ps

# ログ確認（Ctrl+C で終了）
docker compose logs -f app
```

---

## 8. 動作確認

### 8-1. サーバー側で確認

```bash
curl http://localhost/buglog/login
```

HTMLが返ってくればOK。

### 8-2. ブラウザで確認

```
http://<SERVER_IP>/buglog/login
```

ログイン画面が表示されたら成功。

- ユーザー名: `.env` の `ADMIN_USERNAME`
- パスワード: `.env` の `ADMIN_PASSWORD`

### 8-3. Unityクライアントからの送信テスト

先に管理画面のプロジェクト設定（`/buglog/p/<slug>/settings`）で AES Key/IV を Unity 側の鍵に合わせる。
UnityクライアントのレポートURLを変更:
```
http://<SERVER_IP>/buglog/report/<slug>
```

送信後、管理画面の一覧に表示されることを確認。鍵が一致しないと 400、slug が違うと 404 が返る。

---

## 9. ドメインとHTTPS設定（推奨）

### 9-1. ドメインの取得

以下のいずれかで取得:
- **お名前.com** / **ムームードメイン**: 年額1,000円〜
- **Route 53**（AWS）: 年額$12〜
- 既存ドメインのサブドメインでもOK（例: `log.yourdomain.com`）

### 9-2. DNSの設定

ドメイン管理画面でAレコードを追加:
```
タイプ: A
名前: log（またはお好みのサブドメイン）
値: <SERVER_IP>
TTL: 300
```

確認（反映まで数分〜数時間）:
```bash
nslookup log.yourdomain.com
```

### 9-3. SSL証明書の取得（Let's Encrypt、無料）

EC2にSSH接続して:

```bash
# certbot インストール
sudo dnf install -y certbot

# Nginx を一時停止
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop nginx

# 証明書を取得
sudo certbot certonly --standalone -d log.yourdomain.com

# Nginx を再起動
docker compose -f docker-compose.yml -f docker-compose.prod.yml start nginx
```

### 9-4. NginxのHTTPS設定

```bash
cat > nginx/nginx.conf << 'EOF'
server {
    listen 80;
    server_name log.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name log.yourdomain.com;
    client_max_body_size 50M;

    ssl_certificate     /etc/letsencrypt/live/log.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/log.yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    # 一度 https で開いたブラウザは以後 http で接続しなくなる（Cookie の平文送信を防ぐ）
    add_header Strict-Transport-Security "max-age=31536000" always;

    location /storage/ {
        alias /app/storage/;
    }

    location / {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
```

> `log.yourdomain.com` を実際のドメインに置き換えてください。

### 9-5. docker-compose.prod.yml にHTTPS設定を追加

`docker-compose.prod.yml` の nginx セクションを書き換え（雛形にコメントアウトで入っているので、
コメントを外すだけでも構いません）:

```yaml
  nginx:
    ports: !override
      - "80:80"
      - "443:443"
    volumes: !override
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf
      - app_storage:/app/storage:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
    restart: always
```

反映:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

ブラウザで `https://log.yourdomain.com/buglog/login` にアクセスして鍵マークが表示されればOK。

---

## 10. 自動起動と自動バックアップ

### 10-1. サーバー再起動時の自動起動

```bash
sudo tee /etc/systemd/system/logserver.service << 'EOF'
[Unit]
Description=Log Server (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ec2-user/log-server
ExecStart=/usr/local/lib/docker/cli-plugins/docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
ExecStop=/usr/local/lib/docker/cli-plugins/docker-compose -f docker-compose.yml -f docker-compose.prod.yml down
User=ec2-user

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable logserver
```

### 10-2. DBの自動バックアップ（毎日午前4時）

```bash
mkdir -p /home/ec2-user/backups
crontab -e
```

以下の行を追加（`i` で入力モード、`Esc` → `:wq` で保存）:

```
0 4 * * * cd /home/ec2-user/log-server && docker compose exec -T db pg_dump -U logserver logserver > /home/ec2-user/backups/backup_$(date +\%Y\%m\%d).sql && aws s3 cp /home/ec2-user/backups/backup_$(date +\%Y\%m\%d).sql s3://your-project-logserver/backups/ && find /home/ec2-user/backups -name "*.sql" -mtime +7 -delete
```

> この1行で「DBダンプ → S3に保存 → 7日以上前のローカルバックアップを削除」を実行します。

### 10-3. SSL証明書の自動更新（HTTPS設定済みの場合）

```bash
sudo crontab -e
```

以下を追加:
```
0 3 1 * * certbot renew --pre-hook "docker compose -f /home/ec2-user/log-server/docker-compose.yml -f /home/ec2-user/log-server/docker-compose.prod.yml stop nginx" --post-hook "docker compose -f /home/ec2-user/log-server/docker-compose.yml -f /home/ec2-user/log-server/docker-compose.prod.yml start nginx"
```

---

## 11. セキュリティチェックリスト

### 必須

- [ ] `.env` のパスワード・シークレットをすべてランダム文字列に変更した
- [ ] `ADMIN_PASSWORD` をデフォルトから変更した
- [ ] PostgreSQLのパスワードをデフォルト（`logserver`）から変更した
- [ ] セキュリティグループでポート22のソースが自分のIPのみ
- [ ] セキュリティグループにポート5432/8000の許可ルールがない
- [ ] `.env` ファイルのパーミッション: `chmod 600 .env`

### 推奨

- [ ] HTTPS を有効にした
- [ ] DB自動バックアップを設定した
- [ ] SSL証明書の自動更新を設定した
- [ ] ログイン後に管理画面からパスワードを変更した
- [ ] `.pem` ファイルを安全な場所に保管した

---

## 12. 運用・メンテナンス

### アプリの更新

```bash
ssh -i logserver-key.pem ec2-user@<SERVER_IP>
cd /home/ec2-user/log-server
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### ログの確認

```bash
docker compose logs -f        # 全サービス
docker compose logs -f app    # アプリのみ
docker compose logs -f db     # DBのみ
```

### DBバックアップ（手動）

```bash
docker compose exec -T db pg_dump -U logserver logserver > backup.sql
```

### DBリストア

バックアップは `pg_dump` のプレーン形式（`--clean` 無し）なので、**稼働中の DB にそのまま流し込むと
`CREATE TABLE` は「already exists」、`COPY` は主キー重複で全件失敗し、実質何も戻りません**
（psql は既定でエラーを無視して進むため、成功したように見えます）。app を止めて DB を作り直してから流します。
既存データはすべてバックアップ時点の内容に置き換わるので、直前に手動バックアップを取ってください。

```bash
cd /home/ec2-user/log-server
C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
aws s3 cp s3://<バケット名>/backups/backup_YYYYMMDD.sql ./backup.sql
$C exec -T db pg_dump -U logserver logserver > before_restore.sql   # 直前の状態も残す
$C stop app                                                          # DB への接続を止める（この間レポート受信は失敗する）
$C exec -T db psql -U logserver -d postgres -c "DROP DATABASE logserver WITH (FORCE);" -c "CREATE DATABASE logserver OWNER logserver;"
$C exec -T db psql -U logserver -d logserver -v ON_ERROR_STOP=1 < backup.sql
$C start app
$C logs app --tail 5                                                 # startup complete を確認
```

`ON_ERROR_STOP=1` を付けているので、途中でエラーが出れば止まります（無言で壊れません）。

### ディスク・メモリの確認

```bash
df -h          # ディスク
free -h        # メモリ・swap
docker stats   # コンテナ別リソース使用量
```

### 不要データの削除

```bash
docker system prune -a
```

---

## 13. コスト削減（リザーブドインスタンス）

安定稼働を確認したら（1〜2ヶ月後が目安）、1年RIに切り替えで大幅に節約できます。

### 手順

1. AWSコンソール → EC2 → 左メニュー「リザーブドインスタンス」
2. 「リザーブドインスタンスの購入」
3. 設定:
   - プラットフォーム: Linux/UNIX
   - インスタンスタイプ: `t4g.nano`
   - 期間: 1年
   - 支払い: 「全額前払い」が最安
4. 購入確定

### 料金比較

| 支払い方法 | 月額 | 年間合計 |
|-----------|------|---------|
| オンデマンド（現在） | ~470円 | ~5,640円 |
| RI 前払いなし | ~340円 | ~4,080円 |
| RI 全額前払い | ~290円 | ~3,480円 |

> 注意: 購入後の取り消し・返金・インスタンスタイプ変更はできません。

---

## 14. トラブルシューティング

### SSH接続できない

| 原因 | 対処 |
|------|------|
| EC2がまだ起動中 | AWSコンソールで「実行中」になるまで待つ |
| セキュリティグループ | ポート22が自分のIPに開放されているか確認 |
| IPアドレスが変わった | 自宅のIPが変わった場合はセキュリティグループを更新 |
| .pemのパーミッション | `chmod 400 logserver-key.pem` |

### コンテナが起動しない

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs
docker compose logs db     # DB関連
docker compose logs app    # アプリ関連
docker compose logs nginx  # Nginx関連
```

### メモリ不足（OOM Killed）

```bash
free -h          # swapが有効か確認
swapon --show    # swap の詳細
docker stats     # どのコンテナがメモリを食っているか
```

swapが無効なら Step 5-2 を再実行。

### DB接続エラー

```bash
docker compose exec db pg_isready -U logserver
```

よくある原因: `docker-compose.prod.yml` の `POSTGRES_PASSWORD` と `.env` の `DATABASE_URL` 内のパスワードが不一致。

### ブラウザからアクセスできない

| 原因 | 対処 |
|------|------|
| セキュリティグループ | ポート80/443が開放されているか確認 |
| Elastic IPが未割り当て | AWSコンソールで確認 |
| Nginxが停止 | `docker compose logs nginx` で確認 |

---

## 15. 通常版へのスケールアップ

アクセス数が増えてt4g.nanoでは厳しくなった場合:

1. AWSコンソール → EC2 → インスタンスを停止
2. インスタンスタイプを `t4g.micro` に変更
3. インスタンスを起動
4. SSH接続して `docker-compose.prod.yml` を 2 箇所編集する:
   - db の `command:` ブロック（軽量構成）をコメントアウト
   - app の `--workers 1` を `--workers 2` に変更
   > 軽量構成の `max_connections=20` を残したまま `--workers 2` にしないこと（接続数が足りなくなる）。
5. 反映（db と app が再作成される）:
   ```bash
   cd /home/ec2-user/log-server
   docker compose -f docker-compose.yml -f docker-compose.prod.yml config > /dev/null &&    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```

> スケールアップはインスタンス停止→タイプ変更→起動だけなので数分で完了します。
> データやIPアドレスは変わりません。

---

## 全体チェックリスト

- [ ] AWS CLI インストール・認証設定
- [ ] S3 バケット作成・ポリシー設定
- [ ] EC2 キーペア作成
- [ ] EC2 セキュリティグループ作成
- [ ] EC2 インスタンス（t4g.nano）作成
- [ ] Elastic IP 割り当て
- [ ] SSH接続確認
- [ ] **swap設定（1GB）**
- [ ] Docker / Docker Compose / Git インストール
- [ ] ソースコード配置
- [ ] .env 作成（パスワード全てランダム生成）
- [ ] docker-compose.prod.yml 作成
- [ ] **docker-compose.prod.yml の db.command で軽量構成を有効化**
- [ ] docker compose up -d --build
- [ ] ブラウザでログイン確認
- [ ] Unityクライアントからの送信テスト
- [ ] 自動起動設定（systemd）
- [ ] 自動バックアップ設定（cron）
- [ ] （推奨）ドメイン・HTTPS設定
- [ ] セキュリティチェックリスト確認
