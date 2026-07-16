"""
Tests for severity hygiene (#79).

Verifies:
- No bare 'except:' remains (SIGINT/SIGTERM must propagate)
- Logger routing through kodo structured logger
- Verified-relevant silent failure paths now emit WARNING
"""

import logging
from pathlib import Path

import pytest


# =============================================================================
# Bare except audit
# =============================================================================

class TestNoBareExcepts:

    PROD_ROOT = Path(__file__).parent.parent / "kodosumi"

    def _find_bare_excepts(self):
        violations = []
        for py_file in self.PROD_ROOT.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            lines = py_file.read_text().splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped == "except:" or stripped.startswith("except: "):
                    violations.append(
                        f"{py_file.relative_to(self.PROD_ROOT)}:{i}: {stripped}")
        return violations

    def test_no_bare_excepts_in_codebase(self):
        violations = self._find_bare_excepts()
        assert violations == [], (
            f"Found {len(violations)} bare 'except:' — "
            f"use 'except Exception:' instead:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# =============================================================================
# Logger routing
# =============================================================================

class TestLoggerRouting:

    def test_expose_control_uses_kodo_logger(self):
        from kodosumi.service.expose import control
        assert control.logger.name == "kodo"

    def test_masumi_control_uses_kodo_logger(self):
        from kodosumi.service.masumi import control
        assert control.logger.name == "kodo"

    def test_boot_uses_kodo_logger(self):
        from kodosumi.service.expose import boot
        assert boot.logger.name == "kodo"


# =============================================================================
# Spooler reconcile: unexpected actor lookup errors must be logged
# Real scenario: GCS instability during OOM → ray.get_actor throws
# non-ValueError → reconciler silently skips all candidates
# =============================================================================

class TestSpoolerReconcileLogging:

    def test_reconcile_actor_unexpected_error_is_logged(self):
        """reconcile_payments: non-ValueError from ray.get_actor
        must emit a WARNING so GCS-instability-driven skips are visible."""
        import inspect
        from kodosumi import spooler

        source = inspect.getsource(spooler.Spooler.reconcile_payments)
        lines = source.split("\n")

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "except Exception:":
                next_lines = [l.strip() for l in lines[i+1:i+4] if l.strip()]
                if next_lines and next_lines[0] == "continue":
                    has_log = any(
                        "log" in l.lower() or "slog" in l.lower()
                        for l in lines[i+1:i+4]
                    )
                    assert has_log, (
                        "reconcile_payments has 'except Exception: continue' "
                        "without logging — unexpected actor errors (e.g. GCS "
                        "instability during OOM) are silently skipped"
                    )
