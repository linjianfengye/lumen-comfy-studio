"""公开部署时防 SSRF：服务器替用户访问的地址（ComfyUI、RunPod 结果链接）必须解析到公网 IP。

本机 / 内网的 ComfyUI 由浏览器直连（见前端 localBridge），服务器不代访问。
每个请求（包括重定向后的请求）都会重新解析并检查目标主机。
"""
import asyncio
import ipaddress
import socket

import httpx

from . import config as C


class BlockedAddress(httpx.TransportError):
    pass


def _bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not a.is_global or a.is_multicast


async def check_host(host: str, port: int | None):
    try:
        if _bad_ip(host):
            raise BlockedAddress(f"不允许访问内网或本机地址：{host}")
        return
    except ValueError:
        pass   # 不是 IP，按域名解析
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedAddress(f"无法解析域名：{host}") from e
    if not infos or any(_bad_ip(i[4][0]) for i in infos):
        raise BlockedAddress(f"不允许访问内网或本机地址：{host}")


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        if request.url.scheme not in ("http", "https"):
            raise BlockedAddress("只允许 http / https")
        await check_host(request.url.host, request.url.port)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def transport(inner: httpx.AsyncBaseTransport | None = None):
    """公开部署返回带检查的 transport；本机运行原样返回（允许访问本机 ComfyUI）。"""
    return GuardedTransport(inner) if C.PUBLIC else inner
