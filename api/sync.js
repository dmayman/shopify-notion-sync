/**
 * Shopify-Notion Sync API
 * Node.js version of the Python sync.py with modern Neon database integration
 */

import { Client } from '@notionhq/client';
import SyncDatabase from '../lib/database.js';

class ShopifyNotionSync {
  constructor() {
    // Get environment variables
    this.shopifyStoreUrl = process.env.SHOPIFY_STORE_URL;
    this.shopifyAccessToken = process.env.SHOPIFY_ACCESS_TOKEN;
    this.notionToken = process.env.NOTION_TOKEN;
    this.notionDatabaseId = process.env.NOTION_DATABASE_ID;

    // Validate environment variables
    const missing = [];
    if (!this.shopifyStoreUrl) missing.push('SHOPIFY_STORE_URL');
    if (!this.shopifyAccessToken) missing.push('SHOPIFY_ACCESS_TOKEN');
    if (!this.notionToken) missing.push('NOTION_TOKEN');
    if (!this.notionDatabaseId) missing.push('NOTION_DATABASE_ID');

    if (missing.length > 0) {
      throw new Error(`Missing environment variables: ${missing.join(', ')}`);
    }

    // Initialize Notion client
    this.notion = new Client({ auth: this.notionToken });

    // Initialize sync database storage
    this.syncStorage = new SyncDatabase();

    console.log(`Initialized sync for store: ${this.shopifyStoreUrl}`);
    console.log(`Notion database ID: ${this.notionDatabaseId}`);
  }

  normalizeShopifyTimestamp(timestampStr) {
    try {
      if (timestampStr.endsWith('Z')) {
        return timestampStr;
      } else if (timestampStr.includes('+') || timestampStr.endsWith('+00:00')) {
        const parsed = new Date(timestampStr);
        return parsed.toISOString();
      } else {
        return timestampStr + (timestampStr.endsWith('Z') ? '' : 'Z');
      }
    } catch (error) {
      console.warn(`⚠️ Timestamp normalization error: ${error}, using original: ${timestampStr}`);
      return timestampStr;
    }
  }

