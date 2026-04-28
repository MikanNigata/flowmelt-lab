# FlowMelt MVP

FlowMelt は、UDP が使えない、または強く制限されたネットワーク環境で、TCP 中心の通信をどの程度快適に扱えるかを検証するための小規模な実験プロジェクトです。

主な対象は、Web ブラウジング、SSH、Git over HTTPS、パッケージレジストリ、API 呼び出しなど、TCP に依存する開発・運用ワークロードです。

## これは何か

FlowMelt は **full VPN ではありません**。

ローカルで SOCKS5 プロキシを立て、アプリケーションからの TCP 接続を受け取り、その接続を TLS/TCP で VPS 上の FlowMelt サーバへ渡します。VPS 側では、対象ホストに対して新しい TCP 接続を張り直します。

```text
Application TCP
  -> local SOCKS5 127.0.0.1:1080
  -> TLS/TCP tunnel
  -> VPS FlowMelt server
  -> target TCP
```

この方式では、IP パケットとしての内側 TCP を外側 TCP トンネルへ丸ごと詰めるのではなく、ローカル SOCKS5 で TCP 接続を終端し、VPS 側で TCP 接続を再生成します。そのため、設計思想としては VPN よりも **TCP 専用プロキシ** に近いものです。

一方で、UDP が使えない環境において、OpenVPN-over-TCP のような full-device TCP トンネルと比較し、ブラウザ、SSH、Git、npm などの実用的な TCP ワークロードがどのように振る舞うかを検証する用途には有効です。

## 目的

FlowMelt の目的は、以下のような問いを検証することです。

- UDP が使えないネットワークで、TCP 中心の作業をどこまで快適に維持できるか。
- full VPN ではなく SOCKS5 プロキシとして割り切ることで、構成をどこまで単純化できるか。
- OpenVPN-over-TCP と比較して、Web、SSH、Git、npm、API 呼び出しの体感やレイテンシがどう変わるか。
- TCP-first な軽量運用モードを、full VPN の代替ではなく補完手段として成立させられるか。

## 非目標

現時点の FlowMelt は、以下を目指していません。

- OS 全体を透過的に経由させる full-device VPN
- UDP、QUIC、ICMP、LAN discovery の転送
- LAN 内部リソースへの透過アクセス
- 匿名化サービス
- アクセス制御、利用規約、ネットワークポリシーを回避するためのツール
- 不特定多数が使う公開プロキシ

利用する場合は、自分が管理している端末、VPS、ネットワーク、または明示的に利用許可を得ている環境に限定してください。

## 現在の構成

FlowMelt は、現在の MVP では次の 2 つの Python プログラムで構成されています。

- `flowmelt_client.py`
  - ローカル PC 上で動作します。
  - `127.0.0.1:1080` に SOCKS5 プロキシを公開します。
  - SOCKS5 CONNECT 要求を受け取り、FlowMelt サーバへ TLS/TCP 接続を張ります。
  - サーバ証明書の SHA-256 fingerprint pinning に対応します。

- `flowmelt_server.py`
  - VPS 上で動作します。
  - デフォルトでは `8443/tcp` で TLS 接続を受け付けます。
  - クライアントから受け取った接続先 host/port に対して、サーバ側から TCP 接続を張ります。
  - 共有トークンによる簡易認証を行います。
  - デフォルトでは宛先ポートを `22,80,443` に制限します。
  - private、loopback、link-local、multicast、reserved、unspecified アドレスへの接続を拒否します。

通信の概略は次のとおりです。

```text
1. アプリケーションが 127.0.0.1:1080 の SOCKS5 に接続する
2. flowmelt_client.py が SOCKS5 CONNECT 要求を解析する
3. クライアントが VPS の flowmelt_server.py へ TLS/TCP 接続する
4. クライアントが共有トークン、接続先 host、接続先 port を送る
5. サーバが認証とポリシーチェックを行う
6. サーバが target TCP へ接続する
7. クライアント側 TCP とサーバ側 TCP の間で双方向 relay を行う
```

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `README.md` | このファイル。概要、使い方、制限、今後の展望を記述します。 |
| `DESIGN.md` | 運用設計、評価結果、今後の設計方針を記述します。 |
| `flowmelt_server.py` | VPS 上で動作する FlowMelt サーバです。 |
| `flowmelt_client.py` | ローカルで動作する SOCKS5 クライアントです。 |
| `start-flowmelt-client.ps1` | Windows でローカル SOCKS5 クライアントを起動する補助スクリプトです。 |
| `stop-flowmelt-client.ps1` | Windows でローカル SOCKS5 クライアントを停止する補助スクリプトです。 |

## 実行前提

MVP の想定環境は次のとおりです。

- VPS 側
  - Python 3
  - 外部から到達可能な TCP ポート。デフォルト例は `8443/tcp`
  - TLS 証明書と秘密鍵
  - 共有トークンファイル

- ローカル側
  - Python 3
  - SOCKS5 を利用できるアプリケーション、または SOCKS5 を指定できるツール
  - Windows の場合は PowerShell 補助スクリプトを利用可能

