/**
 * Debug endpoint to find Shop Pay installment orders
 */

import { Client } from '@notionhq/client';

export default async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  const now = new Date().toISOString();

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
    // Get environment variables
    const shopifyStoreUrl = process.env.SHOPIFY_STORE_URL;
    const shopifyAccessToken = process.env.SHOPIFY_ACCESS_TOKEN;

    if (!shopifyStoreUrl || !shopifyAccessToken) {
      throw new Error('Missing Shopify environment variables');
    }

    // Search for Shop Pay orders specifically
    const searchQueries = [
      // Search by gateway
      'query: "gateway:shopify"',
      // Search by payment method name (for newer orders)
      'query: "payment_method_name:shop_pay_installments"',
      // Search by financial status patterns common with installments
      'query: "financial_status:pending OR financial_status:partially_paid"',
      // Search recent orders without date filter
      ''
    ];

    const results = {};

    for (let i = 0; i < searchQueries.length; i++) {
      const searchQuery = searchQueries[i];
      const queryName = searchQuery ? searchQuery.replace('query: ', '').replace(/"/g, '') : 'recent_orders';
      
      console.log(`🔍 Searching: ${queryName}`);

      const query = `
        query {
          orders(first: 10, sortKey: UPDATED_AT, reverse: true, ${searchQuery}) {
            edges {
              node {
                id
                name
                createdAt
                updatedAt
                displayFinancialStatus
                paymentGatewayNames
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
                }
                totalPriceSet {
                  presentmentMoney {
                    amount
                    currencyCode
                  }
                }
              }
            }
          }
        }
      `;

      const url = `https://${shopifyStoreUrl}/admin/api/2023-10/graphql.json`;
      
      const headers = {
        'X-Shopify-Access-Token': shopifyAccessToken,
        'Content-Type': 'application/json'
      };

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ query })
      });

      if (!response.ok) {
        throw new Error(`Shopify API error for ${queryName}: ${response.status}`);
      }

      const data = await response.json();

      if (data.errors) {
        console.error(`GraphQL errors for ${queryName}:`, data.errors);
        results[queryName] = { error: data.errors };
        continue;
      }

      const orders = data.data.orders.edges;
      results[queryName] = {
        count: orders.length,
        orders: orders.map(edge => ({
          id: edge.node.name,
          createdAt: edge.node.createdAt,
          updatedAt: edge.node.updatedAt,
          financialStatus: edge.node.displayFinancialStatus,
          paymentGateways: edge.node.paymentGatewayNames,
          transactions: edge.node.transactions.map(t => ({
            gateway: t.gateway,
            kind: t.kind,
            status: t.status,
            paymentMethodName: t.paymentDetails?.paymentMethodName || null
          })),
          totalAmount: edge.node.totalPriceSet?.presentmentMoney?.amount
        }))
      };

      console.log(`  Found ${orders.length} orders`);
    }

    return res.status(200).json({
      status: 'success',
      message: 'Shop Pay installments debug search completed',
      search_results: results,
      recommendations: [
        'Look for orders with gateway: "shopify"',
        'Check paymentMethodName for "shop_pay_installments"',
        'Verify date ranges - installment orders might have unexpected timestamps',
        'Check if orders appear in "recent_orders" but not in date-filtered queries'
      ],
      timestamp: now
    });

  } catch (error) {
    console.error('Debug Shop Pay search failed:', error);
    
    return res.status(500).json({
      status: 'error',
      message: `Debug search failed: ${error.toString()}`,
      timestamp: now
    });
  }
}