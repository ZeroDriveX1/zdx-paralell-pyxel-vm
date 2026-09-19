"""TLS helpers for legacy and mutual-session ZDX transports."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class TLSConfig:
    ca_file: str
    certificate_file: str | None = None
    private_key_file: str | None = None
    require_peer_certificate: bool = True
    crl_file: str | None = None

    def _validate_paths(self):
        required = [self.ca_file]
        if self.certificate_file:
            required.append(self.certificate_file)
        if self.private_key_file:
            required.append(self.private_key_file)
        if self.crl_file:
            required.append(self.crl_file)
        missing = [path for path in required if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError(f"missing TLS files: {missing}")
        if bool(self.certificate_file) != bool(self.private_key_file):
            raise ValueError("certificate and private key must be configured together")

    def server_context(self) -> ssl.SSLContext:
        self._validate_paths()
        if not self.certificate_file:
            raise ValueError("server TLS requires a certificate and private key")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(self.certificate_file, self.private_key_file)
        context.load_verify_locations(cafile=self.ca_file)
        context.verify_mode = (
            ssl.CERT_REQUIRED if self.require_peer_certificate
            else ssl.CERT_OPTIONAL
        )
        self._configure_crl(context)
        return context

    def client_context(self) -> ssl.SSLContext:
        self._validate_paths()
        context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH, cafile=self.ca_file
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        if self.certificate_file:
            context.load_cert_chain(self.certificate_file, self.private_key_file)
        elif self.require_peer_certificate:
            raise ValueError("mutual TLS requires a client certificate and key")
        self._configure_crl(context)
        return context

    def _configure_crl(self, context: ssl.SSLContext) -> None:
        if self.crl_file:
            context.load_verify_locations(cafile=self.crl_file)
            context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
