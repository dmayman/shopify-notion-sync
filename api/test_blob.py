# api/test_blob.py
from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime
from vercel_blob import put, get

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Test Vercel Blob storage with simple timestamp read/write"""
        try:
            print(f"[{datetime.now()}] Testing Vercel Blob storage")
            
            # Get environment variables
            blob_token = os.getenv('BLOB_READ_WRITE_TOKEN')
            if not blob_token:
                raise ValueError("BLOB_READ_WRITE_TOKEN environment variable is required")
            
            filename = "test-timestamp.txt"
            current_time = datetime.now().isoformat()
            
            # Try to read existing file first
            existing_data = None
            try:
                # Check if we have a stored URL from previous runs
                existing_url = os.getenv('TEST_BLOB_URL')
                if existing_url:
                    print(f"📖 Trying to read existing file from: {existing_url}")
                    result = get(url=existing_url)
                    if result and result.body:
                        existing_data = result.body.read().decode('utf-8')
                        print(f"📄 Found existing data: {existing_data}")
            except Exception as e:
                print(f"📄 No existing file found or error reading: {e}")
            
            # Write new timestamp
            print(f"💾 Writing new timestamp: {current_time}")
            upload_result = put(filename, current_time, token=blob_token)
            
            if hasattr(upload_result, 'url'):
                new_url = upload_result.url
                print(f"✅ Upload successful! New URL: {new_url}")
                
                # Store the URL for next time (in a real app, you'd persist this properly)
                os.environ['TEST_BLOB_URL'] = new_url
            else:
                new_url = "No URL returned"
            
            # Send response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "success",
                "message": "Vercel Blob test completed",
                "previous_timestamp": existing_data,
                "new_timestamp": current_time,
                "blob_url": new_url,
                "timestamp": datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"❌ Blob test failed: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Blob test failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(error_response, indent=2).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()