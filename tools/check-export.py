#!/usr/bin/env python3
"""Offline privacy/packaging checks; no runtime imports or device actions."""
from pathlib import Path
import re,sys
root=Path(__file__).resolve().parents[1];errors=[];files=0
forbidden={'config.json','secrets.h','debug_telegram.json','hosts.yml','.env','token'}
patterns=[r'\bgh[pousr]_[A-Za-z0-9]{30,}\b',r'\b\d{8,12}:[A-Za-z0-9_-]{30,50}\b',r'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}\b',r'\bAIza[0-9A-Za-z_-]{30,}\b',r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b']
for p in root.rglob('*'):
 if '.git' in p.parts or not p.is_file():continue
 files+=1;rel=str(p.relative_to(root))
 if p.name in forbidden or any(x in rel.split('/') for x in ['__pycache__','.pio','.venv','node_modules']):errors.append((rel,'forbidden file/path'))
 if re.search(r'\.(?:db(?:-wal|-shm)?|sqlite3?|jsonl|bin|elf|gguf)$',p.name):errors.append((rel,'runtime/binary data'))
 if p.suffix=='.onnx':continue
 try:s=p.read_text()
 except UnicodeError:continue
 for pat in patterns:
  if re.search(pat,s):errors.append((rel,'credential-like value'))
for rel,reason in errors:print(rel+': '+reason)
print(f'Checked {files} files: {len(errors)} failures');sys.exit(bool(errors))
