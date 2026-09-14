# AWS デプロイ手順書 — Lightsail（最小コスト構成）

常時公開するサーバーを AWS で最も安く運用する構成です。

### この構成のスペック

| 項目 | 値 |
|------|-----|
| インスタンス | Lightsail 最小バンドル（デュアルスタック） |
| RAM | 0.5GB + swap 1GB |
| CPU | 2 vCPU（x86_64） |
| ディスク | 20GB SSD |
| 転送量 | 1TB/月 込み |
| 固定 IPv4 | **バンドル料金に含まれる** |
| 画像保存 | S3 |
| 月額 | **$5（定額・コミットなし）** |

### なぜ EC2 より安いのか

**2024年2月から、パブリック IPv4 アドレスはインスタンスに割り当てていても
$0.005/時（約 $3.65/月）が課金される。** EC2 は「インスタンス代 + IPv4 + EBS」の
合算になるため、最小の t4g.nano でも約 $8.4/月かかる。

| 構成 | 内訳 | 月額 |
|------|------|-----:|
| Lightsail 最小 | バンドル定額（IPv4・転送量込み） | **$5.00** |
| EC2 t4g.nano | $3.94 + IPv4 $3.65 + EBS 8GB $0.77 | $8.36 |
| EC2 t4g.nano + 1年RI | ~$2.5 + IPv4 $3.65 + EBS $0.77 | ~$6.9 |

IPv4 と EBS だけで $4.4 になるため、リザーブドインスタンスで 1〜3 年コミットしても
Lightsail の定額を下回らない。

> **IPv6 のみのバンドル（さらに約 30% 安い）は選ばないこと。**
> 閲覧者の回線やゲーム実機が IPv4 のみだと、管理画面もレポート送信も一切到達できない。

---

## 目次