現時点の Python コードは、標準ライブラリのみで動作する最小構成を意識しています。

## テスト用セットアップ例

以下は検証用の最小例です。本番運用を前提とした手順ではありません。

### 1. 共有トークンを作成する

Linux / macOS / VPS 側の例:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > flowmelt-token.txt
chmod 600 flowmelt-token.txt
```

Windows 側の例:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))" | Set-Content -NoNewline .\flowmelt-token.txt
```

サーバとクライアントの `flowmelt-token.txt` は同じ内容にしてください。トークンは 24 bytes 以上である必要があります。

### 2. 検証用 TLS 証明書を作成する

自己署名証明書を使った検証例です。

```bash
openssl req -x509 -newkey rsa:3072 -nodes \
  -keyout key.pem \
  -out cert.pem \
  -days 30 \
  -subj "/CN=flowmelt.local"
```

証明書の SHA-256 fingerprint を確認します。

```bash
openssl x509 -in cert.pem -outform DER | openssl dgst -sha256
```

出力された SHA-256 値を、クライアント起動時の `--pin-sha256` または `-PinSha256` に指定します。

### 3. VPS 側でサーバを起動する

```bash
python3 flowmelt_server.py \
  --listen-host 0.0.0.0 \
  --listen-port 8443 \
  --token-file ./flowmelt-token.txt \
  --cert-file ./cert.pem \
  --key-file ./key.pem \
  --allowed-ports 22,80,443
```

デフォルトでは、宛先ポートは `22`, `80`, `443` に制限されます。private address、loopback address、link-local address などへの接続も拒否されます。

### 4. ローカル側でクライアントを起動する

Python で直接起動する例:

```powershell
python .\flowmelt_client.py `
  --listen-host 127.0.0.1 `
  --listen-port 1080 `
  --server-host <your-vps-ip-or-domain> `
  --server-port 8443 `
  --token-file .\flowmelt-token.txt `
  --pin-sha256 <certificate-sha256>
```

PowerShell 補助スクリプトを使う例:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-flowmelt-client.ps1 -ServerHost <your-vps-ip-or-domain> -PinSha256 <certificate-sha256>
```

停止する場合:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop-flowmelt-client.ps1
```

## 動作確認

`curl` で SOCKS5 経由の外部 IP を確認できます。

```powershell
curl.exe --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
```

ブラウザで試す場合は、SOCKS5 proxy として次を指定します。

```text
Host: 127.0.0.1
Port: 1080
SOCKS version: SOCKS5
```

DNS 解決も proxy 側に寄せたい場合は、アプリケーション側で remote DNS / SOCKS hostname 解決を有効にしてください。`curl` の場合は `--socks5-hostname` を使います。

## セキュリティ上の考え方

FlowMelt MVP は公開プロキシとして使う設計ではありません。

現在の安全側の前提は次のとおりです。

- サーバは TLS を要求します。
- クライアントはサーバ証明書の SHA-256 fingerprint pinning に対応します。
- 共有トークンが一致しない限り、サーバは outbound 接続を開始しません。
- サーバの宛先ポートはデフォルトで `22,80,443` に制限されます。
- サーバは、private、loopback、link-local、multicast、reserved、unspecified アドレスへの接続を拒否します。
- `--allow-private` を有効にしない限り、内部ネットワーク向けの踏み台として使いにくい設定になっています。

注意点もあります。

- `--pin-sha256` を指定しない場合、TLS の暗号化は行われても、サーバ認証の強度は下がります。
- 共有トークンが漏れると、不正利用のリスクがあります。
- サーバを internet-facing に置く場合は、firewall、systemd hardening、専用ユーザ、ログ設定、rate limit などを別途検討してください。
- `INFO` ログでは接続先 host/port が記録される可能性があります。運用時は必要に応じて log level を調整してください。

## 現在の制限

- TCP CONNECT のみ対応しています。
- UDP、QUIC、ICMP、LAN discovery、broadcast、multicast は扱いません。
- full-device routing は行いません。
- アプリケーション側が SOCKS5 を使える必要があります。
- proxied TCP connection ごとに、外側の TLS/TCP 接続を 1 本作ります。
- 現時点では multiplexing、connection pooling、再接続制御、帯域制御はありません。
- 認証は共有トークン方式のみです。
- サーバ側の宛先ポートはデフォルトで `22`, `80`, `443` に限定されています。
- private、loopback、link-local、multicast、reserved、unspecified な宛先アドレスはサーバ側で拒否されます。
- 実験コードであり、個人検証向けです。

## 評価観点

FlowMelt の価値は、単純な速度だけでは判断しにくいです。次のような観点で direct access、OpenVPN-over-TCP、FlowMelt を比較すると、設計の良し悪しが見えやすくなります。

- 小さい HTTP request の latency
- Git 操作の応答性
- npm / pip / package registry へのアクセス
- SSH banner / interactive SSH の体感
- 複数接続時の p50 / p90 latency
- 大きめの download における throughput
- 接続失敗時の復帰性
- DNS 解決の扱い
- アプリケーションごとの SOCKS5 対応状況

