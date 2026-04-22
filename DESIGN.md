# FlowMelt Operational Design

## Goal

FlowMelt is an experiment for networks where UDP is unavailable or heavily
restricted.

The design goal is not to replace every feature of a VPN.  The first practical
goal is to make TCP-heavy workflows feel better than OpenVPN-over-TCP:

- web browsing
- Git over HTTPS
- SSH
- package registries
- API calls

## Current Shape

```text
Application TCP
  -> local SOCKS5 127.0.0.1:1080
  -> TLS over TCP 8443
  -> VPS FlowMelt server
  -> target TCP
```

This avoids encapsulating inner TCP packets in an outer TCP tunnel.  Instead,
the TCP flow is terminated locally and recreated from the VPS.

## Security Posture

The server is exposed on `8443/tcp`, but it is not an open proxy:

- TLS is required.
- The client pins the server certificate SHA-256 fingerprint.
- A random shared token is required before any outbound connection is made.
- The service runs as the unprivileged `flowmelt` user.
- systemd applies `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
  `PrivateTmp`, and `MemoryMax=128M`.
- Allowed destination ports are limited to `22,80,443`.
- Private, loopback, link-local, multicast, reserved, and unspecified target
  addresses are refused.
- Server log level is `WARNING` in operation to avoid recording routine
  destination metadata.

## Test Results From This PC

Direct connection is the baseline from the local ISP.  FlowMelt means SOCKS5 via
`127.0.0.1:1080` to the VPS.

```text
google generate_204:
  direct   total ~0.055s
  flowmelt total ~0.145s

Cloudflare trace:
  direct   total ~0.067s
  flowmelt total ~0.073s

GitHub homepage:
  direct   total ~0.188s
  flowmelt total ~0.167s

npm @openai/codex metadata:
  direct   total ~0.390s
  flowmelt total ~0.279s

Cloudflare download:
  100KB  direct ~0.155s, flowmelt ~0.133s
  1MB    direct ~0.140s, flowmelt ~0.237s
  10MB   direct ~0.348s, flowmelt ~0.422s
  50MB   direct ~1.404s, flowmelt ~1.321s

24 parallel google generate_204 requests:
  direct   wall ~0.465s, p50 ~0.184s, p90 ~0.229s
  flowmelt wall ~0.513s, p50 ~0.228s, p90 ~0.273s
```

Other TCP tests:

- `git ls-remote https://github.com/openai/openai-python.git HEAD`: OK.
- `npm view @openai/codex version`: OK.
- `ssh.github.com:443` SSH banner through SOCKS: OK.
- `github.com:22` SSH banner through SOCKS: OK.

Policy tests:

- `api.ipify.org:443`: OK.
- `example.com:25`: blocked.
- `169.254.169.254:80`: blocked.

## Interpretation

FlowMelt is not universally faster than direct access.  That is expected.

The promising part is that several real TCP-heavy destinations, especially
GitHub/npm paths, were comparable or faster through the VPS while avoiding a
full OpenVPN TCP packet tunnel.

This suggests the design is worth continuing as a TCP-first operational mode,
especially for development workflows and web browsing through constrained
networks.

## Operational Modes

### Mode 1: Proxy Mode

Use browser/app SOCKS support directly.

Pros:

- low risk
- no kernel driver
- easy to stop
- works today

Cons:

- apps must support SOCKS/proxy settings
- not a full-device VPN
- UDP is not covered

### Mode 2: TUN Mode

Use a local TUN-to-SOCKS layer and route TCP through FlowMelt.

Candidates:

- sing-box local TUN inbound
- tun2socks

Pros:

- closer to a VPN user experience
- can catch most TCP apps automatically

Cons:

- more moving parts on Windows
- DNS handling must be explicit
- UDP must be blocked, proxied poorly, or handled with special cases

### Mode 3: Native FlowMelt TUN

Build a native client that owns the TUN interface and implements TCP state
handling directly.

Pros:

- best fit for the architecture
- can prioritize small flows and DNS
- avoids generic TUN-to-SOCKS compromises

Cons:

- much more engineering work
- Windows driver/permissions complexity
- needs careful security review

## Next Build Steps

1. Add an HTTP health endpoint bound to localhost on the client.
2. Add basic per-connection metrics locally.
3. Add a PAC file for browser testing.
4. Add optional local DNS forwarding through the SOCKS tunnel.
5. Evaluate sing-box TUN mode using FlowMelt as the upstream SOCKS proxy.
6. If TUN mode is stable, package start/stop/status scripts.

## Current Recommendation

Treat FlowMelt as a second operational path next to OpenVPN:

```text
OpenVPN TCP 443:
  full-device fallback, works broadly, slower

FlowMelt TCP 8443:
  TCP-first proxy mode, better for browser/Git/npm experiments
```

Do not remove OpenVPN yet.  FlowMelt is promising, but it is not a full VPN
replacement until TUN mode is proven.
