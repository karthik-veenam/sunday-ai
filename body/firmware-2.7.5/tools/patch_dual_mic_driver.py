"""Idempotent private-cache patch. Run before rebuilding after library restoration."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'.pio/libdeps/stackchan/M5Unified/src/utility/Mic_Class.inl'
s=p.read_text()
if '        if (!self->_cfg.stereo) {\n        auto value_tmp' in s:
 print('Dual-mic private driver guard already present')
else:
 a=s.index('        auto value_tmp = (sv0 + sv1) << 3;');b=s.index('        sum_value[0] = sv0 + offset;',a)
 original=s[a:b];assert 'int32_t offset = self->_offset;' in original
 s=s[:a]+'        int32_t offset = 0;\n        if (!self->_cfg.stereo) {\n'+original.replace('int32_t offset = self->_offset;','offset = self->_offset;')+'        }\n'+s[b:]
 p.write_text(s);print('Applied private stereo-only DC guard; mono branch preserved')