1. [事前準備](#1-事前準備)
2. [S3バケットの作成（画像保存用）](#2-s3バケットの作成画像保存用)
3. [Lightsailインスタンスの作成](#3-lightsailインスタンスの作成)
4. [固定IPとファイアウォール](#4-固定ipとファイアウォール)
5. [SSH接続](#5-ssh接続)
6. [サーバーの初期設定](#6-サーバーの初期設定)
7. [アプリケーションの配置と起動](#7-アプリケーションの配置と起動)
8. [動作確認](#8-動作確認)
9. [ドメインとHTTPS設定](#9-ドメインとhttps設定)
10. [自動起動・自動バックアップ・証明書の自動更新](#10-自動起動自動バックアップ証明書の自動更新)
11. [セキュリティチェックリスト](#11-セキュリティチェックリスト)
12. [運用・メンテナンス](#12-運用メンテナンス)
13. [トラブルシューティング](#13-トラブルシューティング)

---

## 1. 事前準備

AWS CLI のインストールと IAM ユーザーの作成は
[aws_deploy_budget.md](aws_deploy_budget.md) の 1〜2 章と同じ。既に済んでいれば読み飛ばす。

この手順書のコマンドを CLI で実行する場合、IAM ユーザーに以下の権限が要る:

- `lightsail:*`（インスタンス・固定IP・ファイアウォール・スナップショット）
- `s3:*`（画像保存用バケットに限定してよい）

コンソール（ブラウザ）だけで完結させることもできる。各章にコンソール手順と CLI 手順の
両方を記載している。

---

## 2. S3バケットの作成（画像保存用）

[aws_deploy_budget.md](aws_deploy_budget.md) の 3 章と同じ手順。既存のバケットを
流用する場合は、バケット名と S3 用アクセスキーだけ手元に用意すればよい。

`STORAGE_MODE=s3` にするとレポートのスクリーンショットが S3 に保存される。
インスタンスのディスク（20GB）を画像で埋めないために、S3 の利用を推奨する。

---

## 3. Lightsailインスタンスの作成

### 3-1. コンソールで作成する場合

1. https://lightsail.aws.amazon.com/ を開く
2. 右上のリージョンが **東京（ap-northeast-1）** になっていることを確認
3. 「インスタンスの作成」をクリック
4. 設定:
   - リージョン / AZ: 東京（ap-northeast-1a）
   - プラットフォーム: **Linux/Unix**
   - 設計図（ブループリント）: **OS のみ → Amazon Linux 2023**
   - ネットワークタイプ: **デュアルスタック**（IPv6 のみを選ばない）
   - プラン: **最小の 512MB プラン（$5/月）**
   - インスタンス名: `log-server`（任意）
5. 「インスタンスの作成」をクリック

### 3-2. SSH鍵をダウンロードする

1. 左メニュー「アカウント」→「SSH キー」タブ
2. リージョンのデフォルトキーの「ダウンロード」をクリック
3. ダウンロードした `.pem` を安全な場所へ置く

**Windows の場合、ダウンロード直後にアクセス権を絞る（PowerShell）:**
```powershell
icacls LightsailDefaultKey-ap-northeast-1.pem /inheritance:r /grant:r "${env:USERNAME}:(R)"
```

> 鍵ファイルのアクセス権を自分だけに絞らないと、SSH クライアントが接続を拒否する。
> 逆に自分を含めない ACL にすると読めなくなり、復旧に管理者権限が要る。**設定後に
> `type` / `cat` で中身が読めることを必ず確認する。**

### 3-3. CLIで作成する場合

```bash
# 現在のバンドル一覧（価格とIDを確認する）
aws lightsail get-bundles --region ap-northeast-1 \
  --query 'bundles[?supportedPlatforms[0]==`LINUX_UNIX`].{Id:bundleId,Price:price,RAM:ramSizeInGb,Disk:diskSizeInGb}' \
  --output table

# ブループリント一覧（Amazon Linux 2023 の blueprintId を確認する）
aws lightsail get-blueprints --region ap-northeast-1 \
  --query 'blueprints[?type==`os`].{Id:blueprintId,Name:name}' --output table
```

上で確認した ID を使って作成する:

```bash
aws lightsail create-instances \
  --region ap-northeast-1 \
  --instance-names log-server \
  --availability-zone ap-northeast-1a \
  --blueprint-id <amazon_linux_2023 の blueprintId> \
  --bundle-id <最小バンドルの bundleId> \
  --ip-address-type dualstack
```

状態が `running` になるまで待つ:

```bash
aws lightsail get-instance --region ap-northeast-1 --instance-name log-server \
  --query 'instance.state.name' --output text
```

---

## 4. 固定IPとファイアウォール

### 4-1. 固定IPの割り当て

インスタンス作成時のパブリック IP は再起動で変わる。DNS を向けるので固定 IP を付ける。

**コンソール:** 「ネットワーキング」タブ →「静的 IP の作成」→ インスタンス `log-server` にアタッチ

**CLI:**
```bash
aws lightsail allocate-static-ip --region ap-northeast-1 --static-ip-name log-server-ip
aws lightsail attach-static-ip --region ap-northeast-1 \
  --static-ip-name log-server-ip --instance-name log-server

# 割り当てられたIPを確認
aws lightsail get-static-ip --region ap-northeast-1 --static-ip-name log-server-ip \
  --query 'staticIp.ipAddress' --output text
```

> 固定 IP は**インスタンスにアタッチしている間は無料**。デタッチしたまま放置すると
> 課金されるので、インスタンスを削除するときは固定 IP も解放すること。

### 4-2. ファイアウォール

Lightsail のファイアウォールはインスタンス単位（EC2 のセキュリティグループに相当）。
既定で 22（SSH）と 80（HTTP）が全開放されているので、22 を自分の IP に絞り、443 を開ける。

**コンソール:** インスタンス →「ネットワーキング」タブ → IPv4 ファイアウォール

| アプリケーション | プロトコル | ポート | 制限 |
|---|---|---|---|
| SSH | TCP | 22 | **自分のIPのみ**（「送信元 IP アドレスに制限する」にチェック） |
| HTTP | TCP | 80 | すべて |
| HTTPS | TCP | 443 | すべて |

**CLI:**
```bash
# 自分のグローバルIPを確認
curl -s https://checkip.amazonaws.com

aws lightsail put-instance-public-ports --region ap-northeast-1 \
  --instance-name log-server \
  --port-infos \
    'fromPort=22,toPort=22,protocol=TCP,cidrs=<YOUR_IP>/32' \
    'fromPort=80,toPort=80,protocol=TCP,cidrs=0.0.0.0/0' \
    'fromPort=443,toPort=443,protocol=TCP,cidrs=0.0.0.0/0'
```

> **`put-instance-public-ports` はルールを「置き換える」。** 指定しなかったポートは
> 閉じられる。22 を書き忘れると自分が締め出される（その場合はコンソールの
> ブラウザ SSH から復旧できる）。

5432（PostgreSQL）と 8000（uvicorn）は**絶対に開けない**。外部に出すのは 80/443 だけ。

---

## 5. SSH接続

```bash
ssh -i LightsailDefaultKey-ap-northeast-1.pem ec2-user@<STATIC_IP>
```

接続できない場合はコンソールのインスタンス画面から「ブラウザを使用して接続」で入れる。

---

## 6. サーバーの初期設定

### 6-1. swap の作成 ★必須

RAM 0.5GB では Docker のビルド中にメモリが尽きる。swap を 1GB 作る。

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # Swap: 1.0Gi と表示されればOK
```

### 6-2. パッケージの更新と Docker のインストール

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
```

### 6-3. Docker Compose のインストール

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

> **Lightsail は x86_64 なので `linux-x86_64` を指定する。**
> EC2 の手順書（t4g = ARM）は `linux-aarch64` を使っているので取り違えないこと。

### 6-4. buildx プラグインのインストール ★必須

Compose v2.24 以降は `--build` に buildx を要求するが、Amazon Linux の `docker`
パッケージには含まれていない。入れずにビルドすると次で止まる:

```
compose build requires buildx 0.17.0 or later
```

```bash
VER=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
sudo curl -sSL "https://github.com/docker/buildx/releases/download/${VER}/buildx-${VER}.linux-amd64" \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx
docker buildx version
```

> buildx のリリース URL はファイル名にバージョンが入るため、`latest/download` の
> 固定 URL が使えない。上のように tag を取得してから組み立てる。

### 6-5. 再接続して確認

```bash
exit
ssh -i LightsailDefaultKey-ap-northeast-1.pem ec2-user@<STATIC_IP>
```

```bash
docker --version        # Docker Engine が表示される
docker compose version  # Docker Compose が表示される
docker ps               # sudo なしで動く（グループ反映の確認）
```

---

## 7. アプリケーションの配置と起動

### 7-1. ソースコードの取得

```bash
cd /home/ec2-user
git clone https://github.com/oTAMAKOo/UniModules.SendReportServer.git log-server
cd log-server
```

> サーバーに置くのは**本体リポジトリだけ**でよい。プロジェクト側のラッパー
> （`include` で本体を取り込む compose）はローカル開発用で、本番では使わない。

### 7-2. 本番用 .env の作成

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

```env
DATABASE_URL=postgresql://logserver:<DB_PASSWORD>@db:5432/logserver

REPORT_AES_KEY=<クライアントと一致する32文字>
REPORT_AES_IV=<クライアントと一致する16文字>

STORAGE_MODE=s3
AWS_ACCESS_KEY_ID=<S3用アクセスキーID>
AWS_SECRET_ACCESS_KEY=<S3用シークレットアクセスキー>
AWS_S3_BUCKET_NAME=<バケット名>
AWS_REGION=ap-northeast-1

ADMIN_USERNAME=admin
ADMIN_PASSWORD=<強力なパスワード>
SECRET_KEY=<ランダム文字列>

URL_PREFIX=/buglog
```

ランダム値の生成:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"  # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"  # DB_PASSWORD
```

> **AES キーを設定しないと静かに壊れる。** 送信は成功しレコードも作られるが、
> 全フィールドが復号できず中身が空のまま保存される。手がかりは
> `AES復号に失敗しました: key=Title` という警告ログだけ。

### 7-3. 本番用 override の作成

```bash
cp docker-compose.prod.yml.example docker-compose.prod.yml
nano docker-compose.prod.yml
```

書き換えるのは 3 箇所:

- `POSTGRES_PASSWORD: CHANGE_ME` → `.env` の `<DB_PASSWORD>` と同じ値
- `--workers 2` → **`--workers 1`**（RAM 0.5GB のため）
- db の `command:`（軽量構成）の**コメントを外す**（RAM 0.5GB のため。内容は `docker-compose.light.yml` と同じ）

### 7-4. 軽量構成について ★必須

軽量構成（PostgreSQL の `shared_buffers=32MB` 等）は、7-3 のとおり `docker-compose.prod.yml` の
`db.command` で有効にする。

> **`cp docker-compose.light.yml docker-compose.override.yml` では効かない。**
> `-f` でファイルを明示して起動する（7-5 と 10-1 の systemd）と、`docker-compose.override.yml` は
> 自動では読み込まれない。override.yml が自動で読まれるのは `-f` 無しの `docker compose up`
> （ローカル開発）だけ。適用の確認は 7-5 で行う。

### 7-5. ビルドと起動

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

初回は 5〜10 分程度かかる。

```bash
docker compose ps
docker compose logs -f app
```

軽量構成が効いているか確認する（`32MB` なら OK。`128MB` なら `docker-compose.prod.yml` の `command:` が抜けている）:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db psql -U logserver -d logserver -c "SHOW shared_buffers"
```

---

## 8. 動作確認

```bash
curl http://localhost/buglog/login   # HTML が返ればOK
```

ブラウザで `http://<STATIC_IP>/buglog/login` を開き、`.env` の
`ADMIN_USERNAME` / `ADMIN_PASSWORD` でログインできることを確認する。

**ログイン後、管理画面からパスワードを変更すること。**

---

## 9. ドメインとHTTPS設定

クライアントは HTTPS で送信するため、**HTTPS は必須**。

### 9-1. DNSの設定

ドメインの管理画面（レジストラまたは DNS ホスティング）で A レコードを追加する:

```
タイプ: A
名前: <サブドメイン>
値: <STATIC_IP>
TTL: 300
```

反映を確認する:
```bash
nslookup -type=A <FQDN> 8.8.8.8
```

> 既存ドメインの DNS を触るときは、**MX / SPF / DKIM / DMARC を消さないこと。**
> メールが止まる。追加するのは A レコード 1 本だけ。

### 9-2. 証明書の取得（Let's Encrypt）

DNS が反映されてから実行する（反映前だと検証に失敗する）。

```bash
sudo dnf install -y certbot
```

> `certbot` が見つからない場合は公式の venv 方式を使う:
> ```bash
> sudo dnf install -y python3 python3-pip
> sudo python3 -m venv /opt/certbot
> sudo /opt/certbot/bin/pip install --upgrade pip certbot
> sudo ln -sf /opt/certbot/bin/certbot /usr/local/bin/certbot
> ```

80 番を certbot に明け渡してから取得する:

```bash
cd /home/ec2-user/log-server
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop nginx
sudo certbot certonly --standalone -d <FQDN> --agree-tos -m <メールアドレス> --no-eff-email
```

### 9-3. nginx の HTTPS 設定

```bash
cat > nginx/nginx.conf << 'EOF'
server {
    listen 80;
    server_name <FQDN>;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name <FQDN>;
    client_max_body_size 50M;

    ssl_certificate     /etc/letsencrypt/live/<FQDN>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<FQDN>/privkey.pem;
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

`<FQDN>` を実際のホスト名に置き換えること（4 箇所）。

### 9-4. 443 と証明書を compose に通す

`docker-compose.prod.yml` の nginx セクションのコメントを外す:

```yaml
  nginx:
    restart: always
    ports: !override
      - "80:80"
      - "443:443"
    volumes: !override
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf
      - app_storage:/app/storage:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
```

反映:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

`https://<FQDN>/buglog/login` を開いて鍵マークが出れば完了。

```bash
curl -I https://<FQDN>/buglog/login   # 200 が返る
```

---

## 10. 自動起動・自動バックアップ・証明書の自動更新

### 10-1. 再起動時の自動起動

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

スクリプトにしてから systemd タイマーで回す。**Amazon Linux 2023 には `crontab` が
入っていない**（`cronie` を別途入れる必要がある）ので、標準の systemd を使う方が早い。

AWS CLI は Amazon Linux 2023 に**プリインストール済み**（`aws --version` で確認）。
認証情報だけ設定する:

```bash
mkdir -p ~/.aws
umask 077
aws configure   # S3用のアクセスキー / シークレット / ap-northeast-1 を設定
chmod 600 ~/.aws/credentials
```

バックアップスクリプト:

```bash
mkdir -p /home/ec2-user/backups
cat > /home/ec2-user/backup-db.sh << 'EOF'
#!/bin/bash
set -euo pipefail

BUCKET="<バケット名>"
DIR="/home/ec2-user/backups"
FILE="${DIR}/backup_$(date +%Y%m%d).sql"

cd /home/ec2-user/log-server
# -T は stdin を読むため、明示的に /dev/null を渡す。
/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T db pg_dump -U logserver logserver > "${FILE}" < /dev/null

if [ ! -s "${FILE}" ]; then
  echo "$(date -Is) ERROR: dump is empty, not uploading" >&2
  exit 1
fi

/usr/bin/aws s3 cp "${FILE}" "s3://${BUCKET}/backups/"
find "${DIR}" -name "*.sql" -mtime +7 -delete
echo "$(date -Is) backup ok: $(basename "${FILE}") ($(stat -c%s "${FILE}") bytes)"
EOF
chmod +x /home/ec2-user/backup-db.sh
```

> **空のダンプを上げないガードを必ず入れる。** DB が落ちていても `pg_dump` の出力は
> 0 バイトのファイルとして作られ、そのまま S3 の正常なバックアップを上書きしてしまう。

> **`exec -T` への `< /dev/null` を省かない。** `-T` は stdin を読むため、スクリプトを
> `ssh host bash -s` のように標準入力経由で流しているとスクリプト本体を食い、
> 以降の行がエラーも出さずに実行されない。

タイマーに登録する（毎日 19:00 UTC = 翌 04:00 JST。サーバーの時刻は UTC）:

```bash
sudo tee /etc/systemd/system/logserver-backup.service > /dev/null <<'EOF'
[Unit]
Description=Log Server DB backup to S3
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
User=ec2-user
Environment=HOME=/home/ec2-user
ExecStart=/home/ec2-user/backup-db.sh
EOF

sudo tee /etc/systemd/system/logserver-backup.timer > /dev/null <<'EOF'
[Unit]
Description=Daily Log Server DB backup

[Timer]
OnCalendar=*-*-* 19:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now logserver-backup.timer
```

> `Environment=HOME=...` が無いと `~/.aws/credentials` を見つけられない。
> `Persistent=true` はサーバー停止で取り逃した分を次回起動時に実行する。

その場で 1 回動かして検証する:

```bash
sudo systemctl start logserver-backup.service
systemctl show logserver-backup.service -p Result   # Result=success
journalctl -u logserver-backup.service -n 20 --no-pager
aws s3 ls s3://<バケット名>/backups/
```

> バックアップは**壊れても誰も気づかない**種類の処理なので、月に一度は
> `aws s3 ls` で最新日付を見ること。`systemctl list-timers` に
> `logserver-backup.timer` が出ていなければタイマー自体が動いていない。

### 10-3. 証明書の自動更新

Let's Encrypt の証明書は 90 日で切れる。standalone で更新するため、更新時だけ 80 番を
nginx から取り上げる必要がある。フックを 2 つ置いて自動化する。

```bash
sudo mkdir -p /etc/letsencrypt/renewal-hooks/pre /etc/letsencrypt/renewal-hooks/post

sudo tee /etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh > /dev/null <<'HOOK'
#!/bin/bash
cd /home/ec2-user/log-server
/usr/local/lib/docker/cli-plugins/docker-compose -f docker-compose.yml -f docker-compose.prod.yml stop nginx
HOOK

sudo tee /etc/letsencrypt/renewal-hooks/post/start-nginx.sh > /dev/null <<'HOOK'
#!/bin/bash
cd /home/ec2-user/log-server
/usr/local/lib/docker/cli-plugins/docker-compose -f docker-compose.yml -f docker-compose.prod.yml start nginx
HOOK

sudo chmod +x /etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/post/start-nginx.sh
```

> `renewal-hooks/pre` と `post` は、**実際に更新が走るときだけ**実行される。
> 期限がまだ先なら nginx は止まらない。

### タイマーを有効にする ★見落としやすい

証明書取得時に certbot が「自動更新を設定した」と表示するが、**Amazon Linux では
`certbot-renew.timer` が無効のままになっている**。有効化しないと更新されない。

```bash
sudo systemctl enable --now certbot-renew.timer
systemctl list-timers certbot-renew.timer --no-pager
```

`NEXT` に次回実行時刻が出れば有効。動作確認（実際には更新しない）:

```bash
sudo certbot renew --dry-run
```

`Congratulations, all simulated renewals succeeded` と、フックが nginx を停止・起動した
ログが出れば正常。フックの出力は `Hook 'pre-hook' ran with error output:` として表示されるが、
これは compose が進捗を stderr に書くためで、エラーではない。

### 10-4. 自動スナップショット（任意）

コンソール → インスタンス →「スナップショット」タブ →「自動スナップショットを有効化」。
実使用量に対して $0.05/GB・月かかる（数GB なら月数十円）。

---

## 11. セキュリティチェックリスト

### 必須

- [ ] `.env` のパスワード・シークレットをすべてランダム文字列に変更した
- [ ] `ADMIN_PASSWORD` をデフォルトから変更し、ログイン後にも変更した
- [ ] PostgreSQL のパスワードをデフォルト（`logserver`）から変更した
- [ ] ファイアウォールの 22 番が自分の IP のみになっている
- [ ] ファイアウォールに 5432 / 8000 の許可ルールが無い
- [ ] `chmod 600 .env` を実行した
- [ ] HTTPS が有効で、`http://` が `https://` にリダイレクトされる

### 推奨

- [ ] DB 自動バックアップを設定し、**翌日に S3 で成否を確認した**
- [ ] 証明書の自動更新を設定し、`--dry-run` が通ることを確認した
- [ ] 自動スナップショットを有効にした
- [ ] SSH 鍵を安全な場所に保管した

---

## 12. 運用・メンテナンス

### アプリの更新

```bash
ssh -i <KEY>.pem ec2-user@<STATIC_IP>
cd /home/ec2-user/log-server
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs app --tail 20
```

ログに alembic の `Running upgrade ...`（マイグレーションがある場合）と
`Application startup complete` が出れば完了。

- `.env` の設定項目が増えた更新（例: Google ログインの `PUBLIC_BASE_URL` / `GOOGLE_*`）は、
  `.env.example` の差分を見て `.env` に追記してから `up -d --build` する
- app コンテナの再作成中（30 秒前後）はレポート受信が止まる。クライアントは再送しないので、
  その間に送られたレポートは失われる
- `docker-compose.prod.yml` / `.env` / `docker-compose.override.yml` は `.gitignore` 対象なので
  `git pull` の影響を受けない。`nginx/nginx.conf`（9-3 で HTTPS 化）はローカル変更として残るため、
  上流でこのファイルが変わったときは `git stash` → `git pull` → `git stash pop` で取り込む

#### 配置先が git 管理されていない場合

7-1 の `git clone` を使わずファイルをコピーして置いた場合（`/home/ec2-user/log-server` に `.git` が無く、
`git pull` が `not a git repository` になる）は、一度だけ以下で git 管理に切り替える。
稼働中のコンテナには影響しない。

```bash
cd /home/ec2-user/log-server
cp nginx/nginx.conf /home/ec2-user/nginx.conf.https      # HTTPS 設定を退避
git init -b main
git remote add origin https://github.com/oTAMAKOo/UniModules.SendReportServer.git
git fetch origin
git reset origin/main            # HEAD とインデックスを origin/main に合わせる（作業ツリーは触らない）
git checkout -- .                # 追跡ファイルを origin/main の内容に揃える（CRLF で置かれていても直る）
cp /home/ec2-user/nginx.conf.https nginx/nginx.conf      # HTTPS 設定を戻す
git branch --set-upstream-to=origin/main main
git status --short               # M が nginx/nginx.conf だけなら OK（?? の未追跡ファイルは無視してよい）
```

以後は上の「アプリの更新」の手順で更新できる。

### ログの確認

```bash
docker compose logs -f app    # アプリ
docker compose logs -f nginx  # リバースプロキシ
docker compose logs -f db     # DB
```

### DBバックアップ（手動）

```bash
cd /home/ec2-user/log-server
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  pg_dump -U logserver logserver > backup.sql
```

### DBリストア

```bash
aws s3 cp s3://<バケット名>/backups/backup_YYYYMMDD.sql ./backup.sql
cat backup.sql | docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T db psql -U logserver logserver
```

### リソースの確認

```bash
df -h          # ディスク（20GB）
free -h        # メモリ・swap
docker stats   # コンテナ別
```

### 不要データの削除

```bash
docker system prune -a
```

### 長期間使わないときの畳み方

**Lightsail は停止（Stop）しても課金は止まらない。** 公式 FAQ に
「インスタンスは実行中または**停止状態**のときに課金される」と明記されている。
EC2 のように「止めて節約」はできない。

課金を止める唯一の方法は**スナップショットを取ってインスタンスを削除する**こと。
バンドル料金は時間割りなので、削除した時点から止まる。

```bash
# 1. スナップショットを取る
aws lightsail create-instance-snapshot --region ap-northeast-1 \
  --instance-name log-server \
  --instance-snapshot-name log-server-$(date +%Y%m%d)

# 完了（state=available）を待つ
aws lightsail get-instance-snapshots --region ap-northeast-1 \
  --query 'instanceSnapshots[].{Name:name,State:state,Size:sizeInGb}' --output table

# 2. インスタンスと固定IPを削除する
aws lightsail delete-instance --region ap-northeast-1 --instance-name log-server
aws lightsail release-static-ip --region ap-northeast-1 --static-ip-name log-server-ip
```

> **固定 IP は必ず解放する。** アタッチ先が無い固定 IP は $0.005/時（約 $3.65/月）
> 課金される。インスタンスだけ消して IP を残すと、ほぼバンドル料金と同額を払い続ける。

畳んでいる間のコストはスナップショット保管料（$0.05/GB・月）だけ。実使用 5GB なら
月 $0.25 程度。

### 復帰する

```bash
aws lightsail create-instances-from-snapshot --region ap-northeast-1 \
  --instance-names log-server \
  --availability-zone ap-northeast-1a \
  --instance-snapshot-name <スナップショット名> \
  --bundle-id <最小バンドルの bundleId>
```

その後:

1. 固定 IP を新規に取得してアタッチする（**IP は以前と変わる**）
2. **DNS の A レコードを新しい IP に書き換える**
3. 証明書の期限が切れていれば `sudo certbot renew --force-renewal` で取り直す
4. アプリは systemd（10-1）で自動起動する。`docker compose ps` で確認

所要 15〜20 分程度。DNS の TTL を 300 にしておくと切り替えが速い。

> **畳んでいる間に送られたレポートは失われる。** クライアントは送信失敗時に
> リトライもローカル保存もしない。畳む前に、レポートを送る可能性がある人へ
> 周知すること。

---

## 13. トラブルシューティング

### ビルド中に OOM で落ちる

swap が無効になっていないか確認する。

```bash
free -h
sudo swapon --show
```

### `docker compose` が見つからない

プラグインのアーキテクチャ違い（`aarch64` を入れていないか）。
6-3 を `linux-x86_64` でやり直す。

### 管理画面に空のレポートが並ぶ

AES キーの不一致。`.env` の `REPORT_AES_KEY` / `REPORT_AES_IV` を
クライアント側と突き合わせる。

```bash
docker compose logs app | grep AES
```

### certbot が「Connection refused」で失敗する

- 80 番がファイアウォールで開いているか
- nginx コンテナを停止したか（standalone は 80 番を自分で使う）
- DNS が反映され、A レコードが固定 IP を指しているか

### SSH で締め出された

コンソールのインスタンス画面「ブラウザを使用して接続」から入り、
ファイアウォールの 22 番ルールを直す。

### 軽量構成が効いていない（`SHOW shared_buffers` が `128MB`）

`docker-compose.prod.yml` の `db.command` がコメントアウトのまま。7-3 を見直して
`up -d`（db が再作成され、数秒 DB が止まる）。`docker-compose.override.yml` に置いても
`-f` 明示の起動では読み込まれない（7-4）。

### `git pull` が「not a git repository」になる

配置先を git clone せずファイルコピーで置いている。12 章「配置先が git 管理されていない場合」の
手順で git 管理に切り替える。
