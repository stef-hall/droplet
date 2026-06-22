from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HOST = "127.0.0.1"
PORT = 8000

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

print(f"Serving on http://{HOST}:{PORT}")
print("For iPhone mic/speech testing, expose this with HTTPS using ngrok/cloudflare tunnel.")
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()