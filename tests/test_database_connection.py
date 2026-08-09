from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from pubg_ai.config import DatabaseConfig
from pubg_ai.database import connect_mysql


def test_mysql_connections_force_kst_session_timezone() -> None:
    connection = object()
    connect = Mock(return_value=connection)
    pymysql = SimpleNamespace(
        connect=connect,
        cursors=SimpleNamespace(DictCursor=object()),
    )

    with patch.dict("sys.modules", {"pymysql": pymysql}):
        result = connect_mysql(
            DatabaseConfig(
                host="127.0.0.1",
                port=3306,
                user="local-user",
                password="secret",
                database="pubg_ai",
            )
        )

    assert result is connection
    assert connect.call_args.kwargs["init_command"] == "SET time_zone = '+09:00'"
