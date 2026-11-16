"""The SPA fallback serves the bundle and nothing outside it.

The route sits outside ``/api``, so the session guard never sees it: a path that
climbed out of the bundle would hand the database (every credential) and the
process environment (the session secret) to anyone who asked.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from ratingrelay import main


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "build"
    (root / "_app").mkdir(parents=True)
    (root / "index.html").write_text("INDEX")
    (root / "_app" / "app.js").write_text("APP")
    (tmp_path / "ratingrelay.db").write_text("SECRET")
    monkeypatch.setattr(main.settings, "frontend_dir", str(root))
    return root


async def _get(path: str) -> str:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200
    return response.text


async def test_serves_a_file_in_the_bundle(bundle):
    assert await _get("/_app/app.js") == "APP"


async def test_unknown_path_falls_back_to_index(bundle):
    assert await _get("/jobs/123") == "INDEX"


@pytest.mark.parametrize(
    "path",
    ["/..%2fratingrelay.db", "/%2e%2e/ratingrelay.db", "/_app/..%2f..%2fratingrelay.db"],
)
async def test_traversal_never_leaves_the_bundle(bundle, path):
    assert await _get(path) == "INDEX"


def test_absolute_path_is_not_followed(bundle, tmp_path):
    assert main.spa_file(str(bundle), str(tmp_path / "ratingrelay.db")) == bundle / "index.html"
