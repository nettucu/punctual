from fastapi.testclient import TestClient

from punctual.main import app


def test_healthz() -> None:
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_logo_defaults_to_the_bundled_one_and_custom_overrides_it(
    client, monkeypatch, tmp_path
):
    from punctual import main

    (tmp_path / "logo.svg").write_text("<svg/>")
    monkeypatch.setattr(main, "STATIC", tmp_path)
    assert client.get("/brand/logo").headers["content-type"].startswith("image/svg")
    (tmp_path / "custom").mkdir()
    (tmp_path / "custom" / "logo.png").write_bytes(b"\x89PNG")
    r = client.get("/brand/logo")
    assert r.headers["content-type"] == "image/png" and r.content == b"\x89PNG"
