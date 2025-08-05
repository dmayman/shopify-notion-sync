# api/sync.py
from http.server import BaseHTTPRequestHandler
import json
import datetime
import os
import requests
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
        """Get orders from Shopify between July 14-22, 2024"""
        # Set specific date range for testing
        start_date = "2025-07-14T00:00:00Z"
        end_date = "2025-07-22T23:59:59Z"
        
        print(f"Fetching orders from {start_date} to {end_date}")
        
        query = f"""
        query {{
            orders(first: {limit}, sortKey: CREATED_AT, reverse: true, query: "created_at:>={start_date} AND created_at:<={end_date}") {{
                edges {{
                    node {{
                        id
                        legacyResourceId
                        name
                        createdAt
                        email
                        customer {{
                            displayName
                        }}
                        totalTaxSet {{
                            presentmentMoney {{
                                amount
                                currencyCode
                            }}
                        }}
                        transactions {{
                            fees {{
                                amount {{
                                    amount
                                    currencyCode
                                }}
                            }}
                        }}
                        lineItems(first: 250) {{
                            edges {{
                                node {{
                                    id
                                    title
                                    variant {{
                                        title
                                        sku
                                    }}
                                    originalUnitPriceSet {{
                                        presentmentMoney {{
                                            amount
                                            currencyCode
                                        }}
                                    }}
                                    discountedUnitPriceSet {{
                                        presentmentMoney {{
                                            amount
                                            currencyCode
                                        }}
                                    }}
                                    quantity
                                }}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        
        return self.fetch_shopify_data(query)

    def calculate_fees(self, transactions):
        """Calculate total fees from order transactions"""
        total_fees = 0.0
        try:
            for transaction in transactions:
                if transaction.get('fees'):
                    for fee in transaction['fees']:
                        if fee.get('amount', {}).get('amount'):
                            total_fees += float(fee['amount']['amount'])
        except (KeyError, ValueError, TypeError):
            pass
        return total_fees

    def get_safe_amount(self, price_set, default=0.0):
        """Safely extract amount from Shopify price set structure"""
        try:
            if price_set and price_set.get('presentmentMoney') and price_set['presentmentMoney'].get('amount'):
                return float(price_set['presentmentMoney']['amount'])
        except (KeyError, ValueError, TypeError):
            pass
        return default

    def transform_order_data(self, order):
        """Transform Shopify order data for Notion"""
        line_items = order.get('lineItems', {}).get('edges', [])
        
        # Base order data
        order_id = order['name']
        order_date = order['createdAt']
        customer_name = order.get('customer', {}).get('displayName', '') if order.get('customer') else ''
        customer_email = order.get('email', '')
        legacy_id = order.get('legacyResourceId', '')
        shopify_url = f"https://admin.shopify.com/store/lil-nice-thing/orders/{legacy_id}" if legacy_id else ''
        
        # Calculate order totals
        total_tax = self.get_safe_amount(order.get('totalTaxSet'))
        total_fees = self.calculate_fees(order.get('transactions', []))
        
        # Process line items
        processed_items = []
        total_listed = 0.0
        total_sold = 0.0
        
        for item_edge in line_items:
            item = item_edge['node']
            
            # Get product info
            product_title = item.get('title', '')
            variant_title = item.get('variant', {}).get('title', '') if item.get('variant') else ''
            sku = item.get('variant', {}).get('sku', '') if item.get('variant') else ''
            
            # Create product name
            if variant_title and variant_title != 'Default Title':
                product_name = f"{product_title} – {variant_title}"
            else:
                product_name = product_title
            
            # Get pricing
            original_price = self.get_safe_amount(item.get('originalUnitPriceSet'))
            discounted_price = self.get_safe_amount(item.get('discountedUnitPriceSet'))
            quantity = item.get('quantity', 1)
            
            # Calculate line totals
            line_listed = original_price * quantity
            line_sold = discounted_price * quantity
            
            total_listed += line_listed
            total_sold += line_sold
            
            processed_items.append({
                'product_name': product_name,
                'sku': sku,
                'listed_for': line_listed,
                'sold_for': line_sold,
                'quantity': quantity
            })
        
        # Determine if multi-product order
        is_multi_product = len(processed_items) > 1
        
        result = {
            'order_id': order_id,
            'order_date': order_date,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'shopify_url': shopify_url,
            'total_tax': total_tax,
            'total_fees': total_fees,
            'total_listed': total_listed,
            'total_sold': total_sold,
            'is_multi_product': is_multi_product,
            'line_items': processed_items
        }
        
        return result

    def create_notion_properties(self, order_id, product_name, date, customer_name, customer_email, 
                                listed_for, sold_for, tax, fee, sku, shopify_url, parent_item=None):
        """Create Notion properties object"""
        net_earning = sold_for - fee
        to_payouts = sold_for + tax - fee
        
        properties = {
            "Order ID": {
                "title": [{"text": {"content": order_id}}]
            },
            "Product name": {
                "rich_text": [{"text": {"content": product_name}}]
            },
            "Listed for": {
                "number": listed_for
            },
            "Sold for": {
                "number": sold_for
            },
            "Tax": {
                "number": tax
            },
            "Fee": {
                "number": fee
            },
            "Net earning": {
                "number": net_earning
            },
            "To payouts": {
                "number": to_payouts
            }
        }
        
        # Add optional fields
        if date:
            properties["Date"] = {"date": {"start": date}}
        if customer_name:
            properties["Customer name"] = {"rich_text": [{"text": {"content": customer_name}}]}
        if customer_email:
            properties["Customer Email"] = {"email": customer_email}
        if sku:
            properties["SKU"] = {"rich_text": [{"text": {"content": sku}}]}
        if shopify_url:
            properties["Shopify URL"] = {"url": shopify_url}
        if parent_item:
            properties["Parent item"] = {"relation": [{"id": parent_item}]}
            
        return properties

    def create_notion_page(self, order_data):
        """Create Notion pages for order (parent + line items if multi-product)"""
        try:
            order = order_data['node']
            transformed_data = self.transform_order_data(order)
            
            created_pages = []
            
            if transformed_data['is_multi_product']:
                # Create parent page
                parent_product_name = f"{len(transformed_data['line_items'])} products"
                parent_properties = self.create_notion_properties(
                    order_id=transformed_data['order_id'],
                    product_name=parent_product_name,
                    date=transformed_data['order_date'],
                    customer_name=transformed_data['customer_name'],
                    customer_email=transformed_data['customer_email'],
                    listed_for=transformed_data['total_listed'],
                    sold_for=transformed_data['total_sold'],
                    tax=transformed_data['total_tax'],
                    fee=transformed_data['total_fees'],
                    sku="",
                    shopify_url=transformed_data['shopify_url']
                )
                
                parent_response = self.notion.pages.create(
                    parent={"database_id": self.notion_database_id},
                    properties=parent_properties
                )
                created_pages.append(parent_response)
                parent_page_id = parent_response['id']
                
                print(f"✅ Created parent page for order {transformed_data['order_id']}")
                
                # Create line item pages
                for idx, line_item in enumerate(transformed_data['line_items']):
                    line_item_id = f"{transformed_data['order_id']}.{idx + 1}"
                    line_item_fee = transformed_data['total_fees'] * (line_item['sold_for'] / transformed_data['total_sold']) if transformed_data['total_sold'] > 0 else 0
                    
                    line_properties = self.create_notion_properties(
                        order_id=line_item_id,
                        product_name=line_item['product_name'],
                        date=None,
                        customer_name=None,
                        customer_email=None,
                        listed_for=line_item['listed_for'],
                        sold_for=line_item['sold_for'],
                        tax=0,
                        fee=line_item_fee,
                        sku=line_item['sku'],
                        shopify_url="",
                        parent_item=parent_page_id
                    )
                    
                    line_response = self.notion.pages.create(
                        parent={"database_id": self.notion_database_id},
                        properties=line_properties
                    )
                    created_pages.append(line_response)
                    
                print(f"✅ Created {len(transformed_data['line_items'])} line item pages")
                
            else:
                # Single product order
                line_item = transformed_data['line_items'][0]
                single_properties = self.create_notion_properties(
                    order_id=transformed_data['order_id'],
                    product_name=line_item['product_name'],
                    date=transformed_data['order_date'],
                    customer_name=transformed_data['customer_name'],
                    customer_email=transformed_data['customer_email'],
                    listed_for=line_item['listed_for'],
                    sold_for=line_item['sold_for'],
                    tax=transformed_data['total_tax'],
                    fee=transformed_data['total_fees'],
                    sku=line_item['sku'],
                    shopify_url=transformed_data['shopify_url']
                )
                
                single_response = self.notion.pages.create(
                    parent={"database_id": self.notion_database_id},
                    properties=single_properties
                )
                created_pages.append(single_response)
                
                print(f"✅ Created single product page for order {transformed_data['order_id']}")
            
            return created_pages
            
        except Exception as e:
            print(f"❌ Error creating Notion page for order {order_data['node']['name']}: {e}")
            print(f"Error details: {str(e)}")
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
            created_pages_count = 0
            processed_orders = 0
            errors = []
            
            # Create Notion pages for each order
            for order_data in orders:
                result = self.create_notion_page(order_data)
                if result:
                    processed_orders += 1
                    created_pages_count += len(result)  # result is now a list of created pages
                else:
                    errors.append(order_data['node']['name'])
            
            # Return summary
            summary = {
                "status": "success",
                "total_orders": len(orders),
                "processed_orders": processed_orders,
                "created_pages": created_pages_count,
                "errors": errors,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            print(f"Sync completed: {processed_orders}/{len(orders)} orders processed, {created_pages_count} total pages created")
            return summary
            
        except Exception as e:
            error_summary = {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.datetime.now().isoformat()
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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests for testing connections"""
        print(f"[{datetime.datetime.now()}] GET request received - Testing connections")
        
        try:
            # Test connections
            sync = ShopifyNotionSync()
            test_results = sync.test_connections()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "connection_test",
                "message": "Testing Shopify and Notion connections",
                "results": test_results,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            print(f"Connection test results: {test_results}")
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"Connection test failed: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Connection test failed: {str(e)}",
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(error_response, indent=2).encode())

    def do_POST(self):
        """Handle POST requests from Notion - Perform actual sync"""
        try:
            print(f"[{datetime.datetime.now()}] POST request received - Starting sync")
            
            # Get request data
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b''
            
            request_data = {}
            if post_data:
                try:
                    request_data = json.loads(post_data.decode('utf-8'))
                    print(f"Request data: {request_data}")
                except json.JSONDecodeError:
                    print("No JSON data in request")
            
            # Create sync instance and run sync
            sync = ShopifyNotionSync()
            
            # Get sync limit from request or default to 5
            sync_limit = request_data.get('limit', 5)
            
            # Perform the sync
            sync_results = sync.sync_orders_to_notion(limit=sync_limit)
            
            # Send response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "status": "sync_completed",
                "message": "🚀 Shopify → Notion sync completed!",
                "sync_results": sync_results,
                "request_info": {
                    "limit": sync_limit,
                    "source": "Notion Button" if 'notion' in self.headers.get('User-Agent', '').lower() else "Direct API"
                },
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            print(f"Sync completed successfully: {sync_results}")
            print(f"Sending full response: {json.dumps(response, indent=2)}")
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"Sync error: {e}")
            print(f"Error type: {type(e).__name__}")
            
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Sync failed: {str(e)}",
                "error_type": type(e).__name__,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(error_response, indent=2).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        print(f"[{datetime.datetime.now()}] OPTIONS request received (CORS)")
        
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()