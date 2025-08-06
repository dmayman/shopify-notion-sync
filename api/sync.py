# api/sync.py
from http.server import BaseHTTPRequestHandler
import json
import datetime
import os
import requests
import time
from datetime import datetime as dt
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
    
    def normalize_shopify_timestamp(self, timestamp_str):
        """Normalize timestamp for Shopify GraphQL query compatibility"""
        try:
            # Parse the timestamp string
            if timestamp_str.endswith('Z'):
                # Already in correct UTC format
                return timestamp_str
            elif '+' in timestamp_str or timestamp_str.endswith('+00:00'):
                # Convert to UTC Z format
                parsed = dt.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                return parsed.strftime('%Y-%m-%dT%H:%M:%SZ')
            else:
                # Assume UTC and add Z
                return timestamp_str + 'Z' if not timestamp_str.endswith('Z') else timestamp_str
        except Exception as e:
            print(f"⚠️ Timestamp normalization error: {e}, using original: {timestamp_str}")
            return timestamp_str

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

    def get_shopify_orders(self, limit=50, date_filter=None, order_ids=None, initial_sync=False):
        """Get orders from Shopify with flexible filtering and proper sorting"""
        query_filter = ""
        
        if order_ids:
            order_filter = " OR ".join([f"name:{oid}" for oid in order_ids])
            query_filter = f'query: "{order_filter}"'
        elif date_filter:
            # Normalize the date filter for Shopify GraphQL
            normalized_date_filter = self.normalize_shopify_timestamp(date_filter) 
            query_filter = f'query: "updated_at:>={normalized_date_filter}"'  # Apply date filter to the query
        else:
            # Default behavior depends on context
            query_filter = ""
            if initial_sync:
                print("Fetching orders for initial sync (from oldest to newest)")
            else:
                print("Fetching recent orders (no date filter)")
        
        # For initial sync: sort by UPDATED_AT ascending (oldest first)
        # For incremental sync: sort by UPDATED_AT descending (newest first) 
        if initial_sync:
            sort_key = "UPDATED_AT"
            reverse = "false"  # oldest first for initial sync
        else:
            sort_key = "UPDATED_AT" 
            reverse = "true"   # newest first for incremental sync
        
        query = f"""
        query {{
            orders(first: {limit}, sortKey: {sort_key}, reverse: {reverse}, {query_filter}) {{
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
        """Enhanced sync strategy with resume capability"""
        last_sync = self.sync_storage.get_last_sync()
        failed_orders = self.sync_storage.get_failed_orders()
        resume_timestamp = self.sync_storage.get_resume_timestamp()
        
        strategy = {
            'sync_type': 'smart',
            'actions': [],
            'needs_initial': last_sync is None,
            'has_failed_orders': len(failed_orders) > 0,
            'resume_timestamp': resume_timestamp
        }
        
        if last_sync is None:
            strategy['sync_type'] = 'initial'
            strategy['actions'].append('Initial sync required - never synced before')
        else:
            if failed_orders:
                strategy['actions'].append(f'Retry {len(failed_orders)} failed orders')
            
            # Use resume timestamp if available, otherwise use last_sync
            sync_from = resume_timestamp if resume_timestamp else last_sync
            strategy['actions'].append(f'Sync orders updated since {sync_from}')
            strategy['sync_from_timestamp'] = sync_from
        
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
                                listed_for, sold_for, tax, fee, sku, shopify_url, payment_status=None, is_multi_product=False, parent_item=None):
        """Create Notion properties object with custom emoji"""
        net_earning = sold_for - fee
        to_payouts = sold_for + tax - fee
        
        # No custom emoji - keep it simple
        page_emoji = None
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
        """Archive existing Notion pages (parent + line items)"""
        if isinstance(page_ids, str):
            page_ids = [page_ids]
        
        if not page_ids:
            return
            
        print(f"🗑️ Archiving {len(page_ids)} existing pages...")
        
        for i, page_id in enumerate(page_ids, 1):
            try:
                # Notion doesn't have a delete API, so we archive the page
                self.notion.pages.update(
                    page_id=page_id,
                    archived=True
                )
                print(f"  🗑️ Archived page {i}/{len(page_ids)}: {page_id}")
            except Exception as e:
                print(f"  ⚠️ Failed to archive page {page_id}: {e}")

    def create_notion_page_with_emoji(self, properties):
        """Create Notion page with rate limiting"""
        page_data = {
            "parent": {"database_id": self.notion_database_id},
            "properties": properties
        }
        
        # Rate limiting: 0.4s delay to stay under 150 requests/minute
        time.sleep(0.4)
        
        return self.notion.pages.create(**page_data)

    def create_notion_page(self, order_data):
        """Create Notion page for order"""
        try:
            order = order_data['node']
            transformed_data = self.transform_order_data(order)
            order_id = transformed_data['order_id']
            
            # Check if order already exists (for updates)
            existing_page_ids = self.sync_storage.get_synced_order_page_ids(order_id)
            if existing_page_ids:
                print(f"🔄 Order {order_id} exists - archiving {len(existing_page_ids)} old pages")
                # Archive all existing pages (parent + line items)
                self.delete_notion_pages(existing_page_ids)
            
            created_pages = []

            # Create parent page
            product_name = f"{len(transformed_data['line_items'])} products" if transformed_data['is_multi_product'] else transformed_data['line_items'][0]['product_name']
            properties, _ = self.create_notion_properties(
                order_id=transformed_data['order_id'],
                product_name=product_name,
                date=transformed_data['order_date'],
                customer_name=transformed_data['customer_name'],
                customer_email=transformed_data['customer_email'],
                listed_for=transformed_data['total_listed'],
                sold_for=transformed_data['total_sold'],
                tax=transformed_data['total_tax'],
                fee=transformed_data['total_fees'],
                sku="",
                shopify_url=transformed_data['shopify_url'],
                payment_status=transformed_data['payment_status']
            )

            parent_response = self.create_notion_page_with_emoji(
                properties=properties
            )
            created_pages.append(parent_response)

            print(f"✅ Created parent page for order {transformed_data['order_id']}")
            
            # For multi-product orders, create individual line item pages
            if transformed_data['is_multi_product']:
                parent_page_id = parent_response['id']
                print(f"🛍️ Creating {len(transformed_data['line_items'])} line item pages...")
                
                for i, line_item in enumerate(transformed_data['line_items'], 1):
                    # Create line item order ID with suffix (e.g., #1234.1, #1234.2)
                    line_item_order_id = f"{transformed_data['order_id']}.{i}"
                    
                    line_properties, _ = self.create_notion_properties(
                        order_id=line_item_order_id,
                        product_name=line_item['product_name'],
                        date=transformed_data['order_date'],
                        customer_name="",  # Blank for line items
                        customer_email="",  # Blank for line items
                        listed_for=line_item['listed_for'],
                        sold_for=line_item['sold_for'],
                        tax=0,  # Blank for line items
                        fee=0,  # Blank for line items
                        sku=line_item['sku'],
                        shopify_url=transformed_data['shopify_url'],
                        payment_status=transformed_data['payment_status'],
                        is_multi_product=True,
                        parent_item=parent_page_id
                    )
                    
                    line_response = self.create_notion_page_with_emoji(
                        properties=line_properties
                    )
                    created_pages.append(line_response)
                    
                    print(f"  ✅ Created line item {i}/{len(transformed_data['line_items'])}: {line_item['product_name'][:30]}...")

            # Record success in sync storage with all page IDs and updatedAt timestamp
            order_updated_at = order['updatedAt']
            all_page_ids = [page['id'] for page in created_pages]
            self.sync_storage.mark_order_synced(order_id, all_page_ids, order_updated_at)

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
        """Ultra-minimal sync function using Shopify's updatedAt field - batch blob writes"""
        try:
            # Check if another sync is already in progress
            print("🔍 Checking if sync is already in progress...")
            if self.sync_storage.is_sync_in_progress():
                print("⚠️ Another sync is already running - blocking this request")
                return {
                    "status": "error",
                    "message": "Another sync is already in progress. Please wait for it to complete.",
                    "timestamp": datetime.datetime.now().isoformat()
                }
            
            # Start sync lock BEFORE batch mode so it gets written immediately
            self.sync_storage.start_sync_lock()
            
            # Now start batch mode for the rest of the sync operations
            self.sync_storage.start_batch_mode()
            
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
                # Initial sync: get orders from oldest to newest (no date filter = from the very beginning)
                print(f"🚀 Starting initial sync (limit: {limit})")
                print(f"   📊 Processing from VERY FIRST ORDER chronologically (oldest updatedAt first)")
                orders_data = self.get_shopify_orders(limit=limit, initial_sync=True)
                orders = orders_data['data']['orders']['edges']
                
                print(f"Found {len(orders)} orders for initial sync")
                
                # Show first and last order timestamps to verify chronological order
                if orders:
                    first_order = orders[0]['node']
                    last_order = orders[-1]['node']
                    print(f"   📅 First order: {first_order['name']} (updatedAt: {first_order['updatedAt']})")
                    print(f"   📅 Last order: {last_order['name']} (updatedAt: {last_order['updatedAt']})")
                
                # Process each order
                for i, order_data in enumerate(orders, 1):
                    result = self.create_notion_page(order_data)
                    if result:
                        processed_orders += 1
                        created_pages_count += len(result)
                    else:
                        errors.append(order_data['node']['name'])
                    
                    # Progress logging every 10 orders
                    if i % 10 == 0:
                        print(f"📊 Progress: {i}/{len(orders)} orders processed ({processed_orders} successful, {len(errors)} errors)")
            
            else:
                # Smart sync: handle failed orders and updated orders
                print(f"🧠 Starting smart sync")
                
                # Note: sync timestamp is handled via sync_strategy
                
                # 1. Retry failed orders first
                failed_order_ids = self.sync_storage.get_failed_orders()
                if failed_order_ids:
                    print(f"🔄 Retrying {len(failed_order_ids)} failed orders")
                    if failed_order_ids:
                        retry_data = self.get_shopify_orders(order_ids=failed_order_ids)
                        retry_orders = retry_data['data']['orders']['edges']
                        for i, order_data in enumerate(retry_orders, 1):
                            result = self.create_notion_page(order_data)
                            if result:
                                processed_orders += 1
                                created_pages_count += len(result)
                            else:
                                errors.append(order_data['node']['name'])
                            
                            # Progress logging every 5 failed orders (smaller batches)
                            if i % 5 == 0:
                                print(f"🔄 Retry Progress: {i}/{len(retry_orders)} failed orders processed")
                
                # 2. Get orders updated since last sync or resume point
                sync_from = sync_strategy.get('sync_from_timestamp')
                if sync_from:
                    print(f"📥 Fetching orders updated since {sync_from} (oldest to newest)")
                    updated_data = self.get_shopify_orders(limit=limit, date_filter=sync_from, initial_sync=True)
                    updated_orders = updated_data['data']['orders']['edges']
                    
                    print(f"Found {len(updated_orders)} updated orders")
                    
                    for i, order_data in enumerate(updated_orders, 1):
                        result = self.create_notion_page(order_data)
                        if result:
                            processed_orders += 1
                            created_pages_count += len(result)
                        else:
                            errors.append(order_data['node']['name'])
                        
                        # Progress logging every 10 orders
                        if i % 10 == 0:
                            print(f"📊 Updated Orders Progress: {i}/{len(updated_orders)} orders processed ({processed_orders} total successful)")
            
            # Mark sync as completed
            self.sync_storage.complete_sync()
            
            # Release sync lock and end batch mode
            self.sync_storage.end_sync_lock()
            blob_success = self.sync_storage.end_batch_mode()
            if not blob_success:
                print("⚠️ Warning: Failed to save sync state to blob storage")
            
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
            # Make sure to release sync lock and end batch mode even if sync fails
            try:
                self.sync_storage.end_sync_lock()
                self.sync_storage.end_batch_mode()
            except:
                pass  # Don't let cleanup failure mask the original error
                
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