  async fetchShopifyData(query) {
    const url = `https://${this.shopifyStoreUrl}/admin/api/2023-10/graphql.json`;
    
    const headers = {
      'X-Shopify-Access-Token': this.shopifyAccessToken,
      'Content-Type': 'application/json'
    };

    console.log('Making request to Shopify GraphQL API...');
    
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({ query })
    });

    if (!response.ok) {
      throw new Error(`Shopify API error: ${response.status} - ${await response.text()}`);
    }

    const data = await response.json();

    if (data.errors) {
      throw new Error(`Shopify GraphQL errors: ${JSON.stringify(data.errors)}`);
    }

    console.log('Successfully fetched data from Shopify');
    return data;
  }

  async getShopifyOrders(limit = 50, dateFilter = null, orderIds = null, initialSync = false) {
    let queryFilter = '';

    if (orderIds) {
      const orderFilter = orderIds.map(oid => `name:${oid}`).join(' OR ');
      queryFilter = `query: "${orderFilter}"`;
    } else if (dateFilter) {
      const normalizedDateFilter = this.normalizeShopifyTimestamp(dateFilter);
      queryFilter = `query: "updated_at:>${normalizedDateFilter}"`;
    } else {
      queryFilter = '';
      if (initialSync) {
        console.log('Fetching orders for initial sync (from oldest to newest)');
      } else {
        console.log('Fetching recent orders (no date filter)');
      }
    }

    const sortKey = 'UPDATED_AT';
    const reverse = initialSync ? 'false' : 'true'; // oldest first for initial sync

    const query = `
      query {
        orders(first: ${limit}, sortKey: ${sortKey}, reverse: ${reverse}, ${queryFilter}) {
          edges {
            node {
              id
              legacyResourceId
              name
              createdAt
              updatedAt
              email
              customer {
                displayName
              }
              totalTaxSet {
                presentmentMoney {
                  amount
                  currencyCode
                }
              }
              transactions {
                status
                kind
                gateway
                paymentDetails {
                  ... on CardPaymentDetails {
                    paymentMethodName
                  }
                  ... on ShopPayInstallmentsPaymentDetails {
                    paymentMethodName
                  }
                }
                fees {
                  amount {
                    amount
                    currencyCode
                  }
                }
              }
              paymentGatewayNames
              displayFinancialStatus
              displayFulfillmentStatus
              lineItems(first: 250) {
                edges {
                  node {
                    id
                    title
                    variant {
                      title
                      sku
                    }
                    originalUnitPriceSet {
                      presentmentMoney {
                        amount
                        currencyCode
                      }
                    }
                    discountedUnitPriceAfterAllDiscountsSet {
                      presentmentMoney {
                        amount
                        currencyCode
                      }
                    }
                    quantity
                  }
                }
              }
            }
          }
        }
      }
    `;

    return this.fetchShopifyData(query);
  }

  async determineSyncStrategy() {
    const lastSync = await this.syncStorage.getLastSync();
    const failedOrders = await this.syncStorage.getFailedOrders();
    const resumeTimestamp = await this.syncStorage.getResumeTimestamp();

    const strategy = {
      sync_type: 'smart',
      actions: [],
      needs_initial: lastSync === null,
      has_failed_orders: failedOrders.length > 0,
      resume_timestamp: resumeTimestamp
    };

    if (lastSync === null) {
      strategy.sync_type = 'initial';
      strategy.actions.push('Initial sync required - never synced before');
    } else {
      if (failedOrders.length > 0) {
        strategy.actions.push(`Retry ${failedOrders.length} failed orders`);
      }

      const syncFrom = resumeTimestamp || lastSync;
      strategy.actions.push(`Sync orders updated since ${syncFrom}`);
      strategy.sync_from_timestamp = syncFrom;
    }

    if (strategy.actions.length === 0) {
      strategy.actions.push('All orders are up to date');
    }

    return strategy;
  }

  calculateFees(transactions) {
    let totalFees = 0;
    try {
      for (const transaction of transactions) {
        if (transaction.fees) {
          for (const fee of transaction.fees) {
            if (fee.amount?.amount) {
              totalFees += parseFloat(fee.amount.amount);
            }
          }
        }
      }
    } catch (error) {
      // Ignore calculation errors
    }
    return totalFees;
  }

  getSafeAmount(priceSet, defaultValue = 0) {
    try {
      if (priceSet?.presentmentMoney?.amount) {
        return parseFloat(priceSet.presentmentMoney.amount);
      }
    } catch (error) {
      // Ignore parsing errors
    }
    return defaultValue;
  }

  getPaymentStatus(order) {
    const financialStatus = order.displayFinancialStatus?.toLowerCase() || '';
    const transactions = order.transactions || [];

    const statusMapping = {
      'pending': 'Pending',
      'authorized': 'Authorized',
      'partially_paid': 'Partially Paid',
      'paid': 'Paid',
      'partially_refunded': 'Partially Refunded',
      'refunded': 'Refunded',
      'voided': 'Voided',
      'expired': 'Expired'
    };

    if (statusMapping[financialStatus]) {
      return statusMapping[financialStatus];
    }

    if (transactions.length === 0) {
      return 'Unknown';
    }

    const hasSale = transactions.some(t => t.kind === 'sale' && t.status === 'success');
    const hasRefund = transactions.some(t => t.kind === 'refund' && t.status === 'success');
    const hasVoid = transactions.some(t => t.kind === 'void' && t.status === 'success');

    if (hasVoid) return 'Voided';
    if (hasRefund && hasSale) return 'Partially Refunded';
    if (hasRefund) return 'Refunded';
    if (hasSale) return 'Paid';
    return 'Pending';
  }

  getPaymentMethod(order) {
    const transactions = order.transactions || [];
    
    // Look for payment method name in transaction payment details
    for (const transaction of transactions) {
      if (transaction.paymentDetails?.paymentMethodName) {
        const methodName = transaction.paymentDetails.paymentMethodName;
        // Format payment method names for better readability
        switch (methodName) {
          case 'shop_pay_installments':
          case 'shopify_installments':
            return 'Shop Pay Installments';
          case 'card':
            return 'Credit Card';
          case 'paypal':
            return 'PayPal';
          default:
            return methodName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        }
      }
    }
    
    // Fallback to gateway information
    const paymentGateways = order.paymentGatewayNames || [];
    if (paymentGateways.length > 0) {
      const gateway = paymentGateways[0];
      switch (gateway) {
        case 'shopify_payments':
          return 'Shopify Payments';
        case 'paypal':
          return 'PayPal';
        default:
          return gateway.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
      }
    }
    
    return 'Unknown';
  }

  transformOrderData(order) {
    const lineItems = order.lineItems?.edges || [];
    const orderId = order.name;
    const orderDate = order.createdAt;
    const customerName = order.customer?.displayName || '';
    const customerEmail = order.email || '';
    const legacyId = order.legacyResourceId || '';
    const shopifyUrl = legacyId ? `https://admin.shopify.com/store/lil-nice-thing/orders/${legacyId}` : '';

    const totalTax = this.getSafeAmount(order.totalTaxSet);
    const totalFees = this.calculateFees(order.transactions || []);
    const paymentStatus = this.getPaymentStatus(order);
    const paymentMethod = this.getPaymentMethod(order);

    const processedItems = [];
    let totalListed = 0;
    let totalSold = 0;

    for (const itemEdge of lineItems) {
      const item = itemEdge.node;
      const productTitle = item.title || '';
      const variantTitle = item.variant?.title || '';
      const sku = item.variant?.sku || '';

      let productName = productTitle;
      if (variantTitle && variantTitle !== 'Default Title') {
        productName = `${productTitle} – ${variantTitle}`;
      }

      const originalPrice = this.getSafeAmount(item.originalUnitPriceSet);
      const discountedPrice = this.getSafeAmount(item.discountedUnitPriceAfterAllDiscountsSet);
      const quantity = item.quantity || 1;

      const lineListed = originalPrice * quantity;
      const lineSold = discountedPrice * quantity;

      totalListed += lineListed;
      totalSold += lineSold;

      processedItems.push({
        product_name: productName,
        sku,
        listed_for: lineListed,
        sold_for: lineSold,
        quantity
      });
    }

    const isMultiProduct = processedItems.length > 1;

    return {
      order_id: orderId,
      order_date: orderDate,
      customer_name: customerName,
      customer_email: customerEmail,
      shopify_url: shopifyUrl,
      total_tax: totalTax,
      total_fees: totalFees,
      total_listed: totalListed,
      total_sold: totalSold,
      payment_status: paymentStatus,
      payment_method: paymentMethod,
      is_multi_product: isMultiProduct,
      line_items: processedItems
    };
  }

  createNotionProperties(orderId, productName, date, customerName, customerEmail, 
                        listedFor, soldFor, tax, fee, sku, shopifyUrl, paymentStatus = null, 
                        paymentMethod = null, parentItem = null) {
    const netEarning = soldFor - fee;
    const toPayouts = soldFor + tax - fee;

    const properties = {
      "Order ID": {
        "title": [{ "text": { "content": orderId } }]
      },
      "Product name": {
        "rich_text": [{ "text": { "content": productName } }]
      },
      "Listed for": {
        "number": listedFor
      },
      "Sold for": {
        "number": soldFor
      },
      "Tax": {
        "number": tax
      },
      "Fee": {
        "number": fee
      },
      "Net earning": {
        "number": netEarning
      },
      "Payout": {
        "number": toPayouts
      }
    };

    // Add optional fields
    if (date) {
      properties["Date"] = { "date": { "start": date } };
    }
    if (customerName) {
      properties["Customer name"] = { "rich_text": [{ "text": { "content": customerName } }] };
    }
    if (customerEmail) {
      properties["Customer Email"] = { "email": customerEmail };
    }
    if (sku) {
      properties["SKU"] = { "rich_text": [{ "text": { "content": sku } }] };
    }
    if (shopifyUrl) {
      properties["Shopify URL"] = { "url": shopifyUrl };
    }
    if (paymentStatus) {
      properties["Payment Status"] = { "rich_text": [{ "text": { "content": paymentStatus } }] };
    }
    if (paymentMethod) {
      properties["Payment Method"] = { "rich_text": [{ "text": { "content": paymentMethod } }] };
    }
    
    // Add Merchant field - always "Shopify"
    properties["Merchant"] = { "rich_text": [{ "text": { "content": "Shopify" } }] };
    
    if (parentItem) {
      properties["Parent item"] = { "relation": [{ "id": parentItem }] };
    }

    return properties;
  }

  async deleteNotionPages(pageIds) {
    if (typeof pageIds === 'string') {
      pageIds = [pageIds];
    }

    if (!pageIds || pageIds.length === 0) {
      return;
    }

    console.log(`🗑️ Archiving ${pageIds.length} existing pages...`);

    for (let i = 0; i < pageIds.length; i++) {
      try {
        await this.notion.pages.update({
          page_id: pageIds[i],
          archived: true
        });
        console.log(`  🗑️ Archived page ${i + 1}/${pageIds.length}: ${pageIds[i]}`);
      } catch (error) {
        console.warn(`  ⚠️ Failed to archive page ${pageIds[i]}: ${error}`);
      }
    }
  }

  async createNotionPageWithRateLimit(properties) {
    const pageData = {
      parent: { database_id: this.notionDatabaseId },
      properties
    };

    // Rate limiting: 0.4s delay to stay under 150 requests/minute
    await new Promise(resolve => setTimeout(resolve, 400));

    return this.notion.pages.create(pageData);
  }

  async createNotionPage(orderData, updateResumePoint = true) {
    try {
      const order = orderData.node;
      const transformedData = this.transformOrderData(order);
      const orderId = transformedData.order_id;

      // Check if order already exists (for updates)
      const existingPageIds = await this.syncStorage.getSyncedOrderPageIds(orderId);
      if (existingPageIds.length > 0) {
        console.log(`🔄 Order ${orderId} exists - updating existing pages (${existingPageIds.length} old pages)`);
        await this.deleteNotionPages(existingPageIds);
      } else {
        console.log(`✨ Creating new pages for order ${orderId}`);
      }

      const createdPages = [];

      // Create parent page
      const productName = transformedData.is_multi_product 
        ? `${transformedData.line_items.length} products`
        : transformedData.line_items[0]?.product_name || 'Unknown Product';

      const properties = this.createNotionProperties(
        transformedData.order_id,
        productName,
        transformedData.order_date,
        transformedData.customer_name,
        transformedData.customer_email,
        transformedData.total_listed,
        transformedData.total_sold,
        transformedData.total_tax,
        transformedData.total_fees,
        '',
        transformedData.shopify_url,
        transformedData.payment_status,
        transformedData.payment_method
      );

      const parentResponse = await this.createNotionPageWithRateLimit(properties);
      createdPages.push(parentResponse);

      console.log(`✅ Created parent page for order ${transformedData.order_id}`);

      // For multi-product orders, create individual line item pages
      if (transformedData.is_multi_product) {
        const parentPageId = parentResponse.id;
        console.log(`🛍️ Creating ${transformedData.line_items.length} line item pages...`);

        for (let i = 0; i < transformedData.line_items.length; i++) {
          const lineItem = transformedData.line_items[i];
          const lineItemOrderId = `${transformedData.order_id}.${i + 1}`;

          const lineProperties = this.createNotionProperties(
            lineItemOrderId,
            lineItem.product_name,
            transformedData.order_date,
            '', // Blank customer name for line items
            '', // Blank customer email for line items
            lineItem.listed_for,
            lineItem.sold_for,
            0, // Blank tax for line items
            0, // Blank fee for line items
            lineItem.sku,
            transformedData.shopify_url,
            transformedData.payment_status,
            transformedData.payment_method,
            parentPageId
          );

          const lineResponse = await this.createNotionPageWithRateLimit(lineProperties);
          createdPages.push(lineResponse);

          console.log(`  ✅ Created line item ${i + 1}/${transformedData.line_items.length}: ${lineItem.product_name.substring(0, 30)}...`);
        }
      }

      // Record success in sync storage with all page IDs and updatedAt timestamp
      const orderUpdatedAt = order.updatedAt;
      const allPageIds = createdPages.map(page => page.id);
      await this.syncStorage.markOrderSynced(orderId, allPageIds, updateResumePoint ? orderUpdatedAt : null);

      return createdPages;

    } catch (error) {
      const orderId = orderData.node.name;
      const errorMessage = error.toString();
      console.error(`❌ Error creating Notion page for order ${orderId}: ${errorMessage}`);

      // Record failure in sync storage
      await this.syncStorage.markOrderFailed(orderId, errorMessage);
      return null;
    }
  }

  async syncOrdersToNotion(mode = 'smart', limit = 50, specificOrderId = null) {
    try {
      // Check if another sync is already in progress
      console.log('🔍 Checking if sync is already in progress...');
      if (await this.syncStorage.isSyncInProgress()) {
        console.log('⚠️ Another sync is already running - blocking this request');
        return {
          status: 'error',
          message: 'Another sync is already in progress. Please wait for it to complete.',
          timestamp: new Date().toISOString()
        };
      }

      // Start sync lock (except for single mode which doesn't need locking)
      if (mode !== 'single') {
        await this.syncStorage.startSyncLock();
      }

      // Determine sync strategy
      let syncStrategy;
      if (mode === 'initial') {
        syncStrategy = { sync_type: 'initial', actions: ['Initial sync requested'] };
      } else if (mode === 'single') {
        syncStrategy = { sync_type: 'single', actions: [`Single order sync for ${specificOrderId}`] };
      } else {
        syncStrategy = await this.determineSyncStrategy();
      }

      console.log(`🔍 Sync strategy: ${syncStrategy.sync_type}`);
      syncStrategy.actions.forEach(action => console.log(`   - ${action}`));

      // Track results
      let createdPagesCount = 0;
      let processedOrders = 0;
      const errors = [];

      if (syncStrategy.sync_type === 'initial' || syncStrategy.needs_initial) {
        // Initial sync: get orders from oldest to newest
        console.log(`🚀 Starting initial sync (limit: ${limit})`);
        console.log('   📊 Processing from VERY FIRST ORDER chronologically (oldest updatedAt first)');
        
        const ordersData = await this.getShopifyOrders(limit, null, null, true);
        const orders = ordersData.data.orders.edges;

        console.log(`Found ${orders.length} orders for initial sync`);

        // Show first and last order timestamps to verify chronological order
        if (orders.length > 0) {
          const firstOrder = orders[0].node;
          const lastOrder = orders[orders.length - 1].node;
          console.log(`   📅 First order: ${firstOrder.name} (updatedAt: ${firstOrder.updatedAt})`);
          console.log(`   📅 Last order: ${lastOrder.name} (updatedAt: ${lastOrder.updatedAt})`);
        }

        // Track the highest updatedAt timestamp for resume capability
        let highestUpdatedAt = null;

        // Process each order
        for (let i = 0; i < orders.length; i++) {
          const orderData = orders[i];
          const orderId = orderData.node.name;
          const orderUpdatedAt = orderData.node.updatedAt;
          
          // For initial sync, process all orders
          // For smart sync, check if we really need to update this order
          let shouldProcess = true;
          
          if (syncStrategy.sync_type !== 'initial') {
            const existingPageIds = await this.syncStorage.getSyncedOrderPageIds(orderId);
            if (existingPageIds.length > 0) {
              // Order exists - only process if it was actually updated after our last processed order timestamp
              const lastProcessedTimestamp = await this.syncStorage.getResumeTimestamp();
              const orderUpdatedAtDate = new Date(orderUpdatedAt);
              const lastProcessedDate = lastProcessedTimestamp ? new Date(lastProcessedTimestamp) : new Date(0);
              
              if (orderUpdatedAtDate < lastProcessedDate) {
                console.log(`⏭️ Skipping ${orderId} - already synced (${orderUpdatedAt} < ${lastProcessedTimestamp})`);
                shouldProcess = false;
              } else {
                console.log(`🔄 Processing ${orderId} - updated since last sync (${orderUpdatedAt} >= ${lastProcessedTimestamp})`);
              }
            }
          }
          
          if (shouldProcess) {
            const result = await this.createNotionPage(orderData);
            if (result) {
              processedOrders++;
              createdPagesCount += result.length;
            } else {
              errors.push(orderId);
            }
          }

          // Track highest updatedAt for resume point (whether processed or skipped)
          if (!highestUpdatedAt || orderUpdatedAt > highestUpdatedAt) {
            highestUpdatedAt = orderUpdatedAt;
          }

          // Progress logging every 10 orders
          if ((i + 1) % 10 === 0) {
            console.log(`📊 Progress: ${i + 1}/${orders.length} orders processed (${processedOrders} successful, ${errors.length} errors)`);
          }
        }

        // Update resume point to highest updatedAt we've seen (not when we completed sync)
        if (highestUpdatedAt) {
          await this.syncStorage.updateResumePoint(highestUpdatedAt);
          console.log(`📍 Updated resume point to highest order updatedAt: ${highestUpdatedAt}`);
        }

      } else {
        // Smart sync: handle failed orders and updated orders
        console.log('🧠 Starting smart sync');

        // 1. Retry failed orders first
        const failedOrderIds = await this.syncStorage.getFailedOrders();
        if (failedOrderIds.length > 0) {
          console.log(`🔄 Retrying ${failedOrderIds.length} failed orders`);
          const retryData = await this.getShopifyOrders(50, null, failedOrderIds);
          const retryOrders = retryData.data.orders.edges;
          
          for (let i = 0; i < retryOrders.length; i++) {
            const result = await this.createNotionPage(retryOrders[i]);
            if (result) {
              processedOrders++;
              createdPagesCount += result.length;
            } else {
              errors.push(retryOrders[i].node.name);
            }

            // Progress logging every 5 failed orders
            if ((i + 1) % 5 === 0) {
              console.log(`🔄 Retry Progress: ${i + 1}/${retryOrders.length} failed orders processed`);
            }
          }
        }

        // 2. Get orders updated since last sync or resume point
        const syncFrom = syncStrategy.sync_from_timestamp;
        if (syncFrom) {
          console.log(`📥 Fetching orders updated since ${syncFrom} (oldest to newest)`);
          const updatedData = await this.getShopifyOrders(limit, syncFrom, null, true);
          const updatedOrders = updatedData.data.orders.edges;

          console.log(`Found ${updatedOrders.length} updated orders`);

          // Track highest updatedAt for resume capability
          let highestUpdatedAt = null;

          for (let i = 0; i < updatedOrders.length; i++) {
            const orderData = updatedOrders[i];
            const orderId = orderData.node.name;
            const orderUpdatedAt = orderData.node.updatedAt;
            
            // Check if we really need to process this order
            let shouldProcess = true;
            const existingPageIds = await this.syncStorage.getSyncedOrderPageIds(orderId);
            
            if (existingPageIds.length > 0) {
              // Order exists - only process if it was actually updated after our last processed order timestamp
              const lastProcessedTimestamp = await this.syncStorage.getResumeTimestamp();
              const orderUpdatedAtDate = new Date(orderUpdatedAt);
              const lastProcessedDate = lastProcessedTimestamp ? new Date(lastProcessedTimestamp) : new Date(0);
              
              if (orderUpdatedAtDate < lastProcessedDate) {
                console.log(`⏭️ Skipping ${orderId} - already synced (${orderUpdatedAt} < ${lastProcessedTimestamp})`);
                shouldProcess = false;
              } else {
                console.log(`🔄 Processing ${orderId} - updated since last sync (${orderUpdatedAt} >= ${lastProcessedTimestamp})`);
              }
            }
            
            if (shouldProcess) {
              const result = await this.createNotionPage(orderData);
              if (result) {
                processedOrders++;
                createdPagesCount += result.length;
              } else {
                errors.push(orderId);
              }
            }

            // Track highest updatedAt for resume point (whether processed or skipped)
            if (!highestUpdatedAt || orderUpdatedAt > highestUpdatedAt) {
              highestUpdatedAt = orderUpdatedAt;
            }

            // Progress logging every 10 orders
            if ((i + 1) % 10 === 0) {
              console.log(`📊 Updated Orders Progress: ${i + 1}/${updatedOrders.length} orders processed (${processedOrders} total successful)`);
            }
          }

          // Update resume point to highest updatedAt we've seen
          if (highestUpdatedAt) {
            await this.syncStorage.updateResumePoint(highestUpdatedAt);
            console.log(`📍 Updated resume point to highest order updatedAt: ${highestUpdatedAt}`);
          }
        }
      }
      
      if (syncStrategy.sync_type === 'single') {
        // Single order sync: get one specific order by ID
        console.log(`🎯 Starting single order sync for ${specificOrderId}`);
        
        // Remove # prefix if present (support both "1234" and "#1234")
        const cleanOrderId = specificOrderId.startsWith('#') ? specificOrderId.substring(1) : specificOrderId;
        
        const orderData = await this.getShopifyOrders(1, null, [cleanOrderId]);
        const orders = orderData.data.orders.edges;

        if (orders.length === 0) {
          console.log(`❌ Order ${specificOrderId} not found in Shopify`);
          errors.push(`Order ${specificOrderId} not found`);
        } else {
          const orderToSync = orders[0];
          const orderId = orderToSync.node.name;
          console.log(`📦 Found order ${orderId}, processing...`);
          
          const result = await this.createNotionPage(orderToSync, false); // Don't update resume point for single sync
          if (result) {
            processedOrders++;
            createdPagesCount += result.length;
            console.log(`✅ Successfully synced order ${orderId} (${result.length} pages created) - No sync state updated`);
          } else {
            errors.push(orderId);
            console.log(`❌ Failed to sync order ${orderId}`);
          }
        }
      }

      // Mark sync as completed (except for single mode which shouldn't affect sync state)
      if (syncStrategy.sync_type !== 'single') {
        await this.syncStorage.completeSync();
      }

      // Release sync lock (only if we acquired it)
      if (syncStrategy.sync_type !== 'single') {
        await this.syncStorage.endSyncLock();
      }

      // Return summary
      const summary = {
        status: 'success',
        sync_type: syncStrategy.sync_type,
        strategy: syncStrategy,
        processed_orders: processedOrders,
        created_pages: createdPagesCount,
        errors,
        timestamp: new Date().toISOString()
      };

      console.log(`✅ Sync completed: ${processedOrders} orders processed, ${createdPagesCount} pages created`);
      if (errors.length > 0) {
        console.log(`⚠️  ${errors.length} orders failed: ${errors}`);
      }

      return summary;

    } catch (error) {
      // Make sure to release sync lock even if sync fails
      try {
        await this.syncStorage.endSyncLock();
      } catch {
        // Don't let cleanup failure mask the original error
      }

      const errorSummary = {
        status: 'error',
        message: error.toString(),
        timestamp: new Date().toISOString()
      };
      console.error(`❌ Sync failed: ${error}`);
      return errorSummary;
    }
  }

  async testConnections() {
    const results = { shopify: false, notion: false, errors: [] };

    // Test Shopify connection
    try {
      const testQuery = `
        query {
          shop {
            name
            email
          }
        }
      `;
      const shopifyResponse = await this.fetchShopifyData(testQuery);
      const shopName = shopifyResponse.data.shop.name;
      console.log(`✅ Shopify connection successful - Store: ${shopName}`);
      results.shopify = true;
    } catch (error) {
      const errorMsg = `❌ Shopify connection failed: ${error}`;
      console.log(errorMsg);
      results.errors.push(errorMsg);
    }

    // Test Notion connection
    try {
      const database = await this.notion.databases.retrieve({ database_id: this.notionDatabaseId });
      const dbTitle = database.title?.[0]?.plain_text || 'Untitled';
      console.log(`✅ Notion connection successful - Database: ${dbTitle}`);
      results.notion = true;
    } catch (error) {
      const errorMsg = `❌ Notion connection failed: ${error}`;
      console.log(errorMsg);
      results.errors.push(errorMsg);
    }

    return results;
  }
}

