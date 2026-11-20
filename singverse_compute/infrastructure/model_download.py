"""Bounded administrator-requested RKNN ZIP downloads, without internal credentials."""
import hashlib
import ipaddress
import re
import socket
import ssl
import time
from http.client import HTTPSConnection
from urllib.parse import urljoin, urlsplit

from ..core.errors import ComputeError


def download_url(value):
    try:
        if not isinstance(value, str) or len(value) > 4096 or any(ord(char) <= 32 for char in value):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or parsed.port == 0):
            raise ValueError()
        return parsed
    except ValueError as error:
        raise ComputeError(422, '模型下载需要不含账号密码的 HTTPS ZIP 地址') from error


def download_source(value):
    if not isinstance(value, dict) or set(value) != {'url', 'sha256'}:
        raise ComputeError(422, '请提供 RKNN 模型 ZIP 的 HTTPS 地址和 SHA-256')
    download_url(value['url'])
    digest = value['sha256']
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
        raise ComputeError(422, '模型 ZIP 的 SHA-256 必须是 64 位十六进制字符串')
    return {'url': value['url'], 'sha256': digest.lower()}


def public_socket(host, port, deadline):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global
                            or ipaddress.ip_address(item[4][0]).is_multicast for item in addresses):
        raise ComputeError(422, '模型下载地址必须指向公网 HTTPS 服务；内网文件请使用离线导入')
    failure = None
    for address in dict.fromkeys(item[4][0] for item in addresses):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ComputeError(422, '模型下载超时，请重试或使用离线导入')
        try:
            return socket.create_connection((address, port), timeout=min(20, remaining))
        except OSError as error:
            failure = error
    raise failure


def download_bundle(source, destination, maximum, cancel, progress):
    """Pin each HTTPS connection to a checked public IP, including redirects."""
    source = download_source(source)
    url = source['url']
    deadline = time.monotonic() + 900
    for _ in range(6):
        if cancel.is_set():
            raise ComputeError(409, '模型下载已取消')
        parsed = download_url(url)
        port = parsed.port or 443
        try:
            # Connect to the checked address rather than resolving the hostname again.
            # TLS still verifies the original hostname; proxy and auth settings are not inherited.
            with public_socket(parsed.hostname, port, deadline) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) as secure:
                    connection = HTTPSConnection(parsed.hostname, port, timeout=20)
                    connection.sock = secure
                    try:
                        path = parsed.path or '/'
                        if parsed.query:
                            path += '?' + parsed.query
                        connection.request('GET', path, headers={'Accept': 'application/zip', 'Accept-Encoding': 'identity'})
                        response = connection.getresponse()
                        if response.status in (301, 302, 303, 307, 308):
                            location = response.getheader('Location')
                            if not location:
                                raise ComputeError(422, '模型下载重定向缺少地址')
                            url = urljoin(url, location)
                            continue
                        if response.status != 200:
                            raise ComputeError(422, '模型下载服务未返回可用文件，请检查 ZIP 地址')
                        length = response.getheader('Content-Length')
                        if length and (not length.isdigit() or int(length) > maximum):
                            raise ComputeError(413, '模型 ZIP 超过大小限制')
                        digest, size = hashlib.sha256(), 0
                        with destination.open('wb') as target:
                            while True:
                                if cancel.is_set():
                                    raise ComputeError(409, '模型下载已取消')
                                if time.monotonic() > deadline:
                                    raise ComputeError(422, '模型下载超时，请重试或使用离线导入')
                                block = response.read1(1024**2)
                                if not block:
                                    break
                                size += len(block)
                                if size > maximum:
                                    raise ComputeError(413, '模型 ZIP 超过大小限制')
                                target.write(block)
                                digest.update(block)
                                progress(size)
                        if not size or (length and size != int(length)):
                            raise ComputeError(422, '模型 ZIP 下载不完整')
                        if digest.hexdigest() != source['sha256']:
                            raise ComputeError(422, '模型 ZIP 的 SHA-256 不匹配')
                        return
                    finally:
                        connection.close()
        except OSError as error:
            raise ComputeError(422, '模型 ZIP 下载连接失败，请检查网络或使用离线导入') from error
    raise ComputeError(422, '模型下载重定向次数过多')
