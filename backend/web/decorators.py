"""Authentication decorators for Flask routes."""

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import abort, redirect, session, url_for


View = TypeVar("View", bound=Callable[..., Any])


def login_required(view: View) -> View:
    """Redirect to login when a user session is missing."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return cast(View, wrapped)


def manager_required(view: View) -> View:
    """Require a logged-in manager and return 403 for other roles."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if session.get("role") != "manager":
            abort(403)
        return view(*args, **kwargs)

    return cast(View, wrapped)
