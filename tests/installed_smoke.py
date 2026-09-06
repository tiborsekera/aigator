"""Run with the wheel-installed interpreter, outside the checkout."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

def run(*args, **kwargs):
    result = subprocess.run(*args, **kwargs, capture_output=True, text=True)
    assert result.returncode == 0, f"{result.args}\n{result.stdout}\n{result.stderr}"
    return result

with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    payload = temp / 'capture.json'
    payload.write_text(json.dumps({'source': 'perplexity', 'id': 'installed', 'turns': [{'author': 'user', 'text': 'wheelneedle'}]}))
    base = [sys.executable, '-m', 'aigator.cli', '--db', str(temp / 'index.db')]
    run(base + ['import', str(payload)], cwd=temp)
    result = run(base + ['search', 'wheelneedle'], cwd=temp)
    assert 'wheelneedle' in result.stdout
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    result = run(base + ['mcp'], input=json.dumps(request) + '\n', cwd=temp)
    assert 'aigator_search' in result.stdout
print('Installed wheel CLI import/search and MCP tools smoke passed.')
