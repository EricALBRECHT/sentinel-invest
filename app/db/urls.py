from sqlalchemy import URL

from app.core.config import settings


def database_url(driver: str) -> str:
    url = URL.create(
        drivername=driver,
        username=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )
    return url.render_as_string(hide_password=False)

