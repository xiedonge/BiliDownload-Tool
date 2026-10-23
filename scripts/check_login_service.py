"""Opt-in live check; generates an unused QR ticket, never prints it or credentials."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
from account_login import LoginClient

client = LoginClient()
ticket = client.generate()
state, _ = client.poll(ticket['key'])
public = client.verify()
assert state == 'waiting'
assert not public['logged_in']
print(json.dumps({'generate': 'ok', 'poll': state, 'anonymous_validation': 'ok',
                  'authenticated_download': 'requires_user_scan'}, ensure_ascii=False))
