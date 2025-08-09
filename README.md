# Shopify-Notion Sync

A serverless function that synchronizes order data from Shopify to a Notion database with complete resume capability and chronological processing.

## Features

- ✅ **Chronological Sync**: Processes orders from oldest to newest by `updatedAt` timestamp
- ✅ **Resume Capability**: Can resume from any interruption using date-based checkpoints
- ✅ **Rate Limiting**: Respects Notion API limits (2700 requests per 15 minutes)
- ✅ **Multi-Product Orders**: Handles single and multi-product orders with proper relationships
- ✅ **Error Tracking**: Tracks failed orders for retry logic
- ✅ **Progress Logging**: Detailed logging every 10 orders processed
- ✅ **Database Storage**: Uses Neon PostgreSQL for reliable sync state management

## API Endpoints

### `/api/sync`

Main synchronization endpoint with multiple operation modes.

#### **GET** - Connection Testing & Status

**Test Connections** (Default)
```bash
GET /api/sync
# or
GET /api/sync?endpoint=test
```

**Response:**
```json
{
  "status": "connection_test",
  "message": "Testing Shopify and Notion connections",
  "results": {
    "shopify": true,
    "notion": true,
    "errors": []
  },
  "timestamp": "2025-08-06T10:30:00.000Z"
}
```

**Get Sync Status**
```bash
GET /api/sync?endpoint=status
```

**Response:**
```json
{
  "status": "sync_status",
  "message": "Current sync status and statistics",
  "statistics": {
    "last_sync": "2025-08-06T10:30:00Z",
    "total_synced_orders": 150,
    "failed_orders_count": 2,
    "failed_orders": ["ORDER123", "ORDER456"],
    "last_processed_updated_at": "2024-06-15T09:30:00Z"
  },
  "next_sync_strategy": {
    "sync_type": "smart",
    "actions": ["Sync orders updated since 2024-06-15T09:30:00Z"],
    "resume_timestamp": "2024-06-15T09:30:00Z"
  },
  "timestamp": "2025-08-06T10:30:00.000Z"
}
```

#### **POST** - Execute Sync

All sync operations support a `limit` parameter (1-1000, default: 50) that can be specified either in the URL query parameters or request body. URL parameters take precedence over request body values.

**Basic Sync** (Default: 50 orders)
```bash
POST /api/sync
Content-Type: application/json

{}
```

**Custom Batch Size** (via request body)
```bash
POST /api/sync
Content-Type: application/json

{"limit": 100}
```

**Custom Batch Size** (via URL parameter)
```bash
POST /api/sync?limit=100
Content-Type: application/json

{}
```

**Force Initial Sync** (with custom limit)
```bash
POST /api/sync?mode=initial&limit=25
Content-Type: application/json

{}
```

**Combined Parameters** (URL params take precedence over request body)
```bash
POST /api/sync?mode=initial&limit=75
Content-Type: application/json

{"limit": 50}
# Final limit will be 75 (from URL param)
```

**Response:**
```json
{
  "status": "sync_completed",
  "message": "🚀 Shopify → Notion sync completed!",
  "sync_results": {
    "status": "success",
    "sync_type": "initial",
    "processed_orders": 50,
    "created_pages": 50,
    "errors": [],
    "strategy": {
      "sync_type": "initial",
      "actions": ["Initial sync required - never synced before"]
    },
    "timestamp": "2025-08-06T10:45:00.000Z"
  },
  "request_info": {
    "limit": 50,
    "source": "Direct API"
  },
  "timestamp": "2025-08-06T10:45:00.000Z"
}
```

#### **OPTIONS** - CORS Preflight

Handles cross-origin requests for browser compatibility.

### `/api/test_db`

Testing endpoint for Neon database connection and sync storage functionality.

#### **GET** - Test Database Operations

```bash
GET /api/test_db
```

**Response:**
```json
{
  "status": "success",
  "message": "Neon database test completed successfully",
  "test_results": {
    "initial_sync_state": {
      "last_sync": null,
      "total_synced_orders": 0,
      "failed_orders_count": 0,
      "sync_in_progress": false
    },
    "sync_lock_test": {
      "before_lock": false,
      "after_lock": true,
      "after_unlock": false
    },
    "sync_statistics": {
      "last_sync": null,
      "total_synced_orders": 1,
      "total_notion_pages": 3,
      "failed_orders_count": 1
    }
  },
  "timestamp": "2025-08-08T10:30:00.000Z"
}
```

## Sync Process

### Initial Sync
- Processes orders from **oldest to newest** by `updatedAt` timestamp
- No date filter applied - starts from very first order
- Creates resume checkpoints after each successful order
- Processes in configurable batches (default: 50 orders)

### Incremental Sync  
- Uses `updated_at:>={resume_timestamp}` filter
- Continues chronologically from last processed order
- Retries failed orders from previous runs
- Maintains chronological order for consistency

