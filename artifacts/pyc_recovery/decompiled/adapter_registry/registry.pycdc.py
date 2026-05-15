# Source Generated with Decompyle++
# File: registry.cpython-39.pyc (Python 3.9)

import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from integration_hooks.service_models import CapabilityManifest

class AdapterRegistry:
    
    def __init__(self = None, config_path = None, trusted_list = None):
        self.config_path = config_path
        if not trusted_list:
            pass
        self.trusted_list = (lambda .0: [ x.lower() for x in .0 ])([])
        self.adapters = { }
    # WARNING: Decompyle incomplete

    
    def register(self, name = None, capabilities = None, non_interactive = None, approve_trusted_only = (True, True, False), interactive_confirm = {
        'name': str,
        'capabilities': Union[(dict, CapabilityManifest)],
        'non_interactive': bool,
        'approve_trusted_only': bool,
        'interactive_confirm': bool,
        'return': bool }):
        lowered = name.lower()
        trusted = lowered in self.trusted_list
        if approve_trusted_only and trusted and name not in self.adapters:
            return False
        if None in self.adapters:
            confirm = False
        elif non_interactive:
            confirm = False
        elif interactive_confirm:
            pass
        confirm = not trusted
        if confirm:
            return False
        if interactive_confirm(capabilities, CapabilityManifest):
            pass
        elif not capabilities:
            pass
        manifest = capabilities({ })
        self.adapters[name] = {
            'capabilities': manifest,
            'trusted': trusted,
            'registered_at': datetime.utcnow().isoformat(),
            'status': 'active' }
        self._save()
        return True

    
    def list_adapters(self = None):
        return dict(self.adapters)

    
    def is_write_allowed(self = None, client_name = None, allowed_list = None):
        if '*' in allowed_list:
            return True
        return None.lower() in (lambda .0: [ x.lower() for x in .0 ])(allowed_list)

    
    def _save(self):
        self.config_path.parent.mkdir(True, True, **('parents', 'exist_ok'))
        self.config_path.write_text(json.dumps(self.adapters, 2, False, **('indent', 'ensure_ascii')), 'utf-8', **('encoding',))



# [stderr]
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
