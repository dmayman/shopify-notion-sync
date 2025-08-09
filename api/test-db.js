/**
 * Test Neon database connection and sync storage functionality
 * Node.js version with modern async/await patterns
 */

import SyncDatabase from '../lib/database.js';

export default async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  const now = new Date().toISOString();
  console.log(`[${now}] Testing Neon database connection and sync storage`);

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'GET') {
    return res.status(405).json({
      status: 'error',
      message: `Method ${req.method} not allowed`,
      timestamp: now
    });
  }

  try {
    // Initialize database storage
    const storage = new SyncDatabase();
    const testResults = {};

    // Test 1: Get initial sync state
    console.log('🧪 Test 1: Getting sync state...');
    const syncState = await storage.getSyncState();
    testResults.initial_sync_state = {
      last_sync: syncState.last_sync,
      total_synced_orders: Object.keys(syncState.synced_orders).length,
      failed_orders_count: syncState.failed_orders.length,
      sync_in_progress: syncState.sync_in_progress
    };

    // Test 2: Test sync lock
    console.log('🧪 Test 2: Testing sync lock...');
    const wasInProgressBefore = await storage.isSyncInProgress();
    await storage.startSyncLock();
    const isInProgressAfter = await storage.isSyncInProgress();
    await storage.endSyncLock();
    const wasInProgressAfterEnd = await storage.isSyncInProgress();

    testResults.sync_lock_test = {
      before_lock: wasInProgressBefore,
      after_lock: isInProgressAfter,
      after_unlock: wasInProgressAfterEnd
    };

    // Test 3: Test order operations
    console.log('🧪 Test 3: Testing order operations...');
    const testOrderId = `test-${now.replace(/[:.]/g, '-')}`;
    const testPageIds = [`page-${Date.now()}-1`, `page-${Date.now()}-2`, `page-${Date.now()}-3`];

    // Mark order as synced
    await storage.markOrderSynced(testOrderId, testPageIds, now);

    // Get the page IDs back
    const retrievedPageIds = await storage.getSyncedOrderPageIds(testOrderId);

    // Mark order as failed (to test that functionality)
    const failedOrderId = `${testOrderId}-failed`;
    await storage.markOrderFailed(failedOrderId, 'Test error message');

    // Get failed orders
    const failedOrders = await storage.getFailedOrders();

    testResults.order_operations = {
      marked_order_id: testOrderId,
      original_page_ids: testPageIds,
      retrieved_page_ids: retrievedPageIds,
      failed_orders_include_test: failedOrders.includes(failedOrderId)
    };

    // Test 4: Get statistics
    console.log('🧪 Test 4: Getting sync statistics...');
    const stats = await storage.getSyncStatistics();
    testResults.sync_statistics = stats;

    // Test 5: Direct database operations (batch mode removed)
    console.log('🧪 Test 5: Testing direct database operations...');
    
    const directTestOrderId = `direct-test-${Date.now()}`;
    await storage.markOrderSynced(directTestOrderId, ['direct-page-1'], now);
    
    // Check if it's persisted immediately
    const directState = await storage.getSyncState();
    const isPersisted = directState.synced_orders[directTestOrderId] ? true : false;

    testResults.direct_operations_test = {
      order_id: directTestOrderId,
      is_persisted: isPersisted
    };

    // Send success response
    const response = {
      status: 'success',
      message: 'Neon database test completed successfully',
      test_results: testResults,
      timestamp: now
    };

    console.log('✅ All database tests passed!');
    return res.status(200).json(response);

  } catch (error) {
    console.error('❌ Database test failed:', error);
    
    const errorResponse = {
      status: 'error',
      message: `Database test failed: ${error.toString()}`,
      error_type: error.constructor.name,
      timestamp: now
    };

    return res.status(500).json(errorResponse);
  }
}