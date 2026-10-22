"""Authenticated history and asynchronous PM2 group operations."""
from flask import Blueprint, current_app, jsonify, request

from auth import auth_required, csrf_required
from service_operations import OperationsError
from service_registry import ServiceNotFoundError

bp = Blueprint("service_operations", __name__, url_prefix="/api")


def _operations():
    return current_app.config["service_operations"]


def _error(exc):
    code = str(exc)
    status = 404 if code in {"group_not_found", "job_not_found"} else 409
    payload = {"error": code}
    if code == "pm2_required":
        status = 409
        payload.update(supported=False, message="Service automation requires the PM2 backend.")
    return jsonify(payload), status


@bp.get("/services/<sid>/events")
@auth_required
def events(sid):
    try:
        return jsonify(_operations().events(sid))
    except ServiceNotFoundError:
        return jsonify(error="service_not_found"), 404


@bp.post("/service-groups/<group>/actions/<action>")
@auth_required
@csrf_required
def group_action(group, action):
    if request.content_length:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload:
            return jsonify(error="expected_empty_object"), 400
    try:
        return jsonify(job=_operations().submit_group(group, action)), 202
    except OperationsError as exc:
        return _error(exc)


@bp.get("/service-operations/jobs/<job_id>")
@auth_required
def job(job_id):
    try:
        return jsonify(job=_operations().job(job_id))
    except OperationsError as exc:
        return _error(exc)
