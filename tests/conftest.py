import pytest

from goldmaker import database


@pytest.fixture()
def conn(tmp_path):
    connection = database.connect(tmp_path / "test.sqlite3")
    yield connection
    connection.close()
