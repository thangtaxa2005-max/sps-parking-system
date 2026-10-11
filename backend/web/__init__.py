"""Flask application factory and request-scoped database access."""

import os
import secrets
import sqlite3
from datetime import date, datetime
from pathlib import Path, PureWindowsPath

from flask import Flask, current_app, g

from backend.cache import CacheState
from backend.config import DB_PATH
from backend.db import connect_database, initialize_schema


def get_shared_cache() -> CacheState:
    """Return a process-wide cache reused across all web requests."""
    cache = current_app.config.get("SHARED_CACHE")
    if cache is None:
        conn = current_app.config.get("DATABASE_CONNECTION")
        if conn is None:
            conn = connect_database(current_app.config["DATABASE_PATH"])
        cache = CacheState(conn)
        current_app.config["SHARED_CACHE"] = cache
    return cache


def get_db() -> sqlite3.Connection:
    """Return the database connection bound to the current request."""
    if "db" not in g:
        injected_connection = current_app.config.get("DATABASE_CONNECTION")
        if injected_connection is not None:
            g.db = injected_connection
        else:
            g.db = connect_database(current_app.config["DATABASE_PATH"])
    return g.db


def _close_db(_error: BaseException | None = None) -> None:
    """Close request-owned database connections, leaving injected ones open."""
    del _error
    connection: sqlite3.Connection | None = g.pop("db", None)
    if (
        connection is not None
        and current_app.config.get("DATABASE_CONNECTION") is None
    ):
        connection.close()


def create_app(
    *,
    database_path: str | Path = DB_PATH,
    database_connection: sqlite3.Connection | None = None,
) -> Flask:
    """Create the web application and initialize its database schema."""
    app = Flask(__name__)

    @app.template_filter("format_datetime")
    def format_datetime_filter(value: str | None) -> str:
        """Format an ISO datetime as dd/mm/yyyy HH:MM."""
        if not value:
            return "—"
        try:
            parsed_datetime = datetime.fromisoformat(str(value))
            return parsed_datetime.strftime("%d/%m/%Y %H:%M")
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter("format_date")
    def format_date_filter(value: str | None) -> str:
        """Format an ISO date as dd/mm/yyyy."""
        if not value:
            return "—"
        try:
            parsed_date = date.fromisoformat(str(value))
            return parsed_date.strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter("image_filename")
    def image_filename_filter(value: str | None) -> str:
        """Extract an event image name from Windows or POSIX stored paths."""
        return PureWindowsPath(str(value or "").replace("/", "\\")).name

    app.config["SECRET_KEY"] = (
        os.environ.get("SPS_WEB_SECRET") or secrets.token_urlsafe(32)
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["DATABASE_PATH"] = str(database_path)
    app.config["DATABASE_CONNECTION"] = database_connection

    setup_connection = (
        database_connection
        if database_connection is not None
        else connect_database(database_path)
    )
    try:
        initialize_schema(setup_connection)
    finally:
        if database_connection is None:
            setup_connection.close()

    app.teardown_appcontext(_close_db)

    from backend.web.auth import auth_bp
    from backend.web.routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    return app


__all__ = ["create_app", "get_db", "get_shared_cache"]
