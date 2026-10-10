from alembic import context

from geomcp_server.db import Base

engine = context.config.attributes["engine"]
with engine.begin() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
