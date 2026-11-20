import dis, marshal, struct, sys, types, zlib
EXE, MOD, NAME = sys.argv[1:4]
data = open(EXE,'rb').read(); cp = data.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
_, plen, tpos, tlen, _, _ = struct.unpack('!8sIIII64s', data[cp:cp+88]); ps = cp+88-plen
toc, i, pyz = data[ps+tpos:ps+tpos+tlen], 0, None
while i < len(toc):
    el, ep, dl, ul, cf, tc = struct.unpack('!IIIIBc', toc[i:i+18])
    if tc in (b'z', b'Z'):
        b = data[ps+ep:ps+ep+dl]; pyz = zlib.decompress(b) if cf else b
    i += el
off, = struct.unpack('!i', pyz[8:12]); table = dict(marshal.loads(pyz[off:]))
_, pos, ln = table[MOD]; root = marshal.loads(zlib.decompress(pyz[pos:pos+ln]))
ins = [x for x in dis.get_instructions(root) if x.opname not in ('CACHE','RESUME','NOP')]
for k, x in enumerate(ins):
    if x.opname == 'STORE_NAME' and x.argval == NAME:
        for j in range(k-1, -1, -1):
            if ins[j].opname == 'LOAD_CONST' and isinstance(ins[j].argval, tuple):
                print(NAME, '->', ins[j].argval); break
        break
