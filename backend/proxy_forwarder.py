#!/usr/bin/env python3
"""
Simple HTTP/HTTPS proxy forwarder that handles upstream proxy authentication.
The browser connects to localhost (no auth), and this forwards to the authenticated proxy.
"""
import asyncio
import os
import sys
import signal
from urllib.parse import urlparse


LISTEN_HOST = os.getenv("PROXY_FORWARDER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("PROXY_FORWARDER_PORT", "8888"))


import random

def get_upstream_proxy(new_session: bool = False):
    """Get upstream proxy URL from environment.
    
    If new_session is True and the URL contains {session}, generate a new session ID.
    """
    # Try SELENIUM_PROXY_ORIGINAL first (set before we override for the browser)
    proxy_url = os.getenv("SELENIUM_PROXY_ORIGINAL", "").strip()
    if not proxy_url:
        proxy_url = os.getenv("SELENIUM_PROXY", "").strip()
    if not proxy_url:
        return None
    
    # Ensure it has a scheme
    if "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"
    
    # Replace {session} or {sid} with a random session ID
    if "{session}" in proxy_url or "{sid}" in proxy_url:
        session_id = str(random.randint(100000, 999999))
        proxy_url = proxy_url.replace("{session}", session_id).replace("{sid}", session_id)
        if new_session:
            print(f"New session ID: {session_id}", file=sys.stderr, flush=True)
    
    return proxy_url


async def pipe(reader, writer):
    """Pipe data between reader and writer."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_connect(client_reader, client_writer, host, port):
    """Handle CONNECT method (HTTPS tunneling)."""
    # Get upstream proxy with potential new session for each connection
    upstream_proxy = get_upstream_proxy(new_session=True)
    if not upstream_proxy:
        client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\nNo upstream proxy configured")
        client_writer.close()
        return
    parsed = urlparse(upstream_proxy)
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 8080
    proxy_auth = None
    
    if parsed.username:
        import base64
        credentials = f"{parsed.username}:{parsed.password or ''}"
        proxy_auth = base64.b64encode(credentials.encode()).decode()
    
    try:
        # Connect to upstream proxy
        upstream_reader, upstream_writer = await asyncio.open_connection(
            proxy_host, proxy_port
        )
        
        # Send CONNECT to upstream proxy
        connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        if proxy_auth:
            connect_req += f"Proxy-Authorization: Basic {proxy_auth}\r\n"
        connect_req += "\r\n"
        
        upstream_writer.write(connect_req.encode())
        await upstream_writer.drain()
        
        # Read proxy response
        response_line = await upstream_reader.readline()
        response = response_line.decode()
        
        # Read headers
        while True:
            header = await upstream_reader.readline()
            if header == b"\r\n" or header == b"\n" or not header:
                break
        
        if "200" in response:
            # Connection established, send success to client
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            
            # Pipe data both ways
            await asyncio.gather(
                pipe(client_reader, upstream_writer),
                pipe(upstream_reader, client_writer),
            )
        else:
            # Proxy refused connection
            client_writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\nUpstream proxy error: {response}".encode())
            await client_writer.drain()
            
    except Exception as e:
        try:
            client_writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\nError: {e}".encode())
            await client_writer.drain()
        except Exception:
            pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


async def handle_http(client_reader, client_writer, request_line, headers):
    """Handle regular HTTP request."""
    upstream_proxy = get_upstream_proxy(new_session=True)
    if not upstream_proxy:
        client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\nNo upstream proxy configured")
        client_writer.close()
        return
    parsed = urlparse(upstream_proxy)
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 8080
    proxy_auth = None
    
    if parsed.username:
        import base64
        credentials = f"{parsed.username}:{parsed.password or ''}"
        proxy_auth = base64.b64encode(credentials.encode()).decode()
    
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            proxy_host, proxy_port
        )
        
        # Forward request with proxy auth
        upstream_writer.write(request_line.encode() + b"\r\n")
        if proxy_auth:
            upstream_writer.write(f"Proxy-Authorization: Basic {proxy_auth}\r\n".encode())
        for header in headers:
            upstream_writer.write(header.encode() + b"\r\n")
        upstream_writer.write(b"\r\n")
        await upstream_writer.drain()
        
        # Pipe response back
        await asyncio.gather(
            pipe(client_reader, upstream_writer),
            pipe(upstream_reader, client_writer),
        )
        
    except Exception as e:
        try:
            client_writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\nError: {e}".encode())
            await client_writer.drain()
        except Exception:
            pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


async def handle_client(client_reader, client_writer):
    """Handle incoming client connection."""
    # Verify upstream proxy is configured (but don't get a session yet)
    if not os.getenv("SELENIUM_PROXY_ORIGINAL", "") and not os.getenv("SELENIUM_PROXY", ""):
        client_writer.write(b"HTTP/1.1 500 No Upstream Proxy\r\n\r\nSELENIUM_PROXY not set")
        client_writer.close()
        return
    
    try:
        # Read request line
        request_line = await asyncio.wait_for(client_reader.readline(), timeout=30)
        if not request_line:
            return
        
        request_line = request_line.decode().strip()
        parts = request_line.split()
        if len(parts) < 3:
            return
        
        method, url, _ = parts[0], parts[1], parts[2]
        
        # Read headers
        headers = []
        while True:
            header = await asyncio.wait_for(client_reader.readline(), timeout=30)
            if header == b"\r\n" or header == b"\n" or not header:
                break
            headers.append(header.decode().strip())
        
        if method == "CONNECT":
            # HTTPS tunnel
            host_port = url.split(":")
            host = host_port[0]
            port = int(host_port[1]) if len(host_port) > 1 else 443
            await handle_connect(client_reader, client_writer, host, port)
        else:
            # Regular HTTP
            await handle_http(client_reader, client_writer, request_line, headers)
            
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        print(f"Error handling client: {e}", file=sys.stderr)
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


async def main():
    upstream = get_upstream_proxy()
    if not upstream:
        print("ERROR: SELENIUM_PROXY environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    parsed = urlparse(upstream)
    print(f"Proxy forwarder starting on {LISTEN_HOST}:{LISTEN_PORT}", file=sys.stderr)
    print(f"Upstream proxy: {parsed.hostname}:{parsed.port} (auth: {'yes' if parsed.username else 'no'})", file=sys.stderr)
    
    server = await asyncio.start_server(
        handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
    )
    
    # Handle shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(server)))
    
    async with server:
        await server.serve_forever()


async def shutdown(server):
    print("Shutting down proxy forwarder...", file=sys.stderr)
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
