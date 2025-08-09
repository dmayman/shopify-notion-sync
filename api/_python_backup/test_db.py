# api/test_db.py
from http.server import BaseHTTPRequestHandler
import json
from datetime import datetime
from lib.sync_storage import SyncDatabaseStorage

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Test Neon database connection and sync storage functionality"""
        try:
            print(f"[{datetime.now()}] Testing Neon database connection and sync storage")
            
            # Initialize database storage
            storage = SyncDatabaseStorage()
            
            # Test basic operations
            test_results = {}
            
            # Test 1: Get initial sync state
            print("🧪 Test 1: Getting sync state...")
            sync_state = storage.get_sync_state()
            test_results['initial_sync_state'] = {
                'last_sync': sync_state.get('last_sync'),
                'total_synced_orders': len(sync_state.get('synced_orders', {})),
                'failed_orders_count': len(sync_state.get('failed_orders', [])),
                'sync_in_progress': sync_state.get('sync_in_progress')
            }
            
            # Test 2: Test sync lock
            print("🧪 Test 2: Testing sync lock...")
            was_in_progress_before = storage.is_sync_in_progress()
            storage.start_sync_lock()
            is_in_progress_after = storage.is_sync_in_progress()
            storage.end_sync_lock()
            was_in_progress_after_end = storage.is_sync_in_progress()
            
            test_results['sync_lock_test'] = {
                'before_lock': was_in_progress_before,
                'after_lock': is_in_progress_after,
                'after_unlock': was_in_progress_after_end
            }
            
            # Test 3: Test order marking
            print("🧪 Test 3: Testing order operations...")
            test_order_id = f"test-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            test_page_ids = [f"page-{i}" for i in range(1, 4)]  # 3 test page IDs
            
            # Mark order as synced
            storage.mark_order_synced(test_order_id, test_page_ids, datetime.now().isoformat())
            
            # Get the page IDs back
            retrieved_page_ids = storage.get_synced_order_page_ids(test_order_id)
            
            # Mark order as failed (to test that functionality)
            storage.mark_order_failed(f"{test_order_id}-failed", "Test error message")
            
            # Get failed orders
            failed_orders = storage.get_failed_orders()
            
            test_results['order_operations'] = {
                'marked_order_id': test_order_id,
                'original_page_ids': test_page_ids,
                'retrieved_page_ids': retrieved_page_ids,
                'failed_orders_include_test': f"{test_order_id}-failed" in failed_orders
            }
            
            # Test 4: Get statistics
            print("🧪 Test 4: Getting sync statistics...")
            stats = storage.get_sync_statistics()
            test_results['sync_statistics'] = stats
            
            # Send response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "success",
                "message": "Neon database test completed successfully",
                "test_results": test_results,
                "timestamp": datetime.now().isoformat()
            }
            
            print("✅ All database tests passed!")
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"❌ Database test failed: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Database test failed: {str(e)}",
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