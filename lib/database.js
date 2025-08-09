/**
 * Neon database storage for sync state management
 * Modern Node.js replacement for Python blob storage with better performance
 */

import { neon } from '@neondatabase/serverless';

class SyncDatabase {
  constructor() {
    this.sql = neon(process.env.DATABASE_URL);
    
    // Initialize tables on first use
    this._initialized = false;
  }

  async _ensureTablesExist() {
    if (this._initialized) return;
    
    try {
      // Create tables using template literals (Neon's required syntax)
      await this.sql`
        CREATE TABLE IF NOT EXISTS sync_state (
          id SERIAL PRIMARY KEY,
          last_sync TIMESTAMPTZ,
          last_processed_updated_at TIMESTAMPTZ,
          sync_in_progress BOOLEAN DEFAULT FALSE,
          sync_started_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
      `;

      await this.sql`
        CREATE TABLE IF NOT EXISTS synced_orders (
          id SERIAL PRIMARY KEY,
          order_id VARCHAR(100) NOT NULL UNIQUE,
          notion_page_ids JSONB NOT NULL,
          updated_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ DEFAULT NOW()
        )
      `;

      await this.sql`
        CREATE TABLE IF NOT EXISTS failed_orders (
          id SERIAL PRIMARY KEY,
          order_id VARCHAR(100) NOT NULL UNIQUE,
          error_message TEXT,
          failed_at TIMESTAMPTZ DEFAULT NOW(),
          retry_count INTEGER DEFAULT 1
        )
      `;

      await this.sql`
        INSERT INTO sync_state (id) VALUES (1) 
        ON CONFLICT (id) DO NOTHING
      `;

      await this.sql`
        CREATE INDEX IF NOT EXISTS idx_synced_orders_order_id ON synced_orders(order_id)
      `;

      await this.sql`
        CREATE INDEX IF NOT EXISTS idx_failed_orders_order_id ON failed_orders(order_id)
      `;
      console.log('✅ Database tables initialized');
      this._initialized = true;
    } catch (error) {
      console.error('❌ Failed to create tables:', error);
      throw error;
    }
  }

  _getInitialSyncState() {
    return {
      last_sync: null,
      synced_orders: {},
      failed_orders: [],
      last_processed_updated_at: null,
      sync_in_progress: false,
      sync_started_at: null
    };
  }


  async getSyncState() {
    await this._ensureTablesExist();

    try {
      // Get main sync state
      const [syncResult] = await this.sql`
        SELECT last_sync, last_processed_updated_at, sync_in_progress, sync_started_at 
        FROM sync_state WHERE id = 1
      `;

      if (!syncResult) {
        return this._getInitialSyncState();
      }

      // Get synced orders
      const ordersResult = await this.sql`SELECT order_id, notion_page_ids FROM synced_orders`;
      const syncedOrders = {};
      ordersResult.forEach(row => {
        syncedOrders[row.order_id] = row.notion_page_ids;
      });

      // Get failed orders
      const failedResult = await this.sql`SELECT order_id FROM failed_orders ORDER BY failed_at`;
      const failedOrders = failedResult.map(row => row.order_id);

      return {
        last_sync: syncResult.last_sync?.toISOString() || null,
        synced_orders: syncedOrders,
        failed_orders: failedOrders,
        last_processed_updated_at: syncResult.last_processed_updated_at?.toISOString() || null,
        sync_in_progress: syncResult.sync_in_progress || false,
        sync_started_at: syncResult.sync_started_at ? syncResult.sync_started_at.toISOString() + 'Z' : null
      };
    } catch (error) {
      console.error('❌ Failed to get sync state:', error);
      return this._getInitialSyncState();
    }
  }

  async getLastSync() {
    const syncState = await this.getSyncState();
    return syncState.last_sync;
  }

  async getFailedOrders() {
    try {
      const result = await this.sql`SELECT order_id FROM failed_orders ORDER BY failed_at`;
      return result.map(row => row.order_id);
    } catch (error) {
      console.error('❌ Failed to get failed orders:', error);
      return [];
    }
  }

  async getSyncedOrderPageIds(orderId) {
    try {
      const [result] = await this.sql`
        SELECT notion_page_ids FROM synced_orders WHERE order_id = ${orderId}
      `;
      return this._normalizePageIds(result?.notion_page_ids);
    } catch (error) {
      console.error('❌ Failed to get synced order page IDs:', error);
      return [];
    }
  }

  _normalizePageIds(syncedOrder) {
    if (Array.isArray(syncedOrder)) {
      return syncedOrder;
    } else if (typeof syncedOrder === 'object' && syncedOrder) {
      // Legacy format handling
      if (syncedOrder.notion_page_ids) {
        return syncedOrder.notion_page_ids;
      } else if (syncedOrder.notion_page_id) {
        return [syncedOrder.notion_page_id];
      }
    } else if (typeof syncedOrder === 'string') {
      return [syncedOrder];
    }
    return [];
  }

  async getSyncedOrderPageId(orderId) {
    const pageIds = await this.getSyncedOrderPageIds(orderId);
    return pageIds[0] || null;
  }

  async markOrderSynced(orderId, notionPageIds, updatedAt = null) {
    if (typeof notionPageIds === 'string') {
      notionPageIds = [notionPageIds];
    }

    try {
      // Insert or update synced order
      await this.sql`
        INSERT INTO synced_orders (order_id, notion_page_ids, updated_at)
        VALUES (${orderId}, ${JSON.stringify(notionPageIds)}, ${updatedAt ? new Date(updatedAt) : null})
        ON CONFLICT (order_id) DO UPDATE SET
          notion_page_ids = EXCLUDED.notion_page_ids,
          updated_at = EXCLUDED.updated_at
      `;

      // Update global resume point if timestamp provided
      if (updatedAt) {
        await this.sql`
          UPDATE sync_state SET 
            last_processed_updated_at = ${new Date(updatedAt)},
            updated_at = NOW()
          WHERE id = 1
        `;
      }

      // Remove from failed orders if it was there
      await this.sql`DELETE FROM failed_orders WHERE order_id = ${orderId}`;

      console.log(`✅ Marked order ${orderId} as synced with ${notionPageIds.length} pages`);
    } catch (error) {
      console.error(`❌ Failed to mark order ${orderId} as synced:`, error);
    }
  }

