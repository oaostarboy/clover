"""Native Windows probe of the actual handoff writer and atomic update."""
import os
from agent import handoff_notes as hn


def test_real_windows_handoff_and_known(tmp_path, monkeypatch):
    assert os.name == 'nt'
    assert not hasattr(os, 'fchmod')
    monkeypatch.setattr(hn, 'notes_dir', lambda: tmp_path / 'private-parent' / 'handoff')
    path = hn.write_notes('native-proof', 'context to hand over')
    assert path is not None and path.read_text(encoding='utf-8') == 'context to hand over'
    assert hn.attach_known(path, ['safe status'])
    assert 'safe status' in path.read_text(encoding='utf-8')
    assert not list(path.parent.glob('*.tmp'))
