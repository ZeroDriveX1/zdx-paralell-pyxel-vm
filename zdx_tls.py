"""Explicit TLS configuration for private ZDX transport."""

from __future__ import annotations

import ssl


def server_context(
    certfile: str,
    keyfile: str,
    *,
    cafile: str | None = None,
    require_client_certificate: bool = False,
) -> ssl.SSLContext:
    """Build a TLS 1.2+ server context for enrolled private peers."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    if cafile:
        context.load_verify_locations(cafile=cafile)
    context.verify_mode = (
        ssl.CERT_REQUIRED if require_client_certificate else ssl.CERT_NONE
    )
    return context


def client_context(*, cafile: str, certfile: str | None = None, keyfile: str | None = None) -> ssl.SSLContext:
    """Build a TLS client context that validates the configured CA."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if certfile or keyfile:
        if not (certfile and keyfile):
            raise ValueError("client certfile and keyfile must be supplied together")
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    return context
