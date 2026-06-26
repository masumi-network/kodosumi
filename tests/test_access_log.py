"""Unit tests for access-log level selection (#71). Pure stdlib, no Ray."""
import logging

from kodosumi.log import access_log_level


def test_normal_request_is_info():
    assert access_log_level(200, "/flow", True, ()) == logging.INFO


def test_error_status_is_warning():
    assert access_log_level(500, "/flow", True, ()) == logging.WARNING
    assert access_log_level(404, "/flow", True, ()) == logging.WARNING


def test_quiet_path_is_debug():
    assert access_log_level(200, "/timeline?pp=25", True, ("/timeline",)) == logging.DEBUG


def test_quiet_path_error_still_warning():
    # Errors must stay visible even on quiet paths.
    assert access_log_level(503, "/timeline", True, ("/timeline",)) == logging.WARNING


def test_disabled_demotes_to_debug():
    assert access_log_level(200, "/flow", False, ()) == logging.DEBUG


def test_disabled_error_still_warning():
    assert access_log_level(500, "/flow", False, ()) == logging.WARNING


def test_status_none_treated_as_non_error():
    # No response.start seen → status None → not an error → normal level.
    assert access_log_level(None, "/flow", True, ()) == logging.INFO


def test_multiple_quiet_prefixes():
    quiet = ("/timeline", "/sumi")
    assert access_log_level(200, "/sumi/x/status", True, quiet) == logging.DEBUG
    assert access_log_level(200, "/admin/flow", True, quiet) == logging.INFO
