# AWS デプロイ手順書 — 通常版（t4g.micro）

チーム開発・中規模プロジェクト向けの標準構成です。

### この構成のスペック

| 項目 | 値 |
|------|-----|
| インスタンス | t4g.micro（ARM Graviton） |
| RAM | 1GB |
| ディスク | 16GB gp3 |
| 画像保存 | S3 |
| 月額 | ~$13（~1,960円）＝ インスタンス $7.88 + パブリックIPv4 $3.65 + EBS 16GB $1.54 |

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

---

## 1. 事前準備（ローカルPC）

### 1-1. AWS CLIのインストール

**Windows の場合:**
1. https://awscli.amazonaws.com/AWSCLIV2.msi をダウンロード
2. ダウンロードした .msi ファイルを実行してインストール
3. コマンドプロンプトを**新しく**開いて確認:
   ```
   aws --version
   ```

**Mac の場合:**
```bash
brew install awscli
aws --version
```

### 1-2. SSHクライアントの確認

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
4. クレジットカード情報を入力
5. 電話番号認証を完了
6. サポートプラン: 「ベーシック（無料）」を選択

### 2-2. IAMユーザーの作成

1. AWSコンソール（https://console.aws.amazon.com/）にログイン
2. 上部の検索バーで「IAM」と検索 → IAMを開く
3. 左メニューの「ユーザー」→「ユーザーを作成」
4. 設定:
   - ユーザー名: `deploy-user`
   - 「AWS マネジメントコンソールへのユーザーアクセスを提供する」にチェック
   - 「IAMユーザーを作成します」を選択
   - パスワードを設定
5. 許可の設定:
   - 「ポリシーを直接アタッチする」を選択
   - `AdministratorAccess` を検索してチェック
6. 「ユーザーの作成」をクリック

### 2-3. アクセスキーの発行

1. 作成したユーザーの詳細ページを開く
2. 「セキュリティ認証情報」タブ → 「アクセスキーを作成」
3. ユースケース: 「コマンドラインインターフェイス (CLI)」を選択
4. **表示されるアクセスキーIDとシークレットアクセスキーをメモ**

### 2-4. AWS CLIの認証設定

```bash
aws configure
```

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

---

## 3. S3バケットの作成（画像保存用）

### 3-1. バケットの作成

```bash
aws s3 mb s3://your-project-logserver --region ap-northeast-1
```

### 3-2. 画像を外部から閲覧可能にする

```bash
aws s3api put-public-access-block \
  --bucket your-project-logserver \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

aws s3api put-bucket-policy --bucket your-project-logserver --policy '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadForReportImages",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::your-project-logserver/report/*"
    }
  ]
}'
```

### 3-3. S3アクセス用のIAMユーザーを作成

```bash
aws iam create-user --user-name logserver-s3

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

# 出力されるArnを使用
aws iam attach-user-policy \
  --user-name logserver-s3 \
  --policy-arn arn:aws:iam::<your-account-id>:policy/LogServerS3Access

aws iam create-access-key --user-name logserver-s3
```

**出力される `AccessKeyId` と `SecretAccessKey` をメモ。**

---

## 4. EC2インスタンスの作成

### 4-1. キーペアの作成

```bash
aws ec2 create-key-pair \
  --key-name logserver-key \
  --query 'KeyMaterial' \
  --output text > logserver-key.pem
```

**Windows（PowerShell）:**
```powershell
icacls logserver-key.pem /inheritance:r /grant:r "${env:USERNAME}:(R)"
```

**Mac/Linux:**
```bash
chmod 400 logserver-key.pem
```

### 4-2. セキュリティグループの作成

```bash
aws ec2 create-security-group \
  --group-name logserver-sg \
  --description "Log Server Security Group"
```

`GroupId` をメモ。

```bash
# 自分のIPを確認
curl -s https://checkip.amazonaws.com

# SSH を自分のIPからのみ許可
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp --port 22 \
  --cidr <YOUR_IP>/32

# HTTP
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp --port 80 \
  --cidr 0.0.0.0/0

# HTTPS
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp --port 443 \
  --cidr 0.0.0.0/0
```

### 4-3. EC2インスタンスの作成

```bash
# AMI ID を取得
aws ssm get-parameters \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query 'Parameters[0].Value' --output text
```

