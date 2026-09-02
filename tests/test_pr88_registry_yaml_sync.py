import shutil
import subprocess
from pathlib import Path

import pytest


def test_registry_state_guards_do_not_depend_on_node() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    registry = (repository_root / (
        'kodosumi/service/admin/templates/expose/_registry_script.html'
    )).read_text()
    dialogs = (repository_root / (
        'kodosumi/service/admin/templates/expose/_registry_dialog_script.html'
    )).read_text()
    dialog_markup = (repository_root / (
        'kodosumi/service/admin/templates/expose/_registry_dialogs.html'
    )).read_text()

    assert 'registrySyncBlocked' in registry
    assert "data.state === 'NotRegistered'" in registry
    assert 'staleUpdate.updatedYaml' in registry
    assert dialogs.count('meta_etag:') >= 4
    assert dialogs.count('blockRegistrySync(') >= 4
    assert dialog_markup.count('activeDialogIdx = null') >= 4


def test_registry_yaml_sync_javascript() -> None:
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is not installed')

    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, 'tests/test_pr88_registry_yaml_sync.js'],
        cwd=repository_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
