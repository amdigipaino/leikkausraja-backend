from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import io, base64

app = Flask(__name__)
CORS(app)

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        if not data or 'pdf' not in data:
            return jsonify({'error': 'No PDF data'}), 400

        pdf_bytes = base64.b64decode(data['pdf'])
        
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            page_w_mm = page.width / 2.8346
            page_h_mm = page.height / 2.8346

            # Get colorspace names from PDF resources
            cs_names = {}
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
                res = reader.pages[0].get('/Resources', {})
                if hasattr(res, 'get_object'): res = res.get_object()
                cs_res = res.get('/ColorSpace', {})
                if hasattr(cs_res, 'get_object'): cs_res = cs_res.get_object()
                for k, v in cs_res.items():
                    obj = v.get_object() if hasattr(v, 'get_object') else v
                    if isinstance(obj, list) and len(obj) > 1:
                        cs_names[k] = str(obj[1]).strip('/')
            except Exception as e:
                pass

            # Parse PDF stream for colored paths
            import pypdf as pypdf2
            reader2 = pypdf2.PdfReader(io.BytesIO(pdf_bytes))
            pg = reader2.pages[0]
            content = pg.get('/Contents')
            if hasattr(content, '__iter__') and not hasattr(content, 'get_data'):
                streams = [c.get_object() for c in content]
            else:
                streams = [content.get_object()]
            
            full = b''.join(s.get_data() for s in streams if hasattr(s, 'get_data'))
            text = full.decode('latin-1', errors='replace')
            lines = text.split('\n')

            page_h_pt = page.height
            cur_cs = 'default'
            cur_lw = 1.0
            pts = []
            groups = {}

            def flush():
                if len(pts) < 2:
                    pts.clear()
                    return
                key = cur_cs
                name = cs_names.get(cur_cs, cur_cs)
                if key not in groups:
                    groups[key] = {'name': name, 'paths': []}
                x0 = min(p[0] for p in pts)
                y0 = min(p[1] for p in pts)
                x1 = max(p[0] for p in pts)
                y1 = max(p[1] for p in pts)
                w = x1 - x0
                h = y1 - y0
                if w > 1 or h > 1:
                    groups[key]['paths'].append({
                        'x': round(x0 / 2.8346, 3),
                        'y': round(y0 / 2.8346, 3),
                        'w': round(w / 2.8346, 3),
                        'h': round(h / 2.8346, 3),
                        'lw': round(cur_lw, 3)
                    })
                pts.clear()

            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                cmd = parts[-1]
                args = parts[:-1]

                if cmd == 'w' and args:
                    try: cur_lw = float(args[0])
                    except: pass
                elif cmd == 'CS' and args:
                    flush()
                    cur_cs = args[0]
                elif cmd == 're' and len(args) >= 4:
                    flush()
                    try:
                        x, y, w, h = float(args[0]), float(args[1]), float(args[2]), float(args[3])
                        # PDF y is from bottom, convert to top-down
                        pts[:] = [
                            [x, page_h_pt - y - h],
                            [x + w, page_h_pt - y - h],
                            [x + w, page_h_pt - y],
                            [x, page_h_pt - y]
                        ]
                    except: pass
                elif cmd == 'm' and len(args) >= 2:
                    flush()
                    try: pts[:] = [[float(args[0]), page_h_pt - float(args[1])]]
                    except: pass
                elif cmd == 'l' and len(args) >= 2:
                    try: pts.append([float(args[0]), page_h_pt - float(args[1])])
                    except: pass
                elif cmd in ('S', 's'):
                    flush()
                elif cmd in ('n', 'Q'):
                    pts.clear()

            flush()

            result = []
            for key, g in groups.items():
                if g['paths']:
                    result.append({
                        'key': key,
                        'name': g['name'],
                        'paths': g['paths']
                    })

            return jsonify({
                'pageW': round(page_w_mm, 2),
                'pageH': round(page_h_mm, 2),
                'csNames': cs_names,
                'groups': result
            })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()[-500:]}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
