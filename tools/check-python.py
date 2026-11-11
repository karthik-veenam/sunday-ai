#!/usr/bin/env python3
"""Parse without imports, pycache writes or runtime side effects."""
from pathlib import Path
import ast,sys
root=Path(__file__).resolve().parents[1];errors=[];count=0
for p in root.rglob('*.py'):
 if '.git' in p.parts:continue
 count+=1
 try:ast.parse(p.read_text(),filename=str(p))
 except (SyntaxError,UnicodeError) as e:errors.append((str(p.relative_to(root)),getattr(e,'lineno',None),type(e).__name__))
for row in errors:print(*row)
print(f'Parsed {count} Python files: {len(errors)} failures');sys.exit(bool(errors))
