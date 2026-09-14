# Claude Code からレポートを読む（MCP / 読み取り API）

バグレポートの URL を Claude Code に渡して、ログとスタックトレースから不具合の原因を調査してもらうための設定手順。

## 目次

1. [仕組みの概要](#1-仕組みの概要)
2. [想定するフロー](#2-想定するフロー)
3. [API トークンを発行する](#3-api-トークンを発行する)
4. [Claude Code に MCP サーバーを登録する](#4-claude-code-に-mcp-サーバーを登録する)
5. [使い方](#5-使い方)
6. [MCP ツール一覧](#6-mcp-ツール一覧)
7. [読み取り API](#7-読み取り-api)
8. [運用](#8-運用)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [セキュリティ上の設計メモ](#10-セキュリティ上の設計メモ)

---

## 1. 仕組みの概要

管理画面（`/buglog/detail/<id>` 等）はログイン必須で、セッション Cookie が無いとログイン画面へ
リダイレクトされる。Claude Code の WebFetch は Cookie も Authorization ヘッダも送れないため、
URL をそのまま渡しても中身は読めない。

そのためサーバーには次の 2 つの入口があり、どちらも**管理画面で発行する個人 API トークン**で認証する。

| 入口 | パス | 用途 |
|---|---|---|
| MCP サーバー（Streamable HTTP） | `/buglog/mcp` | Claude Code から直接ツールとして呼ぶ（推奨） |
| 読み取り専用 API | `/buglog/api/reports/...` | curl やスクリプトから JSON / Markdown で取得 |

- トークンは**ユーザーごと**に発行し、そのユーザーの権限でレポートの**閲覧・検索だけ**ができる。削除や設定変更はできない
- トークンの平文は発行時に一度だけ表示され、DB にはハッシュしか保存されない。有効期限の設定ができ、削除すれば即座に使えなくなる
- MCP サーバーは `.env` の `MCP_ENABLED=false` で無効化できる（読み取り API は常に有効）
- `URL_PREFIX` を変えている場合は、以下の `/buglog` をその値に読み替える

## 2. 想定するフロー

1. 不具合を見つけた人がアプリからバグレポートを送る。送信完了時に画面へレポート URL が表示される
2. その URL をチャット等でエンジニアに共有する
3. 手の空いたエンジニアが Claude Code に URL を貼って「このレポートの原因を調べて」と頼む
4. Claude が `get_report` ツールでレポート全文（基本情報・エラーの要約・時系列ログ・スタックトレース）を取得し、
   プロジェクトのコードと突き合わせて原因を調査する

エンジニア側の準備は「トークンを 1 回発行して環境変数に入れる」だけ。MCP サーバーの登録は
プロジェクトの `.mcp.json` に入れておけば全員で共有できる。

## 3. API トークンを発行する

1. 管理画面にログインし、右上のアバター → **管理メニュー** → **API トークン** を開く
2. 名前（PC 名や用途）と有効期限を選んで **発行する**
3. 表示されたトークン（`blt_` で始まる文字列）の下に、**OS 別のセットアップコマンド**（Windows は PowerShell、
   macOS / Linux はターミナル用）が出る。自分の OS の「コピー」を押し、ターミナルに貼り付けて Enter を押す。
   トークンが環境変数 `BUGLOG_TOKEN` に保存される
4. Claude Code を再起動し、`/mcp` で `buglog` が connected になっていれば完了（プロジェクトの `.mcp.json` に
   登録済みであること。無い場合は 4 章）

**この画面を離れるとトークンは二度と表示されない**。コマンドを実行し損ねたら削除して再発行する。

手で設定する場合は、次のように環境変数を置く。

```bash
# macOS / Linux（~/.zshrc や ~/.bashrc に追記）
export BUGLOG_TOKEN=blt_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

```powershell
# Windows（ユーザー環境変数として保存。新しいターミナルから有効）
[Environment]::SetEnvironmentVariable("BUGLOG_TOKEN", "blt_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "User")
```

Claude Code はターミナルの環境変数を読むため、設定後は Claude Code（デスクトップアプリならアプリ自体）を再起動する。

トークンはパスワードと同じ扱いにする。チャットやリポジトリに貼らない。漏れたら管理画面で削除して作り直す。

## 4. Claude Code に MCP サーバーを登録する

プロジェクトごとに一度だけ行う設定。導入先プロジェクトの `.mcp.json` に書いてコミットしておけば、各メンバーは
3. の手順（トークン発行 + 環境変数）だけで使える。

### プロジェクトで共有する（推奨）

導入先プロジェクトのリポジトリ直下に `.mcp.json` を置いてコミットする。トークンは環境変数から
読むので、ファイル自体に秘匿情報は入らない。

```json
{
  "mcpServers": {
    "buglog": {
      "type": "http",
      "url": "https://<FQDN>/buglog/mcp",
      "headers": {
        "Authorization": "Bearer ${BUGLOG_TOKEN}"
      }
    }
  }
}
```

- `<FQDN>` は本番サーバーのホスト名。管理画面の「API トークン」ページに実際の URL 入りのスニペットが表示されるので、そこからコピーできる
- 既に別の MCP サーバーが `.mcp.json` にある場合は `mcpServers` の中に `buglog` を追記する
- チームメイトはそのプロジェクトで最初に Claude Code を起動したとき、この MCP サーバーの使用を一度承認する
- `${BUGLOG_TOKEN}` が未設定の人の環境では接続エラーになるだけで、他の機能には影響しない

### 自分の環境だけに登録する

`claude` コマンド（Claude Code CLI）が入っている環境のみ。デスクトップアプリだけを使っている場合は `claude` が
PATH に無いため、上の `.mcp.json` を使う。

```bash
claude mcp add --transport http --scope user buglog https://<FQDN>/buglog/mcp --header "Authorization: Bearer ${BUGLOG_TOKEN}"
```

`--scope user` で全プロジェクト共通になる。省略するとカレントプロジェクトだけの設定になる。

### 接続を確認する

Claude Code で `/mcp` を実行すると登録済みサーバーと接続状態が表示される。`buglog` が
connected になっていれば完了。失敗している場合は [9. トラブルシューティング](#9-トラブルシューティング)。

## 5. 使い方

Claude Code にレポートの URL を貼って依頼するだけでよい。

```
https://<FQDN>/buglog/detail/123 このレポートの原因をざっくり調べて
```

Claude は `get_report` にその URL を渡し、返ってきた Markdown（下記）を読んで調査を始める。
ID だけ（`#123`）でも通る。

より確実にしたい場合は、導入先プロジェクトの `CLAUDE.md` に一文足す。

```markdown
- `/buglog/detail/<id>` の URL が渡されたら、まず MCP ツール `get_report` にその URL を渡してレポート全文を取得し、それを元に調査する
```

### Claude が受け取る内容（Markdown）

```markdown
# Bug Report #123: NullReferenceException in Battle

## 基本情報
| 項目 | 値 |
|---|---|
| 投稿時間 | 2026-09-15 10:23:45 |
| ユーザー | tester01 (u_0001) |
| 端末 | iPhone15,2 |
| BuildNumber | 1234 |
| BranchName | develop |
| ログ件数 | 87（エラー・例外 2 件） |
| スクリーンショット | https://<bucket>.s3.ap-northeast-1.amazonaws.com/report/images/....png?X-Amz-Algorithm=...&X-Amz-Expires=1800&X-Amz-Signature=... |
| 詳細ページ | https://<FQDN>/buglog/detail/123 |

## エラー・例外の要約
- [80] Exception: NullReferenceException: Object reference not set to an instance of an object
- [86] Error: Failed to load asset ...

## ログ（時系列）
- [1] Log: ...
### [80] Exception
```
NullReferenceException: ...
  at BattleController.Update () ...
```
```

Warning / Log は 1 行に畳み、エラー・例外だけスタックトレース付きで展開される。

### MCP を設定していない人向けの逃げ道

詳細ページ右上（「削除」ボタンの隣）の **「Markdown をコピー」** ボタンを押すと、同じ Markdown がクリップボードに入る。
これを Claude に貼り付ければ、トークンや MCP の設定なしでも調査を頼める（レポートが長い場合は
MCP の方が確実）。

## 6. MCP ツール一覧

| ツール | 引数 | 内容 |
|---|---|---|
| `get_report` | `report`: URL / ID / `#ID` | レポート 1 件の全文を Markdown で返す |
| `search_reports` | `query`, `date_from`, `date_to`, `limit`（既定 20、最大 50） | 全テキストフィールドの部分一致検索。同じ例外が他でも出ているか、特定ビルドの報告を探すとき |
| `list_recent_reports` | `limit` | 最近の投稿を新しい順に要約 |

書き込み系のツールは提供しない。

## 7. 読み取り API

MCP を使わずスクリプトから取得する場合。認証は `Authorization: Bearer <トークン>`。
ブラウザで管理画面にログインしていれば、セッション Cookie でも呼べる（「Markdown をコピー」がこれを使う）。

| メソッド / パス | 内容 |
|---|---|
| `GET /buglog/api/reports/<id>` | JSON。`?format=md` または `Accept: text/markdown` で Markdown |
| `GET /buglog/api/reports/<id>.md` | Markdown |
| `GET /buglog/api/reports?q=&date_from=&date_to=&page=&per_page=` | 検索（要約の一覧、新しい順。`per_page` は最大 100） |
| `GET /buglog/api/resolve?ref=<URL か ID>` | URL からレポート ID を取り出す |

```bash
curl -H "Authorization: Bearer $BUGLOG_TOKEN" https://<FQDN>/buglog/api/reports/123.md
curl -H "Authorization: Bearer $BUGLOG_TOKEN" "https://<FQDN>/buglog/api/reports?q=NullReference&per_page=5"
```

認証失敗は `401`（`WWW-Authenticate: Bearer`）、存在しないレポートは `404`、レート制限超過は `429`。

## 8. 運用

- **レート制限**: MCP と読み取り API を合わせて IP ごとに 120 回/分。認証前に数えるので、トークン総当たりの抑止にもなる
- **トークンの上限**: ユーザーあたり有効なトークンは 10 個まで。使わないものは削除する
- **失効 = 削除**: 「API トークン」ページの一覧から本人が削除する。履歴は残さない（残すと発行・削除の繰り返しで増え続けるため）。期限切れの行も同じボタンで片付ける。ユーザーを無効化するとそのユーザーのトークンは使えなくなり、ユーザーを削除するとトークンの行も消える
- **有効期限**: 発行時に無期限 / 30 / 90 / 365 日から選ぶ。既定は 90 日
- **MCP を止める**: `.env` に `MCP_ENABLED=false` を書いてコンテナを再作成する。`/buglog/mcp` が 404 になる。読み取り API は残る
- **`.env` の `PUBLIC_BASE_URL`**: 設定しておくと、Markdown 内の詳細ページ URL と（local ストレージ時の）スクリーンショット URL が絶対 URL になる。未設定だとプレフィックスからの相対パスになり、Claude が画像を開けない
- **スクリーンショット URL の期限**: S3 ストレージ時の画像 URL は署名付きで、`get_report` を呼んだ時点から `S3_PRESIGN_EXPIRE_SECONDS`（既定 1800 秒 = 30 分）で失効する。Markdown を保存しても画像リンクは後で切れる。再取得は `get_report` を呼び直す（詳細ページ URL やレポート ID には期限が無い）。期限内は URL を知る誰でも画像を開けるので、チャットやノートには画像 URL ではなく詳細ページ URL かレポート ID を貼る
- **本番更新**: 依存パッケージ（`mcp`）が増えているため、更新時は必ず `up -d --build` でイメージを作り直す。マイグレーション 006（`api_token` テーブル）は起動時に自動適用される

## 9. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| `/mcp` で `buglog` が failed | `BUGLOG_TOKEN` が未設定、または Claude Code 起動後に設定した。ターミナル（デスクトップアプリならアプリ）を再起動する |
| `/mcp` に `buglog` が出てこない | MCP サーバーが登録されていない。プロジェクトの `.mcp.json` に `buglog` があるか、初回の承認ダイアログで拒否していないか確認する |
| PowerShell で `claude` が認識されない | Claude Code CLI が入っていない（デスクトップアプリのみ）。`.mcp.json` で登録する（4 章） |
| MCP 接続時に 401 | トークンが削除済み・期限切れ、またはユーザーが無効化されている。「API トークン」ページで状態を確認し、必要なら再発行する |
| MCP 接続時に 404 | サーバー側で `MCP_ENABLED=false`、または URL の `URL_PREFIX` が違う。管理画面の「API トークン」ページに表示される URL と比べる |
| 429 が返る | 1 分 120 回の上限。同じ IP から複数人が使っている場合は上限に当たりやすい。少し待つ |
| Claude が URL を貼っても WebFetch しようとする | `.mcp.json` が読まれていない（承認していない）か、ツールの存在に気づいていない。「`get_report` で読んで」と明示するか、`CLAUDE.md` に一文足す（5. を参照） |
| Markdown 内のスクリーンショット URL が相対パス | `.env` の `PUBLIC_BASE_URL` が未設定。設定してコンテナを再作成する |
| 画像 URL を開くと 403（`Request has expired`） | 署名付き URL の期限切れ（既定 30 分）。管理画面ならページを再読み込み、Claude なら `get_report` を呼び直して新しい URL を得る |
| 画像 URL を開くと 403（`RequestTimeTooSkewed`） | サーバーの時計がずれている。インスタンスの時刻同期（chrony 等）を確認する |
| 「Markdown をコピー」が `HTTP 401` | ブラウザのセッションが切れている。再ログインする |
| サーバー起動時に `RuntimeError: Task group is not initialized` | `main.py` の lifespan で `mcp_server.session_lifespan()` が呼ばれていない。`MCP_ENABLED` とマウントの条件が食い違っていないか確認する |
| nginx 経由で MCP が 421 を返す | SDK の DNS リバインディング保護が有効になっている。`mcp_server.py` で `enable_dns_rebinding_protection=False` を渡しているか確認する |

## 10. セキュリティ上の設計メモ

- **署名付き共有 URL（誰でも 1 件読める URL）は採用しなかった。** URL を知る人がログとユーザー ID を読めてしまい、
  チャットに貼られた URL がそのまま認証情報になる。加えて WebFetch は取得内容を要約するため調査精度も落ちる。
  代わりに「トークン + MCP」で、ログイン済みユーザーと同じ範囲のアクセスに限定している
- トークンは `blt_` + 43 文字のランダム文字列（`secrets.token_urlsafe(32)`）。十分に長いランダム値なので SHA-256 で
  保存し、照合は等価比較で足りる（bcrypt のようなストレッチングは不要）
- MCP の認証は SDK 標準の `TokenVerifier` ではなく、MCP アプリの前に置いた ASGI ラッパーで行う。SDK 標準は OAuth の
  認可サーバー前提で `issuer_url` が必須なため。ラッパーで 401 を返すので、未認証のクライアントは MCP のハンドシェイクにも到達しない
- MCP は stateless + JSON 応答。本番の uvicorn 複数ワーカーでもセッションの共有が要らず、SSE を使わないので nginx の
  バッファリング設定も不要
- SDK の DNS リバインディング保護は無効化している。nginx 越しでは Host が公開 FQDN になり、既定の localhost 限定では
  421 になるため。エンドポイントは認証必須なので保護が無くても問題ない
- レート制限はインメモリでワーカーごとに独立（既存のログイン制限と同じ性質）
- `last_used_at` は 1 分に 1 回だけ更新し、Claude の連続呼び出しで書き込みが増えないようにしている
