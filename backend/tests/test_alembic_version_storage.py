import os
import sys
from unittest.mock import MagicMock, patch

from alembic.runtime.migration import MigrationContext
from sqlalchemy import String
from sqlalchemy.dialects import postgresql

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from alembic_support import (  # noqa: E402
    ALEMBIC_VERSION_NUM_LENGTH,
    configure_alembic_version_table_storage,
    ensure_alembic_version_table_capacity,
    run_alembic_online_migrations,
)


def test_configure_alembic_version_table_storage_uses_wide_revision_ids():
    configure_alembic_version_table_storage()

    ctx = MigrationContext.configure(url="sqlite://")

    version_num_type = ctx._version.c.version_num.type
    assert isinstance(version_num_type, String)
    assert version_num_type.length == ALEMBIC_VERSION_NUM_LENGTH


def test_ensure_alembic_version_table_capacity_widens_short_postgres_column():
    connection = MagicMock()
    connection.dialect = postgresql.dialect()
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["alembic_version"]
    inspector.get_columns.return_value = [{"name": "version_num", "type": String(32)}]

    with patch("alembic_support.inspect", return_value=inspector):
        ensure_alembic_version_table_capacity(connection)

    connection.exec_driver_sql.assert_called_once_with(
        f"ALTER TABLE alembic_version "
        f"ALTER COLUMN version_num TYPE VARCHAR({ALEMBIC_VERSION_NUM_LENGTH})"
    )


def test_ensure_alembic_version_table_capacity_skips_wide_or_unbounded_columns():
    connection = MagicMock()
    connection.dialect = postgresql.dialect()
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["alembic_version"]

    with patch("alembic_support.inspect", return_value=inspector):
        inspector.get_columns.return_value = [{"name": "version_num", "type": String(ALEMBIC_VERSION_NUM_LENGTH)}]
        ensure_alembic_version_table_capacity(connection)

        inspector.get_columns.return_value = [{"name": "version_num", "type": String()}]
        ensure_alembic_version_table_capacity(connection)

    connection.exec_driver_sql.assert_not_called()


def test_run_alembic_online_migrations_uses_engine_begin_and_widens_before_running():
    connectable = MagicMock()
    connection = MagicMock()
    begin_context = MagicMock()
    begin_context.__enter__.return_value = connection
    connectable.begin.return_value = begin_context

    alembic_context = MagicMock()
    migration_context = MagicMock()
    alembic_context.begin_transaction.return_value = migration_context

    events = []

    def _record_ensure(conn):
        events.append(("ensure", conn))

    def _record_configure(**kwargs):
        events.append(("configure", kwargs["connection"], kwargs["target_metadata"]))

    def _record_run():
        events.append(("run", None))

    target_metadata = object()

    alembic_context.configure.side_effect = _record_configure
    alembic_context.run_migrations.side_effect = _record_run

    with patch("alembic_support.ensure_alembic_version_table_capacity", side_effect=_record_ensure):
        run_alembic_online_migrations(
            connectable,
            alembic_context=alembic_context,
            target_metadata=target_metadata,
        )

    connectable.begin.assert_called_once_with()
    connectable.connect.assert_not_called()
    alembic_context.begin_transaction.assert_called_once_with()
    assert events == [
        ("ensure", connection),
        ("configure", connection, target_metadata),
        ("run", None),
    ]
