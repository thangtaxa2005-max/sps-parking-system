"""Read-only dashboard routes."""

import sqlite3
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from backend import api_core
from backend.web import get_db, get_shared_cache
from backend.web.decorators import login_required, manager_required


main_bp = Blueprint("main", __name__)


def _credential_uid(
    conn: sqlite3.Connection, credential_id: int, vehicle_id: int
) -> str:
    """Return a credential UID after confirming it belongs to the selected vehicle."""
    row = conn.execute(
        "SELECT value FROM credentials WHERE id = ? AND vehicle_id = ?",
        (credential_id, vehicle_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Không tìm thấy thẻ id={credential_id} của xe này.")
    return str(row["value"])


@main_bp.get("/")
def index() -> Any:
    """Redirect the root page to the dashboard."""
    return redirect(url_for("main.dashboard"))


@main_bp.get("/dashboard")
@login_required
def dashboard() -> str:
    """Render capacity, alert, and recent image-event summaries."""
    connection = get_db()
    return render_template(
        "dashboard.html",
        capacity=api_core.get_capacity_summary(connection),
        open_alerts=api_core.count_open_alerts(connection),
        events=api_core.get_events_with_images(connection, limit=5),
    )


@main_bp.get("/api/dashboard-status")
@login_required
def dashboard_status() -> Any:
    """Return live counters and recent events for the dashboard."""
    connection = get_db()
    return jsonify(
        capacity=api_core.get_capacity_summary(connection),
        open_alerts=api_core.count_open_alerts(connection),
        events_html=render_template(
            "_dashboard_events.html",
            events=api_core.get_events_with_images(connection, limit=5),
        ),
    )


@main_bp.get("/settings")
@login_required
@manager_required
def settings_page() -> str:
    """Render editable system settings."""
    return render_template("settings.html", settings=api_core.get_settings(get_db()))


@main_bp.post("/settings")
@login_required
@manager_required
def settings_update() -> Any:
    """Validate and save editable system settings."""
    try:
        api_core.update_settings(
            get_db(), get_shared_cache(),
            capacity_total=int(request.form.get("capacity_total", "")),
            default_quota_car=int(request.form.get("default_quota_car", "")),
            default_quota_moto=int(request.form.get("default_quota_moto", "")),
            ocr_threshold=float(request.form.get("ocr_threshold", "")),
        )
    except (TypeError, ValueError) as error:
        flash(str(error), "danger")
    else:
        flash("Đã lưu cài đặt.", "success")
    return redirect(url_for("main.settings_page"))


@main_bp.get("/accounts")
@login_required
@manager_required
def accounts_page() -> str:
    """Render resident accounts and linked Telegram status."""
    return render_template(
        "accounts.html",
        units=api_core.list_units(get_db()),
        users=api_core.list_users_with_units(get_db()),
    )


@main_bp.post("/accounts/unit")
@login_required
@manager_required
def account_create_unit() -> Any:
    """Create an apartment using the configured default vehicle quotas."""
    unit_code = request.form.get("unit_code", "").strip()
    try:
        api_core.create_unit(get_db(), get_shared_cache(), unit_code)
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã lưu căn hộ.", "success")
    return redirect(url_for("main.accounts_page"))


@main_bp.post("/accounts/resident")
@login_required
@manager_required
def account_create_resident() -> Any:
    """Create a resident account for an apartment."""
    try:
        api_core.create_resident_user(
            get_db(), get_shared_cache(), unit_id=int(request.form.get("unit_id", "")),
            username=request.form.get("username", "").strip(),
            password=request.form.get("password", ""),
        )
    except (TypeError, ValueError) as error:
        flash(str(error), "danger")
    else:
        flash("Đã tạo tài khoản cư dân.", "success")
    return redirect(url_for("main.accounts_page"))


@main_bp.post("/accounts/<int:user_id>/telegram-code")
@login_required
@manager_required
def account_telegram_code(user_id: int) -> Any:
    """Generate and display a one-time Telegram link code."""
    try:
        code = api_core.create_telegram_link_code(get_db(), user_id)
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash(f"Mã liên kết Telegram (hết hạn sau 15 phút): {code}", "success")
    return redirect(url_for("main.accounts_page"))


@main_bp.post("/accounts/<int:user_id>/unlink-telegram")
@login_required
@manager_required
def account_unlink_telegram(user_id: int) -> Any:
    """Remove a resident Telegram link."""
    api_core.clear_telegram_link(get_db(), user_id)
    flash("Đã hủy liên kết Telegram.", "success")
    return redirect(url_for("main.accounts_page"))


@main_bp.post("/accounts/<int:user_id>/test-notification")
@login_required
@manager_required
def account_test_notification(user_id: int) -> Any:
    """Queue one manager-requested Telegram delivery check."""
    try:
        api_core.queue_telegram_test_notification(
            get_db(), recipient_user_id=user_id,
            manager_user_id=int(session["user_id"]),
        )
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã xếp thông báo thử; bot sẽ gửi khi đang chạy.", "success")
    return redirect(url_for("main.accounts_page"))


@main_bp.post("/gate/open")
@login_required
@manager_required
def gate_open() -> Any:
    """Queue a manual open command for a connected gate."""
    try:
        api_core.enqueue_open_command(
            get_db(), gate=request.form.get("gate", ""),
            actor=str(session.get("username", "manager")),
        )
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã gửi lệnh mở cổng; cổng sẽ mở khi ESP32 đang kết nối.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.get("/history")
@login_required
@manager_required
def history() -> str:
    """Display recent gate events, including links to captured images."""
    return render_template("history.html", events=api_core.list_events(get_db()))


@main_bp.get("/alerts")
@login_required
@manager_required
def alerts() -> str:
    """Display unresolved manager alerts."""
    connection = get_db()
    return render_template(
        "alerts.html",
        alerts=api_core.list_open_alerts(connection),
        vehicles=api_core.list_vehicles_with_credentials(connection),
    )


@main_bp.post("/alerts/<int:alert_id>/resolve")
@login_required
@manager_required
def alert_resolve(alert_id: int) -> Any:
    """Acknowledge one alert as the logged-in manager."""
    try:
        api_core.resolve_alert(get_db(), alert_id, int(session["user_id"]))
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã đánh dấu cảnh báo là đã xử lý.", "success")
    return redirect(url_for("main.alerts"))


@main_bp.post("/alerts/<int:alert_id>/correct-and-open")
@login_required
@manager_required
def alert_correct_and_open(alert_id: int) -> Any:
    """Correct a chosen vehicle and queue one manual gate open."""
    try:
        in_lot_text = request.form.get("in_lot", "")
        if in_lot_text not in ("0", "1"):
            raise ValueError("Trạng thái xe không hợp lệ.")
        api_core.correct_occupancy_and_open_gate(
            get_db(), get_shared_cache(),
            alert_id=alert_id,
            manager_user_id=int(session["user_id"]),
            vehicle_id=int(request.form.get("vehicle_id", "")),
            in_lot=in_lot_text == "1",
            gate=request.form.get("gate", ""),
        )
    except (TypeError, ValueError) as error:
        flash(str(error), "danger")
    else:
        flash("Đã sửa trạng thái xe, ghi lịch sử và gửi lệnh mở cổng.", "success")
    return redirect(url_for("main.alerts"))


@main_bp.get("/images/<path:filename>")
@login_required
@manager_required
def event_image(filename: str) -> Any:
    """Serve event images only from the configured data/images directory."""
    project_root = Path(__file__).resolve().parents[2]
    image_root = (project_root / "data" / "images").resolve()
    target = (image_root / filename).resolve()
    try:
        target.relative_to(image_root)
    except ValueError:
        abort(404)
    if not target.is_file():
        abort(404)
    return send_file(target)


@main_bp.get("/requests")
@login_required
@manager_required
def requests_list() -> str:
    """Display pending requests or approved requests awaiting card issuance."""
    show_approved = request.args.get("show") == "approved"
    status = "approved_waiting_card" if show_approved else "pending"
    return render_template(
        "requests.html",
        requests=api_core.list_registration_requests(get_db(), status=status),
        show_approved=show_approved,
        enroll_state=api_core.get_enroll_state(get_db()),
    )


@main_bp.post("/enroll/start")
@login_required
@manager_required
def enroll_start() -> Any:
    """Start a 60-second card-enrollment window for a request or vehicle."""
    connection = get_db()
    try:
        gate = request.form.get("gate", "in")
        request_id = request.form.get("request_id")
        vehicle_id = request.form.get("vehicle_id")
        kwargs: dict[str, Any] = {"gate": gate}
        if request_id:
            kwargs["request_id"] = int(request_id)
        elif vehicle_id:
            kwargs["vehicle_id"] = int(vehicle_id)
        else:
            raise ValueError("Phải chọn yêu cầu đăng ký hoặc xe.")
        api_core.set_enroll_state(connection, get_shared_cache(), **kwargs)
    except (TypeError, ValueError) as error:
        flash(str(error), "danger")
    else:
        flash("Đã bật chế độ cấp thẻ trong 60 giây. Hãy quẹt thẻ tại cổng đã chọn.", "success")
    target = request.form.get("next", "requests")
    return redirect(url_for("main.vehicles_list" if target == "vehicles" else "main.requests_list", **({"show": "approved"} if target == "requests" else {})))


@main_bp.post("/enroll/clear")
@login_required
@manager_required
def enroll_clear() -> Any:
    """Cancel the active card-enrollment window."""
    api_core.clear_enroll_state(get_db(), get_shared_cache())
    flash("Đã tắt chế độ cấp thẻ.", "success")
    target = request.form.get("next", "requests")
    return redirect(url_for("main.vehicles_list" if target == "vehicles" else "main.requests_list", **({"show": "approved"} if target == "requests" else {})))


@main_bp.post("/requests/<int:request_id>/approve")
@login_required
@manager_required
def request_approve(request_id: int) -> Any:
    """Approve a pending registration request."""
    try:
        api_core.approve_registration_request(get_db(), get_shared_cache(), request_id)
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã duyệt yêu cầu đăng ký.", "success")
    return redirect(url_for("main.requests_list"))


@main_bp.post("/requests/<int:request_id>/reject")
@login_required
@manager_required
def request_reject(request_id: int) -> Any:
    """Reject a pending registration request with a required reason."""
    try:
        api_core.reject_registration_request(
            get_db(),
            get_shared_cache(),
            request_id,
            request.form.get("reason", ""),
        )
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã từ chối yêu cầu đăng ký.", "success")
    return redirect(url_for("main.requests_list"))


@main_bp.get("/vehicles")
@login_required
@manager_required
def vehicles_list() -> str:
    """Display the registered vehicles and their credentials."""
    return render_template(
        "vehicles.html",
        vehicles=api_core.list_vehicles_with_credentials(get_db()),
        enroll_state=api_core.get_enroll_state(get_db()),
    )


@main_bp.post(
    "/vehicles/<int:vehicle_id>/credentials/<int:credential_id>/lock"
)
@login_required
@manager_required
def vehicle_credential_lock(vehicle_id: int, credential_id: int) -> Any:
    """Temporarily lock a credential belonging to the selected vehicle."""
    try:
        uid = _credential_uid(get_db(), credential_id, vehicle_id)
        api_core.lock_credential(get_db(), get_shared_cache(), uid)
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã khóa thẻ.", "success")
    return redirect(url_for("main.vehicles_list"))


@main_bp.post(
    "/vehicles/<int:vehicle_id>/credentials/<int:credential_id>/unlock"
)
@login_required
@manager_required
def vehicle_credential_unlock(vehicle_id: int, credential_id: int) -> Any:
    """Unlock a credential unless it has been permanently marked as lost."""
    try:
        uid = _credential_uid(get_db(), credential_id, vehicle_id)
        api_core.unlock_credential(get_db(), get_shared_cache(), uid)
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã mở khóa thẻ.", "success")
    return redirect(url_for("main.vehicles_list"))


@main_bp.post(
    "/vehicles/<int:vehicle_id>/credentials/<int:credential_id>/mark-lost"
)
@login_required
@manager_required
def vehicle_credential_mark_lost(vehicle_id: int, credential_id: int) -> Any:
    """Permanently mark a credential as lost."""
    try:
        uid = _credential_uid(get_db(), credential_id, vehicle_id)
        api_core.mark_credential_lost(get_db(), get_shared_cache(), uid)
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã báo mất thẻ.", "success")
    return redirect(url_for("main.vehicles_list"))


@main_bp.post("/vehicles/<int:vehicle_id>/extend")
@login_required
@manager_required
def vehicle_extend(vehicle_id: int) -> Any:
    """Extend a vehicle plan by one of the supported periods."""
    try:
        try:
            months = int(request.form.get("months", ""))
        except ValueError as error:
            raise ValueError("Kỳ hạn gia hạn không hợp lệ.") from error
        if months not in (1, 3, 6, 12):
            raise ValueError("Kỳ hạn gia hạn phải là 1, 3, 6 hoặc 12 tháng.")
        vehicle = api_core.get_vehicle_by_id(get_db(), vehicle_id)
        if vehicle is None:
            raise ValueError(f"Không tìm thấy xe id={vehicle_id}.")
        new_expiry = api_core.extend_vehicle_plan(
            get_db(),
            get_shared_cache(),
            str(vehicle["plate"]),
            months,
        )
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash(
            f"Đã gia hạn gói đến ngày {new_expiry.strftime('%d/%m/%Y')}.",
            "success",
        )
    return redirect(url_for("main.vehicles_list"))


@main_bp.post("/vehicles/<int:vehicle_id>/occupancy")
@login_required
@manager_required
def vehicle_occupancy(vehicle_id: int) -> Any:
    """Manually adjust occupancy and record the manager as the actor."""
    try:
        vehicle = api_core.get_vehicle_by_id(get_db(), vehicle_id)
        if vehicle is None:
            raise ValueError(f"Không tìm thấy xe id={vehicle_id}.")
        value = request.form.get("in_lot") == "1"
        actor = str(session.get("username", "manager"))
        api_core.set_vehicle_in_lot(
            get_db(), get_shared_cache(), str(vehicle["plate"]), value, actor=actor
        )
    except ValueError as error:
        flash(str(error), "danger")
    else:
        flash("Đã cập nhật trạng thái xe và ghi audit.", "success")
    return redirect(url_for("main.vehicles_list"))
