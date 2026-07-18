import tempfile

from zdx_network import ZDXMessage
from zdx_node import ZDXNode


def test_message_roundtrip():
    msg = ZDXMessage(kind="test", payload={"value": 1})
    decoded = ZDXMessage.decode(msg.encode()[4:])
    assert decoded.kind == "test"
    assert decoded.payload["value"] == 1


def test_frame_hash():
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"pyxel-frame")
        f.flush()
        result = ZDXNode.hash_frame(f.name)
        assert len(result) == 64
