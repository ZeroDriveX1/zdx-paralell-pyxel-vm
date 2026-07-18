import socket
import threading

from zdx_server import ZDXServer
from zdx_network import ZDXMessage, send_message, recv_message


def test_server_startup():
    server = ZDXServer(port=9876)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()

    client = socket.create_connection(("127.0.0.1", 9876), timeout=2)
    send_message(client, ZDXMessage(kind="heartbeat", payload={}))
    response = recv_message(client)

    assert response.kind == "heartbeat"
    server.stop()
    client.close()
