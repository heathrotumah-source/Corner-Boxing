import functools
import http.server
import os
import socketserver

ROOT = "/Users/heath/Desktop/Corner"
PORT = int(os.environ.get("PORT", 8743))
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
with socketserver.TCPServer(("0.0.0.0", PORT), handler) as httpd:
    httpd.serve_forever()