  async markOrderFailed(orderId, errorMessage = null) {
    try {
      await this.sql`
        INSERT INTO failed_orders (order_id, error_message, failed_at, retry_count)
        VALUES (${orderId}, ${errorMessage}, NOW(), 1)
        ON CONFLICT (order_id) DO UPDATE SET
          error_message = EXCLUDED.error_message,
          failed_at = NOW(),
          retry_count = failed_orders.retry_count + 1
      `;
      console.log(`⚠️ Marked order ${orderId} as failed`);
    } catch (error) {
      console.error(`❌ Failed to mark order ${orderId} as failed:`, error);
    }
  }

  async completeSync(timestamp = null) {
    if (!timestamp) {
      timestamp = new Date().toISOString();
    }

    try {
      await this.sql`
        UPDATE sync_state SET 
          last_sync = ${new Date(timestamp)},
          updated_at = NOW()
        WHERE id = 1
      `;
      console.log(`✅ Sync completed at ${timestamp}`);
    } catch (error) {
      console.error('❌ Failed to mark sync as completed:', error);
    }
  }

  async getResumeTimestamp() {
    const syncState = await this.getSyncState();
    return syncState.last_processed_updated_at;
  }

  async updateResumePoint(updatedAt) {
    try {
      await this.sql`
        UPDATE sync_state SET 
          last_processed_updated_at = ${new Date(updatedAt)},
          updated_at = NOW()
        WHERE id = 1
      `;
      console.log(`📍 Updated resume point to ${updatedAt}`);
    } catch (error) {
      console.error('❌ Failed to update resume point:', error);
    }
  }

  async isSyncInProgress() {
    try {
      const [result] = await this.sql`
        SELECT sync_in_progress, sync_started_at FROM sync_state WHERE id = 1
      `;

      if (!result) {
        return false;
      }

      const isInProgress = result.sync_in_progress;
      const syncStartedAt = result.sync_started_at;

      // If sync has been running for more than 10 minutes, assume it's stuck
      if (isInProgress && syncStartedAt) {
        const now = new Date();
        const startTime = new Date(syncStartedAt);
        const tenMinutesAgo = new Date(now.getTime() - 10 * 60 * 1000);

        if (startTime < tenMinutesAgo) {
          console.log('⚠️ Sync appears stuck (>10 minutes), allowing new sync');
          return false;
        }
      }

      return isInProgress;
    } catch (error) {
      console.error('❌ Failed to check sync progress:', error);
      return false;
    }
  }

  async startSyncLock() {
    try {
      await this.sql`
        UPDATE sync_state SET 
          sync_in_progress = TRUE,
          sync_started_at = NOW(),
          updated_at = NOW()
        WHERE id = 1
      `;
      console.log('🔒 Sync lock acquired');
    } catch (error) {
      console.error('❌ Failed to acquire sync lock:', error);
    }
  }

  async endSyncLock() {
    try {
      await this.sql`
        UPDATE sync_state SET 
          sync_in_progress = FALSE,
          sync_started_at = NULL,
          updated_at = NOW()
        WHERE id = 1
      `;
      console.log('🔓 Sync lock released');
    } catch (error) {
      console.error('❌ Failed to release sync lock:', error);
    }
  }

  async getSyncStatistics() {
    try {
      // Get main sync state
      const [syncResult] = await this.sql`
        SELECT last_sync, last_processed_updated_at, sync_in_progress, sync_started_at 
        FROM sync_state WHERE id = 1
      `;

      // Count synced orders
      const [ordersCount] = await this.sql`SELECT COUNT(*) as count FROM synced_orders`;

      // Count total pages across all orders
      const pageResults = await this.sql`
        SELECT jsonb_array_length(notion_page_ids) as page_count 
        FROM synced_orders 
        WHERE notion_page_ids IS NOT NULL
      `;
      const totalPages = pageResults.reduce((sum, row) => sum + (row.page_count || 0), 0);

      // Count failed orders
      const [failedCount] = await this.sql`SELECT COUNT(*) as count FROM failed_orders`;

      // Get failed order IDs
      const failedResult = await this.sql`SELECT order_id FROM failed_orders ORDER BY failed_at`;
      const failedOrders = failedResult.map(row => row.order_id);

      return {
        last_sync: syncResult?.last_sync?.toISOString() || null,
        total_synced_orders: ordersCount.count,
        total_notion_pages: totalPages,
        failed_orders_count: failedCount.count,
        failed_orders: failedOrders,
        last_processed_updated_at: syncResult?.last_processed_updated_at?.toISOString() || null,
        sync_in_progress: syncResult?.sync_in_progress || false,
        sync_started_at: syncResult?.sync_started_at ? syncResult.sync_started_at.toISOString() + 'Z' : null
      };
    } catch (error) {
      console.error('❌ Failed to get sync statistics:', error);
      return {
        last_sync: null,
        total_synced_orders: 0,
        total_notion_pages: 0,
        failed_orders_count: 0,
        failed_orders: [],
        last_processed_updated_at: null,
        sync_in_progress: false,
        sync_started_at: null
      };
    }
  }
}

export default SyncDatabase;