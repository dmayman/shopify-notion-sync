# api/sync.py
from http.server import BaseHTTPRequestHandler
import json
import datetime
import sys
import os

# Add the current directory to Python path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests for testing connections"""
        print(f"[{datetime.datetime.now()}] GET request received - Testing connections")
        
        try:
            from shopify_notion_sync import ShopifyNotionSync
            
            # Test connections
            sync = ShopifyNotionSync()
            test_results = sync.test_connections()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "connection_test",
                "message": "Testing Shopify and Notion connections",
                "results": test_results,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            print(f"Connection test results: {test_results}")
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"Connection test failed: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Connection test failed: {str(e)}",
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(error_response, indent=2).encode())

    def do_POST(self):
        """Handle POST requests from Notion - Perform actual sync"""
        try:
            print(f"[{datetime.datetime.now()}] POST request received - Starting sync")
            
            # Get request data
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b''
            
            request_data = {}
            if post_data:
                try:
                    request_data = json.loads(post_data.decode('utf-8'))
                    print(f"Request data: {request_data}")
                except json.JSONDecodeError:
                    print("No JSON data in request")
            
            # Import and run sync
            from shopify_notion_sync import ShopifyNotionSync
            
            sync = ShopifyNotionSync()
            
            # Get sync limit from request or default to 5
            sync_limit = request_data.get('limit', 5)
            
            # Perform the sync
            sync_results = sync.sync_orders_to_notion(limit=sync_limit)
            
            # Send response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "sync_completed",
                "message": "🚀 Shopify → Notion sync completed!",
                "sync_results": sync_results,
                "request_info": {
                    "limit": sync_limit,
                    "source": "Notion Button" if 'notion' in self.headers.get('User-Agent', '').lower() else "Direct API"
                },
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            print(f"Sync completed successfully: {sync_results}")
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"Sync error: {e}")
            print(f"Error type: {type(e).__name__}")
            
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Sync failed: {str(e)}",
                "error_type": type(e).__name__,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(error_response, indent=2).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        print(f"[{datetime.datetime.now()}] OPTIONS request received (CORS)")
        
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()