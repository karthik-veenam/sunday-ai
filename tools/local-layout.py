#!/usr/bin/env python3
"""Plan layout; --apply creates absent links only and never replaces live paths."""
from pathlib import Path
import argparse
p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args();r=Path(__file__).resolve().parents[1]
pairs={'brain':'/home/djpi/sunday2-rebuild','voice':'/home/djpi/sunday','audio':'/home/djpi/sunday-audio','wake/sherpa':'/home/djpi/sunday-sherpa-verifier','wake/diagnostics':'/home/djpi/sunday-verifier-notifications','wake/runtime':'/home/djpi/sunday-openwakeword-colab/full-suite-v1/runtime','wake/models/sunday_candidate_20261107':'/home/djpi/openwakeword-models/sunday_candidate_20261107','wake/models/sunday_v5_v6_filter':'/home/djpi/openwakeword-models/sunday_v5_v6_filter'}
if a.apply:
 conflicts=[target for rel,target in pairs.items() if Path(target).exists() or Path(target).is_symlink()]
 if conflicts:p.error('Refusing existing paths: '+', '.join(conflicts))
for rel,target in pairs.items():
 print(target+' -> '+str(r/rel))
 if a.apply:
  dest=Path(target);dest.parent.mkdir(parents=True,exist_ok=True);dest.symlink_to(r/rel,target_is_directory=True)
