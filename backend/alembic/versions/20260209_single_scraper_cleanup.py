"""drop legacy scraper columns"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260209_single_scraper_cleanup"
down_revision = "20260202_add_cloud_override_setting"
branch_labels = None
depends_on = None


def upgrade():
    # Normalize mode to a single value
    op.execute("UPDATE analysis_runs SET mode='live'")
    with op.batch_alter_table("analysis_runs") as batch:
        batch.alter_column("mode", server_default="live")
        if _has_column("analysis_runs", "use_cloud_http"):
            batch.drop_column("use_cloud_http")
        if _has_column("analysis_runs", "use_local_scraper"):
            batch.drop_column("use_local_scraper")

    with op.batch_alter_table("settings") as batch:
        if _has_column("settings", "local_scraper_windows"):
            batch.drop_column("local_scraper_windows")
        if _has_column("settings", "cloud_scraper_disabled"):
            batch.drop_column("cloud_scraper_disabled")

    _shrink_marketdatasource_enum()


def downgrade():
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("use_cloud_http", sa.Boolean(), nullable=False, server_default="false"))
        batch.add_column(sa.Column("use_local_scraper", sa.Boolean(), nullable=False, server_default="true"))
        batch.alter_column("mode", server_default="mixed")

    with op.batch_alter_table("settings") as batch:
        batch.add_column(sa.Column("local_scraper_windows", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column(
                "cloud_scraper_disabled",
                sa.Boolean().with_variant(sa.Boolean(), "postgresql"),
                nullable=False,
                server_default="true",
            )
        )

    _restore_marketdatasource_enum()


def _has_column(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = [col["name"] for col in insp.get_columns(table_name)]
    return column_name in cols


def _shrink_marketdatasource_enum():
    # Align ProductMarketData.source with single-scraper worldview
    op.execute("UPDATE product_market_data SET source='scraping' WHERE source NOT IN ('scraping', 'api')")
    op.execute("ALTER TYPE marketdatasource RENAME TO marketdatasource_old")
    op.execute("CREATE TYPE marketdatasource AS ENUM ('scraping', 'api')")
    op.execute(
        "ALTER TABLE product_market_data ALTER COLUMN source TYPE marketdatasource USING source::text::marketdatasource"
    )
    op.execute("DROP TYPE marketdatasource_old")


def _restore_marketdatasource_enum():
    op.execute("ALTER TYPE marketdatasource RENAME TO marketdatasource_new")
    op.execute("CREATE TYPE marketdatasource AS ENUM ('scraping', 'api', 'cloud_http', 'local')")
    op.execute(
        "ALTER TABLE product_market_data ALTER COLUMN source TYPE marketdatasource USING source::text::marketdatasource"
    )
    op.execute("DROP TYPE marketdatasource_new")
