from zdx_protocol_simulator import ZDXProtocolSimulator


def test_protocol_envelope():
    sim = ZDXProtocolSimulator()
    message = sim.send("heartbeat", {"ok": True})

    assert message["type"] == "heartbeat"
    assert sim.validate(message)
