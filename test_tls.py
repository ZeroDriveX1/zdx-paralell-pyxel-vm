import datetime
import ipaddress
import socket
import ssl
import threading
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from security.identity import NodeIdentity
from zdx_network import TrustedKeyStore
from zdx_node import ZDXNode
from zdx_server import ZDXServer
from zdx_session import NodeCredentials
from zdx_tls import TLSConfig


def _write(path, data):
    path.write_bytes(data)
    return str(path)


def _certificates(tmp_path, *, expired_server=False, revoke_server=False):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = Ed25519PrivateKey.generate()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ZDX Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False,
        ), True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, algorithm=None)
    )

    def issue(name, server=False, expired=False):
        key = Ed25519PrivateKey.generate()
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=2))
            .not_valid_after(
                now - datetime.timedelta(days=1)
                if expired else now + datetime.timedelta(days=10)
            )
            .add_extension(
                x509.ExtendedKeyUsage([
                    ExtendedKeyUsageOID.SERVER_AUTH if server
                    else ExtendedKeyUsageOID.CLIENT_AUTH
                ]),
                False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ), True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()
                ), False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
            )
        )
        if server:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]),
                False,
            )
        return key, builder.sign(ca_key, algorithm=None)

    server_key, server_cert = issue(
        "localhost", server=True, expired=expired_server
    )
    rotated_key, rotated_cert = issue("localhost", server=True)
    client_key, client_cert = issue("worker")

    def private_bytes(key):
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    result = {
        "ca": _write(tmp_path / "ca.pem", ca_cert.public_bytes(serialization.Encoding.PEM)),
        "server_cert": _write(tmp_path / "server.pem", server_cert.public_bytes(serialization.Encoding.PEM)),
        "server_key": _write(tmp_path / "server-key.pem", private_bytes(server_key)),
        "client_cert": _write(tmp_path / "client.pem", client_cert.public_bytes(serialization.Encoding.PEM)),
        "client_key": _write(tmp_path / "client-key.pem", private_bytes(client_key)),
        "rotated_server_cert": _write(tmp_path / "server-rotated.pem", rotated_cert.public_bytes(serialization.Encoding.PEM)),
        "rotated_server_key": _write(tmp_path / "server-rotated-key.pem", private_bytes(rotated_key)),
    }
    revoked = []
    if revoke_server:
        revoked.append(
            x509.RevokedCertificateBuilder()
            .serial_number(server_cert.serial_number)
            .revocation_date(now - datetime.timedelta(minutes=1))
            .build()
        )
    crl_builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_name)
        .last_update(now - datetime.timedelta(minutes=1))
        .next_update(now + datetime.timedelta(days=1))
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ), False,
        )
    )
    for item in revoked:
        crl_builder = crl_builder.add_revoked_certificate(item)
    result["crl"] = _write(
        tmp_path / "ca.crl",
        crl_builder.sign(ca_key, algorithm=None).public_bytes(serialization.Encoding.PEM),
    )
    return result


def _start_server(tmp_path, certificates):
    coordinator = NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))
    worker = NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))
    trust = TrustedKeyStore()
    trust.enroll(worker.node_id, worker.public_key_bytes)
    server = ZDXServer(
        host="127.0.0.1", port=0, credentials=coordinator, trust=trust,
        state_path=str(tmp_path / "state.json"),
        tls_config=TLSConfig(
            certificates["ca"], certificates["server_cert"],
            certificates["server_key"],
        ),
    )
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    deadline = time.time() + 2
    while server.port == 0 and time.time() < deadline:
        time.sleep(0.01)
    return server, thread, coordinator, worker


def test_mutual_tls_authenticated_zdx_session(tmp_path):
    certificates = _certificates(tmp_path)
    server, thread, coordinator, worker = _start_server(tmp_path, certificates)
    client = ZDXNode(
        host="127.0.0.1", port=server.port, credentials=worker,
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"],
        ),
        server_hostname="localhost",
    )
    client.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    with client.connect() as connection:
        assert connection.version() == "TLSv1.3"
        assert client.ping(connection).kind == "heartbeat_ack"
    server.stop()
    thread.join(2)


def test_hostname_and_expired_certificate_rejected(tmp_path):
    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()
    certificates = _certificates(valid_dir)
    server, thread, coordinator, worker = _start_server(valid_dir, certificates)
    client = ZDXNode(
        host="127.0.0.1", port=server.port, credentials=worker,
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"],
        ), server_hostname="wrong.example",
    )
    client.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    with pytest.raises(ssl.SSLCertVerificationError, match="Hostname mismatch"):
        client.connect()
    server.stop(); thread.join(2)

    expired_dir = tmp_path / "expired"
    expired_dir.mkdir()
    expired = _certificates(expired_dir, expired_server=True)
    server, thread, coordinator, worker = _start_server(expired_dir, expired)
    client = ZDXNode(
        host="127.0.0.1", port=server.port, credentials=worker,
        tls_config=TLSConfig(
            expired["ca"], expired["client_cert"], expired["client_key"]
        ), server_hostname="localhost",
    )
    client.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    with pytest.raises(ssl.SSLCertVerificationError, match="expired"):
        client.connect()
    server.stop(); thread.join(2)


def test_revoked_server_certificate_rejected(tmp_path):
    certificates = _certificates(tmp_path, revoke_server=True)
    server, thread, coordinator, worker = _start_server(tmp_path, certificates)
    client = ZDXNode(
        host="127.0.0.1", port=server.port, credentials=worker,
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"], crl_file=certificates["crl"],
        ), server_hostname="localhost",
    )
    client.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    with pytest.raises(ssl.SSLCertVerificationError, match="revoked"):
        client.connect()
    server.stop(); thread.join(2)


def test_certificate_rotation_for_new_connections(tmp_path):
    certificates = _certificates(tmp_path)
    server, thread, coordinator, worker = _start_server(tmp_path, certificates)
    server.reload_tls(TLSConfig(
        certificates["ca"], certificates["rotated_server_cert"],
        certificates["rotated_server_key"],
    ))
    client = ZDXNode(
        host="127.0.0.1", port=server.port, credentials=worker,
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"],
        ), server_hostname="localhost",
    )
    client.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    with client.connect() as connection:
        peer = x509.load_der_x509_certificate(connection.getpeercert(binary_form=True))
        rotated = x509.load_pem_x509_certificate(Path(certificates["rotated_server_cert"]).read_bytes())
        assert peer.serial_number == rotated.serial_number
    server.stop(); thread.join(2)


def test_tls_config_rejects_missing_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        TLSConfig(str(tmp_path / "missing")).client_context()
