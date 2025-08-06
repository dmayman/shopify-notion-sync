"""
Vercel Blob storage for sync state management
Uses proven patterns from test_blob.py
"""

import json
import os
import requests
from datetime import datetime
from typing import Dict, List, Optional
import vercel_blob

class SyncBlobStorage:
    def __init__(self):
        self.blob_token = os.getenv('BLOB_READ_WRITE_TOKEN')
        if not self.blob_token:
            raise ValueError("BLOB_READ_WRITE_TOKEN environment variable is required")
        
        self.sync_state_filename = "sync-state.json"
        self.batch_mode = False
        self.cached_sync_state = None

    def _get_initial_sync_state(self) -> Dict:
        """Get initial sync state structure"""
        return {
            "last_sync": None,
            "synced_orders": {},
            "failed_orders": [],
            "last_processed_updated_at": None
        }

    def _read_sync_state_from_blob(self) -> Optional[Dict]:
        """Read sync state from existing blob using test_blob.py patterns"""
        try:
            print("📋 Listing existing blobs to find sync state...")
            blobs_response = vercel_blob.list()
            
            if 'blobs' in blobs_response:
                for blob in blobs_response['blobs']:
                    if blob.get('pathname') == self.sync_state_filename:
                        blob_url = blob.get('url') or blob.get('downloadUrl')
                        print(f"📖 Found sync state file: {blob_url}")
                        
                        try:
                            # Download the existing file content with retry logic
                            response = requests.get(blob_url, timeout=10)
                            response.raise_for_status()
                            content = response.text.strip()
                            
                            if not content:
                                print("📄 Blob exists but is empty, using initial state")
                                return None
                            
                            print(f"📄 Read sync state data: {len(content)} chars")
                            parsed_data = json.loads(content)
                            
                            # Validate the parsed data has required structure
                            if not isinstance(parsed_data, dict):
                                print("📄 Invalid sync state format, using initial state")
                                return None
                                
                            return parsed_data
                            
                        except requests.exceptions.RequestException as req_error:
                            print(f"📄 HTTP error reading blob: {req_error}")
                            if "403" in str(req_error) or "Forbidden" in str(req_error):
                                print("📄 Blob access forbidden - URL may have changed after write")
                                # Try to continue with a fresh list attempt (but not infinite retry)
                                return None
                            raise
                        
                        except json.JSONDecodeError as json_error:
                            print(f"📄 JSON parse error: {json_error}")
                            print(f"📄 Raw content: {content[:200]}...")
                            return None
            
            print("📄 No existing sync state found")
            return None
            
        except Exception as e:
            print(f"📄 Error reading sync state: {e}")
            return None

    def start_batch_mode(self):
        """Start batch mode - cache state in memory and defer writes"""
        if not self.batch_mode:  # Only read if not already in batch mode
            self.cached_sync_state = self._read_sync_state_from_blob()
            if self.cached_sync_state is None:
                print("📄 Using initial sync state for batch mode")
                self.cached_sync_state = self._get_initial_sync_state()
        self.batch_mode = True
        print("🔄 Started batch mode - deferring blob writes")

    def end_batch_mode(self):
        """End batch mode and write accumulated changes to blob"""
        if self.batch_mode and self.cached_sync_state:
            print("💾 Ending batch mode - writing all changes to blob")
            success = self.save_sync_state(self.cached_sync_state)
            self.batch_mode = False
            self.cached_sync_state = None
            return success
        return True

    def get_sync_state(self) -> Dict:
        """Get current sync state from blob storage or cache"""
        if self.batch_mode and self.cached_sync_state:
            return self.cached_sync_state
            
        sync_state = self._read_sync_state_from_blob()
        if sync_state is None:
            print("📄 Using initial sync state")
            return self._get_initial_sync_state()
        return sync_state

    def save_sync_state(self, sync_state: Dict) -> bool:
        """Save sync state to blob storage using test_blob.py patterns"""
        if self.batch_mode:
            # In batch mode, just update the cached state
            self.cached_sync_state = sync_state
            print("🔄 Updated cached sync state (batch mode)")
            return True
            
        try:
            json_content = json.dumps(sync_state, indent=2)
            print(f"💾 Writing sync state: {len(json_content)} chars")
            
            # Add a small delay to avoid rapid write-read cycles
            import time
            time.sleep(0.1)
            
            upload_result = vercel_blob.put(
                self.sync_state_filename, 
                json_content.encode('utf-8'),
                options={'allowOverwrite': True}
            )
            
            if upload_result and 'url' in upload_result:
                print(f"✅ Sync state saved! URL: {upload_result['url']}")
                
                # Add another small delay after write to allow blob to settle
                time.sleep(0.1)
                return True
            else:
                print(f"⚠️ Upload result: {upload_result}")
                return False
                
        except Exception as e:
            print(f"❌ Failed to save sync state: {e}")
            return False

    def get_last_sync(self) -> Optional[str]:
        """Get last successful sync timestamp"""
        sync_state = self.get_sync_state()
        return sync_state.get('last_sync')

    def get_failed_orders(self) -> List[str]:
        """Get list of failed order IDs"""
        sync_state = self.get_sync_state()
        return sync_state.get('failed_orders', [])

    def get_synced_order_page_id(self, order_id: str) -> Optional[str]:
        """Get Notion page ID for synced order"""
        sync_state = self.get_sync_state()
        synced_order = sync_state.get('synced_orders', {}).get(order_id)
        
        # Handle both old format (string) and new format (dict)
        if isinstance(synced_order, dict):
            return synced_order.get('notion_page_id')
        else:
            return synced_order  # String format (backward compatibility)

    def mark_order_synced(self, order_id: str, notion_page_id: str, updated_at: str = None):
        """Mark order as successfully synced with updatedAt timestamp"""
        sync_state = self.get_sync_state()
        
        # Store both page ID and updatedAt timestamp
        if updated_at:
            sync_state['synced_orders'][order_id] = {
                "notion_page_id": notion_page_id,
                "updated_at": updated_at
            }
            # Update resume point
            sync_state['last_processed_updated_at'] = updated_at
        else:
            # Fallback to simple string for backward compatibility
            sync_state['synced_orders'][order_id] = notion_page_id
        
        # Remove from failed orders if it was there
        if order_id in sync_state.get('failed_orders', []):
            sync_state['failed_orders'].remove(order_id)
        
        if self.save_sync_state(sync_state):
            print(f"✅ Marked order {order_id} as synced")
        else:
            print(f"❌ Failed to mark order {order_id} as synced")

    def mark_order_failed(self, order_id: str):
        """Mark order as failed to sync"""
        sync_state = self.get_sync_state()
        
        if order_id not in sync_state.get('failed_orders', []):
            sync_state.setdefault('failed_orders', []).append(order_id)
        
        if self.save_sync_state(sync_state):
            print(f"⚠️ Marked order {order_id} as failed")
        else:
            print(f"❌ Failed to mark order {order_id} as failed")

    def complete_sync(self, timestamp: str = None):
        """Mark sync as completed with current timestamp"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        sync_state = self.get_sync_state()
        sync_state['last_sync'] = timestamp
        
        if self.save_sync_state(sync_state):
            print(f"✅ Sync completed at {timestamp}")
        else:
            print(f"❌ Failed to mark sync as completed")

    def get_resume_timestamp(self) -> Optional[str]:
        """Get the timestamp to resume sync from"""
        sync_state = self.get_sync_state()
        return sync_state.get('last_processed_updated_at')

    def update_resume_point(self, updated_at: str):
        """Update the resume point timestamp"""
        sync_state = self.get_sync_state()
        sync_state['last_processed_updated_at'] = updated_at
        
        if self.save_sync_state(sync_state):
            print(f"📍 Updated resume point to {updated_at}")
        else:
            print(f"❌ Failed to update resume point")

    def get_sync_statistics(self) -> Dict:
        """Get sync statistics"""
        sync_state = self.get_sync_state()
        
        return {
            'last_sync': sync_state.get('last_sync'),
            'total_synced_orders': len(sync_state.get('synced_orders', {})),
            'failed_orders_count': len(sync_state.get('failed_orders', [])),
            'failed_orders': sync_state.get('failed_orders', []),
            'last_processed_updated_at': sync_state.get('last_processed_updated_at')
        }