```bash
aws ec2 run-instances \
  --image-id ami-0abcdef1234567890 \
  --instance-type t4g.micro \
  --key-name logserver-key \
  --security-group-ids sg-xxxxxxxxx \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":16,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=LogServer}]' \
  --count 1
```

> **通常版のポイント:**
> - `t4g.micro`: ARM Graviton、RAM 1GB、月額~920円
> - `VolumeSize:16`: 16GBディスク（余裕を持たせる）

`InstanceId` をメモ。

### 4-4. Elastic IP（固定IP）の割り当て

```bash
aws ec2 allocate-address --domain vpc
```

`AllocationId` と `PublicIp` をメモ。

```bash
aws ec2 associate-address \
  --instance-id i-xxxxxxxxx \
  --allocation-id eipalloc-xxxxxxxxx
```

> `PublicIp` が `<SERVER_IP>` になります。

---

## 5. EC2にSSH接続

```bash
ssh -i logserver-key.pem ec2-user@<SERVER_IP>
```

初回は `yes` と入力。

> **通常版ではswap設定は不要です。** RAM 1GBで十分動作します。

---

## 6. サーバーの初期設定

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
docker --version
docker compose version
git --version
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

**方法B: ローカルPCからSCPで転送**
```bash
# ローカルPCで（リポジトリの親ディレクトリから実行）
scp -i logserver-key.pem -r ./UniModules.SendReportServer ec2-user@<SERVER_IP>:/home/ec2-user/log-server
```

> 転送先 `/home/ec2-user/log-server` が既に存在する場合は、中身だけを転送する（ディレクトリ指定だと `log-server/UniModules.SendReportServer` と二重にネストするため）:
```bash
scp -i logserver-key.pem -r ./UniModules.SendReportServer/* ec2-user@<SERVER_IP>:/home/ec2-user/log-server/
```

### 7-2. 本番用 .env ファイルの作成

```bash
cp .env.example .env
nano .env
```

```env
# Database
DATABASE_URL=postgresql://logserver:<DB_PASSWORD>@db:5432/logserver

# AES Encryption
REPORT_AES_KEY=<Unity側のAESキー（32文字）>
REPORT_AES_IV=<Unity側のAES IV（16文字）>

# Storage mode
STORAGE_MODE=s3

# AWS S3
AWS_ACCESS_KEY_ID=<S3用アクセスキーID>
AWS_SECRET_ACCESS_KEY=<S3用シークレットアクセスキー>
AWS_S3_BUCKET_NAME=<バケット名>
AWS_REGION=ap-northeast-1

# Admin credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<強力なパスワード>

# Session secret
SECRET_KEY=<ランダム文字列>

# URL prefix
URL_PREFIX=/buglog
```

**ランダム生成:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"  # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"  # DB_PASSWORD
```

### 7-3. docker-compose.prod.yml の作成

リポジトリに雛形があるので、コピーして書き換えます。

```bash
cp docker-compose.prod.yml.example docker-compose.prod.yml
vi docker-compose.prod.yml
```

書き換えるのは 1 箇所だけです。

- `POSTGRES_PASSWORD: CHANGE_ME` → `.env` の `DB_PASSWORD` と同じ値

> **通常版は雛形のままの `--workers 2`** で構いません（RAM 1GBなら余裕あり）。
> **軽量構成（db の `command:`）は不要です。** 雛形のコメントアウトのままにしてください。PostgreSQLはデフォルト設定で動作します。
>
> 雛形の `ports: !override []` と `volumes: !override` は、開発用 compose の 5432 公開と
> `./app` バインドマウントを打ち消すためのものです。`!override` を外すと compose が
> マージしてしまい、どちらも残ります（`!override` は Docker Compose v2.24.0 以降）。

### 7-4. ビルドと起動

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### 7-5. 起動確認

```bash
docker compose ps
docker compose logs -f app
```

---

## 8. 動作確認

### 8-1. サーバー側

```bash
curl http://localhost/buglog/login
```

### 8-2. ブラウザ

```
http://<SERVER_IP>/buglog/login
```

### 8-3. Unityクライアントからの送信テスト

レポートURL: `http://<SERVER_IP>/buglog/report`

---

## 9. ドメインとHTTPS設定（推奨）

### 9-1. ドメインの取得

お名前.com、ムームードメイン、Route 53 等で取得。

### 9-2. DNSの設定

Aレコードを追加:
```
タイプ: A
名前: log
値: <SERVER_IP>
TTL: 300
```

