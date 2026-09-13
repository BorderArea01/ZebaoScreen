"""Screen compatibility: headless final delivery must not block voice."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import server  # noqa: E402


@pytest.mark.parametrize("slow_subscriber", [False, True])
def test_full_history_accepts_latest_and_duplicate_without_growth(slow_subscriber):
    server._diag_final_backlog.clear()
    server._diag_final_seen_ids.clear()
    server._diag_final_seen_order.clear()
    server._diag_final_evictions = 0
    subscriber = server._DiagSubscriber() if slow_subscriber else None
    if subscriber:
        server._diag_subscribers.add(subscriber)
    try:
        with TestClient(server.app).websocket_connect("/api/diag/ws?role=producer") as ws:
            for index in range(1, 71):
                event = {"type": "diag.update", "delivery": "final", "final_id": f"test-{index}",
                         "utterance_id": index, "payload": {"asr": {"utterance_id": index}}}
                ws.send_json(event)
                assert ws.receive_json()["type"] == "diag.final.ack"
            ws.send_json(event)
            assert ws.receive_json()["duplicate"] is True
        queue = subscriber.finals if subscriber else server._diag_final_backlog
        assert len(queue) == 64 and queue[-1]["final_id"] == "test-70"
        assert server._diag_final_evictions == 6
    finally:
        if subscriber:
            server._diag_subscribers.remove(subscriber)
        server._diag_final_backlog.clear()
        server._diag_final_seen_ids.clear()
        server._diag_final_seen_order.clear()
        server._diag_final_evictions = 0


def test_overlay_http_contract_is_available_without_hardware():
    client = TestClient(server.app)
    assert client.get("/api/overlays").json()["overlays"]["asr"]["visible"] is False
    assert client.post("/api/overlays/asr", json={"visible": False}).status_code == 200
    assert client.post("/api/overlays/unknown", json={"visible": True}).status_code == 404
