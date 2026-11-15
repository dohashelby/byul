"""Exercise probe lifetime and actual Gateway TCP selection without AWS."""

import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

WP = Path(__file__).resolve().parents[1] / "project-context/refactor-2026-11/wp7"


def test_import_probe_stays_alive_for_identity_sampling(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(WP))
    from server_checks import Live

    sleeps = []
    real_path = tmp_path / "probe.py"
    live = SimpleNamespace(normal=lambda: None, wait_api=lambda *_: None)

    def kube(*argv):
        source = argv[-1]
        assert "time.sleep(12);p.parent.mkdir" in source
        start = source.index("p=pathlib.Path(")
        end = source.index(");", start)
        source = source[:start] + "p=pathlib.Path(" + repr(str(real_path)) + source[end:]
        with patch("time.sleep", side_effect=sleeps.append):
            exec(source, {})

    live.kube = kube
    Live.R_08(live)
    assert sleeps == [12]
    assert not real_path.exists()


def test_disconnect_resets_gateway_socket_instead_of_port_forward(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(WP))
    from rehearsal_checks import FakeLive, Connection
    import server_checks

    live = FakeLive(tmp_path)
    live.c = {"disconnect_via_cluster_service": True}
    old_kube = live.kube

    def kube(*args, **kwargs):
        if args[:2] == ("get", "service"):
            assert args[2] == "serving-gateway" and kwargs["private"]
            return json.dumps({"spec": {"clusterIP": "127.0.0.2", "ports": [{"port": 8080, "targetPort": 8080}]}})
        return old_kube(*args, **kwargs)

    live.kube = kube
    destinations = []

    def connect(host, port, **kwargs):
        destinations.append((host, port))
        return Connection(host, port, **kwargs)

    with patch.object(server_checks.http.client, "HTTPConnection", side_effect=connect):
        live.N_06()
    assert destinations == [("127.0.0.2", 8080)]
    receipt = json.loads((live.out / "disconnect-response-start.json").read_text())
    assert receipt["direct_gateway_tcp"] is True and receipt["observed_at"]
    assert json.loads((live.out / "disconnect-closed.json").read_text())["first_line_seen"]
    assert live.normal_count == 2 and live.restored == 1
