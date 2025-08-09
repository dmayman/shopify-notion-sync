"""
Neon database storage for sync state management (psycopg2 version)
Replacement for Vercel Blob storage with better performance and reliability
"""

import json
import psycopg2
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from lib.db import db_pool

class SyncDatabaseStorage:
    def __init__(self):
        if db_pool is None:
            raise ValueError("Database connection not available. Please set DATABASE_URL environment variable.")
        
        # Initialize database tables if they don't exist
        self._ensure_tables_exist()
        self.batch_mode = False
        self.cached_sync_state = None
    
    def _ensure_tables_exist(self):
        """Create database tables if they don't exist"""
        create_tables_sql = """
        -- Sync state table (replaces the JSON blob)
        CREATE TABLE IF NOT EXISTS sync_state (
            id SERIAL PRIMARY KEY,
            last_sync TIMESTAMPTZ,
            last_processed_updated_at TIMESTAMPTZ,
            sync_in_progress BOOLEAN DEFAULT FALSE,
            sync_started_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Synced orders table (replaces synced_orders JSON object)
        CREATE TABLE IF NOT EXISTS synced_orders (
            id SERIAL PRIMARY KEY,
            order_id VARCHAR(100) NOT NULL UNIQUE,
            notion_page_ids TEXT NOT NULL, -- JSON string of page IDs
            updated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Failed orders table (replaces failed_orders JSON array)
        CREATE TABLE IF NOT EXISTS failed_orders (
            id SERIAL PRIMARY KEY,
            order_id VARCHAR(100) NOT NULL UNIQUE,
            error_message TEXT,
            failed_at TIMESTAMPTZ DEFAULT NOW(),
            retry_count INTEGER DEFAULT 1
        );
        
        -- Ensure we have at least one sync_state row
        INSERT INTO sync_state (id) VALUES (1) 
        ON CONFLICT (id) DO NOTHING;
        
        -- Create indexes for better performance
        CREATE INDEX IF NOT EXISTS idx_synced_orders_order_id ON synced_orders(order_id);
        CREATE INDEX IF NOT EXISTS idx_failed_orders_order_id ON failed_orders(order_id);
        """
        
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(create_tables_sql)
            conn.commit()
            cur.close()
            print("✅ Database tables initialized")
        except Exception as e:
            print(f"❌ Failed to create tables: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def _get_initial_sync_state(self) -> Dict:
        """Get initial sync state structure (for compatibility)"""
        return {
            "last_sync": None,
            "synced_orders": {},
            "failed_orders": [],
            "last_processed_updated_at": None,
            "sync_in_progress": False,
            "sync_started_at": None
        }

    def start_batch_mode(self):
        """Start batch mode - cache state in memory and defer writes"""
        if not self.batch_mode:
            self.cached_sync_state = self.get_sync_state()
        self.batch_mode = True
        print("🔄 Started batch mode - deferring database writes")

    def end_batch_mode(self):
        """End batch mode and commit accumulated changes"""
        if self.batch_mode and self.cached_sync_state:
            print("💾 Ending batch mode - committing all changes to database")
            self.batch_mode = False
            # In database mode, changes are already committed, no need to do anything special
            self.cached_sync_state = None
        return True

    def get_sync_state(self) -> Dict:
        """Get current sync state from database or cache"""
        if self.batch_mode and self.cached_sync_state:
            return self.cached_sync_state
            
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            
            # Get main sync state
            cur.execute(
                "SELECT last_sync, last_processed_updated_at, sync_in_progress, sync_started_at FROM sync_state WHERE id = 1"
            )
            sync_result = cur.fetchone()
            
            if not sync_result:
                cur.close()
                return self._get_initial_sync_state()
            
            # Get synced orders
            synced_orders = {}
            cur.execute("SELECT order_id, notion_page_ids FROM synced_orders")
            orders_result = cur.fetchall()
            for row in orders_result:
                try:
                    synced_orders[row[0]] = json.loads(row[1])
                except json.JSONDecodeError:
                    synced_orders[row[0]] = [row[1]]  # Fallback for single ID
            
            # Get failed orders
            failed_orders = []
            cur.execute("SELECT order_id FROM failed_orders ORDER BY failed_at")
            failed_result = cur.fetchall()
            for row in failed_result:
                failed_orders.append(row[0])
            
            cur.close()
            
            return {
                "last_sync": sync_result[0].isoformat() if sync_result[0] else None,
                "synced_orders": synced_orders,
                "failed_orders": failed_orders,
                "last_processed_updated_at": sync_result[1].isoformat() if sync_result[1] else None,
                "sync_in_progress": sync_result[2],
                "sync_started_at": sync_result[3].isoformat() + 'Z' if sync_result[3] else None
            }
        except Exception as e:
            print(f"❌ Failed to get sync state: {e}")
            return self._get_initial_sync_state()
        finally:
            db_pool.putconn(conn)

    def save_sync_state(self, sync_state: Dict) -> bool:
        """Save sync state to database"""
        if self.batch_mode:
            # In batch mode, just update the cached state
            self.cached_sync_state = sync_state
            print("🔄 Updated cached sync state (batch mode)")
            return True
            
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            
            # Update main sync state
            cur.execute("""
                UPDATE sync_state SET 
                    last_sync = %s,
                    last_processed_updated_at = %s,
                    sync_in_progress = %s,
                    sync_started_at = %s,
                    updated_at = NOW()
                WHERE id = 1
            """, (
                datetime.fromisoformat(sync_state['last_sync'].replace('Z', '+00:00')) if sync_state.get('last_sync') else None,
                datetime.fromisoformat(sync_state['last_processed_updated_at'].replace('Z', '+00:00')) if sync_state.get('last_processed_updated_at') else None,
                sync_state.get('sync_in_progress', False),
                datetime.fromisoformat(sync_state['sync_started_at'].replace('Z', '+00:00')) if sync_state.get('sync_started_at') else None
            ))
            conn.commit()
            cur.close()
            print("💾 Sync state saved to database")
            return True
        except Exception as e:
            print(f"❌ Failed to save sync state: {e}")
            conn.rollback()
            return False
        finally:
            db_pool.putconn(conn)

    def get_last_sync(self) -> Optional[str]:
        """Get last successful sync timestamp"""
        sync_state = self.get_sync_state()
        return sync_state.get('last_sync')

    def get_failed_orders(self) -> List[str]:
        """Get list of failed order IDs"""
        if self.batch_mode and self.cached_sync_state:
            return self.cached_sync_state.get('failed_orders', [])
            
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT order_id FROM failed_orders ORDER BY failed_at")
            result = cur.fetchall()
            cur.close()
            return [row[0] for row in result]
        except Exception as e:
            print(f"❌ Failed to get failed orders: {e}")
            return []
        finally:
            db_pool.putconn(conn)

    def get_synced_order_page_ids(self, order_id: str) -> list:
        """Get all Notion page IDs for synced order (parent + line items)"""
        if self.batch_mode and self.cached_sync_state:
            synced_order = self.cached_sync_state.get('synced_orders', {}).get(order_id)
        else:
            conn = db_pool.getconn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT notion_page_ids FROM synced_orders WHERE order_id = %s", (order_id,))
                result = cur.fetchone()
                cur.close()
                synced_order = json.loads(result[0]) if result else None
            except Exception as e:
                print(f"❌ Failed to get synced order page IDs: {e}")
                synced_order = None
            finally:
                db_pool.putconn(conn)
        
        if isinstance(synced_order, list):
            return synced_order
        elif isinstance(synced_order, dict):
            # Legacy format handling
            if 'notion_page_ids' in synced_order:
                return synced_order.get('notion_page_ids', [])
            elif 'notion_page_id' in synced_order:
                return [synced_order.get('notion_page_id')]
        elif isinstance(synced_order, str):
            return [synced_order]
        
        return []

    def get_synced_order_page_id(self, order_id: str) -> Optional[str]:
        """Get primary Notion page ID for synced order (backward compatibility)"""
        page_ids = self.get_synced_order_page_ids(order_id)
        return page_ids[0] if page_ids else None

    def mark_order_synced(self, order_id: str, notion_page_ids: list, updated_at: str = None):
        """Mark order as successfully synced with all page IDs (parent + line items)"""
        if isinstance(notion_page_ids, str):
            notion_page_ids = [notion_page_ids]
        
        if self.batch_mode and self.cached_sync_state:
            # Update cached state
            self.cached_sync_state['synced_orders'][order_id] = notion_page_ids
            if updated_at:
                self.cached_sync_state['last_processed_updated_at'] = updated_at
            # Remove from failed orders
            if order_id in self.cached_sync_state.get('failed_orders', []):
                self.cached_sync_state['failed_orders'].remove(order_id)
            print(f"✅ Marked order {order_id} as synced with {len(notion_page_ids)} pages (cached)")
            return
        
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            
            # Insert or update synced order
            cur.execute("""
                INSERT INTO synced_orders (order_id, notion_page_ids, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (order_id) DO UPDATE SET
                    notion_page_ids = EXCLUDED.notion_page_ids,
                    updated_at = EXCLUDED.updated_at
            """, (order_id, json.dumps(notion_page_ids), 
                 datetime.fromisoformat(updated_at.replace('Z', '+00:00')) if updated_at else None))
            
            # Update global resume point if timestamp provided
            if updated_at:
                cur.execute("""
                    UPDATE sync_state SET 
                        last_processed_updated_at = %s,
                        updated_at = NOW()
                    WHERE id = 1
                """, (datetime.fromisoformat(updated_at.replace('Z', '+00:00')),))
            
            # Remove from failed orders if it was there
            cur.execute("DELETE FROM failed_orders WHERE order_id = %s", (order_id,))
            
            conn.commit()
            cur.close()
            print(f"✅ Marked order {order_id} as synced with {len(notion_page_ids)} pages")
        except Exception as e:
            print(f"❌ Failed to mark order {order_id} as synced: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def mark_order_failed(self, order_id: str, error_message: str = None):
        """Mark order as failed to sync"""
        if self.batch_mode and self.cached_sync_state:
            # Update cached state
            if order_id not in self.cached_sync_state.get('failed_orders', []):
                self.cached_sync_state.setdefault('failed_orders', []).append(order_id)
            print(f"⚠️ Marked order {order_id} as failed (cached)")
            return
        
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            
            # Insert or update failed order with retry count
            cur.execute("""
                INSERT INTO failed_orders (order_id, error_message, failed_at, retry_count)
                VALUES (%s, %s, NOW(), 1)
                ON CONFLICT (order_id) DO UPDATE SET
                    error_message = EXCLUDED.error_message,
                    failed_at = NOW(),
                    retry_count = failed_orders.retry_count + 1
            """, (order_id, error_message))
            conn.commit()
            cur.close()
            print(f"⚠️ Marked order {order_id} as failed")
        except Exception as e:
            print(f"❌ Failed to mark order {order_id} as failed: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def complete_sync(self, timestamp: str = None):
        """Mark sync as completed with current timestamp"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        if self.batch_mode and self.cached_sync_state:
            self.cached_sync_state['last_sync'] = timestamp
            print(f"✅ Sync completed at {timestamp} (cached)")
            return
        
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE sync_state SET 
                    last_sync = %s,
                    updated_at = NOW()
                WHERE id = 1
            """, (datetime.fromisoformat(timestamp.replace('Z', '+00:00')) if 'T' in timestamp else datetime.now(),))
            conn.commit()
            cur.close()
            print(f"✅ Sync completed at {timestamp}")
        except Exception as e:
            print(f"❌ Failed to mark sync as completed: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def get_resume_timestamp(self) -> Optional[str]:
        """Get the timestamp to resume sync from"""
        sync_state = self.get_sync_state()
        return sync_state.get('last_processed_updated_at')

    def update_resume_point(self, updated_at: str):
        """Update the resume point timestamp"""
        if self.batch_mode and self.cached_sync_state:
            self.cached_sync_state['last_processed_updated_at'] = updated_at
            print(f"📍 Updated resume point to {updated_at} (cached)")
            return
        
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE sync_state SET 
                    last_processed_updated_at = %s,
                    updated_at = NOW()
                WHERE id = 1
            """, (datetime.fromisoformat(updated_at.replace('Z', '+00:00')),))
            conn.commit()
            cur.close()
            print(f"📍 Updated resume point to {updated_at}")
        except Exception as e:
            print(f"❌ Failed to update resume point: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def is_sync_in_progress(self) -> bool:
        """Check if a sync is currently in progress"""
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT sync_in_progress, sync_started_at FROM sync_state WHERE id = 1"
            )
            result = cur.fetchone()
            cur.close()
            
            if not result:
                return False
                
            is_in_progress = result[0]
            sync_started_at = result[1]
            
            # If sync has been running for more than 10 minutes, assume it's stuck
            if is_in_progress and sync_started_at:
                if datetime.now(sync_started_at.tzinfo) - sync_started_at > timedelta(minutes=10):
                    print("⚠️ Sync appears stuck (>10 minutes), allowing new sync")
                    return False
            
            return is_in_progress
        except Exception as e:
            print(f"❌ Failed to check sync progress: {e}")
            return False
        finally:
            db_pool.putconn(conn)

    def start_sync_lock(self):
        """Start sync lock to prevent concurrent syncs"""
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE sync_state SET 
                    sync_in_progress = TRUE,
                    sync_started_at = NOW(),
                    updated_at = NOW()
                WHERE id = 1
            """)
            conn.commit()
            cur.close()
            print("🔒 Sync lock acquired")
            
            # Also update cache if in batch mode
            if self.batch_mode and self.cached_sync_state:
                self.cached_sync_state['sync_in_progress'] = True
                self.cached_sync_state['sync_started_at'] = datetime.now().isoformat() + 'Z'
        except Exception as e:
            print(f"❌ Failed to acquire sync lock: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def end_sync_lock(self):
        """Release sync lock"""
        if self.batch_mode and self.cached_sync_state:
            self.cached_sync_state['sync_in_progress'] = False
            self.cached_sync_state['sync_started_at'] = None
            print("🔓 Sync lock released (cached)")
            return
        
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE sync_state SET 
                    sync_in_progress = FALSE,
                    sync_started_at = NULL,
                    updated_at = NOW()
                WHERE id = 1
            """)
            conn.commit()
            cur.close()
            print("🔓 Sync lock released")
        except Exception as e:
            print(f"❌ Failed to release sync lock: {e}")
            conn.rollback()
        finally:
            db_pool.putconn(conn)

    def get_sync_statistics(self) -> Dict:
        """Get sync statistics"""
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            
            # Get main sync state
            cur.execute(
                "SELECT last_sync, last_processed_updated_at, sync_in_progress, sync_started_at FROM sync_state WHERE id = 1"
            )
            sync_result = cur.fetchone()
            
            # Count synced orders
            cur.execute("SELECT COUNT(*) FROM synced_orders")
            orders_count = cur.fetchone()[0]
            
            # Count total pages across all orders (sum JSON array lengths)
            cur.execute("SELECT notion_page_ids FROM synced_orders")
            page_results = cur.fetchall()
            total_pages = 0
            for row in page_results:
                try:
                    page_ids = json.loads(row[0])
                    total_pages += len(page_ids) if isinstance(page_ids, list) else 1
                except:
                    total_pages += 1
            
            # Count failed orders
            cur.execute("SELECT COUNT(*) FROM failed_orders")
            failed_count = cur.fetchone()[0]
            
            # Get failed order IDs
            cur.execute("SELECT order_id FROM failed_orders ORDER BY failed_at")
            failed_result = cur.fetchall()
            failed_orders = [row[0] for row in failed_result]
            
            cur.close()
            
            return {
                'last_sync': sync_result[0].isoformat() if sync_result and sync_result[0] else None,
                'total_synced_orders': orders_count,
                'total_notion_pages': total_pages,
                'failed_orders_count': failed_count,
                'failed_orders': failed_orders,
                'last_processed_updated_at': sync_result[1].isoformat() if sync_result and sync_result[1] else None,
                'sync_in_progress': sync_result[2] if sync_result else False,
                'sync_started_at': sync_result[3].isoformat() + 'Z' if sync_result and sync_result[3] else None
            }
        except Exception as e:
            print(f"❌ Failed to get sync statistics: {e}")
            return {
                'last_sync': None,
                'total_synced_orders': 0,
                'total_notion_pages': 0,
                'failed_orders_count': 0,
                'failed_orders': [],
                'last_processed_updated_at': None,
                'sync_in_progress': False,
                'sync_started_at': None
            }
        finally:
            db_pool.putconn(conn)