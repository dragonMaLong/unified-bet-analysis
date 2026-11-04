"""Group SMP files by subset-303 record version and embedded software string (read-only)."""
import json, re, struct, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tristar_bet.smp import TriStarSmpParser

def blocks(path):
    d = path.read_bytes(); p = TriStarSmpParser(); h = p._parse_header(path, d)
    return {s.subset_id: d[s.offset:s.offset + s.total_size] for s in p._parse_directory(d, h.directory_offset)}

def first_wstr(b, start=18, limit=400):
    m = re.search(rb'\xe0\x01\x00(.{4})((?:[\x20-\x7e]\x00)+)\x00\x00', b[start:start + limit], re.S)
    return m.group(2).decode('utf-16le') if m else None

def survey(path):
    try:
        bl = blocks(path)
    except Exception as e:
        return {'path': str(path), 'error': str(e)}
    out = {'path': str(path)}
    for sid in (301, 302, 303, 320):
        b = bl.get(sid, b'')
        out[f'v{sid}'] = b[18] if len(b) > 18 else None
    out['software303'] = first_wstr(bl.get(303, b''))
    b = bl.get(303, b'')
    out['row58_frames'] = len(re.findall(rb'\x05\x00\xe0\x00\x00\x00\x00\x00\x00\xf8\x7f\x04\x00', b))
    out['analysis_kinds'] = sorted(k for k in bl if k in range(400, 700))[:20]
    return out

if __name__ == '__main__':
    files = []
    for a in sys.argv[1:-1]:
        p = Path(a); files += [p] if p.is_file() else sorted(q for q in p.rglob('*') if q.suffix.lower() == '.smp')
    rows = [survey(f) for f in files]
    Path(sys.argv[-1]).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    c = collections.Counter((r.get('v303'), r.get('software303'), r.get('row58_frames', 0) > 0) for r in rows)
    for k, v in sorted(c.items(), key=str): print(v, k)
