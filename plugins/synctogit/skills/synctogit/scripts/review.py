#!/usr/bin/env python3
"""One loopback review page: image previews, a checkbox per file, one final action.

Serves only files named in the plan. Accepts no path or command from the page.
Writes the user's selections to --out and exits. Cancelling or closing writes a cancel.
"""
import argparse, html, json, os, secrets, socket, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"token": None, "plan": None, "assets": {}, "result": None, "used": False}

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e3e0;--card:#fff;--ok:#0a7d33}
@media(prefers-color-scheme:dark){:root{--bg:#17181a;--fg:#ececed;--mut:#9b9ba1;
--line:#2e3033;--card:#1f2124;--ok:#4ad06a}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,sans-serif;
margin:0;padding:28px}
main{max-width:860px;margin:0 auto}
h1{font-size:18px;margin:0 0 2px}
.sub{color:var(--mut);margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
margin-bottom:12px}
.msg{font-weight:600;margin:10px 0 18px}
label{display:flex;gap:10px;align-items:flex-start;padding:5px 0}
input[type=checkbox]{margin-top:4px;width:16px;height:16px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
.pair figure{margin:0}.pair img{width:100%;border:1px solid var(--line);border-radius:6px;
background:#fff;cursor:zoom-in}
.pair figcaption{color:var(--mut);font-size:12px;margin-top:4px}
.zoom img{cursor:zoom-out;max-width:none;width:auto}
.reason{color:var(--mut)}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px;position:sticky;bottom:0;
background:var(--bg);padding:14px 0}
button{font:inherit;padding:9px 15px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:600}
button:focus-visible{outline:2px solid #4c8dff;outline-offset:2px}
table{border-collapse:collapse;width:100%}td{padding:3px 8px 3px 0;vertical-align:top}
code{font-size:12px}
"""

JS = """
document.addEventListener('click',e=>{
  if(e.target.tagName==='IMG'){e.target.closest('figure').classList.toggle('zoom');}
});
function send(action){
  const files=[...document.querySelectorAll('.f:checked')].map(c=>c.value);
  const convs=[...document.querySelectorAll('.c:checked')].map(c=>c.value);
  const arch=[...document.querySelectorAll('.a:checked')].map(c=>c.value);
  if(action==='approve_all'){document.querySelectorAll('.c').forEach(c=>c.checked=true);}
  const body={action,files,
    conversions:action==='approve_all'?[...document.querySelectorAll('.c')].map(c=>c.value):convs,
    archive_consent:arch};
  fetch(location.pathname+'submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(()=>{
      document.body.innerHTML='<main><h1>Sent back to Claude Code.</h1>'+
      '<p class=sub>You can close this tab.</p></main>';});
}
window.addEventListener('beforeunload',()=>{
  if(!window.__sent){navigator.sendBeacon(location.pathname+'submit',
    JSON.stringify({action:'cancel'}));}
});
"""


def esc(s):
    return html.escape(str(s), quote=True)


def human(n):
    if n is None:
        return "—"
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else "%d KB" % max(1, n // 1000)


def page(plan, token):
    p, out = plan, []
    out.append("<main><h1>%s</h1><div class=sub>Local folder: <code>%s</code><br>"
               "Branch %s → %s</div>" % (esc(p.get("repository", "(no remote)")),
                                         esc(p.get("root", "")), esc(p.get("branch", "?")),
                                         esc(p.get("upstream") or "no upstream")))
    out.append("<div class=msg>%s</div>" % esc(p.get("message", "")))

    convs = p.get("conversions", [])
    if convs:
        out.append("<div class=card><b>%d image%s to approve</b> — nothing is selected until "
                   "you choose. Click a preview to zoom.</div>" % (len(convs),
                                                                   "" if len(convs) == 1 else "s"))
        for i, c in enumerate(convs):
            out.append("<div class=card><label><input type=checkbox class=c value='%s'>"
                       "<span><b>%s</b> → <code>%s</code><br><span class=reason>%s → %s"
                       "%s</span></span></label>" % (
                           esc(c["src"]), esc(c["src"]), esc(c.get("output", "")),
                           human(c.get("source_size")), human(c.get("output_bytes")),
                           (" · %s → %s px" % ("×".join(map(str, c.get("source_dims", []))),
                                               "×".join(map(str, c.get("output_dims", [])))))
                           if c.get("source_dims") else ""))
            if c.get("preview_id") or c.get("original_id"):
                out.append("<div class=pair>")
                for label, key in (("Original", "original_id"), ("Proposed", "preview_id")):
                    if c.get(key):
                        out.append("<figure><img loading=lazy src='/%s/asset/%s' alt='%s of %s'>"
                                   "<figcaption>%s</figcaption></figure>"
                                   % (token, esc(c[key]), esc(label), esc(c["src"]), esc(label)))
                    else:
                        out.append("<figure><figcaption>%s: preview unavailable</figcaption>"
                                   "</figure>" % esc(label))
                out.append("</div>")
            out.append("</div>")

    files = p.get("files", [])
    if files:
        out.append("<div class=card><b>Files to upload</b> — untick anything you want to leave "
                   "out of this sync.")
        for f in files:
            out.append("<label><input type=checkbox class=f value='%s' checked>"
                       "<span><code>%s</code> <span class=reason>%s%s</span></span></label>"
                       % (esc(f["path"]), esc(f["path"]), esc(f.get("what", "")),
                          " · " + human(f.get("size")) if f.get("size") else ""))
        out.append("</div>")

    for mv in p.get("archive_moves", []):
        out.append("<div class=card><b>Archive move</b> — this is a separate decision from the "
                   "sync.<label><input type=checkbox class=a value='%s'><span>Move "
                   "<code>%s</code> → <code>%s</code>. The original is preserved, never "
                   "uploaded. Leaving this unticked publishes nothing for this file.</span>"
                   "</label></div>" % (esc(mv["src"]), esc(mv["src"]), esc(mv["dest"])))

    exc = p.get("excluded", [])
    if exc:
        out.append("<details class=card><summary><b>%d excluded</b> — every file and its "
                   "reason</summary><table>" % len(exc))
        for e in exc:
            out.append("<tr><td><code>%s</code></td><td class=reason>%s</td></tr>"
                       % (esc(e["path"]), esc(e.get("reason", ""))))
        out.append("</table>")
        if any(e.get("category") in ("experimental", "data-binary", "media", "archive")
               for e in exc):
            out.append("<p class=reason>Large files and important experimental data should be "
                       "saved directly to the lab server.</p>")
        if any(e.get("category") == "powerpoint" for e in exc):
            out.append("<p class=reason><b>Managed locally only</b> — PowerPoint stays on "
                       "your machine, and is not sent to the lab server either.</p>")
        out.append("</details>")

    out.append("<div class=actions>")
    if convs:
        out.append("<button class=primary onclick=\"window.__sent=1;send('approve_all')\">"
                   "Approve all &amp; sync</button>"
                   "<button onclick=\"window.__sent=1;send('approve_selected')\">"
                   "Approve selected &amp; sync</button>"
                   "<button onclick=\"window.__sent=1;send('no_conversions')\">"
                   "Sync without these conversions</button>")
    else:
        out.append("<button class=primary onclick=\"window.__sent=1;send('approve_selected')\">"
                   "Confirm sync</button>")
    out.append("<button onclick=\"window.__sent=1;send('cancel')\">Cancel</button></div></main>")
    return ("<title>synctogit review</title><style>%s</style>%s<script>%s</script>"
            % (CSS, "".join(out), JS))


class Server(ThreadingHTTPServer):
    # handler threads must never keep the process alive after the approval is captured
    daemon_threads = True
    block_on_close = False


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _bad(self, code=404):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        t = STATE["token"]
        if self.path in ("/%s/" % t, "/%s" % t):
            body = page(STATE["plan"], t).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        prefix = "/%s/asset/" % t
        if self.path.startswith(prefix):
            aid = self.path[len(prefix):]
            path = STATE["assets"].get(aid)      # id -> path, from the plan only
            if not path or not os.path.isfile(path):
                return self._bad()
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._bad()

    def do_POST(self):
        if self.path != "/%s/submit" % STATE["token"]:
            return self._bad()
        origin = self.headers.get("Origin")
        if origin and "127.0.0.1" not in origin and "localhost" not in origin:
            return self._bad(403)
        if STATE["used"]:
            return self._bad(409)                # single use
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return self._bad(400)
        STATE["used"] = True
        STATE["result"] = {
            "action": str(body.get("action", "cancel")),
            "files": [str(x) for x in body.get("files", [])],
            "conversions": [str(x) for x in body.get("conversions", [])],
            "archive_consent": [str(x) for x in body.get("archive_consent", [])],
        }
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    STATE["plan"] = plan
    STATE["token"] = secrets.token_urlsafe(24)
    for c in plan.get("conversions", []):
        for key, src in (("original_id", c.get("original_path")),
                         ("preview_id", c.get("staged"))):
            if src and os.path.isfile(src):
                aid = secrets.token_urlsafe(10)
                STATE["assets"][aid] = src
                c[key] = aid

    srv = Server(("127.0.0.1", 0), H)
    port = srv.socket.getsockname()[1]
    url = "http://127.0.0.1:%d/%s/" % (port, STATE["token"])
    print(json.dumps({"url": url}), flush=True)
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    timer = threading.Timer(a.timeout, lambda: threading.Thread(target=srv.shutdown,
                                                                daemon=True).start())
    timer.daemon = True          # never hold the process open waiting for the timeout
    timer.start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    timer.cancel()
    result = STATE["result"] or {"action": "cancel",
                                 "note": "page closed or timed out: nothing is approved"}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
