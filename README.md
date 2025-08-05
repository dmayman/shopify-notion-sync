# Shopify-Notion Sync

A serverless function to sync data between Shopify and Notion.

## Current Status: Proof of Concept
- ✅ Vercel serverless function setup
- ✅ Static response for testing
- 🔄 TODO: Add Shopify GraphQL integration
- 🔄 TODO: Add Notion database updates

## API Endpoints

### GET /api/sync
Test endpoint that returns a static response.

### POST /api/sync  
Main sync endpoint (currently returns static data).

## Testing

You can test the endpoint by visiting:
- `https://your-project.vercel.app/api/sync` (GET)
- Or by making a POST request to the same URL

## Deployment

This project is deployed on Vercel. Any push to the main branch will automatically deploy.

## Environment Variables 
These are private and set in Vercel.

- `SHOPIFY_STORE_URL`
- `SHOPIFY_ACCESS_TOKEN` 
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`