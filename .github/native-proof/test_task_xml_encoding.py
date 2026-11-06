"""Exercise actual task-export write statements, without a task or gateway."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


class TaskXmlEncoding(unittest.TestCase):
    def test_export_statements_write_standards_parseable_bytes(self):
        pwsh = os.environ.get('PWSH') or shutil.which('pwsh')
        if pwsh is None:
            self.fail('PowerShell is required for this serialization test')
        source = Path(__file__).with_name('windows.ps1').read_text()
        statements = re.findall(r'^\s*\$taskDefinition(?:Before|After) \| Set-Content[^\r\n]+', source, re.M)
        self.assertEqual(len(statements), 2)
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['TASK_XML_TEST_DIR'] = tmp
            script = "$ErrorActionPreference='Stop'; $evidence=$env:TASK_XML_TEST_DIR; "
            script += "$taskDefinitionBefore='<?xml version=\"1.0\" encoding=\"UTF-16\"?><Task><Settings/><Actions>évidence</Actions></Task>'; "
            script += '$taskDefinitionAfter=$taskDefinitionBefore;\n' + '\n'.join(statements)
            subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-Command', script], env=env, check=True, capture_output=True)
            for name in ['before', 'after']:
                data = (Path(tmp) / f'task-definition-{name}.xml').read_bytes()
                root = ET.fromstring(data)
                self.assertEqual(root.findtext('Actions'), 'évidence')


if __name__ == '__main__':
    unittest.main()
