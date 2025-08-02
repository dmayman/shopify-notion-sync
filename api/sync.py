# api/sync.py
import json

def handler(request):
    """Vercel serverless function handler"""
    
    # Set response headers
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json'
    }
    
    if request.method == 'OPTIONS':
        # Handle CORS preflight
        return {
            'statusCode': 200,
            'headers': headers,
            'body': ''
        }
    
    elif request.method == 'GET':
        # Handle GET requests for testing
        response_data = {
            "status": "success",
            "message": "Shopify-Notion sync endpoint is working!",
            "method": "GET",
            "static_value": "Hello from Vercel! 🚀",
            "instructions": "Make a POST request to trigger the sync"
        }
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(response_data, indent=2)
        }
    
    elif request.method == 'POST':
        try:
            # Get request body if present
            request_data = {}
            if hasattr(request, 'body') and request.body:
                try:
                    request_data = json.loads(request.body)
                except json.JSONDecodeError:
                    request_data = {"raw_data": str(request.body)}
            
            # This is where we'll add Shopify GraphQL logic later
            # For now, return static data
            
            response_data = {
                "status": "success",
                "message": "Sync completed successfully! ✅",
                "static_value": "This will be replaced with real Shopify data",
                "timestamp": "2025-08-02T12:00:00Z",
                "records_processed": 42,
                "received_data": request_data,
                "next_steps": [
                    "Add Shopify GraphQL queries",
                    "Connect to Notion database",
                    "Add real data synchronization"
                ]
            }
            
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(response_data, indent=2)
            }
            
        except Exception as e:
            error_response = {
                "status": "error",
                "message": f"Sync failed: {str(e)}",
                "static_value": "Error occurred",
                "error_type": type(e).__name__
            }
            
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps(error_response, indent=2)
            }
    
    else:
        # Handle unsupported methods
        return {
            'statusCode': 405,
            'headers': headers,
            'body': json.dumps({
                "status": "error",
                "message": f"Method {request.method} not allowed"
            })
        }