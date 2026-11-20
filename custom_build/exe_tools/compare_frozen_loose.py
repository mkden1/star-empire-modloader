"""Read-only: compare each game module frozen inside Client.exe with the loose
.py of the same name (compiled with the same Python), by bytecode+constants."""
import marshal, struct, sys, types, zlib, os
sys.path.insert(0, os.path.dirname(__file__))
EXE, LOOSE = sys.argv[1], sys.argv[2]
data = open(EXE, 'rb').read()
cp = data.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
_, plen, tpos, tlen, _, _ = struct.unpack('!8sIIII64s', data[cp:cp+88])
ps = cp + 88 - plen
toc, i, pyz = data[ps+tpos:ps+tpos+tlen], 0, None
while i < len(toc):
    el, ep, dl, ul, cf, tc = struct.unpack('!IIIIBc', toc[i:i+18])
    if tc in (b'z', b'Z'):
        blob = data[ps+ep:ps+ep+dl]; blob = zlib.decompress(blob) if cf else blob; pyz = blob
    i += el
off, = struct.unpack('!i', pyz[8:12]); table = marshal.loads(pyz[off:])
table = dict(table) if not isinstance(table, dict) else table
def sig(co):
    return (co.co_code, co.co_names, tuple(sig(c) if isinstance(c, types.CodeType) else c for c in co.co_consts))
same, diff, missing = [], [], []
for f in sorted(os.listdir(LOOSE)):
    if not f.endswith('.py') or f.startswith('test_'): continue
    m = f[:-3]
    if m not in table: missing.append(m); continue
    ispkg, pos, ln = table[m]
    frozen = marshal.loads(zlib.decompress(pyz[pos:pos+ln]))
    try: loose = compile(open(os.path.join(LOOSE, f), 'rb').read(), f, 'exec')
    except Exception as e: diff.append((m, f'compile error {e}')); continue
    (same if sig(frozen) == sig(loose) else diff).append(m if sig(frozen) == sig(loose) else (m, 'differs'))
print(f"loose game modules checked: {len(same)+len(diff)}  identical-to-frozen: {len(same)}  differ: {len(diff)}  not-frozen: {len(missing)}")
print("DIFFER:", [d[0] if isinstance(d, tuple) else d for d in diff])
