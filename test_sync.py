import tempfile

from zdx_sync import FrameSync


def test_frame_registration():
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"zdx-frame")
        f.flush()

        sync = FrameSync()
        digest = sync.register(f.name)

        assert len(digest) == 64
        assert sync.verify(f.name, digest)
