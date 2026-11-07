"""
LinkedIn visual renderer for n8n.

POST /render   JSON body:
  {
    "format":   "image" | "gif",
    "template": "compare" | "checklist" | "stat" | "steps",
    "data":     { ...template fields... },
    "author":   { "name": "Hanzala Masood", "title": "Automation Engineer" }
  }
Returns image/png (format=image) or image/gif (format=gif).
GET /health -> "ok"

Templates (all 1080x1080, black + neon green):
  compare   { tag, title_parts:[str,str,str], subtitle, lanes:[{title, tagline, nodes:[str..], use_for}], footnote }
  checklist { tag, headline, highlight, intro, items:[{ok:bool, text}], source }
  stat      { tag, big, headline, items:[{ok:bool, text}], source }
  steps     { tag, headline, highlight, steps:[{title, sub, out}], footnote }   (best as gif, works as image)
"""
import html, json, os, shutil, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("PORT", "8080"))
LOCK = threading.Lock()
LANE_COLORS = ["#39ff14", "#ffb020", "#4fd8ff"]

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1080px;overflow:hidden;background:#050805;color:#fff;
  font-family:'Inter','Helvetica Neue',Arial,sans-serif}
.card{position:relative;width:1080px;height:1080px;padding:64px 72px;
  background:radial-gradient(900px 600px at 85% 10%,rgba(57,255,20,.14),transparent 60%),
             radial-gradient(700px 500px at 5% 95%,rgba(57,255,20,.08),transparent 60%),#050805}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(57,255,20,.05) 1px,transparent 1px),
  linear-gradient(90deg,rgba(57,255,20,.05) 1px,transparent 1px);background-size:54px 54px}
