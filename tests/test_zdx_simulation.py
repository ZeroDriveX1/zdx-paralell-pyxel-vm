from zdx_simulator import SimulatedNode, ZDXSimulator


def test_scheduler_selects_capable_node():
    sim = ZDXSimulator()
    sim.add_node(SimulatedNode("cpu", {"cpu_count": 2}))
    sim.add_node(SimulatedNode("accelerated", {"cpu_count": 4, "gpu": True}))

    selected = sim.best_node()

    assert selected[0] == "accelerated"


def test_registry_snapshot():
    sim = ZDXSimulator()
    sim.add_node(SimulatedNode("node-a", {"cpu_count": 1}))

    assert "node-a" in sim.snapshot()
