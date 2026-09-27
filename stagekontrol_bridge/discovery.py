"""Annonce du bridge sur le réseau local (mDNS / DNS-SD, service « _stagekontrol._tcp ») :
l'app le trouve toute seule, sans saisir d'adresse IP."""

from __future__ import annotations

import socket

SERVICE_TYPE = "_stagekontrol._tcp.local."


async def announce(port: int, ips: list[str]):
    """Publie le service ; renvoie un objet à passer à `withdraw`, ou None si zeroconf est absent."""
    try:
        from zeroconf import IPVersion, ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf
    except ImportError:
        return None
    name = socket.gethostname().split(".")[0] or "StageKontrol"
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        port=port,
        addresses=[socket.inet_aton(ip) for ip in ips],
        properties={"app": "stagekontrol", "version": "1"},
        server=f"{name}.local.",
    )
    zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    await zc.async_register_service(info, allow_name_change=True)
    return zc, info


async def withdraw(handle) -> None:
    if handle is None:
        return
    zc, info = handle
    await zc.async_unregister_service(info)
    await zc.async_close()
