"""Read-only: per-function delta between frozen (new exe) and loose (old) source."""
import marshal, struct, sys, types, zlib, os
EXE, LOOSE = sys.argv[1], sys.argv[2]; MODS = sys.argv[3:]
data = open(EXE,'rb').read(); cp = data.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
_, plen, tpos, tlen, _, _ = struct.unpack('!8sIIII64s', data[cp:cp+88]); ps = cp+88-plen
toc, i, pyz = data[ps+tpos:ps+tpos+tlen], 0, None
while i < len(toc):
    el, ep, dl, ul, cf, tc = struct.unpack('!IIIIBc', toc[i:i+18])
    if tc in (b'z', b'Z'):
        b = data[ps+ep:ps+ep+dl]; pyz = zlib.decompress(b) if cf else b
    i += el
off, = struct.unpack('!i', pyz[8:12]); table = dict(marshal.loads(pyz[off:]))
def sig(co): return (co.co_code, co.co_names, tuple(sig(c) if isinstance(c, types.CodeType) else c for c in co.co_consts))
def funcs(co, out):
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            out[c.co_qualname] = sig(c); funcs(c, out)
    return out
for m in MODS:
    _, pos, ln = table[m]
    fz = funcs(marshal.loads(zlib.decompress(pyz[pos:pos+ln])), {})
    lz = funcs(compile(open(os.path.join(LOOSE, m + '.py'), 'rb').read(), m, 'exec'), {})
    added = sorted(set(fz) - set(lz)); removed = sorted(set(lz) - set(fz))
    changed = sorted(q for q in set(fz) & set(lz) if fz[q] != lz[q])
    print(f"\n=== {m}: functions frozen={len(fz)} loose={len(lz)}  ADDED {len(added)}  REMOVED {len(removed)}  CHANGED {len(changed)}")
    for label, names in (('ADDED', added), ('REMOVED', removed), ('CHANGED', changed)):
        if names: print(f"  {label}: " + ", ".join(n for n in names if '<genexpr>' not in n and '<lambda>' not in n and '<listcomp>' not in n)[:1800])
