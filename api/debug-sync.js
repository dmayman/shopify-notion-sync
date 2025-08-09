/**
 * Debug sync status and force release lock if needed
 */

import SyncDatabase from '../lib/database.js';

export default async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  const now = new Date().toISOString();

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const storage = new SyncDatabase();

    if (req.method === 'POST') {
      // Force release sync lock
      console.log('🔓 Force releasing sync lock...');
      await storage.endSyncLock();
      await storage.completeSync();
      
      return res.status(200).json({
        status: 'success',
        message: 'Sync lock released and sync marked as complete',
        timestamp: now
      });
    }

    // GET: Check detailed sync state
    const syncState = await storage.getSyncState();
    const stats = await storage.getSyncStatistics();

    // Check if sync has been stuck for more than 10 minutes
    let stuckStatus = 'OK';
    if (syncState.sync_in_progress && syncState.sync_started_at) {
      const startTime = new Date(syncState.sync_started_at.replace('ZZ', 'Z'));
      const now = new Date();
      const minutesStuck = (now - startTime) / (1000 * 60);
      
      if (minutesStuck > 10) {
        stuckStatus = `STUCK for ${Math.floor(minutesStuck)} minutes`;
      } else {
        stuckStatus = `Running for ${Math.floor(minutesStuck)} minutes`;
      }
    }

    // Get recent synced orders to see what actually worked
    const recentOrders = Object.keys(syncState.synced_orders).slice(-5);
    const recentOrdersDetails = {};
    
    for (const orderId of recentOrders) {
      const pageIds = await storage.getSyncedOrderPageIds(orderId);
      recentOrdersDetails[orderId] = {
        page_count: pageIds.length,
        page_ids: pageIds
      };
    }

    return res.status(200).json({
      status: 'debug_info',
      message: 'Detailed sync state information',
      sync_state: syncState,
      statistics: stats,
      stuck_status: stuckStatus,
      recent_orders: recentOrdersDetails,
      recommendations: syncState.sync_in_progress ? [
        'Sync appears to be stuck',
        'Run POST /api/debug-sync to force release the lock',
        'Check vercel logs for any errors during sync'
      ] : [
        'Sync state looks normal'
      ],
      timestamp: now
    });

  } catch (error) {
    console.error('Debug sync failed:', error);
    
    return res.status(500).json({
      status: 'error',
      message: `Debug failed: ${error.toString()}`,
      timestamp: now
    });
  }
}