# shopify_notion_sync.py
import os
import requests
import json
from datetime import datetime
from notion_client import Client

class ShopifyNotionSync:
    def __init__(self):
        # Get environment variables
        self.shopify_store_url = os.getenv('SHOPIFY_STORE_URL')
        self.shopify_access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')
        self.notion_token = os.getenv('NOTION_TOKEN')
        self.notion_database_id = os.getenv('NOTION_DATABASE_ID')
        
        # Validate environment variables
        if not all([self.shopify_store_url, self.shopify_access_token, self.notion_token, self.notion_database_id]):
            missing = []
            if not self.shopify_store_url: missing.append('SHOPIFY_STORE_URL')
            if not self.shopify_access_token: missing.append('SHOPIFY_ACCESS_TOKEN')
            if not self.notion_token: missing.append('NOTION_TOKEN')
            if not self.notion_database_id: missing.append('NOTION_DATABASE_ID')
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")
        
        # Initialize Notion client
        self.notion = Client(auth=self.notion_token)
        
        print(f"Initialized sync for store: {self.shopify_store_url}")
        print(f"Notion database ID: {self.notion_database_id}")

    def fetch_shopify_data(self, query):
        """Fetch data from Shopify using GraphQL"""
        url = f'https://{self.shopify_store_url}/admin/api/2023-10/graphql.json'
        
        headers = {
            'X-Shopify-Access-Token': self.shopify_access_token,
            'Content-Type': 'application/json'
        }
        
        payload = {'query': query}
        
        print(f"Making request to Shopify GraphQL API...")
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"Shopify API error: {response.status_code} - {response.text}")
        
        data = response.json()
        
        if 'errors' in data:
            raise Exception(f"Shopify GraphQL errors: {data['errors']}")
        
        print(f"Successfully fetched data from Shopify")
        return data

    def get_recent_orders(self, limit=10):
        """Get recent orders from Shopify"""
        query = f"""
        query {{
            orders(first: {limit}, sortKey: CREATED_AT, reverse: true) {{
                edges {{
                    node {{
                        id
                        name
                        email
                        createdAt
                        updatedAt
                        totalPriceV2 {{
                            amount
                            currencyCode
                        }}
                        financialStatus
                        fulfillmentStatus
                        customer {{
                            firstName
                            lastName
                            email
                        }}
                        shippingAddress {{
                            city
                            country
                        }}
                        lineItems(first: 5) {{
                            edges {{
                                node {{
                                    title
                                    quantity
                                    variant {{
                                        title
                                        price
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        
        return self.fetch_shopify_data(query)

    def create_notion_page(self, order_data):
        """Create a new page in Notion database"""
        try:
            order = order_data['node']
            
            # Prepare properties for Notion
            properties = {
                "Order ID": {
                    "title": [
                        {
                            "text": {
                                "content": order['name']
                            }
                        }
                    ]
                },
                "Total": {
                    "number": float(order['totalPriceV2']['amount'])
                },
                "Currency": {
                    "rich_text": [
                        {
                            "text": {
                                "content": order['totalPriceV2']['currencyCode']
                            }
                        }
                    ]
                },
                "Status": {
                    "select": {
                        "name": order['financialStatus'].title()
                    }
                },
                "Created": {
                    "date": {
                        "start": order['createdAt']
                    }
                }
            }
            
            # Add customer info if available
            if order.get('customer'):
                customer = order['customer']
                customer_name = f"{customer.get('firstName', '')} {customer.get('lastName', '')}".strip()
                if customer_name:
                    properties["Customer"] = {
                        "rich_text": [
                            {
                                "text": {
                                    "content": customer_name
                                }
                            }
                        ]
                    }
                
                if customer.get('email'):
                    properties["Email"] = {
                        "email": customer['email']
                    }
            
            # Create the page
            response = self.notion.pages.create(
                parent={"database_id": self.notion_database_id},
                properties=properties
            )
            
            print(f"Created Notion page for order {order['name']}")
            return response
            
        except Exception as e:
            print(f"Error creating Notion page for order {order_data['node']['name']}: {e}")
            return None

    def sync_orders_to_notion(self, limit=5):
        """Main sync function - get orders from Shopify and create Notion pages"""
        try:
            print(f"Starting sync of {limit} recent orders...")
            
            # Fetch orders from Shopify
            orders_data = self.get_recent_orders(limit)
            orders = orders_data['data']['orders']['edges']
            
            print(f"Found {len(orders)} orders to sync")
            
            # Track results
            created_count = 0
            errors = []
            
            # Create Notion pages for each order
            for order_data in orders:
                result = self.create_notion_page(order_data)
                if result:
                    created_count += 1
                else:
                    errors.append(order_data['node']['name'])
            
            # Return summary
            summary = {
                "status": "success",
                "total_orders": len(orders),
                "created_pages": created_count,
                "errors": errors,
                "timestamp": datetime.now().isoformat()
            }
            
            print(f"Sync completed: {created_count}/{len(orders)} pages created")
            return summary
            
        except Exception as e:
            error_summary = {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }
            print(f"Sync failed: {e}")
            return error_summary

    def test_connections(self):
        """Test both Shopify and Notion connections"""
        results = {"shopify": False, "notion": False, "errors": []}
        
        # Test Shopify connection
        try:
            test_query = """
            query {
                shop {
                    name
                    email
                }
            }
            """
            shopify_response = self.fetch_shopify_data(test_query)
            shop_name = shopify_response['data']['shop']['name']
            print(f"✅ Shopify connection successful - Store: {shop_name}")
            results["shopify"] = True
        except Exception as e:
            error_msg = f"❌ Shopify connection failed: {e}"
            print(error_msg)
            results["errors"].append(error_msg)
        
        # Test Notion connection
        try:
            database = self.notion.databases.retrieve(database_id=self.notion_database_id)
            db_title = database['title'][0]['plain_text'] if database['title'] else 'Untitled'
            print(f"✅ Notion connection successful - Database: {db_title}")
            results["notion"] = True
        except Exception as e:
            error_msg = f"❌ Notion connection failed: {e}"
            print(error_msg)
            results["errors"].append(error_msg)
        
        return results