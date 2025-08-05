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
            raise ValueError("BLOB_READ_WRITE_TOKEN environment variable is required")
        
        # Use the Vercel Blob API endpoint for uploading with a fixed pathname
        self.blob_api_url = "https://blob.vercel-storage.com"
        self.sync_state_filename = "sync-state.json"
        
    def _make_request(self, method: str, url: str, **kwargs):
        """Make authenticated request to Vercel Blob API"""
        headers = {
            'Authorization': f'Bearer {self.blob_read_write_token}',
            **kwargs.get('headers', {})
        }
        kwargs['headers'] = headers
        
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response
    
    def get_sync_state(self) -> Dict:
        """Get current sync state from blob storage"""
        try:
            # List all blobs to find our sync state file
            list_url = f"{self.blob_api_url}/list"
            response = self._make_request('GET', list_url)
            blobs = response.json().get('blobs', [])
            
            # Find the sync state file
            sync_state_blob = None
            for blob in blobs:
                if blob.get('pathname') == self.sync_state_filename:
                    sync_state_blob = blob
                    break
            
            if not sync_state_blob:
                # File doesn't exist yet, return initial state
                return {
                    "last_sync": None,
                    "synced_orders": {},
                    "failed_orders": []
                }
            
            # Get the content of the sync state file
            content_response = requests.get(sync_state_blob['url'])
            content_response.raise_for_status()
            return content_response.json()
            
        except requests.exceptions.RequestException as e:
            if "404" in str(e):
                # File doesn't exist yet, return initial state
                return {
                    "last_sync": None,
                    "synced_orders": {},
                    "failed_orders": []
                }
            raise e
    
    def save_sync_state(self, sync_state: Dict):
        """Save sync state to blob storage with fixed filename"""
        # First, delete any existing sync state files to avoid duplicates
        try:
            self._cleanup_old_sync_files()
        except Exception as e:
            print(f"Warning: Could not cleanup old files: {e}")
        
        # Upload new sync state with fixed pathname
        upload_url = f"{self.blob_api_url}?filename={self.sync_state_filename}"
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        response = self._make_request(
            'PUT', 
            upload_url,
            headers=headers,
            data=json.dumps(sync_state, indent=2)
        )
        return response.json()
    
    def _cleanup_old_sync_files(self):
        """Delete old sync state files to prevent accumulation"""
        try:
            # List all blobs
            list_url = f"{self.blob_api_url}/list"
            response = self._make_request('GET', list_url)
            blobs = response.json().get('blobs', [])
            
            # Find and delete old sync state files (keep only the newest one)
            sync_files = [blob for blob in blobs if 'sync-state' in blob.get('pathname', '')]
            
            # Sort by creation time, keep the newest, delete the rest
            if len(sync_files) > 1:
                sync_files.sort(key=lambda x: x.get('uploadedAt', ''), reverse=True)
                files_to_delete = sync_files[1:]  # Keep the first (newest), delete the rest
                
                for old_file in files_to_delete:
                    delete_url = f"{self.blob_api_url}/delete?url={old_file['url']}"
                    self._make_request('POST', delete_url)
                    print(f"🗑️  Deleted old sync file: {old_file['pathname']}")
        
        except Exception as e:
            print(f"Could not cleanup old sync files: {e}")
    
    def get_last_sync(self) -> Optional[str]:
        """Get last successful sync timestamp"""
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