/**
 * TESTING ONLY: Reset database to clean state
 * WARNING: This will delete ALL sync data!
 */

import SyncDatabase from '../lib/database.js';

export default async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  const now = new Date().toISOString();

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({
      status: 'error',
      message: 'Only POST method allowed for database reset',
      timestamp: now
    });
  }

  try {
    const storage = new SyncDatabase();

    // Get current state before reset (for confirmation)
    const statsBefore = await storage.getSyncStatistics();

    console.log('🧹 Resetting database to clean state...');

    // Clear all data from tables
    await storage.sql`DELETE FROM failed_orders`;
    console.log('  ✅ Cleared failed_orders table');

    await storage.sql`DELETE FROM synced_orders`;
    console.log('  ✅ Cleared synced_orders table');

    // Reset sync state to initial values
    await storage.sql`
      UPDATE sync_state SET 
        last_sync = NULL,
        last_processed_updated_at = NULL,
        sync_in_progress = FALSE,
        sync_started_at = NULL,
        updated_at = NOW()
      WHERE id = 1
    `;
    console.log('  ✅ Reset sync_state to initial values');

    // Get stats after reset for confirmation
    const statsAfter = await storage.getSyncStatistics();

    const response = {
      status: 'success',
      message: '🧹 Database reset completed successfully',
      before_reset: {
        total_synced_orders: statsBefore.total_synced_orders,
        total_notion_pages: statsBefore.total_notion_pages,
        failed_orders_count: statsBefore.failed_orders_count,
        last_sync: statsBefore.last_sync
      },
      after_reset: {
        total_synced_orders: statsAfter.total_synced_orders,
        total_notion_pages: statsAfter.total_notion_pages,
        failed_orders_count: statsAfter.failed_orders_count,
        last_sync: statsAfter.last_sync
      },
      warning: 'This was a DESTRUCTIVE operation - all sync history has been cleared',
      next_steps: [
        'Run initial sync to start fresh: POST /api/sync?mode=initial&limit=5',
        'Or run smart sync (will be treated as initial): POST /api/sync?limit=5'
      ],
      timestamp: now
    };

    console.log('✅ Database reset completed successfully');
    return res.status(200).json(response);

  } catch (error) {
    console.error('❌ Database reset failed:', error);
    
    return res.status(500).json({
      status: 'error',
      message: `Database reset failed: ${error.toString()}`,
      error_type: error.constructor.name,
      warning: 'Database may be in inconsistent state - check manually',
      timestamp: now
    });
  }
}