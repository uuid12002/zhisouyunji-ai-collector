"""Validate untrusted onboarding config without touching real user settings."""
import json
import os
from pathlib import Path
import tempfile
import sys
import types

with tempfile.TemporaryDirectory() as temp:
    os.environ['COLLECTOR_CLIENT_HOME']=temp
    import desktop_client as client
    incoming=Path(temp)/'incoming.json'
    good={'server_url':'https://collector.example.com:443','device_id':'test-device',
          'device_key':'x'*32,'auto_start':True,'command':'do not execute','slots':999}
    incoming.write_text(json.dumps(good),encoding='utf-8')
    imported=client.import_connection(incoming)
    assert imported['auto_start'] is False and imported['slots']==1
    assert 'command' not in imported
    original=client.CONFIG.read_bytes()
    for url in ['http://public.example.com','https://host:99999','https://host:0','file:///tmp/file','https://user:pass@host','https://host/path']:
        incoming.write_text(json.dumps(good|{'server_url':url}),encoding='utf-8')
        try:
            client.import_connection(incoming)
            raise AssertionError('Invalid URL accepted: '+url)
        except ValueError:
            pass
        assert client.CONFIG.read_bytes()==original
    assert client.validate_config(imported)['server_url'].endswith(':443')
    assert client.validate_config(imported | {'platforms':['doubao','deepseek','yuanbao','qianwen']})['platforms']==['doubao','deepseek','yuanbao','qianwen']
    calls=[]
    class Key:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    stub=types.SimpleNamespace(HKEY_CURRENT_USER=1,REG_SZ=1,CreateKey=lambda *args:Key(),
        SetValueEx=lambda *args:calls.append(('set',args[-1])),DeleteValue=lambda *args:calls.append(('delete',args[-1])))
    original_registry=sys.modules.get('winreg')
    sys.modules['winreg']=stub
    try:
        client.set_startup(True)
        client.set_startup(False)
        assert calls[0][0]=='set' and '--autostart' in calls[0][1]
        assert calls[1]==('delete','WebCollector')
    finally:
        if original_registry is None:sys.modules.pop('winreg',None)
        else:sys.modules['winreg']=original_registry
    print('PASS: onboarding import; allowed fields only; no implicit autostart; HTTPS and port validation; invalid import preserves old configuration')