.rel{position:relative}
.tag{display:inline-block;background:#39ff14;color:#041004;font-weight:800;font-size:26px;padding:8px 18px;border-radius:8px}
h1{font-weight:900;letter-spacing:-1.5px;line-height:1.05}
.g{color:#39ff14}
.sub{font-size:28px;color:#c9d6c9;line-height:1.35;font-weight:500}
ul{list-style:none}
li{display:flex;gap:20px;align-items:flex-start;font-size:31px;line-height:1.3;margin-bottom:22px;font-weight:600}
.ic{flex:0 0 44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:900}
.ok{background:#39ff14;color:#041004}.no{background:#ff3b3b;color:#fff}
.foot{position:absolute;left:72px;right:72px;bottom:44px;display:flex;justify-content:space-between;align-items:flex-end;
  gap:30px;font-size:21px;color:#8fa38f;line-height:1.35}
.foot b{color:#fff;font-size:26px;display:block}
.foot .src{max-width:600px}
"""

FONT = '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800;900&display=swap" rel="stylesheet">'


def e(s):
    return html.escape(str(s or ""))


def page(body, extra_css="", script=""):
    return (f"<!doctype html><html><head><meta charset='utf-8'>{FONT}<style>{CSS}{extra_css}</style></head>"
            f"<body><div class='card'><div class='grid'></div>{body}</div>{script}</body></html>")


def foot(left, author):
    return (f"<div class='foot'><div class='src'>{e(left)}</div><div style='text-align:right;flex:0 0 auto'>"
            f"<b>{e(author.get('name'))}</b>{e(author.get('title'))}</div></div>")


def li(it):
    ok = bool(it.get("ok", True))
    return (f"<li><span class='ic {'ok' if ok else 'no'}'>{'✓' if ok else '✕'}</span>"
            f"<span>{e(it.get('text'))}</span></li>")


# ---------- templates ----------
def t_checklist(d, a):
    items = (d.get("items") or [])[:6]
    return page(f"""
<div class='rel'><span class='tag'>{e(d.get('tag'))}</span>
<h1 style='font-size:72px;margin-top:30px'>{e(d.get('headline'))}<br><span class='g'>{e(d.get('highlight'))}</span></h1>
<p class='sub' style='margin-top:22px'>{e(d.get('intro'))}</p>
<ul style='margin-top:34px'>{''.join(li(i) for i in items)}</ul></div>
{foot(('Source: ' + d['source']) if d.get('source') else '', a)}""")


def t_stat(d, a):
    items = (d.get("items") or [])[:4]
    big = str(d.get("big", ""))
    size = 250 if len(big) <= 4 else 190 if len(big) <= 6 else 140
    return page(f"""
<div class='rel'><span class='tag'>{e(d.get('tag'))}</span>
<div style='font-size:{size}px;font-weight:900;line-height:.9;letter-spacing:-6px;color:#39ff14;margin-top:34px;
  text-shadow:0 0 40px rgba(57,255,20,.35)'>{e(big)}</div>
<h1 style='font-size:56px;margin-top:30px;letter-spacing:-1px'>{e(d.get('headline'))}</h1>
<ul style='margin-top:36px'>{''.join(li(i) for i in items)}</ul></div>
{foot(('Source: ' + d['source']) if d.get('source') else '', a)}""")


def t_compare(d, a):
    lanes = (d.get("lanes") or [])[:3]
    parts = (d.get("title_parts") or [l.get("title", "") for l in lanes])[:3]
    title = " <span style='color:#fff'>vs</span> ".join(
        f"<span style='color:{LANE_COLORS[i]}'>{e(p)}</span>" for i, p in enumerate(parts))
    lane_h = {1: 560, 2: 340, 3: 238}[max(1, len(lanes))]
    blocks = []
    for i, l in enumerate(lanes):
        c = LANE_COLORS[i]
        nodes = (l.get("nodes") or [])[:4]
        flow = []
        for j, n in enumerate(nodes):
            filled = j == 1 and len(nodes) > 2
            flow.append(f"<div class='node' style='border-color:{c};{'background:'+c+';color:#041004;' if filled else ''}'>{e(n)}</div>")
            if j < len(nodes) - 1:
                flow.append(f"<div class='arr' style='background:{c}'><i style='border-left-color:{c}'></i></div>")
        blocks.append(f"""
<div class='lane' style='height:{lane_h}px'>
 <div class='lh'><div class='lt' style='color:{c};border-color:{c}'>{e(l.get('title'))}</div><div class='ln' style='color:{c}'>{e(l.get('tagline'))}</div></div>
 <div class='flow'>{''.join(flow)}</div>
 <div class='use'><b>Use for:</b> {e(l.get('use_for'))}</div>
</div>""")
    css = """
.lane{position:relative;margin-top:20px;border-radius:18px;background:rgba(255,255,255,.035);border:1.5px solid rgba(255,255,255,.10);
  padding:18px 24px;display:flex;flex-direction:column;justify-content:space-between}
.lh{display:flex;justify-content:space-between;align-items:baseline;gap:20px}
.lt{font-size:32px;font-weight:900;padding-left:14px;border-left:6px solid}
.ln{font-size:20px;font-weight:700;text-align:right}
.flow{display:flex;align-items:center;gap:0}
.node{flex:1 1 0;min-width:0;border:2px solid;border-radius:12px;background:#0b120b;padding:16px 10px;text-align:center;
  font-size:21px;font-weight:800;line-height:1.2}
.arr{flex:0 0 46px;height:3px;position:relative;margin:0 6px}
.arr i{position:absolute;right:-2px;top:-7px;border:8px solid transparent;border-right:0;border-left:13px solid}
.use{font-size:20px;color:#a9b9a9}.use b{color:#fff}
"""
    return page(f"""
<div class='rel'><h1 style='text-align:center;font-size:52px'>{title}</h1>
<p class='sub' style='text-align:center;font-size:24px;margin-top:10px'>{e(d.get('subtitle'))}</p>
{''.join(blocks)}</div>
{foot(d.get('footnote', ''), a)}""", css)


STEPS_CSS = """
.steps{position:relative;margin-top:26px;display:flex;flex-direction:column;gap:10px}
.rail{position:absolute;left:35px;top:50px;width:4px;background:#1a231a;border-radius:2px}
.fill{position:absolute;left:35px;top:50px;width:4px;height:0;background:#39ff14;border-radius:2px;box-shadow:0 0 14px rgba(57,255,20,.7)}
.step{position:relative;display:grid;grid-template-columns:74px 300px 1fr;gap:18px;align-items:center;
  padding:0 22px 0 0;border-radius:16px;border:2px solid transparent;opacity:.28}
.step.on{opacity:1;border-color:#39ff14;background:rgba(57,255,20,.08);box-shadow:0 0 30px rgba(57,255,20,.18)}
.step.done{opacity:.9}
.num{width:74px;height:74px;border-radius:50%;background:#0b120b;border:3px solid #2a3a2a;display:flex;align-items:center;
  justify-content:center;font-weight:900;font-size:30px;position:relative;z-index:2}
.step.on .num,.step.done .num{background:#39ff14;color:#041004;border-color:#39ff14}
.t{font-size:28px;font-weight:800;line-height:1.15}.s{font-size:19px;color:#9fb29f;margin-top:4px}
.out{font-family:'DejaVu Sans Mono',Menlo,monospace;font-size:19px;line-height:1.35;color:#d8f5d0;background:#0b120b;
  border:1px solid #223322;border-radius:10px;padding:10px 16px;min-height:56px;white-space:pre-wrap;overflow:hidden}
.cur{display:inline-block;width:10px;height:22px;background:#39ff14;vertical-align:-3px;margin-left:2px}
"""

STEPS_JS = """
<script>
const STEPS=__STEPS__, START=0.6, STEP=__STEP__;
const box=document.getElementById('steps');
const H=__H__;
STEPS.forEach((s,i)=>{const d=document.createElement('div');d.className='step';d.style.height=H+'px';
 d.innerHTML=`<div class="num">${i+1}</div><div><div class="t"></div><div class="s"></div></div><div class="out"></div>`;
 d.querySelector('.t').textContent=s.title||'';d.querySelector('.s').textContent=s.sub||'';box.appendChild(d)});
const els=[...document.querySelectorAll('.step')],outs=[...document.querySelectorAll('.out')],fill=document.getElementById('fill'),rail=document.getElementById('rail');
const railH=(STEPS.length-1)*(H+10);rail.style.height=railH+'px';
window.render=function(t){STEPS.forEach((s,i)=>{const a=START+i*STEP,e=els[i],txt=s.out||'';
  e.classList.toggle('on',t>=a&&t<a+STEP);e.classList.toggle('done',t>=a+STEP);
  const n=Math.max(0,Math.min(txt.length,Math.floor((t-a)/1.0*txt.length)));
  outs[i].textContent=t<a?'':txt.slice(0,n);
  if(t>=a&&t<a+STEP&&n<txt.length){const c=document.createElement('span');c.className='cur';outs[i].appendChild(c)}});
 const p=Math.max(0,Math.min(1,(t-START)/(STEPS.length*STEP-0.3)));fill.style.height=(p*railH)+'px'};
window.TOTAL=START+STEPS.length*STEP+2.2;
render(window.__STATIC__?999:0);
</script>"""


def t_steps(d, a, static):
    steps = (d.get("steps") or [])[:6]
    n = max(1, len(steps))
    h = min(130, int((650 - 10 * (n - 1)) / n))
    js = (STEPS_JS.replace("__STEPS__", json.dumps(steps)).replace("__STEP__", "1.7")
          .replace("__H__", str(h)).replace("window.__STATIC__", "true" if static else "false"))
    return page(f"""
<div class='rel'><span class='tag'>{e(d.get('tag'))}</span>
<h1 style='font-size:58px;margin-top:20px'>{e(d.get('headline'))}<br><span class='g'>{e(d.get('highlight'))}</span></h1></div>
<div class='steps' id='steps'><div class='rail' id='rail'></div><div class='fill' id='fill'></div></div>
{foot(d.get('footnote', ''), a)}""", STEPS_CSS, js)


# ---------- rendering ----------
def render(req):
    fmt = req.get("format", "image")
    tpl = req.get("template", "checklist")
    d = req.get("data") or {}
    a = req.get("author") or {"name": "Hanzala Masood", "title": "Automation Engineer"}
    if tpl == "steps":
        doc = t_steps(d, a, static=(fmt != "gif"))
    else:
        fmt = "image"  # only the steps template animates
        doc = {"compare": t_compare, "checklist": t_checklist, "stat": t_stat}.get(tpl, t_checklist)(d, a)

    with LOCK, sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1080, "height": 1080})
        pg.set_content(doc, wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        if fmt != "gif":
            out = pg.screenshot(type="png")
            b.close()
            return out, "image/png"
        total = pg.evaluate("window.TOTAL")
        fps = 12
        tmp = tempfile.mkdtemp()
        try:
            for i in range(int(total * fps)):
                pg.evaluate(f"render({i / fps})")
                pg.screenshot(path=f"{tmp}/f{i:04d}.jpg", type="jpeg", quality=92)
            b.close()
            gif = f"{tmp}/out.gif"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{tmp}/f%04d.jpg",
                            "-vf", "scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];"
                                   "[b][p]paletteuse=dither=bayer:bayer_scale=4",
                            "-loop", "0", gif], check=True)
            return open(gif, "rb").read(), "image/gif"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, b"ok", "text/plain") if self.path == "/health" else self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/render":
            return self._send(404, b"not found", "text/plain")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            body, ctype = render(req)
            self._send(200, body, ctype)
        except Exception as ex:  # report the error to n8n instead of hanging
            self._send(500, json.dumps({"error": str(ex)}).encode(), "application/json")


if __name__ == "__main__":
    print(f"renderer listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
