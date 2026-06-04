from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber, pypdf, io, base64

app = Flask(__name__)
CORS(app)

SPOT_COLORS = {
    'DK1': '#0055CC', 'DK2': '#CC0000', 'CREASE': '#00AA44',
    'Kiss Cut': '#0055CC', 'KissCut': '#0055CC', 'CUT1': '#CC0000',
    'Die Cut': '#0055CC', 'Perf': '#FF6600', 'Score': '#00AA44', 'ccd': '#000000',
}
DEFAULT_COLOR = '#FF0099'

def get_spot_color(name):
    for k, v in SPOT_COLORS.items():
        if k.lower() == name.lower() or name.lower() in k.lower() or k.lower() in name.lower():
            return v
    return DEFAULT_COLOR

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        if not data or 'pdf' not in data:
            return jsonify({'error': 'No PDF data'}), 400

        pdf_bytes = base64.b64decode(data['pdf'])

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            page_w_mm = round(page.width / 2.8346, 3)
            page_h_mm = round(page.height / 2.8346, 3)
            page_h_pt = page.height

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pg = reader.pages[0]

        cs_names = {}
        try:
            res = pg.get('/Resources', {})
            if hasattr(res, 'get_object'): res = res.get_object()
            cs_res = res.get('/ColorSpace', {})
            if hasattr(cs_res, 'get_object'): cs_res = cs_res.get_object()
            for k, v in cs_res.items():
                obj = v.get_object() if hasattr(v, 'get_object') else v
                if isinstance(obj, list) and len(obj) > 1:
                    cs_names[k] = str(obj[1]).strip('/')
        except: pass

        content_obj = pg.get('/Contents')
        if hasattr(content_obj, '__iter__') and not hasattr(content_obj, 'get_data'):
            streams = [c.get_object() for c in content_obj]
        else:
            streams = [content_obj.get_object()]
        full = b''.join(s.get_data() for s in streams if hasattr(s, 'get_data'))
        lines_list = full.decode('latin-1', errors='replace').split('\n')

        cur_cs = 'default'
        cur_lw = 1.0
        cur_seg = []
        groups = {}
        # CTM transform tracking
        ctm_stack = [(0.0, 0.0)]
        cur_tx, cur_ty = 0.0, 0.0

        def apply_t(x, y): return x + cur_tx, y + cur_ty

        def flush(cmd):
            if not cur_seg: return
            key = cur_cs
            name = cs_names.get(cur_cs, cur_cs)
            if key not in groups: groups[key] = {'name': name, 'paths': []}
            path_ops = []
            is_rect = len(cur_seg) == 1 and cur_seg[0][0] == 're'
            if is_rect:
                try:
                    x, y, w, h = [float(v) for v in cur_seg[0][1][:4]]
                    ax, ay = apply_t(x, y + abs(h))
                    path_ops = [{'op': 're', 'args': [
                        round(ax / 2.8346, 3), round((page_h_pt - ay) / 2.8346, 3),
                        round(abs(w) / 2.8346, 3), round(abs(h) / 2.8346, 3)
                    ]}]
                except: pass
            else:
                for op, args in cur_seg:
                    try:
                        vals = [float(v) for v in args]
                        if op in ('m', 'l') and len(vals) >= 2:
                            ax, ay = apply_t(vals[0], vals[1])
                            path_ops.append({'op': op, 'args': [round(ax/2.8346,3), round((page_h_pt-ay)/2.8346,3)]})
                        elif op == 'c' and len(vals) >= 6:
                            ax1,ay1 = apply_t(vals[0],vals[1]); ax2,ay2 = apply_t(vals[2],vals[3]); ax3,ay3 = apply_t(vals[4],vals[5])
                            path_ops.append({'op': 'c', 'args': [
                                round(ax1/2.8346,3), round((page_h_pt-ay1)/2.8346,3),
                                round(ax2/2.8346,3), round((page_h_pt-ay2)/2.8346,3),
                                round(ax3/2.8346,3), round((page_h_pt-ay3)/2.8346,3)
                            ]})
                        elif op == 'h':
                            path_ops.append({'op': 'h', 'args': []})
                    except: pass
            if path_ops:
                xs, ys = [], []
                for po in path_ops:
                    a = po['args']
                    if po['op'] == 're': xs += [a[0], a[0]+a[2]]; ys += [a[1], a[1]+a[3]]
                    elif po['op'] in ('m','l'): xs.append(a[0]); ys.append(a[1])
                    elif po['op'] == 'c': xs += [a[0],a[2],a[4]]; ys += [a[1],a[3],a[5]]
                bbox = {'x': round(min(xs),3), 'y': round(min(ys),3), 'w': round(max(xs)-min(xs),3), 'h': round(max(ys)-min(ys),3)} if xs and ys else None
                groups[key]['paths'].append({'ops': path_ops, 'bbox': bbox, 'lw': round(cur_lw/2.8346,3), 'isRect': is_rect, 'isCurve': any(po['op']=='c' for po in path_ops)})
            cur_seg.clear()

        for line in lines_list:
            parts = line.strip().split()
            if not parts: continue
            cmd = parts[-1]; args = parts[:-1]
            if cmd == 'w' and args:
                try: cur_lw = float(args[0])
                except: pass
            elif cmd == 'cm' and len(args) >= 6:
                try:
                    e, f = float(args[4]), float(args[5])
                    cur_tx += e; cur_ty += f
                except: pass
            elif cmd == 'q':
                ctm_stack.append((cur_tx, cur_ty))
            elif cmd == 'Q':
                cur_seg.clear()
                if len(ctm_stack) > 1: cur_tx, cur_ty = ctm_stack.pop()
                else: cur_tx, cur_ty = 0.0, 0.0
            elif cmd == 'CS' and args:
                flush(cmd); cur_cs = args[0]
            elif cmd in {'m','l','c','v','y','h','re'}:
                cur_seg.append((cmd, args))
            elif cmd in {'S','s','B','b','f','F','SCN','SC'}:
                flush(cmd)
            elif cmd == 'n':
                cur_seg.clear()
        flush('end')

        result = []
        for key, g in groups.items():
            filtered = [p for p in g['paths'] if p['bbox'] and (p['bbox']['w'] > 2 or p['bbox']['h'] > 2)]
            if filtered:
                name = g['name']
                result.append({'key': key, 'name': name, 'color': get_spot_color(name), 'paths': filtered})

        return jsonify({'pageW': page_w_mm, 'pageH': page_h_mm, 'csNames': cs_names, 'groups': result})

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()[-800:]}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'version': '4.0'})

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify({'version': '4.0', 'features': ['ctm', 'spot-colors', 'bezier']})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
