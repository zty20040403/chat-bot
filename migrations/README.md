# PostgreSQL schema migrations

The bot uses Alembic as the only authority for its PostgreSQL schema.

```bash
gaoji-db upgrade
gaoji-db current
```

`AI_POSTGRES_DSN` and, optionally, `AI_POSTGRES_SCHEMA` must be present in the
environment. Runtime code never creates or alters tables.
