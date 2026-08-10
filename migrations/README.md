# PostgreSQL schema migrations

The bot uses Alembic as the only authority for its PostgreSQL schema.

```bash
qq-deepseek-bot-db upgrade
qq-deepseek-bot-db current
```

`AI_POSTGRES_DSN` and, optionally, `AI_POSTGRES_SCHEMA` must be present in the
environment. Runtime code never creates or alters tables.
