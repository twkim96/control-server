"""시스템 환경 조회 API."""

from __future__ import annotations

import os

import psutil
from flask import Blueprint, current_app, jsonify

from auth import auth_required
from process_manager import RuntimeState
from python_finder import list_python_interpreters
from resource_sampler import ResourceSampler

bp = Blueprint("system_api", __name__, url_prefix="/api/system")


@bp.get("/python_interpreters")
@auth_required
def python_interpreters():
    items = list_python_interpreters()
    return jsonify({"interpreters": [i.to_dict() for i in items]})


@bp.get("/controller_resource")
@auth_required
def controller_resource():
    sampler: ResourceSampler = current_app.config["resource_sampler"]
    state = _current_controller_state()
    return jsonify(
        {
            "resource": sampler.sample(
                "system:controller",
                state,
                include_children=False,
            ).to_dict()
        }
    )


def _current_controller_state() -> RuntimeState:
    pid = os.getpid()
    try:
        create_time = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
        create_time = None
    return RuntimeState(pid=pid, create_time=create_time)


__all__ = ["bp"]
