# AWS 側の準備手順（インフラ担当者向け）

バグレポートサーバー（UniModules.SendReportServer）を AWS 上で動かすために、**AWS アカウント側で
作成・設定してもらうもの**をまとめた手順書です。サーバー内部のセットアップ（Docker、HTTPS、
バックアップの登録など）はアプリ側の担当者が行うので、この手順書の範囲は AWS リソースの作成と、
その情報の引き渡しまでです。

所要時間は 30〜60 分、月額はおおよそ **$5〜6**（Lightsail $5 定額 + S3 数十 MB）です。

## 全体像

```
[Unity クライアント] --HTTPS--> [Lightsail インスタンス: nginx + FastAPI + PostgreSQL]
                                        |                     |
                                        | 署名付き URL / 保存   | 毎日のバックアップ
                                        v                     v
                                 [S3 バケット: report/*]   [S3 バケット: backups/*]
```

| 作るもの | 用途 | 数 |
|---|---|---|
| S3 バケット | スクリーンショットと DB バックアップの保存 | 1 |
| IAM ポリシー + IAM ユーザー（アクセスキー） | サーバーが S3 に読み書きするための認証情報 | 1 組 |
| Lightsail インスタンス + 固定 IP + ファイアウォール | サーバー本体 | 1 |
| DNS の A レコード | HTTPS 用のホスト名 | 1 |
| （任意）運用者用の読み取り専用 IAM ユーザー | 状態確認・コスト確認 | 1 |
| （任意）SES | 管理画面の招待メール送信 | - |

すべて **東京リージョン（ap-northeast-1）** で作成してください。

## 決めておくこと

作業前に、アプリ側の担当者と次の値を決めます。

| 項目 | 例 | 備考 |
|---|---|---|
| ホスト名（FQDN） | `buglog.example.com` | クライアントの送信先として配布物に埋め込まれるため、後から変えにくい |
| S3 バケット名 | `example-buglog` | 全世界で一意。会社名や用途を含めた小文字英数字とハイフン |
| IAM ユーザー名 | `buglog-server-s3` | サーバーが使う。プロジェクト名は入れない（複数プロジェクトで共用するため） |
| Lightsail インスタンス名 | `log-server` | 任意 |
| 管理者の接続元 IP | `203.0.113.10/32` | SSH（22 番）を許可する IP。固定 IP でなければ後で更新が必要 |

## 1. S3 バケット

画像は**非公開のまま**、アプリが発行する期限付きの署名付き URL で配信します。公開設定は一切不要です。

### コンソール

1. S3 → バケットを作成
2. バケット名: 決めた名前、リージョン: 東京
3. オブジェクト所有者: **ACL 無効（推奨）**
4. パブリックアクセスをすべてブロック: **オン（4 項目すべて）**
5. バケットのバージョニング: 無効でよい
6. デフォルトの暗号化: **SSE-S3（Amazon S3 マネージドキー）**
7. 作成

バケットポリシー・CORS・静的ウェブサイトホスティングは設定しないでください。

### CLI

```bash
BUCKET=<バケット名>
aws s3api create-bucket --bucket "$BUCKET" --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=Service,Value=buglog}]'
```

### 任意: ライフサイクル

バックアップは毎日 1 ファイル（数十 KB〜数 MB）増えます。放置しても費用は小さいですが、
気になる場合は `backups/` に 90 日程度で削除するライフサイクルルールを付けてください。
`report/` にはルールを付けないでください（画像が消えると管理画面から見られなくなります）。

## 2. サーバー用 IAM ユーザー

サーバーは**このバケットだけ**に対して、画像の読み書き削除とバックアップの書き込みができれば十分です。
他のサービスやバケットへの権限は付けないでください。

### 2-1. ポリシーを作成

