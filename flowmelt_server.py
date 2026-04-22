#!/usr/bin/env python3
import argparse
import asyncio
import hmac
import ipaddress
import logging
import signal
import socket
import ssl
import struct
from pathlib import Path


MAGIC = b"FMELT1"


async def read_exact(reader: asyncio.StreamReader, size: int) -> bytes:
    return await reader.readexactly(size)


async def read_request(reader: asyncio.StreamReader) -> tuple[str, int, bytes]:
    magic = await read_exact(reader, len(MAGIC))
    if magic != MAGIC:
        raise ValueError("bad magic")

    token_len = struct.unpack("!H", await read_exact(reader, 2))[0]
    if token_len < 16 or token_len > 4096:
        raise ValueError("bad token length")
    token = await read_exact(reader, token_len)

    host_len = struct.unpack("!H", await read_exact(reader, 2))[0]
    if host_len < 1 or host_len > 255:
        raise ValueError("bad host length")
    host = (await read_exact(reader, host_len)).decode("utf-8", "strict")

    port = struct.unpack("!H", await read_exact(reader, 2))[0]
    if port < 1:
        raise ValueError("bad port")

    return host, port, token


async def send_status(writer: asyncio.StreamWriter, code: int, message: str) -> None:
    payload = message.encode("utf-8", "replace")[:512]
    writer.write(struct.pack("!BH", code, len(payload)) + payload)
    await writer.drain()


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        if writer.can_write_eof():
            writer.write_eof()
        else:
            writer.close()


class FlowMeltServer:
    def __init__(
        self,
        token: bytes,
        connect_timeout: float,
        family: int,
        allowed_ports: set[int],
        allow_private: bool,
    ) -> None:
        self.token = token
        self.connect_timeout = connect_timeout
        self.family = family
        self.allowed_ports = allowed_ports
        self.allow_private = allow_private

    def check_ip_allowed(self, ip_text: str) -> None:
        if self.allow_private:
            return
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"refusing private or reserved address: {ip}")

    async def connect_remote(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.allowed_ports and port not in self.allowed_ports:
            raise ValueError(f"port {port} is not allowed")

        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(
            host,
            port,
            family=self.family,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        last_error = None

        for family, socktype, proto, _canonname, sockaddr in infos:
            ip_text = sockaddr[0]
            try:
                self.check_ip_allowed(ip_text)
                sock = socket.socket(family, socktype, proto)
                sock.setblocking(False)
                await asyncio.wait_for(
                    loop.sock_connect(sock, sockaddr),
                    timeout=self.connect_timeout,
                )
                return await asyncio.open_connection(sock=sock)
            except Exception as exc:
                last_error = exc
                try:
                    sock.close()
                except Exception:
                    pass

        raise ConnectionError(last_error or "no usable address")

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        remote_reader = None
        remote_writer = None

        try:
            host, port, token = await read_request(reader)
            if not hmac.compare_digest(token, self.token):
                await send_status(writer, 1, "authentication failed")
                return

            logging.info("connect %s:%s from %s", host, port, peer)
            remote_reader, remote_writer = await self.connect_remote(host, port)
            await send_status(writer, 0, "ok")

            await asyncio.gather(
                relay(reader, remote_writer),
                relay(remote_reader, writer),
            )
        except Exception as exc:
            logging.warning("client %s failed: %s", peer, exc)
            try:
                await send_status(writer, 2, str(exc))
            except Exception:
                pass
        finally:
            for stream_writer in (remote_writer, writer):
                if stream_writer is not None:
                    stream_writer.close()
                    try:
                        await stream_writer.wait_closed()
                    except Exception:
                        pass


def load_token(path: Path) -> bytes:
    token = path.read_text(encoding="utf-8").strip().encode("utf-8")
    if len(token) < 24:
        raise ValueError("token must be at least 24 bytes")
    return token


def parse_ports(value: str) -> set[int]:
    ports = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        port = int(item)
        if port < 1 or port > 65535:
            raise ValueError(f"invalid port: {port}")
        ports.add(port)
    return ports


async def main() -> None:
    parser = argparse.ArgumentParser(description="FlowMelt TCP-terminating tunnel server")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8443)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--cert-file", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--outbound-family", choices=("auto", "ipv4", "ipv6"), default="ipv4")
    parser.add_argument("--allowed-ports", default="22,80,443")
    parser.add_argument("--allow-private", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.load_cert_chain(args.cert_file, args.key_file)

    family = {
        "auto": socket.AF_UNSPEC,
        "ipv4": socket.AF_INET,
        "ipv6": socket.AF_INET6,
    }[args.outbound_family]
    app = FlowMeltServer(
        load_token(Path(args.token_file)),
        args.connect_timeout,
        family,
        parse_ports(args.allowed_ports),
        args.allow_private,
    )
    server = await asyncio.start_server(
        app.handle_client,
        host=args.listen_host,
        port=args.listen_port,
        ssl=ssl_context,
        backlog=128,
    )

    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logging.info("listening on %s", addrs)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    async with server:
        await stop_event.wait()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
