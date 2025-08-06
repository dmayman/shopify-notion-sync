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
            "last_processed_updated_at": None,
            "sync_in_progress": False,
            "sync_started_at": None
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
            
            # Save the state before disabling batch mode
            state_to_save = self.cached_sync_state.copy()
            
            # Disable batch mode first so save_sync_state writes to blob
            self.batch_mode = False
            self.cached_sync_state = None
            
            # Now actually write to blob
            success = self.save_sync_state(state_to_save)
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
            
            upload_result = vercel_blob.put(
                self.sync_state_filename, 
                json_content.encode('utf-8'),
                options={'allowOverwrite': True}
            )
            
            if upload_result and 'url' in upload_result:
                print(f"✅ Sync state saved! URL: {upload_result['url']}")
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

    def get_synced_order_page_ids(self, order_id: str) -> list:
        """Get all Notion page IDs for synced order (parent + line items)"""
        sync_state = self.get_sync_state()
        synced_order = sync_state.get('synced_orders', {}).get(order_id)
        
        if isinstance(synced_order, list):
            # New simplified format - direct array of page IDs
            return synced_order
        elif isinstance(synced_order, dict):
            # Legacy dict formats
            if 'notion_page_ids' in synced_order:
                return synced_order.get('notion_page_ids', [])
            elif 'notion_page_id' in synced_order:
                return [synced_order.get('notion_page_id')]
        elif isinstance(synced_order, str):
            # Very old format - single string page ID
            return [synced_order]
        
        return []

    def get_synced_order_page_id(self, order_id: str) -> Optional[str]:
        """Get primary Notion page ID for synced order (backward compatibility)"""
        page_ids = self.get_synced_order_page_ids(order_id)
        return page_ids[0] if page_ids else None

    def mark_order_synced(self, order_id: str, notion_page_ids: list, updated_at: str = None):
        """Mark order as successfully synced with all page IDs (parent + line items)"""
        sync_state = self.get_sync_state()
        
        # Handle both single page ID (string) and multiple page IDs (list)
        if isinstance(notion_page_ids, str):
            notion_page_ids = [notion_page_ids]
        
        # Store just the page IDs array - no need for per-order timestamps
        sync_state['synced_orders'][order_id] = notion_page_ids
        
        # Update global resume point if timestamp provided
        if updated_at:
            sync_state['last_processed_updated_at'] = updated_at
        
        # Remove from failed orders if it was there
        if order_id in sync_state.get('failed_orders', []):
            sync_state['failed_orders'].remove(order_id)
        
        if self.save_sync_state(sync_state):
            print(f"✅ Marked order {order_id} as synced with {len(notion_page_ids)} pages")
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

    def is_sync_in_progress(self) -> bool:
        """Check if a sync is currently in progress - ALWAYS reads from blob"""
        # Always read fresh from blob to detect concurrent syncs
        sync_state = self._read_sync_state_from_blob()
        if sync_state is None:
            sync_state = self._get_initial_sync_state()
            
        is_in_progress = sync_state.get('sync_in_progress', False)
        sync_started_at = sync_state.get('sync_started_at')
        
        # If sync has been running for more than 10 minutes, assume it's stuck
        if is_in_progress and sync_started_at:
            try:
                from datetime import datetime, timedelta
                started = datetime.fromisoformat(sync_started_at.replace('Z', '+00:00'))
                if datetime.now(started.tzinfo) - started > timedelta(minutes=10):
                    print("⚠️ Sync appears stuck (>10 minutes), allowing new sync")
                    return False
            except:
                pass
        
        return is_in_progress

    def start_sync_lock(self):
        """Start sync lock to prevent concurrent syncs - ALWAYS writes to blob immediately"""
        from datetime import datetime
        sync_state = self.get_sync_state()
        sync_state['sync_in_progress'] = True
        sync_state['sync_started_at'] = datetime.now().isoformat() + 'Z'
        
        # ALWAYS write sync lock to blob immediately, regardless of batch mode
        was_batch_mode = self.batch_mode
        self.batch_mode = False  # Temporarily disable batch mode
        success = self.save_sync_state(sync_state)
        self.batch_mode = was_batch_mode  # Restore batch mode state
        
        if success:
            print("🔒 Sync lock acquired and written to blob")
        else:
            print("❌ Failed to acquire sync lock")
            
        # Also update cache if in batch mode
        if self.batch_mode:
            self.cached_sync_state = sync_state

    def end_sync_lock(self):
        """Release sync lock"""
        sync_state = self.get_sync_state()
        sync_state['sync_in_progress'] = False
        sync_state['sync_started_at'] = None
        
        if self.batch_mode:
            self.cached_sync_state = sync_state
        else:
            self.save_sync_state(sync_state)
        
        print("🔓 Sync lock released")

    def get_sync_statistics(self) -> Dict:
        """Get sync statistics"""
        sync_state = self.get_sync_state()
        
        # Count total pages across all orders
        total_pages = 0
        synced_orders = sync_state.get('synced_orders', {})
        for order_id in synced_orders:
            page_ids = self.get_synced_order_page_ids(order_id)
            total_pages += len(page_ids)
        
        return {
            'last_sync': sync_state.get('last_sync'),
            'total_synced_orders': len(synced_orders),
            'total_notion_pages': total_pages,  # New metric
            'failed_orders_count': len(sync_state.get('failed_orders', [])),
            'failed_orders': sync_state.get('failed_orders', []),
            'last_processed_updated_at': sync_state.get('last_processed_updated_at'),
            'sync_in_progress': sync_state.get('sync_in_progress', False),
            'sync_started_at': sync_state.get('sync_started_at')
        }