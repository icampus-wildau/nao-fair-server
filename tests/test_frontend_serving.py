from fastapi.testclient import TestClient

from nao_fair.app import create_app


def test_built_frontend_and_spa_fallback(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>NAO Fair</html>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "outside.txt").write_text("private")
    (dist / "leak.txt").symlink_to(tmp_path / "outside.txt")
    monkeypatch.setenv("NAO_FAIR_FRONTEND_DIR", str(dist))
    app = create_app(db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    with TestClient(app) as client:
        assert client.get("/").text == "<html>NAO Fair</html>"
        assert client.get("/llm/studio").text == "<html>NAO Fair</html>"
        assert client.get("/assets/app.js").text == "console.log('app')"
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/api").status_code == 404
        assert client.get("/leak.txt").status_code == 404
        assert client.get("/%2e%2e%2foutside.txt").status_code == 404
        assert client.get("/api/robot/status").status_code == 200


def test_no_frontend_build_keeps_api_available(tmp_path, monkeypatch):
    monkeypatch.setenv("NAO_FAIR_FRONTEND_DIR", str(tmp_path / "missing"))
    app = create_app(db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/robot/status").status_code == 200


def test_test_driver_route_is_not_hidden_by_frontend(tmp_path, monkeypatch):
    from nao_fair.test_server import create_test_app

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("frontend")
    monkeypatch.setenv("NAO_FAIR_FRONTEND_DIR", str(dist))
    monkeypatch.setenv("NAO_FAIR_DB", str(tmp_path / "db"))
    monkeypatch.setenv("NAO_FAIR_SECRETS", str(tmp_path / "secrets"))
    with TestClient(create_test_app()) as client:
        assert client.get("/api/test/driver").json() == {"ok": True}
    with TestClient(create_app()) as client:
        assert client.get("/api/test/driver").status_code == 404