IAM → ポリシー → ポリシーを作成 → JSON。名前は `BugLogServerS3Policy`。
`<バケット名>` を実際の名前に置き換えます。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListTargetBucketOnly",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::<バケット名>"
    },
    {
      "Sid": "ReportImagesReadWriteDelete",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<バケット名>/report/*"
    },
    {
      "Sid": "BackupsWriteAndRead",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::<バケット名>/backups/*"
    }
  ]
}
```

### 2-2. ユーザーを作成してキーを発行

1. IAM → ユーザー → ユーザーの作成。名前 `buglog-server-s3`
2. **コンソールアクセスは付けない**（プログラムからのアクセスのみ）
3. 許可: 「ポリシーを直接アタッチ」→ `BugLogServerS3Policy`
4. 作成後、「セキュリティ認証情報」→ アクセスキーを作成 → ユースケース「AWS の外部で実行されるアプリケーション」
5. アクセスキー ID とシークレットアクセスキーを控える（**シークレットはこの画面でしか見られません**）

CLI の場合:

```bash
aws iam create-policy --policy-name BugLogServerS3Policy --policy-document file://BugLogServerS3Policy.json
aws iam create-user --user-name buglog-server-s3
aws iam attach-user-policy --user-name buglog-server-s3 \
  --policy-arn arn:aws:iam::<アカウントID>:policy/BugLogServerS3Policy
aws iam create-access-key --user-name buglog-server-s3
```

## 3. Lightsail インスタンス

### 3-1. インスタンス

Lightsail コンソール（https://lightsail.aws.amazon.com/）で、リージョンが東京になっていることを確認してから作成します。

| 設定 | 値 |
|---|---|
| リージョン / AZ | 東京（ap-northeast-1a） |
| プラットフォーム | Linux/Unix |
| 設計図 | **OS のみ → Amazon Linux 2023** |
| ネットワークタイプ | **デュアルスタック**（IPv6 のみは選ばない） |
| プラン | **最小の 512 MB プラン（$5/月）** |
| インスタンス名 | `log-server` |
| SSH キー | 「新しいキーを作成」で専用のキーペアを作る（アカウント既定のキーを使い回さない） |

CLI:

```bash
aws lightsail create-key-pair --region ap-northeast-1 --key-pair-name buglog-key \
  --query 'privateKeyBase64' --output text > buglog-key.pem
chmod 600 buglog-key.pem   # Windows は手順書 aws_deploy_lightsail.md 3-2 の icacls を参照

aws lightsail create-instances --region ap-northeast-1 \
  --instance-names log-server --availability-zone ap-northeast-1a \
  --blueprint-id amazon_linux_2023 --bundle-id nano_3_0 \
  --key-pair-name buglog-key --ip-address-type dualstack
```

> `blueprint-id` / `bundle-id` は変わることがあります。エラーになったら
> `aws lightsail get-blueprints` / `aws lightsail get-bundles` で現在の ID を確認してください。

### 3-2. 固定 IP

再起動で IP が変わらないよう、静的 IP を作ってアタッチします。

- コンソール: ネットワーキング → 静的 IP の作成 → `log-server` にアタッチ。名前 `log-server-ip`
- CLI:

```bash
aws lightsail allocate-static-ip --region ap-northeast-1 --static-ip-name log-server-ip
aws lightsail attach-static-ip --region ap-northeast-1 --static-ip-name log-server-ip --instance-name log-server
aws lightsail get-static-ip --region ap-northeast-1 --static-ip-name log-server-ip --query 'staticIp.ipAddress' --output text
```

> 静的 IP はインスタンスにアタッチしている間は無料です。インスタンスを削除するときは静的 IP も解放してください（放置すると課金されます）。

### 3-3. ファイアウォール

インスタンス → ネットワーキング → IPv4 ファイアウォールを次の 3 行だけにします。

| アプリケーション | プロトコル | ポート | 送信元 |
|---|---|---|---|
| SSH | TCP | 22 | **管理者の IP のみ**（「送信元 IP アドレスに制限する」） |
| HTTP | TCP | 80 | すべて（Let's Encrypt の検証と HTTPS へのリダイレクトに使う） |
| HTTPS | TCP | 443 | すべて |

5432（PostgreSQL）と 8000（アプリ）は開けないでください。IPv6 ファイアウォールも同じ内容にします。

CLI（**ルールを置き換える**コマンドなので 22 を必ず含める）:

```bash
aws lightsail put-instance-public-ports --region ap-northeast-1 --instance-name log-server --port-infos \
  'fromPort=22,toPort=22,protocol=TCP,cidrs=<管理者IP>/32' \
  'fromPort=80,toPort=80,protocol=TCP,cidrs=0.0.0.0/0' \
  'fromPort=443,toPort=443,protocol=TCP,cidrs=0.0.0.0/0'
```

### 3-4. 自動スナップショット（推奨）

インスタンス → スナップショット → 自動スナップショットを有効化。日次で 7 世代保持、費用は 20 GB × $0.05/GB で月 $1 前後です。
DB は別途 S3 にバックアップされるので、無くても復旧はできます。

## 4. DNS

ドメインの DNS に A レコードを 1 本追加します。

```
タイプ: A
名前: <サブドメイン>（例: buglog）
値: <静的 IP>
TTL: 300
```

Route 53 の場合:

```bash
aws route53 change-resource-record-sets --hosted-zone-id <ゾーンID> --change-batch '{
  "Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
    "Name": "<FQDN>", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "<静的IP>"}]}}]}'
```

> 既存ドメインの場合、MX / SPF / DKIM / DMARC には触らないでください。追加するのは A レコード 1 本だけです。

反映確認: `nslookup -type=A <FQDN> 8.8.8.8`

HTTPS 証明書（Let's Encrypt）はサーバー側で取得するので、ACM 等での発行は不要です。

## 5. 任意: 運用者用の読み取り専用 IAM ユーザー

アプリ側の担当者がコストや S3 の状態を CLI で確認できるように、読み取り専用のユーザーがあると運用が楽になります。
無くても構いません（コンソールを共有する運用でも可）。

ポリシー例（`BugLogOpsPolicy`）:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListAllMyBuckets", "s3:GetBucketLocation", "s3:ListBucket",
      "s3:GetBucketPolicy", "s3:GetBucketPublicAccessBlock", "s3:GetLifecycleConfiguration"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::<バケット名>/backups/*"},
    {"Effect": "Allow", "Action": ["lightsail:Get*"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ce:GetCostAndUsage", "ce:GetCostForecast", "ce:GetDimensionValues"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"], "Resource": "*"}
  ]
}
```

## 6. 任意: 招待メール（SES）

管理画面で Google ログインのユーザーを招待するときにメールを自動送信したい場合だけ必要です。
未設定でも招待リンクを画面に表示して手渡しできるので、最初は無しで問題ありません。

必要な場合: SES で送信元ドメイン（または送信元アドレス）を検証し、サンドボックス解除を申請します。
サーバー用 IAM ユーザーに `ses:SendEmail` / `ses:SendRawEmail` を追加してください。

## 7. 引き渡すもの

次の情報をアプリ側の担当者に渡してください。**アクセスキーのシークレットと SSH 秘密鍵はメールやチャットに平文で貼らず**、
パスワードマネージャーの共有機能や、1 回だけ見られる共有リンクなど、安全な経路で渡してください。

| 項目 | 値 | 秘匿 |
|---|---|---|
| AWS アカウント ID | | |
| リージョン | ap-northeast-1 | |
| S3 バケット名 | | |
| サーバー用 IAM ユーザー名 | `buglog-server-s3` | |
| アクセスキー ID | | |
| シークレットアクセスキー | | **秘匿** |
| Lightsail インスタンス名 | `log-server` | |
| 静的 IP | | |
| SSH 秘密鍵（.pem） | | **秘匿** |
| SSH ユーザー名 | `ec2-user`（Amazon Linux 2023 の既定） | |
| ホスト名（FQDN） | | |
| 22 番を許可した管理者 IP | | |
| （任意）運用者用 IAM ユーザーのアクセスキー | | **秘匿** |
| （任意）SES の送信元アドレス | | |

## 8. 引き渡し後にアプリ側が行うこと（参考）

以下はこの手順書の範囲外で、アプリ側の担当者が `docs/aws_deploy_lightsail.md` の 5 章以降に沿って行います。

- SSH で接続し、swap・Docker・Docker Compose をインストール
- リポジトリを配置し、`.env`（バケット名・アクセスキー・ホスト名・AES 鍵など）を作成して起動
- Let's Encrypt で証明書を取得して HTTPS 化、自動更新を登録
- DB の自動バックアップ（S3 の `backups/`）と自動起動を登録
- 管理画面でプロジェクトとユーザーを作成

## 9. 費用と注意

- Lightsail の最小プランは **停止しても課金が止まりません**。長期間使わないときはスナップショットを取ってインスタンスを削除します
- 転送量はプランに 1 TB/月 含まれています。バグレポート用途で超えることはまずありません
- 静的 IP はアタッチ中は無料、デタッチして放置すると課金されます
- S3 は画像とバックアップで月 $1 未満が目安です
- ルートユーザーのアクセスキーは作らず、IAM ユーザーで作業してください

## チェックリスト

- [ ] S3 バケット: 東京、パブリックアクセスブロック 4 項目オン、バケットポリシー無し、SSE-S3
- [ ] IAM ポリシー `BugLogServerS3Policy`: 当該バケットの `report/*`（読み書き削除）と `backups/*`（読み書き）だけ
- [ ] IAM ユーザー `buglog-server-s3`: コンソールアクセス無し、上のポリシーのみ、アクセスキー発行済み
- [ ] Lightsail: Amazon Linux 2023、512 MB プラン、デュアルスタック、専用 SSH キー
- [ ] 静的 IP をアタッチ済み
- [ ] ファイアウォール: 22（管理者 IP のみ）/ 80 / 443 だけ。5432 と 8000 は無し
- [ ] DNS の A レコードが静的 IP を指し、`nslookup` で解決できる
- [ ] 引き渡し表の項目を安全な経路で渡した
