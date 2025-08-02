# api/sync.py
from http.server import BaseHTTPRequestHandler
import json
import urllib.parse

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests for testing"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        response = {
            "status": "success",
            "message": "Shopify-Notion sync endpoint is working!",
            "method": "GET",
            "static_value": "Hello from Vercel! 🚀"
        }
        self.wfile.write(json.dumps(response, indent=2).encode())

    def do_POST(self):
        """Handle POST requests from Notion"""
        try:
            # Get the content length to read the request body
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            # Try to parse JSON data if present
            request_data = {}
            if post_data:
                try:
                    request_data = json.loads(post_data.decode('utf-8'))
                except json.JSONDecodeError:
                    request_data = {"raw_data": post_data.decode('utf-8')}
            
            # This is where we'll add Shopify GraphQL logic later
            # For now, return static data
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "success",
                "message": "Sync completed successfully! ✅",
                "static_value": "This will be replaced with real Shopify data",
                "timestamp": "2025-08-02T12:00:00Z",
                "records_processed": 42,
                "received_data": request_data
            }
            
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Sync failed: {str(e)}",
                "static_value": "Error occurred"
            }
            self.wfile.write(json.dumps(error_response, indent=2).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()