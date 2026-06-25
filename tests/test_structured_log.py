"""Unit tests for the structured logging helper (#81).

Pure stdlib — no Ray cluster, no full suite. Run with:
    pytest tests/test_structured_log.py -v
"""
import io
import json
import logging

from kodosumi.log import StructuredFormatter, slog, LOG_FILE_FORMAT


def _capture(formatter: logging.Formatter):
    """Return (logger, get_lines) with a single StringIO handler."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    log = logging.getLogger(f"test.{id(stream)}")
    log.handlers.clear()
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)

    def get_lines():
        handler.flush()
        return [ln for ln in stream.getvalue().splitlines() if ln]

    return log, get_lines


def test_structured_formatter_emits_valid_json():
    log, lines = _capture(StructuredFormatter())
    log.info("hello world")
    doc = json.loads(lines()[0])
    assert set(["ts", "level", "logger", "event"]).issubset(doc)
    assert doc["level"] == "INFO"
    assert doc["event"] == "hello world"  # plain message falls back to event


def test_slog_fields_merged():
    log, lines = _capture(StructuredFormatter())
    slog(log, logging.INFO, "spooler.finished",
         fid="6836f4a2", agent="my-agent", status="finished", duration_ms=42.0)
    doc = json.loads(lines()[0])
    assert doc["event"] == "spooler.finished"
    assert doc["fid"] == "6836f4a2"
    assert doc["agent"] == "my-agent"
    assert doc["status"] == "finished"
    assert doc["duration_ms"] == 42.0


def test_slog_omits_none_fields():
    log, lines = _capture(StructuredFormatter())
    slog(log, logging.INFO, "spooler.saved", fid="abc")
    doc = json.loads(lines()[0])
    assert doc["fid"] == "abc"
    # agent/status/duration_ms/node were None → must not appear (slim records)
    for absent in ("agent", "status", "duration_ms", "node"):
        assert absent not in doc


def test_slog_extra_kwargs_merged():
    log, lines = _capture(StructuredFormatter())
    slog(log, logging.WARNING, "reconcile.sweep", candidates=3)
    doc = json.loads(lines()[0])
    assert doc["event"] == "reconcile.sweep"
    assert doc["candidates"] == 3
    assert doc["level"] == "WARNING"


def test_slog_exc_info_captured():
    log, lines = _capture(StructuredFormatter())
    try:
        raise ValueError("boom")
    except ValueError:
        slog(log, logging.ERROR, "job.failed", fid="x", exc_info=True)
    doc = json.loads(lines()[0])
    assert doc["event"] == "job.failed"
    assert "exc" in doc and "ValueError: boom" in doc["exc"]


def test_plain_formatter_backward_compat():
    # slog() must work with an ordinary formatter (pre-migration call sites).
    log, lines = _capture(logging.Formatter(LOG_FILE_FORMAT))
    slog(log, logging.INFO, "sumi.start_job", fid="abc", agent="ag")
    line = lines()[0]
    assert not line.startswith("{")           # not JSON
    assert "sumi.start_job" in line            # event rendered as message


def test_every_record_is_valid_json():
    # Guard against malformed JSON dropping log lines in Loki.
    log, lines = _capture(StructuredFormatter())
    log.debug("plain")
    slog(log, logging.INFO, "evt", fid="f", duration_ms=1.5)
    slog(log, logging.ERROR, "boom", node="ray-head")
    for ln in lines():
        json.loads(ln)  # raises if any line is not valid JSON
