"""Login and logout routes with in-memory login-attempt throttling."""

import logging
import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from backend import api_core
from backend.web import get_db


logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__)
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_ATTEMPTS = 5
_attempts_by_ip: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()
View = TypeVar("View", bound=Callable[..., Any])


def _throttled(view: View) -> View:
    """Reject login submissions after five failures within one minute."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if request.method != "POST":
            return view(*args, **kwargs)
        remote_ip = request.remote_addr or "unknown"
        now = time.monotonic()
        with _attempts_lock:
            for ip, attempts in list(_attempts_by_ip.items()):
                active = [
                    attempt
                    for attempt in attempts
                    if now - attempt < _LOGIN_WINDOW_SECONDS
                ]
                if active:
                    _attempts_by_ip[ip] = active
                else:
                    _attempts_by_ip.pop(ip, None)
            active_attempts = _attempts_by_ip.get(remote_ip, [])
            if len(active_attempts) >= _LOGIN_MAX_ATTEMPTS:
                logger.warning("Login rate limit reached from ip=%s", remote_ip)
                return (
                    render_template(
                        "login.html",
                        error="Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau.",
                    ),
                    429,
                )
        return view(*args, **kwargs)

    return cast(View, wrapped)


@auth_bp.route("/login", methods=["GET", "POST"])
@_throttled
def login() -> Any:
    """Render the login page or authenticate credentials."""
    if request.method == "GET":
        if "user_id" in session:
            return redirect(url_for("main.dashboard"))
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    user = api_core.verify_user_password(get_db(), username, password)
    if user is None:
        remote_ip = request.remote_addr or "unknown"
        with _attempts_lock:
            _attempts_by_ip.setdefault(remote_ip, []).append(time.monotonic())
        logger.warning("Failed web login username=%r ip=%s", username, remote_ip)
        return render_template(
            "login.html", error="Tên đăng nhập hoặc mật khẩu không đúng."
        )

    with _attempts_lock:
        _attempts_by_ip.pop(request.remote_addr or "unknown", None)
    session.clear()
    session["user_id"] = int(user["id"])
    session["username"] = str(user["username"])
    session["role"] = str(user["role"])
    logger.info(
        "Successful web login username=%s role=%s",
        user["username"],
        user["role"],
    )
    return redirect(url_for("main.dashboard"))


@auth_bp.post("/logout")
def logout() -> Any:
    """Clear the current user session and redirect to login."""
    session.clear()
    return redirect(url_for("auth.login"))
