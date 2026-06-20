"""Shared Alembic helpers for version table compatibility."""

from alembic.ddl.impl import DefaultImpl
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, inspect

ALEMBIC_VERSION_TABLE = "alembic_version"
ALEMBIC_VERSION_NUM_LENGTH = 255

_VERSION_TABLE_PATCHED = False
_MIGRATION_CONTEXT_INIT_PATCHED = False


def configure_alembic_version_table_storage() -> None:
    """Ensure newly created Alembic version tables allow longer revision ids."""
    global _VERSION_TABLE_PATCHED, _MIGRATION_CONTEXT_INIT_PATCHED

    if not _MIGRATION_CONTEXT_INIT_PATCHED:
        original_init = MigrationContext.__init__

        def _patched_migration_context_init(self, dialect, connection, opts, environment_context=None):
            original_init(self, dialect, connection, opts, environment_context=environment_context)
            version_num = self._version.c.version_num
            if getattr(version_num.type, "length", None) != ALEMBIC_VERSION_NUM_LENGTH:
                self._version = Table(
                    self.version_table,
                    MetaData(),
                    Column("version_num", String(ALEMBIC_VERSION_NUM_LENGTH), nullable=False),
                    schema=self.version_table_schema,
                )
                if opts.get("version_table_pk", True):
                    self._version.append_constraint(
                        PrimaryKeyConstraint(
                            "version_num",
                            name=f"{self.version_table}_pkc",
                        )
                    )

        MigrationContext.__init__ = _patched_migration_context_init
        _MIGRATION_CONTEXT_INIT_PATCHED = True

    if _VERSION_TABLE_PATCHED:
        return

    def _version_table_impl(
        self,
        *,
        version_table: str,
        version_table_schema: str | None,
        version_table_pk: bool,
        **kw,
    ) -> Table:
        table = Table(
            version_table,
            MetaData(),
            Column("version_num", String(ALEMBIC_VERSION_NUM_LENGTH), nullable=False),
            schema=version_table_schema,
        )
        if version_table_pk:
            table.append_constraint(
                PrimaryKeyConstraint(
                    "version_num",
                    name=f"{version_table}_pkc",
                )
            )
        return table

    DefaultImpl.version_table_impl = _version_table_impl
    _VERSION_TABLE_PATCHED = True


def ensure_alembic_version_table_capacity(
    connection,
    *,
    version_table: str = ALEMBIC_VERSION_TABLE,
    version_table_schema: str | None = None,
) -> None:
    """Widen an existing PostgreSQL alembic_version table before upgrades run."""
    if connection.dialect.name != "postgresql":
        return

    inspector = inspect(connection)
    if version_table not in inspector.get_table_names(schema=version_table_schema):
        return

    version_column = next(
        (
            column
            for column in inspector.get_columns(version_table, schema=version_table_schema)
            if column.get("name") == "version_num"
        ),
        None,
    )
    if version_column is None:
        return

    current_length = getattr(version_column.get("type"), "length", None)
    if current_length is None or current_length >= ALEMBIC_VERSION_NUM_LENGTH:
        return

    preparer = connection.dialect.identifier_preparer
    qualified_table = preparer.format_table(Table(version_table, MetaData(), schema=version_table_schema))
    connection.exec_driver_sql(
        f"ALTER TABLE {qualified_table} "
        f"ALTER COLUMN version_num TYPE VARCHAR({ALEMBIC_VERSION_NUM_LENGTH})"
    )


def run_alembic_online_migrations(connectable, *, alembic_context, target_metadata) -> None:
    """Run online migrations in an engine-owned transaction.

    SQLAlchemy 2 rolls back open implicit transactions when a bare Connection
    context manager exits. Alembic's online migrations need an Engine.begin()
    scope so DDL, alembic_version widening, and the final version update persist.
    """
    with connectable.begin() as connection:
        ensure_alembic_version_table_capacity(connection)
        alembic_context.configure(connection=connection, target_metadata=target_metadata)
        with alembic_context.begin_transaction():
            alembic_context.run_migrations()