`DESIGN.md` には、現時点の設計意図、測定結果、運用モードの比較を記録しています。

## 今後の展望

FlowMelt は現時点では MVP ですが、今後の発展方向は大きく 5 段階に分けられます。

### Phase 1: MVP の安定化

短期的には、現在の SOCKS5 over TLS/TCP 構成を安定させます。

- README、DESIGN、セットアップ手順を整備する。
- 起動スクリプトとファイル配置を整理する。
- client / server の基本的な smoke test を追加する。
- 接続失敗時のエラーメッセージを改善する。
- 証明書 pinning、token file、allowed ports の設定ミスを検出しやすくする。
- 最小限の CI を追加し、構文エラーや基本 lint を検出する。

### Phase 2: 運用性の改善

次に、個人検証から継続運用へ進めるための土台を作ります。

- クライアント側に localhost-only の health endpoint を追加する。
- 現在の接続数、成功数、失敗数、接続先 port、転送 byte 数などの簡易 metrics を追加する。
- Windows の start / stop / status スクリプトを整備する。
- VPS 向け systemd unit の例を追加する。
- systemd hardening の推奨設定をドキュメント化する。
- ログレベルとログ内容を整理し、通常運用時に不要な接続先情報を残しすぎないようにする。

### Phase 3: 使いやすさの改善

SOCKS5 を直接指定できるアプリケーションではすぐに使えますが、実用性を上げるにはユーザー体験の改善が必要です。

- ブラウザ検証用の PAC file を追加する。
- Git、npm、curl、PowerShell、Windows proxy 設定のサンプルを追加する。
- ローカル DNS の扱いを整理する。
- SOCKS5 hostname 解決を使うべきケースを明確化する。
- 設定ファイル方式を導入し、長い CLI option を減らす。
- token、server host、pin、listen port をまとめて管理できるようにする。

### Phase 4: TUN / tun2socks 連携の検証

FlowMelt は full VPN ではありませんが、TUN-to-SOCKS 層と組み合わせれば、TCP アプリケーションの一部をより透過的に流せる可能性があります。

候補は次のような構成です。

```text
Application TCP
  -> local TUN / tun2socks layer
  -> local SOCKS5 127.0.0.1:1080
  -> FlowMelt TLS/TCP tunnel
  -> VPS
  -> target TCP
```

検証すべき点は次のとおりです。

- Windows で安定して起動停止できるか。
- DNS をどこで解決するか。
- UDP をどう扱うか、または明示的に対象外として落とすか。
- OS 全体の通信を流す場合に、意図しない通信まで proxy されないか。
- OpenVPN-over-TCP と比べて、実運用上のメリットがあるか。

TUN モードが安定するまでは、FlowMelt は full VPN の置き換えではなく、TCP-first な補助経路として扱うべきです。

### Phase 5: 長期的な発展

長期的には、次のような方向性が考えられます。

- native client 化
  - Windows / macOS / Linux で扱いやすい単体バイナリにする。
  - Python 実験実装から、より配布しやすい実装へ移行する。

- 接続管理の高度化
  - connection pooling や multiplexing の有効性を検証する。
  - ただし、単純な多重化は head-of-line blocking を悪化させる可能性があるため、測定を前提に判断する。

- 認証と運用セキュリティの強化
  - token rotation
  - mTLS
  - 設定ファイルの権限チェック
  - rate limiting
  - allowlist / denylist policy

- 観測性の改善
  - ローカル metrics
  - 接続失敗理由の分類
  - benchmark harness
  - direct / OpenVPN TCP / FlowMelt の比較レポート生成

- 開発ワークロードへの最適化
  - GitHub、npm、PyPI、SSH、API 呼び出しに対する実測を増やす。
  - 小さい request が多いケースと、大きい download のケースを分けて評価する。
  - ブラウザ利用時の DNS、HTTP/2、keep-alive の影響を確認する。

## 推奨される現時点の位置づけ

現時点では、FlowMelt は OpenVPN の代替ではなく、並行して試す TCP-first な実験経路です。

```text
OpenVPN over TCP:
  full-device fallback
  対応範囲は広い
  TCP-over-TCP の影響を受けやすい

FlowMelt:
  SOCKS5 proxy mode
  TCP ワークロードに限定
  構成が軽い
  ブラウザ / Git / npm / SSH の比較検証に向く
```

OpenVPN など既存の full VPN 経路を削除する段階ではありません。FlowMelt は、UDP が使えない環境で TCP 中心の作業を改善できるかを検証するための、実験的な第 2 経路として扱うのが現実的です。

## 注意事項

このリポジトリは実験用です。

- 管理権限または利用許可のある環境でのみ使用してください。
- 不特定多数に公開する open proxy として運用しないでください。
- 共有トークン、秘密鍵、証明書 fingerprint の管理に注意してください。
- 実運用前には、ログ、firewall、systemd hardening、権限分離、rate limit、監査方法を必ず見直してください。
