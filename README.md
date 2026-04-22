# FlowMelt MVP

FlowMelt is a small experiment for UDP-blocked networks.

It is not a full VPN.  It is a TCP-terminating SOCKS5 tunnel:

```text
Application TCP -> local SOCKS5 -> TLS/TCP tunnel -> VPS -> target TCP
```

This avoids carrying inner TCP packets inside an outer TCP tunnel.  It is closer
to a proxy than a VPN, but it is useful for comparing web/SSH/Git behavior
against OpenVPN-over-TCP.

## Files

- `flowmelt_server.py`: runs on the VPS.
- `flowmelt_client.py`: runs locally and exposes `127.0.0.1:1080` SOCKS5.
- `start-flowmelt-client.ps1`: starts the local Windows SOCKS5 client.
- `stop-flowmelt-client.ps1`: stops the local Windows SOCKS5 client.

## Test Instance Template

- Server: `<your-vps-ip-or-domain>:8443`
- Local SOCKS5: `127.0.0.1:1080`
- Credentials:
  - `flowmelt-token.txt`
  - server certificate SHA-256 pin

Start locally:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-flowmelt-client.ps1 -ServerHost <your-vps-ip-or-domain> -PinSha256 <certificate-sha256>
```

Stop locally:

```powershell
powershell -ExecutionPolicy Bypass -File .\stop-flowmelt-client.ps1
```

Test with curl:

```powershell
curl.exe --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
```

## Limits

- TCP CONNECT only.
- No UDP, ICMP, LAN discovery, or full-device routing.
- One outer TLS/TCP connection per proxied TCP connection.
- Experimental code, suitable for personal testing only.
- Server-side operation is limited to destination ports `22`, `80`, and `443`.
- Private, loopback, link-local, multicast, reserved, and unspecified target
  addresses are refused by the server.
