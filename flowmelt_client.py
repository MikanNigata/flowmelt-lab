#!/usr/bin/env python3
import argparse
import asyncio
import binascii
import hashlib
import logging
import socket
import ssl
import struct
from pathlib import Path


MAGIC = b"FMELT1"


async def read_exact(reader: asyncio.StreamReader, size: int) -> bytes:
    return await reader.readexactly(size)


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


def load_token(path: Path) -> bytes:
    token = path.read_text(encoding="utf-8").strip().encode("utf-8")
    if len(token) < 24:
        raise ValueError("token must be at least 24 bytes")
    return token


def parse_pin(pin: str | None) -> str | None:
    if not pin:
        return None
    pin = pin.strip().lower()
    if pin.startswith("sha256:"):
        pin = pin[7:]
    pin = pin.replace(":", "")
    if len(pin) != 64:
        raise ValueError("pin must be a SHA-256 hex fingerprint")
    int(pin, 16)
    return pin


def verify_pin(writer: asyncio.StreamWriter, expected_pin: str | None) -> None:
    if not expected_pin:
        return
    ssl_obj = writer.get_extra_info("ssl_object")
    if ssl_obj is None:
        raise RuntimeError("TLS object is missing")
    cert = ssl_obj.getpeercert(binary_form=True)
    actual = hashlib.sha256(cert).hexdigest()
    if actual.lower() != expected_pin:
        raise RuntimeError(f"server certificate pin mismatch: {actual}")


async def send_tunnel_request(
    writer: asyncio.StreamWriter, token: bytes, host: str, port: int
) -> None:
    host_bytes = host.encode("utf-8", "strict")
    if len(host_bytes) > 255:
        raise ValueError("host name too long")
    writer.write(
        MAGIC
        + struct.pack("!H", len(token))
        + token
        + struct.pack("!H", len(host_bytes))
        + host_bytes
        + struct.pack("!H", port)
    )
    await writer.drain()


async def read_tunnel_status(reader: asyncio.StreamReader) -> tuple[int, str]:
    code, length = struct.unpack("!BH", await read_exact(reader, 3))
    message = (await read_exact(reader, length)).decode("utf-8", "replace")
    return code, message


async def parse_socks_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> tuple[str, int]:
    ver = (await read_exact(reader, 1))[0]
    if ver != 5:
        raise ValueError("only SOCKS5 is supported")
    nmethods = (await read_exact(reader, 1))[0]
    await read_exact(reader, nmethods)
    writer.write(b"\x05\x00")
    await writer.drain()

    header = await read_exact(reader, 4)
    ver, cmd, _reserved, atyp = header
    if ver != 5 or cmd != 1:
        writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        raise ValueError("only SOCKS5 CONNECT is supported")

    if atyp == 1:
        host = socket.inet_ntoa(await read_exact(reader, 4))
    elif atyp == 3:
        length = (await read_exact(reader, 1))[0]
        host = (await read_exact(reader, length)).decode("idna")
    elif atyp == 4:
        host = socket.inet_ntop(socket.AF_INET6, await read_exact(reader, 16))
    else:
        writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        raise ValueError("unsupported address type")

    port = struct.unpack("!H", await read_exact(reader, 2))[0]
    return host, port


async def handle_socks_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    args: argparse.Namespace,
    token: bytes,
    expected_pin: str | None,
) -> None:
    peer = client_writer.get_extra_info("peername")
    tunnel_reader = None
    tunnel_writer = None

    try:
        host, port = await parse_socks_request(client_reader, client_writer)
        logging.info("proxy %s:%s from %s", host, port, peer)

        ssl_context = ssl._create_unverified_context()
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tunnel_reader, tunnel_writer = await asyncio.open_connection(
            args.server_host,
            args.server_port,
            ssl=ssl_context,
            server_hostname=args.server_name,
        )
        verify_pin(tunnel_writer, expected_pin)

        await send_tunnel_request(tunnel_writer, token, host, port)
        code, message = await read_tunnel_status(tunnel_reader)
        if code != 0:
            logging.warning("server rejected %s:%s: %s", host, port, message)
            client_writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            return

        client_writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await client_writer.drain()

        await asyncio.gather(
            relay(client_reader, tunnel_writer),
            relay(tunnel_reader, client_writer),
        )
    except Exception as exc:
        logging.warning("client %s failed: %s", peer, exc)
    finally:
        for stream_writer in (tunnel_writer, client_writer):
            if stream_writer is not None:
                stream_writer.close()
                try:
                    await stream_writer.wait_closed()
                except Exception:
                    pass


async def main() -> None:
    parser = argparse.ArgumentParser(description="FlowMelt local SOCKS5 client")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=1080)
    parser.add_argument("--server-host", required=True)
    parser.add_argument("--server-port", type=int, default=8443)
    parser.add_argument("--server-name", default="flowmelt.local")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--pin-sha256")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    token = load_token(Path(args.token_file))
    expected_pin = parse_pin(args.pin_sha256)

    server = await asyncio.start_server(
        lambda r, w: handle_socks_client(r, w, args, token, expected_pin),
        args.listen_host,
        args.listen_port,
    )

    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logging.info("SOCKS5 listening on %s", addrs)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
