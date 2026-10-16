import pytest

from app.api import routes


@pytest.fixture(autouse=True)
def _fresh_user_cache():
    """Cache người dùng theo token nằm ở cấp module: không xoá thì test này trúng cache của test trước (cùng token "access-token")."""
    routes.clear_user_cache()
    routes._ADMIN_FAILS.clear()
    yield
    routes.clear_user_cache()
    routes._ADMIN_FAILS.clear()
