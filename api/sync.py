# api/sync.py
from http.server import BaseHTTPRequestHandler
import json
import datetime
import os
import requests
from notion_client import Client
from .blob_storage import SyncBlobStorage

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
        
        # Initialize sync blob storage
        self.sync_storage = SyncBlobStorage()
        
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

    def get_shopify_orders(self, limit=50, date_filter=None, order_ids=None):
        """Get orders from Shopify with flexible filtering"""
        query_filter = ""
        
        if date_filter:
            query_filter = f'query: "{date_filter}"'
        elif order_ids:
            # Query specific orders by ID
            order_filter = " OR ".join([f"name:{oid}" for oid in order_ids])
            query_filter = f'query: "{order_filter}"'
        else:
            # Default: get recent orders (no date filter)
            query_filter = ""
            print("Fetching recent orders (no date filter)")
        
        query = f"""
        query {{
            orders(first: {limit}, sortKey: CREATED_AT, reverse: true, {query_filter}) {{
                edges {{
                    node {{
                        id
                        legacyResourceId
                        name
                        createdAt
                        updatedAt
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
                            status
                            kind
                            gateway
                            fees {{
                                amount {{
                                    amount
                                    currencyCode
                                }}
                            }}
                        }}
                        displayFinancialStatus
                        displayFulfillmentStatus
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
                                    discountedUnitPriceAfterAllDiscountsSet {{
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

    def determine_sync_strategy(self):
        """Ultra-minimal sync strategy determination"""
        last_sync = self.sync_storage.get_last_sync()
        failed_orders = self.sync_storage.get_failed_orders()
        
        strategy = {
            'sync_type': 'smart',
            'actions': [],
            'needs_initial': last_sync is None,
            'has_failed_orders': len(failed_orders) > 0
        }
        
        if last_sync is None:
            strategy['sync_type'] = 'initial'
            strategy['actions'].append('Initial sync required - never synced before')
        else:
            if failed_orders:
                strategy['actions'].append(f'Retry {len(failed_orders)} failed orders')
            strategy['actions'].append(f'Sync orders updated since {last_sync}')
        
        if not strategy['actions']:
            strategy['actions'].append('All orders are up to date')
        
        return strategy

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

    def get_payment_status(self, order):
        """Determine payment status from Shopify order data"""
        financial_status = order.get('displayFinancialStatus', '').lower()
        transactions = order.get('transactions', [])
        
        # Map Shopify financial status to readable status
        status_mapping = {
            'pending': 'Pending',
            'authorized': 'Authorized',
            'partially_paid': 'Partially Paid',
            'paid': 'Paid',
            'partially_refunded': 'Partially Refunded',
            'refunded': 'Refunded',
            'voided': 'Voided',
            'expired': 'Expired'
        }
        
        # Use the display financial status as primary source
        if financial_status in status_mapping:
            return status_mapping[financial_status]
        
        # Fallback: analyze transactions
        if not transactions:
            return 'Unknown'
        
        # Check transaction kinds and statuses
        has_sale = any(t.get('kind') == 'sale' and t.get('status') == 'success' for t in transactions)
        has_refund = any(t.get('kind') == 'refund' and t.get('status') == 'success' for t in transactions)
        has_void = any(t.get('kind') == 'void' and t.get('status') == 'success' for t in transactions)
        
        if has_void:
            return 'Voided'
        elif has_refund and has_sale:
            return 'Partially Refunded'
        elif has_refund:
            return 'Refunded'
        elif has_sale:
            return 'Paid'
        else:
            return 'Pending'

    def get_product_category_emoji(self, product_name):
        """Determine custom emoji based on product category"""
        if not product_name:
            return None
            
        product_lower = product_name.lower()
        
        # Category mapping to custom emoji names
        if product_lower.startswith('necklace'):
            return 'necklace'
        elif product_lower.startswith('bracelet'):
            return 'bracelet'
        elif product_lower.startswith('charm'):
            return 'charm'
        elif product_lower.startswith('ring'):
            return 'ring'
        elif product_lower.startswith('earring'):
            return 'earring'
        elif product_lower.startswith(('luggage', 'bag')):
            return 'bag'
        else:
            return None  # No specific emoji for this category

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
        
        # Get payment status
        payment_status = self.get_payment_status(order)
        
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
            discounted_price = self.get_safe_amount(item.get('discountedUnitPriceAfterAllDiscountsSet'))
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
            'payment_status': payment_status,
            'is_multi_product': is_multi_product,
            'line_items': processed_items
        }
        
        return result

    def create_notion_properties(self, order_id, product_name, date, customer_name, customer_email, 
                                listed_for, sold_for, tax, fee, sku, shopify_url, payment_status=None, 
                                is_multi_product=False, parent_item=None):
        """Create Notion properties object with custom emoji"""
        net_earning = sold_for - fee
        to_payouts = sold_for + tax - fee
        
        # Determine emoji for the page
        page_emoji = None
        if is_multi_product:
            page_emoji = 'shopping_bags'
        else:
            page_emoji = self.get_product_category_emoji(product_name)
        
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
            "Payout": {
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
        if payment_status:
            properties["Payment Status"] = {"rich_text": [{"text": {"content": payment_status}}]}
        if parent_item:
            properties["Parent item"] = {"relation": [{"id": parent_item}]}
            
        return properties, page_emoji

    def delete_notion_pages(self, page_ids):
        """Delete existing Notion pages"""
        if isinstance(page_ids, str):
            page_ids = [page_ids]
        
        for page_id in page_ids:
            try:
                # Notion doesn't have a delete API, so we archive the page
                self.notion.pages.update(
                    page_id=page_id,
                    archived=True
                )
                print(f"🗑️  Archived Notion page {page_id}")
            except Exception as e:
                print(f"⚠️  Failed to archive page {page_id}: {e}")

    def create_notion_page_with_emoji(self, properties, emoji_name=None):
        """Create Notion page with custom emoji if provided"""
        page_data = {
            "parent": {"database_id": self.notion_database_id},
            "properties": properties
        }
        
        # Add custom emoji if provided
        if emoji_name:
            page_data["icon"] = {
                "type": "emoji",
                "emoji": f":{emoji_name}:"
            }
        
        return self.notion.pages.create(**page_data)

    def create_notion_page(self, order_data):
        """Create Notion pages for order (parent + line items if multi-product)"""
        try:
            order = order_data['node']
            transformed_data = self.transform_order_data(order)
            order_id = transformed_data['order_id']
            
            # Check if order already exists (for updates)
            existing_page_id = self.sync_storage.get_synced_order_page_id(order_id)
            if existing_page_id:
                print(f"🔄 Order {order_id} exists - archiving old pages")
                # For multi-product orders, we might have multiple pages to archive
                # For now, just archive the main page (could be improved to track line items)
                self.delete_notion_pages(existing_page_id)
            
            created_pages = []
            
            if transformed_data['is_multi_product']:
                # Create parent page
                parent_product_name = f"{len(transformed_data['line_items'])} products"
                parent_properties, parent_emoji = self.create_notion_properties(
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
                    shopify_url=transformed_data['shopify_url'],
                    payment_status=transformed_data['payment_status'],
                    is_multi_product=True
                )
                
                parent_response = self.create_notion_page_with_emoji(
                    properties=parent_properties,
                    emoji_name=parent_emoji
                )
                created_pages.append(parent_response)
                parent_page_id = parent_response['id']
                
                print(f"✅ Created parent page for order {transformed_data['order_id']}")
                
                # Create line item pages
                for idx, line_item in enumerate(transformed_data['line_items']):
                    line_item_id = f"{transformed_data['order_id']}.{idx + 1}"
                    
                    line_properties, line_emoji = self.create_notion_properties(
                        order_id=line_item_id,
                        product_name=line_item['product_name'],
                        date=None,
                        customer_name=None,
                        customer_email=None,
                        listed_for=line_item['listed_for'],
                        sold_for=line_item['sold_for'],
                        tax=0,
                        fee=0,
                        sku=line_item['sku'],
                        shopify_url="",
                        payment_status=None,
                        is_multi_product=False,
                        parent_item=parent_page_id
                    )
                    
                    line_response = self.create_notion_page_with_emoji(
                        properties=line_properties,
                        emoji_name=line_emoji
                    )
                    created_pages.append(line_response)
                    
                print(f"✅ Created {len(transformed_data['line_items'])} line item pages")
                
                # Record success in sync storage
                self.sync_storage.mark_order_synced(order_id, parent_page_id)
                
            else:
                # Single product order
                line_item = transformed_data['line_items'][0]
                single_properties, single_emoji = self.create_notion_properties(
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
                    shopify_url=transformed_data['shopify_url'],
                    payment_status=transformed_data['payment_status'],
                    is_multi_product=False
                )
                
                single_response = self.create_notion_page_with_emoji(
                    properties=single_properties,
                    emoji_name=single_emoji
                )
                created_pages.append(single_response)
                
                print(f"✅ Created single product page for order {order_id}")
                
                # Record success in sync storage
                self.sync_storage.mark_order_synced(order_id, single_response['id'])
            
            return created_pages
            
        except Exception as e:
            order_id = order_data['node']['name']
            error_message = str(e)
            print(f"❌ Error creating Notion page for order {order_id}: {error_message}")
            print(f"Error details: {error_message}")
            
            # Record failure in sync storage
            self.sync_storage.mark_order_failed(order_id)
            return None

    def sync_orders_to_notion(self, mode='smart', limit=50):
        """Ultra-minimal sync function using Shopify's updatedAt field"""
        try:
            # Determine sync strategy
            if mode == 'initial':
                sync_strategy = {'sync_type': 'initial', 'actions': ['Initial sync requested']}
            else:
                sync_strategy = self.determine_sync_strategy()
            
            print(f"🔍 Sync strategy: {sync_strategy['sync_type']}")
            for action in sync_strategy['actions']:
                print(f"   - {action}")
            
            # Track results
            created_pages_count = 0
            processed_orders = 0
            errors = []
            
            if sync_strategy['sync_type'] == 'initial' or sync_strategy['needs_initial']:
                # Initial sync: get all recent orders
                print(f"🚀 Starting initial sync (limit: {limit})")
                orders_data = self.get_shopify_orders(limit=limit)
                orders = orders_data['data']['orders']['edges']
                
                print(f"Found {len(orders)} orders for initial sync")
                
                # Process each order
                for order_data in orders:
                    result = self.create_notion_page(order_data)
                    if result:
                        processed_orders += 1
                        created_pages_count += len(result)
                    else:
                        errors.append(order_data['node']['name'])
            
            else:
                # Smart sync: handle failed orders and updated orders
                print(f"🧠 Starting smart sync")
                
                # Get last sync timestamp
                last_sync = self.sync_storage.get_last_sync()
                
                # 1. Retry failed orders first
                failed_order_ids = self.sync_storage.get_failed_orders()
                if failed_order_ids:
                    print(f"🔄 Retrying {len(failed_order_ids)} failed orders")
                    if failed_order_ids:
                        retry_data = self.get_shopify_orders(order_ids=failed_order_ids)
                        for order_data in retry_data['data']['orders']['edges']:
                            result = self.create_notion_page(order_data)
                            if result:
                                processed_orders += 1
                                created_pages_count += len(result)
                            else:
                                errors.append(order_data['node']['name'])
                
                # 2. Get orders updated since last sync
                if last_sync:
                    date_filter = f"updated_at:>={last_sync}"
                    print(f"📥 Fetching orders updated since {last_sync}")
                    updated_data = self.get_shopify_orders(limit=limit, date_filter=date_filter)
                    updated_orders = updated_data['data']['orders']['edges']
                    
                    print(f"Found {len(updated_orders)} updated orders")
                    
                    for order_data in updated_orders:
                        result = self.create_notion_page(order_data)
                        if result:
                            processed_orders += 1
                            created_pages_count += len(result)
                        else:
                            errors.append(order_data['node']['name'])
            
            # Mark sync as completed
            self.sync_storage.complete_sync()
            
            # Return summary
            summary = {
                "status": "success",
                "sync_type": sync_strategy['sync_type'],
                "strategy": sync_strategy,
                "processed_orders": processed_orders,
                "created_pages": created_pages_count,
                "errors": errors,
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            print(f"✅ Sync completed: {processed_orders} orders processed, {created_pages_count} pages created")
            if errors:
                print(f"⚠️  {len(errors)} orders failed: {errors}")
            
            return summary
            
        except Exception as e:
            error_summary = {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.datetime.now().isoformat()
            }
            print(f"❌ Sync failed: {e}")
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
        """Handle GET requests for sync status or connection testing"""
        print(f"[{datetime.datetime.now()}] GET request received")
        
        try:
            # Parse query parameters
            query_params = {}
            if hasattr(self, 'path') and '?' in self.path:
                query_string = self.path.split('?')[1]
                for param in query_string.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        query_params[key] = value
            
            endpoint = query_params.get('endpoint', 'test')
            
            sync = ShopifyNotionSync()
            
            if endpoint == 'status':
                # Get sync status and statistics
                print("Getting sync status...")
                
                # Get sync statistics
                stats = sync.sync_storage.get_sync_statistics()
                
                # Get sync strategy
                strategy = sync.determine_sync_strategy()
                
                response = {
                    "status": "sync_status",
                    "message": "Current sync status and statistics",
                    "statistics": stats,
                    "next_sync_strategy": strategy,
                    "timestamp": datetime.datetime.now().isoformat()
                }
                
            else:
                # Default: test connections
                print("Testing connections...")
                test_results = sync.test_connections()
                
                response = {
                    "status": "connection_test",
                    "message": "Testing Shopify and Notion connections",
                    "results": test_results,
                    "timestamp": datetime.datetime.now().isoformat()
                }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            print(f"GET response: {response}")
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"GET request failed: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                "status": "error",
                "message": f"Request failed: {str(e)}",
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
            
            # Parse query parameters for sync mode
            query_params = {}
            if hasattr(self, 'path') and '?' in self.path:
                query_string = self.path.split('?')[1]
                for param in query_string.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        query_params[key] = value

            # Get sync parameters
            sync_mode = query_params.get('mode', 'smart')  # 'initial' or 'smart'
            sync_limit = request_data.get('limit', 50)
            
            print(f"Sync mode: {sync_mode}, limit: {sync_limit}")
            
            # Perform the sync
            sync_results = sync.sync_orders_to_notion(mode=sync_mode, limit=sync_limit)
            
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