### 9-3. SSL証明書の取得

```bash
sudo dnf install -y certbot
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop nginx
sudo certbot certonly --standalone -d log.yourdomain.com
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

### 9-5. docker-compose.prod.yml にHTTPS追加

nginx セクションを書き換え（雛形にコメントアウトで入っているので、コメントを外すだけでも構いません）:

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

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

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

### 10-2. DBの自動バックアップ

```bash
mkdir -p /home/ec2-user/backups
crontab -e
```

追加:
```
0 4 * * * cd /home/ec2-user/log-server && docker compose exec -T db pg_dump -U logserver logserver > /home/ec2-user/backups/backup_$(date +\%Y\%m\%d).sql && aws s3 cp /home/ec2-user/backups/backup_$(date +\%Y\%m\%d).sql s3://your-project-logserver/backups/ && find /home/ec2-user/backups -name "*.sql" -mtime +7 -delete
```

### 10-3. SSL証明書の自動更新

```bash
sudo crontab -e
```

追加:
```
0 3 1 * * certbot renew --pre-hook "docker compose -f /home/ec2-user/log-server/docker-compose.yml -f /home/ec2-user/log-server/docker-compose.prod.yml stop nginx" --post-hook "docker compose -f /home/ec2-user/log-server/docker-compose.yml -f /home/ec2-user/log-server/docker-compose.prod.yml start nginx"
```

---

## 11. セキュリティチェックリスト

### 必須

- [ ] `.env` のパスワード・シークレットをすべてランダム文字列に変更した
- [ ] `ADMIN_PASSWORD` をデフォルトから変更した
- [ ] PostgreSQLのパスワードをデフォルトから変更した
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
```

### DBバックアップ（手動）

```bash
docker compose exec -T db pg_dump -U logserver logserver > backup.sql
```

### DBリストア

```bash
aws s3 cp s3://your-project-logserver/backups/backup_20260409.sql ./backup.sql
cat backup.sql | docker compose exec -T db psql -U logserver logserver
```

### ディスク・メモリの確認

```bash
df -h          # ディスク
free -h        # メモリ
docker stats   # コンテナ別リソース
```

---

## 13. コスト削減（リザーブドインスタンス）

安定稼働を確認したら1年RIに切り替え。

1. AWSコンソール → EC2 → 「リザーブドインスタンス」
2. 「リザーブドインスタンスの購入」
3. インスタンスタイプ: `t4g.micro`、期間: 1年、支払い: 全額前払い

| 支払い方法 | 月額 | 年間合計 |
|-----------|------|---------|
| オンデマンド | ~920円 | ~11,040円 |
| RI 前払いなし | ~660円 | ~7,920円 |
| RI 全額前払い | ~570円 | ~6,840円 |

---

## 14. トラブルシューティング

### SSH接続できない

| 原因 | 対処 |
|------|------|
| EC2がまだ起動中 | 「実行中」になるまで待つ |
| セキュリティグループ | ポート22が自分のIPに開放されているか確認 |
| IPアドレスが変わった | セキュリティグループを更新 |
| .pemのパーミッション | `chmod 400 logserver-key.pem` |

### コンテナが起動しない

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs
```

### DB接続エラー

`docker-compose.prod.yml` の `POSTGRES_PASSWORD` と `.env` の `DATABASE_URL` 内のパスワードが一致しているか確認。

### ブラウザからアクセスできない

セキュリティグループでポート80/443が開放されているか、Elastic IPが割り当てられているか確認。

---

## 全体チェックリスト

- [ ] AWS CLI インストール・認証設定
- [ ] S3 バケット作成・ポリシー設定
- [ ] EC2 キーペア作成
- [ ] EC2 セキュリティグループ作成
- [ ] EC2 インスタンス（t4g.micro）作成
- [ ] Elastic IP 割り当て
- [ ] SSH接続確認
- [ ] Docker / Docker Compose / Git インストール
- [ ] ソースコード配置
- [ ] .env 作成（パスワード全てランダム生成）
- [ ] docker-compose.prod.yml 作成
- [ ] docker compose up -d --build
- [ ] ブラウザでログイン確認
- [ ] Unityクライアントからの送信テスト
- [ ] 自動起動設定（systemd）
- [ ] 自動バックアップ設定（cron）
- [ ] （推奨）ドメイン・HTTPS設定
- [ ] セキュリティチェックリスト確認
