# api/test_blob.py
from http.server import BaseHTTPRequestHandler
import json
import os
import requests
from datetime import datetime
import vercel_blob

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
            existing_url = None
            
            try:
                # List existing blobs to find our test file
                print("📋 Listing existing blobs...")
                blobs_response = vercel_blob.list()
                
                if 'blobs' in blobs_response:
                    for blob in blobs_response['blobs']:
                        if blob.get('pathname') == filename:
                            existing_url = blob.get('url') or blob.get('downloadUrl')
                            print(f"📖 Found existing file: {existing_url}")
                            break
                
                if existing_url:
                    # Download the existing file content
                    response = requests.get(existing_url)
                    response.raise_for_status()
                    existing_data = response.text
                    print(f"📄 Found existing data: {existing_data}")
                    
            except Exception as e:
                print(f"📄 No existing file found or error reading: {e}")
            
            # Write new timestamp (allow overwrite for testing)
            print(f"💾 Writing new timestamp: {current_time}")
            upload_result = vercel_blob.put(filename, current_time.encode('utf-8'), options={'allowOverwrite': True})
            
            if upload_result and 'url' in upload_result:
                new_url = upload_result['url']
                print(f"✅ Upload successful! New URL: {new_url}")
            else:
                new_url = "No URL returned"
                print(f"Upload result: {upload_result}")
            
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