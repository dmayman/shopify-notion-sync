"""
Vercel Blob storage for sync state management
Ultra-minimal approach with single JSON file
"""

import json
import os
import requests
from datetime import datetime
from typing import Dict, List, Optional

class SyncBlobStorage:
    def __init__(self):
        self.blob_read_write_token = os.getenv('BLOB_READ_WRITE_TOKEN')
        if not self.blob_read_write_token:
            raise ValueError("BLOB_READ_WRITE_TOKEN environment variable must be set to interact with Vercel Blob storage.")
        
        # Get the blob store URL from environment (project-specific)
        self.blob_api_url = os.getenv('BLOB_STORE_URL', 'https://blob.vercel-storage.com')
        self.sync_state_filename = "sync-state.json"
        
    def _make_request(self, method: str, url: str, **kwargs):
        """Make authenticated request to Vercel Blob API"""
        headers = {
            'Authorization': f'Bearer {self.blob_read_write_token}',
            **kwargs.get('headers', {})
        }
        
        #  Merge headers instead of overwriting
        headers.update(kwargs.get('headers', {}))
        kwargs['headers'] = headers
        
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response
    
    def get_sync_state(self) -> Dict:
        """Get current sync state from blob storage or fallback"""
        # First check if we have in-memory fallback data
        if hasattr(self, '_memory_sync_state'):
            print("📄 Using in-memory sync state (fallback mode)")
            return self._memory_sync_state
        
        try:
            # Try to get the sync state blob directly if we know its URL
            sync_state_url = os.getenv('SYNC_STATE_BLOB_URL')
            
            if sync_state_url:
                # Try to fetch the existing sync state
                response = requests.get(sync_state_url)
                response.raise_for_status() # Ensure request was successful
                return response.json()
            elif os.getenv('BLOB_STORE_URL'):
                # If blob store URL is defined but no sync state URL, it means the file may not exist
                # in that case, return initial state
                print("📄 No existing sync state found, using initial state")
                return {
                    "last_sync": None,
                    "synced_orders": {},
                    "failed_orders": []
                }
            else:
                # No URL stored yet, return initial state and warn that URL is not defined
                print("📄 No existing sync state found and BLOB_STORE_URL is not defined.")
                return {
                    "last_sync": None,
                    "synced_orders": {},
                    "failed_orders": []

                }
                
        except requests.exceptions.RequestException as e:
            if "404" in str(e):
                # File doesn't exist yet, return initial state
                print("📄 No existing sync state found, using initial state")
                return {
                    "last_sync": None,
                    "synced_orders": {},
                    "failed_orders": []
                }
            print(f"⚠️  Error fetching sync state: {e}")
            # Return default state on any error
            return {
                "last_sync": None,
                "synced_orders": {},
                "failed_orders": []

            }
    
    def save_sync_state(self, sync_state: Dict):
        """Save sync state to Vercel Blob storage using correct API"""
        # Use PUT method with token as query parameter (per Vercel Blob docs)
        upload_url = f"{self.blob_api_url}/{self.sync_state_filename}?token={self.blob_read_write_token}"
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        # Convert sync state to JSON string
        json_content = json.dumps(sync_state, indent=2)
        
        response = requests.put(upload_url, data=json_content, headers=headers)
        response.raise_for_status()
        
        result = response.json()
        
        # Store the blob URL for future reads
        if 'url' in result:
            # Store the URL for future reads (you could set this as env var)
            print(f"💾 Sync state uploaded to blob storage. URL: {result['url']}")
            self._blob_url = result['url']

            # Set environment variable for SYNC_STATE_BLOB_URL
            os.environ['SYNC_STATE_BLOB_URL'] = result['url']

        
        sync_state = self.get_sync_state()
        return sync_state.get('last_sync')
    
    def update_last_sync(self, timestamp: str):
        """Update last sync timestamp"""
        sync_state = self.get_sync_state()
        sync_state['last_sync'] = timestamp
        self.save_sync_state(sync_state)
    
    def is_order_synced(self, order_id: str) -> bool:
        """Check if order has been synced"""
        sync_state = self.get_sync_state()
        return order_id in sync_state.get('synced_orders', {})
    
    def get_synced_order_page_id(self, order_id: str) -> Optional[str]:
        """Get Notion page ID for synced order"""
        sync_state = self.get_sync_state()
        return sync_state.get('synced_orders', {}).get(order_id)
    
    def mark_order_synced(self, order_id: str, notion_page_id: str):
        """Mark order as successfully synced"""
        sync_state = self.get_sync_state()
        sync_state['synced_orders'][order_id] = notion_page_id
        
        # Remove from failed orders if it was there
        if order_id in sync_state.get('failed_orders', []):
            sync_state['failed_orders'].remove(order_id)
        
        self.save_sync_state(sync_state)
    
    def mark_order_failed(self, order_id: str):
        """Mark order as failed to sync"""
        sync_state = self.get_sync_state()
        
        if order_id not in sync_state.get('failed_orders', []):
            sync_state.setdefault('failed_orders', []).append(order_id)
        
        self.save_sync_state(sync_state)
    
    def get_failed_orders(self) -> List[str]:
        """Get list of failed order IDs"""
        sync_state = self.get_sync_state()
        return sync_state.get('failed_orders', [])
    
    def complete_sync(self, timestamp: str = None):
        """Mark sync as completed with current timestamp"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        self.update_last_sync(timestamp)
    
    def get_sync_statistics(self) -> Dict:
        """Get sync statistics"""
        sync_state = self.get_sync_state()
        
        return {
            'last_sync': sync_state.get('last_sync'),
            'total_synced_orders': len(sync_state.get('synced_orders', {})),
            'failed_orders_count': len(sync_state.get('failed_orders', [])),
            'failed_orders': sync_state.get('failed_orders', [])
        }

    def get_last_sync(self) -> Optional[str]:
        """Get last successful sync timestamp"""
        sync_state = self.get_sync_state()