// Vercel serverless function handler
export default async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-API-Key');

  const now = new Date().toISOString();
  console.log(`[${now}] ${req.method} request received`);

  if (req.method === 'OPTIONS') {
    // Handle CORS preflight
    console.log(`[${now}] OPTIONS request received (CORS)`);
    return res.status(200).end();
  }

  try {
    // Check for API key authentication
    const apiKey = req.headers['x-api-key'] || req.query.api_key;
    const expectedApiKey = process.env.SYNC_API_KEY?.trim();
    
    
    // Only require API key if it's configured (i.e., in production)
    if (expectedApiKey) {
      if (!apiKey || apiKey !== expectedApiKey) {
        console.warn(`Unauthorized sync attempt from ${req.headers['x-forwarded-for'] || req.connection?.remoteAddress}`);
        return res.status(401).json({
          status: 'error',
          message: 'Unauthorized - Invalid or missing API key',
          timestamp: now
        });
      }
      console.log('✅ API key authenticated');
    } else {
      console.log('🔓 Development mode - API key not required');
    }


    const sync = new ShopifyNotionSync();

    if (req.method === 'GET') {
      // Handle GET requests for sync status or connection testing
      const { endpoint } = req.query;

      if (endpoint === 'status') {
        // Get sync status and statistics
        console.log('Getting sync status...');
        const stats = await sync.syncStorage.getSyncStatistics();
        const strategy = await sync.determineSyncStrategy();

        const response = {
          status: 'sync_status',
          message: 'Current sync status and statistics',
          statistics: stats,
          next_sync_strategy: strategy,
          timestamp: now
        };

        console.log(`GET response: ${JSON.stringify(response)}`);
        return res.status(200).json(response);

      } else {
        // Default: test connections
        console.log('Testing connections...');
        const testResults = await sync.testConnections();

        const response = {
          status: 'connection_test',
          message: 'Testing Shopify and Notion connections',
          results: testResults,
          timestamp: now
        };

        console.log(`GET response: ${JSON.stringify(response)}`);
        return res.status(200).json(response);
      }

    } else if (req.method === 'POST') {
      // Handle POST requests - Perform actual sync
      console.log(`[${now}] POST request received - Starting sync`);

      // Get sync parameters from query and body
      const syncMode = req.query.mode || 'smart'; // 'initial', 'smart', or 'single'
      const syncLimit = parseInt(req.query.limit || req.body?.limit || 50);
      const orderId = req.query.order_id;

      // Validate parameters
      if (syncMode === 'single' && !orderId) {
        throw new Error('order_id is required for single order sync mode');
      }
      
      if (syncMode !== 'single' && (syncLimit < 1 || syncLimit > 1000)) {
        throw new Error(`Limit must be between 1 and 1000, got: ${syncLimit}`);
      }

      if (syncMode === 'single') {
        console.log(`Sync mode: ${syncMode}, order_id: ${orderId}`);
      } else {
        console.log(`Sync mode: ${syncMode}, limit: ${syncLimit}`);
      }

      // Perform the sync
      const syncResults = await sync.syncOrdersToNotion(syncMode, syncLimit, orderId);

      // Send response
      const response = {
        status: 'sync_completed',
        message: '🚀 Shopify → Notion sync completed!',
        sync_results: syncResults,
        request_info: {
          limit: syncLimit,
          source: req.headers['user-agent']?.toLowerCase().includes('notion') ? 'Notion Button' : 'Direct API'
        },
        timestamp: now
      };

      console.log('Sync completed successfully:', syncResults);
      return res.status(200).json(response);

    } else {
      return res.status(405).json({
        status: 'error',
        message: `Method ${req.method} not allowed`,
        timestamp: now
      });
    }

  } catch (error) {
    console.error(`${req.method} request failed:`, error);
    
    const errorResponse = {
      status: 'error',
      message: `Request failed: ${error.toString()}`,
      error_type: error.constructor.name,
      timestamp: now
    };

    return res.status(500).json(errorResponse);
  }
}