### Data Structure in Notion
Each order creates a Notion page with:

- **Order ID**: Shopify order number
- **Product name**: Product title (or "X products" for multi-product orders)
- **Date**: Order creation date
- **Customer name**: Customer display name
- **Customer Email**: Customer email address
- **Listed for**: Original total price
- **Sold for**: Final total price (after discounts)
- **Tax**: Total tax amount
- **Fee**: Payment processing fees
- **Net earning**: Sold for - Fee
- **Payout**: Sold for + Tax - Fee  
- **Payment Status**: Current payment status
- **SKU**: Product SKU (for single products)
- **Shopify URL**: Direct link to order in Shopify admin

### Custom Features
- **Multi-product orders**: Display 🛍️ emoji and summary
- **Category-based emojis**: Automatic emoji selection based on product types
- **Parent-child relationships**: Link line items to parent orders (when implemented)

## Error Handling

### Common Error Responses

**Missing Environment Variables:**
```json
{
  "status": "error",
  "message": "Sync failed: Missing environment variables: NOTION_TOKEN, NOTION_DATABASE_ID",
  "timestamp": "2025-08-06T10:30:00.000Z"
}
```

**API Connection Failure:**
```json
{
  "status": "error",
  "message": "Sync failed: Shopify API error: 401 - Unauthorized",
  "timestamp": "2025-08-06T10:30:00.000Z"
}
```

**Rate Limit Exceeded:**
```json
{
  "status": "error", 
  "message": "Sync failed: Notion API rate limit exceeded",
  "timestamp": "2025-08-06T10:30:00.000Z"
}
```

### Retry Logic
- Failed orders are tracked in blob storage
- Next sync run will automatically retry failed orders
- Failed orders are processed before new orders
- Maximum retry attempts prevent infinite loops

## Environment Variables

Required environment variables (set in Vercel dashboard):

```bash
# Shopify Configuration
SHOPIFY_STORE_URL=your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Notion Configuration  
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Neon Database
DATABASE_URL=postgresql://user:password@host/database?sslmode=require
```

## Usage Examples

### Complete Initial Sync Workflow

1. **Test connections first:**
```bash
curl -X GET https://your-project.vercel.app/api/sync
```

2. **Check current status:**
```bash
curl -X GET https://your-project.vercel.app/api/sync?endpoint=status
```

3. **Start initial sync:**
```bash
curl -X POST https://your-project.vercel.app/api/sync \
  -H "Content-Type: application/json" \
  -d '{"limit": 50}'
```

4. **Continue with next batches:**
```bash
# Repeat POST requests until all orders are synced
curl -X POST https://your-project.vercel.app/api/sync \
  -H "Content-Type: application/json" \
  -d '{"limit": 50}'
```

### Monitoring Progress

Check sync statistics between batches:
```bash
curl -X GET https://your-project.vercel.app/api/sync?endpoint=status
```

The response will show:
- Total orders synced so far
- Current resume timestamp  
- Any failed orders that need retry
- Next sync strategy

## Rate Limiting & Performance

- **Notion API**: 0.4 second delay between page creations (150 requests/minute)
- **Batch processing**: Default 50 orders per batch (~30-40 seconds)
- **Shopify GraphQL**: Optimized queries with proper cost management
- **Resume capability**: Can handle interruptions gracefully

### Vercel Plan Considerations

- **Hobby Plan**: 10 second timeout - use smaller batches (20-25 orders)
- **Pro Plan**: 60 second timeout - can handle 100+ orders per batch  
- **Enterprise Plan**: 900 second timeout - can process 500+ orders per batch

## Deployment

1. **Clone and deploy to Vercel:**
```bash
git clone <repository>
cd shopify-notion-sync
vercel --prod
```

2. **Set environment variables** in Vercel dashboard

3. **Test the deployment:**
```bash
curl https://your-project.vercel.app/api/sync
```

## Troubleshooting

### Sync Appears Stuck
- Check `/api/sync?endpoint=status` for current state
- Look for failed orders that need retry
- Verify environment variables are set correctly

### Timeout Errors
- Reduce batch size with `{"limit": 25}`
- Check your Vercel plan limits
- Monitor processing time in logs

### Missing Orders
- Sync processes chronologically - check `last_processed_updated_at`
- Failed orders are tracked separately and retried
- Use initial sync mode to start from beginning

### Rate Limit Issues
- Built-in rate limiting should prevent this
- Check if multiple syncs are running simultaneously  
- Wait 15 minutes for Notion rate limits to reset

## Architecture

- **Platform**: Vercel serverless functions
- **Runtime**: Python 3.9
- **Database**: Neon PostgreSQL for sync state management
- **APIs**: Shopify GraphQL Admin API, Notion REST API
- **Dependencies**: `requests`, `notion-client`, `psycopg[binary,pool]`