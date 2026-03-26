"""Example multi-step quote form site for HAR recording testing.

Serves a Salesforce-like "Create New Quote" wizard at /example-site.
All interactions use real HTTP API calls to enable meaningful HAR captures.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter(prefix="/example-site", tags=["example-site"])

# ── In-memory data stores ──────────────────────────────────────────────────

CUSTOMERS: dict[str, dict] = {
    "cust-001": {
        "id": "cust-001", "name": "Acme Corporation", "industry": "Manufacturing",
        "contact": "Jane Smith", "email": "jane@acme.com", "phone": "+1-555-0101",
        "address": "123 Industrial Blvd", "city": "Chicago", "state": "IL", "zip": "60601", "country": "US",
    },
    "cust-002": {
        "id": "cust-002", "name": "TechNova Solutions", "industry": "Technology",
        "contact": "Michael Chen", "email": "mchen@technova.io", "phone": "+1-555-0202",
        "address": "456 Innovation Way", "city": "San Francisco", "state": "CA", "zip": "94105", "country": "US",
    },
    "cust-003": {
        "id": "cust-003", "name": "Global Logistics Inc", "industry": "Logistics",
        "contact": "Sarah Johnson", "email": "sjohnson@globallog.com", "phone": "+1-555-0303",
        "address": "789 Freight Ave", "city": "Dallas", "state": "TX", "zip": "75201", "country": "US",
    },
    "cust-004": {
        "id": "cust-004", "name": "Meridian Healthcare", "industry": "Healthcare",
        "contact": "Dr. Robert Kim", "email": "rkim@meridian.health", "phone": "+1-555-0404",
        "address": "321 Medical Center Dr", "city": "Boston", "state": "MA", "zip": "02101", "country": "US",
    },
    "cust-005": {
        "id": "cust-005", "name": "Pinnacle Financial Group", "industry": "Finance",
        "contact": "Amanda Torres", "email": "atorres@pinnacle.fin", "phone": "+1-555-0505",
        "address": "555 Wall Street", "city": "New York", "state": "NY", "zip": "10005", "country": "US",
    },
}

CATEGORIES: list[dict] = [
    {"id": "cat-sw", "name": "Software Licenses", "description": "Enterprise software and SaaS subscriptions"},
    {"id": "cat-hw", "name": "Hardware", "description": "Servers, networking, and peripherals"},
    {"id": "cat-svc", "name": "Professional Services", "description": "Consulting, implementation, and training"},
    {"id": "cat-sup", "name": "Support Plans", "description": "Maintenance and support agreements"},
]

PRODUCTS: dict[str, dict] = {
    "prod-001": {"id": "prod-001", "category_id": "cat-sw", "name": "Enterprise CRM Suite", "sku": "SW-CRM-ENT", "unit_price": 15000.00, "description": "Full CRM platform — 50 user license"},
    "prod-002": {"id": "prod-002", "category_id": "cat-sw", "name": "Analytics Dashboard Pro", "sku": "SW-ANA-PRO", "unit_price": 8500.00, "description": "Real-time analytics with custom dashboards"},
    "prod-003": {"id": "prod-003", "category_id": "cat-sw", "name": "Document Management System", "sku": "SW-DMS-STD", "unit_price": 4200.00, "description": "Cloud document storage and workflow"},
    "prod-004": {"id": "prod-004", "category_id": "cat-hw", "name": "Application Server — 64 Core", "sku": "HW-SRV-64", "unit_price": 24500.00, "description": "High-performance rack server with 256GB RAM"},
    "prod-005": {"id": "prod-005", "category_id": "cat-hw", "name": "Network Switch — 48 Port", "sku": "HW-NSW-48", "unit_price": 3800.00, "description": "Managed L3 switch with 10GbE uplinks"},
    "prod-006": {"id": "prod-006", "category_id": "cat-hw", "name": "Storage Array — 50TB", "sku": "HW-STO-50", "unit_price": 18700.00, "description": "Enterprise NVMe storage array"},
    "prod-007": {"id": "prod-007", "category_id": "cat-hw", "name": "UPS Battery Backup — 3kVA", "sku": "HW-UPS-3K", "unit_price": 2100.00, "description": "Rackmount UPS with network card"},
    "prod-008": {"id": "prod-008", "category_id": "cat-svc", "name": "Implementation Package — Standard", "sku": "SVC-IMP-STD", "unit_price": 12000.00, "description": "40 hours of implementation consulting"},
    "prod-009": {"id": "prod-009", "category_id": "cat-svc", "name": "Implementation Package — Premium", "sku": "SVC-IMP-PRM", "unit_price": 28000.00, "description": "100 hours including data migration"},
    "prod-010": {"id": "prod-010", "category_id": "cat-svc", "name": "Training — On-Site (5 days)", "sku": "SVC-TRN-5D", "unit_price": 7500.00, "description": "On-site training for up to 20 users"},
    "prod-011": {"id": "prod-011", "category_id": "cat-sup", "name": "Gold Support — Annual", "sku": "SUP-GLD-1Y", "unit_price": 9600.00, "description": "24/7 support, 4hr SLA, dedicated TAM"},
    "prod-012": {"id": "prod-012", "category_id": "cat-sup", "name": "Silver Support — Annual", "sku": "SUP-SLV-1Y", "unit_price": 4800.00, "description": "Business hours support, 8hr SLA"},
    "prod-013": {"id": "prod-013", "category_id": "cat-sup", "name": "Bronze Support — Annual", "sku": "SUP-BRZ-1Y", "unit_price": 2400.00, "description": "Email-only support, next business day"},
}

SHIPPING_METHODS: list[dict] = [
    {"id": "ship-std", "name": "Standard Ground", "cost": 0.00, "days": "7-10 business days"},
    {"id": "ship-exp", "name": "Express Shipping", "cost": 250.00, "days": "3-5 business days"},
    {"id": "ship-ovn", "name": "Overnight Priority", "cost": 750.00, "days": "1 business day"},
    {"id": "ship-wgl", "name": "White Glove Delivery", "cost": 1500.00, "days": "Scheduled — includes installation"},
]

PAYMENT_TERMS: list[dict] = [
    {"id": "pay-net15", "name": "Net 15", "description": "Payment due within 15 days"},
    {"id": "pay-net30", "name": "Net 30", "description": "Payment due within 30 days"},
    {"id": "pay-net60", "name": "Net 60", "description": "Payment due within 60 days"},
    {"id": "pay-cod", "name": "Cash on Delivery", "description": "Payment at time of delivery"},
    {"id": "pay-50dep", "name": "50% Deposit", "description": "50% upfront, 50% on delivery"},
]

CURRENCIES: list[dict] = [
    {"id": "USD", "name": "US Dollar", "symbol": "$"},
    {"id": "EUR", "name": "Euro", "symbol": "\u20ac"},
    {"id": "GBP", "name": "British Pound", "symbol": "\u00a3"},
    {"id": "CAD", "name": "Canadian Dollar", "symbol": "CA$"},
]

TAX_RATE = 0.0825  # 8.25%
INSURANCE_RATE = 0.02  # 2% of subtotal

quotes_store: dict[str, dict] = {}


# ── Pydantic models ────────────────────────────────────────────────────────

class QuoteCreate(BaseModel):
    customer_id: str
    quote_name: str
    currency: str = "USD"
    payment_terms: str = "pay-net30"
    expiry_date: str | None = None
    notes: str = ""


class LineItemAdd(BaseModel):
    product_id: str
    quantity: int = 1
    discount_percent: float = 0.0


class QuoteOptions(BaseModel):
    shipping_method: str | None = None
    insurance: bool | None = None
    global_discount_percent: float | None = None
    notes: str | None = None


# ── Helper ──────────────────────────────────────────────────────────────────

def _compute_totals(quote: dict) -> dict:
    subtotal = 0.0
    for item in quote["line_items"]:
        product = PRODUCTS[item["product_id"]]
        line_total = product["unit_price"] * item["quantity"]
        line_total -= line_total * (item["discount_percent"] / 100)
        item["unit_price"] = product["unit_price"]
        item["line_total"] = round(line_total, 2)
        subtotal += line_total

    global_disc = subtotal * (quote.get("global_discount_percent", 0) / 100)
    after_discount = subtotal - global_disc
    tax = after_discount * TAX_RATE
    shipping = 0.0
    if quote.get("shipping_method"):
        ship = next((s for s in SHIPPING_METHODS if s["id"] == quote["shipping_method"]), None)
        if ship:
            shipping = ship["cost"]
    insurance = after_discount * INSURANCE_RATE if quote.get("insurance") else 0.0
    grand_total = after_discount + tax + shipping + insurance

    return {
        "subtotal": round(subtotal, 2),
        "global_discount": round(global_disc, 2),
        "after_discount": round(after_discount, 2),
        "tax_rate": TAX_RATE,
        "tax": round(tax, 2),
        "shipping": round(shipping, 2),
        "insurance": round(insurance, 2),
        "grand_total": round(grand_total, 2),
    }


def _quote_response(quote: dict) -> dict:
    totals = _compute_totals(quote)
    customer = CUSTOMERS.get(quote["customer_id"], {})
    return {**quote, "totals": totals, "customer": customer}


# ── API endpoints ──────────────────────────────────────────────────────────

@router.get("/api/customers")
async def search_customers(q: str = ""):
    results = [c for c in CUSTOMERS.values() if q.lower() in c["name"].lower() or q.lower() in c["industry"].lower()] if q else list(CUSTOMERS.values())
    return results


@router.get("/api/customers/{customer_id}")
async def get_customer(customer_id: str):
    if customer_id not in CUSTOMERS:
        raise HTTPException(404, "Customer not found")
    return CUSTOMERS[customer_id]


@router.get("/api/categories")
async def list_categories():
    return CATEGORIES


@router.get("/api/products")
async def list_products(category_id: str | None = None):
    if category_id:
        return [p for p in PRODUCTS.values() if p["category_id"] == category_id]
    return list(PRODUCTS.values())


@router.get("/api/products/{product_id}")
async def get_product(product_id: str):
    if product_id not in PRODUCTS:
        raise HTTPException(404, "Product not found")
    return PRODUCTS[product_id]


@router.get("/api/shipping-methods")
async def list_shipping_methods():
    return SHIPPING_METHODS


@router.get("/api/payment-terms")
async def list_payment_terms():
    return PAYMENT_TERMS


@router.get("/api/currencies")
async def list_currencies():
    return CURRENCIES


@router.post("/api/quotes")
async def create_quote(body: QuoteCreate):
    quote_id = f"Q-{uuid.uuid4().hex[:8].upper()}"
    if body.customer_id not in CUSTOMERS:
        raise HTTPException(404, "Customer not found")
    quote = {
        "id": quote_id,
        "customer_id": body.customer_id,
        "quote_name": body.quote_name,
        "currency": body.currency,
        "payment_terms": body.payment_terms,
        "expiry_date": body.expiry_date or str(date.today() + timedelta(days=30)),
        "notes": body.notes,
        "status": "draft",
        "line_items": [],
        "shipping_method": None,
        "insurance": False,
        "global_discount_percent": 0.0,
    }
    quotes_store[quote_id] = quote
    return _quote_response(quote)


@router.get("/api/quotes/{quote_id}")
async def get_quote(quote_id: str):
    if quote_id not in quotes_store:
        raise HTTPException(404, "Quote not found")
    return _quote_response(quotes_store[quote_id])


@router.post("/api/quotes/{quote_id}/line-items")
async def add_line_item(quote_id: str, body: LineItemAdd):
    if quote_id not in quotes_store:
        raise HTTPException(404, "Quote not found")
    if body.product_id not in PRODUCTS:
        raise HTTPException(404, "Product not found")

    item_id = f"li-{uuid.uuid4().hex[:6]}"
    product = PRODUCTS[body.product_id]
    item = {
        "id": item_id,
        "product_id": body.product_id,
        "product_name": product["name"],
        "sku": product["sku"],
        "quantity": body.quantity,
        "discount_percent": body.discount_percent,
    }
    quotes_store[quote_id]["line_items"].append(item)
    return _quote_response(quotes_store[quote_id])


@router.delete("/api/quotes/{quote_id}/line-items/{item_id}")
async def remove_line_item(quote_id: str, item_id: str):
    if quote_id not in quotes_store:
        raise HTTPException(404, "Quote not found")
    quote = quotes_store[quote_id]
    quote["line_items"] = [li for li in quote["line_items"] if li["id"] != item_id]
    return _quote_response(quote)


@router.patch("/api/quotes/{quote_id}/options")
async def update_quote_options(quote_id: str, body: QuoteOptions):
    if quote_id not in quotes_store:
        raise HTTPException(404, "Quote not found")
    quote = quotes_store[quote_id]
    if body.shipping_method is not None:
        quote["shipping_method"] = body.shipping_method
    if body.insurance is not None:
        quote["insurance"] = body.insurance
    if body.global_discount_percent is not None:
        quote["global_discount_percent"] = body.global_discount_percent
    if body.notes is not None:
        quote["notes"] = body.notes
    return _quote_response(quote)


@router.post("/api/quotes/{quote_id}/submit")
async def submit_quote(quote_id: str):
    if quote_id not in quotes_store:
        raise HTTPException(404, "Quote not found")
    quote = quotes_store[quote_id]
    if not quote["line_items"]:
        raise HTTPException(400, "Cannot submit a quote with no line items")
    quote["status"] = "submitted"
    return _quote_response(quote)


# ── HTML page ──────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def example_site_page():
    return _HTML_PAGE


_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QuotePro — New Quote Wizard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f0f2f5; color: #1a1a2e; }
  .app-header { background: #1a1a2e; color: #fff; padding: 12px 24px; display: flex; align-items: center; gap: 12px; }
  .app-header h1 { font-size: 18px; font-weight: 600; }
  .app-header .subtitle { font-size: 12px; color: #94a3b8; }
  .container { max-width: 900px; margin: 24px auto; padding: 0 16px; }
  .wizard-progress { display: flex; gap: 0; margin-bottom: 24px; background: #fff; border-radius: 8px; overflow: hidden; border: 1px solid #e2e8f0; }
  .step-indicator { flex: 1; padding: 12px 8px; text-align: center; font-size: 12px; font-weight: 500; color: #94a3b8; border-right: 1px solid #e2e8f0; transition: all 0.2s; position: relative; }
  .step-indicator:last-child { border-right: none; }
  .step-indicator.active { background: #2563eb; color: #fff; }
  .step-indicator.completed { background: #dcfce7; color: #166534; }
  .step-indicator .step-num { display: block; font-size: 18px; font-weight: 700; }
  .panel { background: #fff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 24px; margin-bottom: 16px; }
  .panel h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #1a1a2e; }
  .form-row { display: flex; gap: 16px; margin-bottom: 14px; }
  .form-row > * { flex: 1; }
  .field { margin-bottom: 14px; }
  .field label { display: block; font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
  .field input, .field select, .field textarea {
    width: 100%; padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; font-family: inherit;
    outline: none; transition: border-color 0.15s;
  }
  .field input:focus, .field select:focus, .field textarea:focus { border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
  .field textarea { resize: vertical; min-height: 60px; }
  .btn { padding: 10px 20px; border: none; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.15s; }
  .btn-primary { background: #2563eb; color: #fff; }
  .btn-primary:hover { background: #1d4ed8; }
  .btn-secondary { background: #f1f5f9; color: #475569; border: 1px solid #d1d5db; }
  .btn-secondary:hover { background: #e2e8f0; }
  .btn-success { background: #16a34a; color: #fff; }
  .btn-success:hover { background: #15803d; }
  .btn-danger { background: #fff; color: #dc2626; border: 1px solid #fecaca; }
  .btn-danger:hover { background: #fef2f2; }
  .btn-sm { padding: 6px 12px; font-size: 12px; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .actions { display: flex; justify-content: space-between; margin-top: 20px; }
  .search-results { border: 1px solid #e2e8f0; border-radius: 6px; max-height: 200px; overflow-y: auto; margin-top: 4px; }
  .search-result { padding: 10px 12px; cursor: pointer; border-bottom: 1px solid #f1f5f9; transition: background 0.1s; }
  .search-result:hover { background: #eff6ff; }
  .search-result:last-child { border-bottom: none; }
  .search-result .name { font-weight: 500; font-size: 14px; }
  .search-result .detail { font-size: 12px; color: #64748b; }
  .selected-card { background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 12px 16px; margin-top: 8px; }
  .selected-card .name { font-weight: 600; color: #166534; }
  .selected-card .detail { font-size: 12px; color: #4ade80; }
  .items-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  .items-table th { text-align: left; padding: 8px 10px; background: #f8fafc; border-bottom: 2px solid #e2e8f0; font-size: 11px; text-transform: uppercase; color: #64748b; letter-spacing: 0.5px; }
  .items-table td { padding: 8px 10px; border-bottom: 1px solid #f1f5f9; }
  .items-table .num { text-align: right; font-variant-numeric: tabular-nums; }
  .totals-table { width: 300px; margin-left: auto; margin-top: 16px; font-size: 14px; }
  .totals-table td { padding: 4px 8px; }
  .totals-table .label { color: #64748b; text-align: right; }
  .totals-table .value { text-align: right; font-variant-numeric: tabular-nums; font-weight: 500; }
  .totals-table .grand { font-size: 16px; font-weight: 700; color: #1a1a2e; border-top: 2px solid #1a1a2e; }
  .toggle-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .toggle-row input[type="checkbox"] { width: 18px; height: 18px; cursor: pointer; }
  .toggle-row label { font-size: 14px; cursor: pointer; }
  .summary-section { margin-bottom: 20px; }
  .summary-section h3 { font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
  .summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 13px; }
  .summary-grid .lbl { color: #64748b; }
  .summary-grid .val { font-weight: 500; }
  .success-banner { background: #f0fdf4; border: 2px solid #22c55e; border-radius: 12px; padding: 32px; text-align: center; }
  .success-banner h2 { color: #166534; font-size: 24px; margin-bottom: 8px; }
  .success-banner p { color: #4ade80; font-size: 14px; }
  .hidden { display: none; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .badge-draft { background: #fef9c3; color: #854d0e; }
  .badge-submitted { background: #dcfce7; color: #166534; }
  .add-item-form { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; padding: 12px; background: #f8fafc; border-radius: 6px; margin-bottom: 12px; }
  .add-item-form .field { margin-bottom: 0; min-width: 140px; }
  .loading { color: #94a3b8; font-style: italic; font-size: 13px; padding: 8px; }
</style>
</head>
<body>
<div class="app-header">
  <div>
    <h1>QuotePro</h1>
    <div class="subtitle">Sales Quote Management System</div>
  </div>
</div>

<div class="container">
  <div class="wizard-progress" id="progress">
    <div class="step-indicator active" data-step="1"><span class="step-num">1</span>Customer</div>
    <div class="step-indicator" data-step="2"><span class="step-num">2</span>Quote Details</div>
    <div class="step-indicator" data-step="3"><span class="step-num">3</span>Line Items</div>
    <div class="step-indicator" data-step="4"><span class="step-num">4</span>Options</div>
    <div class="step-indicator" data-step="5"><span class="step-num">5</span>Review</div>
  </div>

  <!-- Step 1: Customer Selection -->
  <div id="step-1" class="panel">
    <h2>Select Customer</h2>
    <div class="field">
      <label>Search Customers</label>
      <input type="text" id="customer-search" placeholder="Type company name or industry..." autocomplete="off">
    </div>
    <div id="customer-results" class="search-results hidden"></div>
    <div id="selected-customer" class="hidden"></div>
    <div class="actions">
      <div></div>
      <button class="btn btn-primary" id="btn-step1-next" disabled>Next: Quote Details</button>
    </div>
  </div>

  <!-- Step 2: Quote Details -->
  <div id="step-2" class="panel hidden">
    <h2>Quote Details</h2>
    <div class="field">
      <label>Quote Name</label>
      <input type="text" id="quote-name" placeholder="e.g. Q4 Infrastructure Upgrade">
    </div>
    <div class="form-row">
      <div class="field">
        <label>Currency</label>
        <select id="quote-currency"><option value="">Loading...</option></select>
      </div>
      <div class="field">
        <label>Payment Terms</label>
        <select id="quote-payment"><option value="">Loading...</option></select>
      </div>
    </div>
    <div class="field">
      <label>Expiry Date</label>
      <input type="date" id="quote-expiry">
    </div>
    <div class="field">
      <label>Notes</label>
      <textarea id="quote-notes" placeholder="Optional notes..."></textarea>
    </div>
    <div class="actions">
      <button class="btn btn-secondary" onclick="goStep(1)">Back</button>
      <button class="btn btn-primary" id="btn-step2-next">Create Quote & Continue</button>
    </div>
  </div>

  <!-- Step 3: Line Items -->
  <div id="step-3" class="panel hidden">
    <h2>Add Line Items</h2>
    <div class="add-item-form">
      <div class="field">
        <label>Category</label>
        <select id="item-category"><option value="">Select category...</option></select>
      </div>
      <div class="field">
        <label>Product</label>
        <select id="item-product"><option value="">Select category first...</option></select>
      </div>
      <div class="field" style="min-width:80px">
        <label>Qty</label>
        <input type="number" id="item-qty" value="1" min="1" style="width:80px">
      </div>
      <div class="field" style="min-width:80px">
        <label>Discount %</label>
        <input type="number" id="item-discount" value="0" min="0" max="100" step="0.5" style="width:80px">
      </div>
      <button class="btn btn-primary btn-sm" id="btn-add-item">Add</button>
    </div>
    <div id="line-items-container">
      <table class="items-table">
        <thead><tr><th>Product</th><th>SKU</th><th class="num">Unit Price</th><th class="num">Qty</th><th class="num">Disc %</th><th class="num">Line Total</th><th></th></tr></thead>
        <tbody id="line-items-body"><tr><td colspan="7" style="text-align:center;color:#94a3b8;padding:20px">No items added yet</td></tr></tbody>
      </table>
    </div>
    <div id="step3-totals"></div>
    <div class="actions">
      <button class="btn btn-secondary" onclick="goStep(2)">Back</button>
      <button class="btn btn-primary" id="btn-step3-next" disabled>Next: Options</button>
    </div>
  </div>

  <!-- Step 4: Options & Discounts -->
  <div id="step-4" class="panel hidden">
    <h2>Shipping, Insurance & Discounts</h2>
    <div class="field">
      <label>Shipping Method</label>
      <select id="opt-shipping"><option value="">Loading...</option></select>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="opt-insurance">
      <label for="opt-insurance">Add shipping insurance (2% of subtotal)</label>
    </div>
    <div class="field">
      <label>Global Discount %</label>
      <input type="number" id="opt-discount" value="0" min="0" max="50" step="0.5">
    </div>
    <div class="field">
      <label>Additional Notes</label>
      <textarea id="opt-notes" placeholder="Special instructions..."></textarea>
    </div>
    <div class="actions">
      <button class="btn btn-secondary" onclick="goStep(3)">Back</button>
      <button class="btn btn-primary" id="btn-step4-next">Save Options & Review</button>
    </div>
  </div>

  <!-- Step 5: Review & Submit -->
  <div id="step-5" class="panel hidden">
    <h2>Review Quote</h2>
    <div id="review-content"></div>
    <div class="actions">
      <button class="btn btn-secondary" onclick="goStep(4)">Back</button>
      <button class="btn btn-success" id="btn-submit" style="font-size:16px;padding:12px 32px">Submit Quote</button>
    </div>
  </div>

  <!-- Success -->
  <div id="step-success" class="hidden">
    <div class="success-banner">
      <h2>Quote Submitted Successfully!</h2>
      <p id="success-detail"></p>
      <button class="btn btn-primary" style="margin-top:16px" onclick="location.reload()">Create Another Quote</button>
    </div>
  </div>
</div>

<script>
const API = '/example-site/api';
let currentStep = 1;
let selectedCustomer = null;
let currentQuote = null;

// Utility
async function apiFetch(path, opts = {}) {
  const url = API + path;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

function fmt(n) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: currentQuote?.currency || 'USD' }).format(n);
}

function goStep(n) {
  currentStep = n;
  document.querySelectorAll('.panel, #step-success').forEach(el => el.classList.add('hidden'));
  document.getElementById('step-' + n)?.classList.remove('hidden');
  document.querySelectorAll('.step-indicator').forEach(el => {
    const s = parseInt(el.dataset.step);
    el.classList.toggle('active', s === n);
    el.classList.toggle('completed', s < n);
  });
  // Load data when entering certain steps
  if (n === 2) loadStep2Data();
  if (n === 3) loadStep3Data();
  if (n === 4) loadStep4Data();
  if (n === 5) loadReview();
}

// ── Step 1: Customer search ──
let searchTimeout;
document.getElementById('customer-search').addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => searchCustomers(e.target.value), 300);
});

async function searchCustomers(q) {
  const results = await apiFetch('/customers?q=' + encodeURIComponent(q));
  const container = document.getElementById('customer-results');
  if (results.length === 0) {
    container.classList.add('hidden');
    return;
  }
  container.classList.remove('hidden');
  container.innerHTML = results.map(c => `
    <div class="search-result" onclick="selectCustomer('${c.id}')">
      <div class="name">${c.name}</div>
      <div class="detail">${c.industry} — ${c.contact} — ${c.city}, ${c.state}</div>
    </div>
  `).join('');
}

async function selectCustomer(id) {
  selectedCustomer = await apiFetch('/customers/' + id);
  document.getElementById('customer-results').classList.add('hidden');
  document.getElementById('customer-search').value = selectedCustomer.name;
  const el = document.getElementById('selected-customer');
  el.classList.remove('hidden');
  el.innerHTML = `
    <div class="selected-card">
      <div class="name">${selectedCustomer.name}</div>
      <div class="detail">${selectedCustomer.contact} — ${selectedCustomer.email}<br>${selectedCustomer.address}, ${selectedCustomer.city}, ${selectedCustomer.state} ${selectedCustomer.zip}</div>
    </div>
  `;
  document.getElementById('btn-step1-next').disabled = false;
}

document.getElementById('btn-step1-next').addEventListener('click', () => goStep(2));

// ── Step 2: Quote details ──
async function loadStep2Data() {
  const [currencies, terms] = await Promise.all([
    apiFetch('/currencies'),
    apiFetch('/payment-terms'),
  ]);
  const currSel = document.getElementById('quote-currency');
  currSel.innerHTML = currencies.map(c => `<option value="${c.id}">${c.symbol} ${c.name}</option>`).join('');
  const termSel = document.getElementById('quote-payment');
  termSel.innerHTML = terms.map(t => `<option value="${t.id}">${t.name} — ${t.description}</option>`).join('');
  // Default expiry: 30 days from now
  const d = new Date(); d.setDate(d.getDate() + 30);
  document.getElementById('quote-expiry').value = d.toISOString().slice(0, 10);
}

document.getElementById('btn-step2-next').addEventListener('click', async () => {
  const name = document.getElementById('quote-name').value.trim();
  if (!name) { alert('Please enter a quote name'); return; }
  try {
    currentQuote = await apiFetch('/quotes', {
      method: 'POST',
      body: JSON.stringify({
        customer_id: selectedCustomer.id,
        quote_name: name,
        currency: document.getElementById('quote-currency').value,
        payment_terms: document.getElementById('quote-payment').value,
        expiry_date: document.getElementById('quote-expiry').value,
        notes: document.getElementById('quote-notes').value,
      }),
    });
    goStep(3);
  } catch (e) { alert('Error creating quote: ' + e.message); }
});

// ── Step 3: Line items ──
async function loadStep3Data() {
  const categories = await apiFetch('/categories');
  const catSel = document.getElementById('item-category');
  catSel.innerHTML = '<option value="">Select category...</option>' +
    categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
}

document.getElementById('item-category').addEventListener('change', async (e) => {
  const prodSel = document.getElementById('item-product');
  if (!e.target.value) {
    prodSel.innerHTML = '<option value="">Select category first...</option>';
    return;
  }
  prodSel.innerHTML = '<option value="">Loading...</option>';
  const products = await apiFetch('/products?category_id=' + e.target.value);
  prodSel.innerHTML = '<option value="">Select product...</option>' +
    products.map(p => `<option value="${p.id}">${p.name} — ${fmt(p.unit_price)}</option>`).join('');
});

document.getElementById('btn-add-item').addEventListener('click', async () => {
  const productId = document.getElementById('item-product').value;
  if (!productId) { alert('Select a product'); return; }
  const qty = parseInt(document.getElementById('item-qty').value) || 1;
  const disc = parseFloat(document.getElementById('item-discount').value) || 0;
  try {
    currentQuote = await apiFetch('/quotes/' + currentQuote.id + '/line-items', {
      method: 'POST',
      body: JSON.stringify({ product_id: productId, quantity: qty, discount_percent: disc }),
    });
    renderLineItems();
    // Reset form
    document.getElementById('item-product').value = '';
    document.getElementById('item-qty').value = '1';
    document.getElementById('item-discount').value = '0';
  } catch (e) { alert('Error adding item: ' + e.message); }
});

function renderLineItems() {
  const tbody = document.getElementById('line-items-body');
  const items = currentQuote.line_items;
  if (items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#94a3b8;padding:20px">No items added yet</td></tr>';
    document.getElementById('btn-step3-next').disabled = true;
    document.getElementById('step3-totals').innerHTML = '';
    return;
  }
  document.getElementById('btn-step3-next').disabled = false;
  tbody.innerHTML = items.map(it => `
    <tr>
      <td>${it.product_name}</td>
      <td>${it.sku}</td>
      <td class="num">${fmt(it.unit_price)}</td>
      <td class="num">${it.quantity}</td>
      <td class="num">${it.discount_percent}%</td>
      <td class="num">${fmt(it.line_total)}</td>
      <td><button class="btn btn-danger btn-sm" onclick="removeItem('${it.id}')">Remove</button></td>
    </tr>
  `).join('');
  const t = currentQuote.totals;
  document.getElementById('step3-totals').innerHTML = `
    <table class="totals-table">
      <tr><td class="label">Subtotal:</td><td class="value">${fmt(t.subtotal)}</td></tr>
    </table>
  `;
}

async function removeItem(itemId) {
  try {
    currentQuote = await apiFetch('/quotes/' + currentQuote.id + '/line-items/' + itemId, { method: 'DELETE' });
    renderLineItems();
  } catch (e) { alert('Error: ' + e.message); }
}

document.getElementById('btn-step3-next').addEventListener('click', () => goStep(4));

// ── Step 4: Options ──
async function loadStep4Data() {
  const methods = await apiFetch('/shipping-methods');
  const sel = document.getElementById('opt-shipping');
  sel.innerHTML = methods.map(m =>
    `<option value="${m.id}">${m.name}${m.cost > 0 ? ' — ' + fmt(m.cost) : ' — Free'} (${m.days})</option>`
  ).join('');
  // Pre-fill with current quote values
  if (currentQuote.shipping_method) sel.value = currentQuote.shipping_method;
  document.getElementById('opt-insurance').checked = currentQuote.insurance || false;
  document.getElementById('opt-discount').value = currentQuote.global_discount_percent || 0;
  document.getElementById('opt-notes').value = currentQuote.notes || '';
}

document.getElementById('btn-step4-next').addEventListener('click', async () => {
  try {
    currentQuote = await apiFetch('/quotes/' + currentQuote.id + '/options', {
      method: 'PATCH',
      body: JSON.stringify({
        shipping_method: document.getElementById('opt-shipping').value,
        insurance: document.getElementById('opt-insurance').checked,
        global_discount_percent: parseFloat(document.getElementById('opt-discount').value) || 0,
        notes: document.getElementById('opt-notes').value,
      }),
    });
    goStep(5);
  } catch (e) { alert('Error saving options: ' + e.message); }
});

// ── Step 5: Review ──
async function loadReview() {
  // Refresh quote data from server
  currentQuote = await apiFetch('/quotes/' + currentQuote.id);
  const c = currentQuote.customer;
  const t = currentQuote.totals;
  const shipMethod = currentQuote.shipping_method;

  const itemsHtml = currentQuote.line_items.map(it => `
    <tr>
      <td>${it.product_name}</td><td>${it.sku}</td>
      <td class="num">${fmt(it.unit_price)}</td><td class="num">${it.quantity}</td>
      <td class="num">${it.discount_percent}%</td><td class="num">${fmt(it.line_total)}</td>
    </tr>
  `).join('');

  document.getElementById('review-content').innerHTML = `
    <div class="summary-section">
      <h3>Customer</h3>
      <div class="summary-grid">
        <div class="lbl">Company</div><div class="val">${c.name}</div>
        <div class="lbl">Contact</div><div class="val">${c.contact} (${c.email})</div>
        <div class="lbl">Address</div><div class="val">${c.address}, ${c.city}, ${c.state} ${c.zip}</div>
      </div>
    </div>
    <div class="summary-section">
      <h3>Quote Info</h3>
      <div class="summary-grid">
        <div class="lbl">Quote ID</div><div class="val">${currentQuote.id}</div>
        <div class="lbl">Name</div><div class="val">${currentQuote.quote_name}</div>
        <div class="lbl">Status</div><div class="val"><span class="badge badge-${currentQuote.status}">${currentQuote.status}</span></div>
        <div class="lbl">Currency</div><div class="val">${currentQuote.currency}</div>
        <div class="lbl">Payment</div><div class="val">${currentQuote.payment_terms}</div>
        <div class="lbl">Expires</div><div class="val">${currentQuote.expiry_date}</div>
      </div>
    </div>
    <div class="summary-section">
      <h3>Line Items (${currentQuote.line_items.length})</h3>
      <table class="items-table">
        <thead><tr><th>Product</th><th>SKU</th><th class="num">Unit Price</th><th class="num">Qty</th><th class="num">Disc</th><th class="num">Total</th></tr></thead>
        <tbody>${itemsHtml}</tbody>
      </table>
    </div>
    <div class="summary-section">
      <h3>Totals</h3>
      <table class="totals-table">
        <tr><td class="label">Subtotal:</td><td class="value">${fmt(t.subtotal)}</td></tr>
        ${t.global_discount > 0 ? `<tr><td class="label">Discount (${currentQuote.global_discount_percent}%):</td><td class="value">-${fmt(t.global_discount)}</td></tr>` : ''}
        <tr><td class="label">Tax (${(t.tax_rate * 100).toFixed(2)}%):</td><td class="value">${fmt(t.tax)}</td></tr>
        <tr><td class="label">Shipping:</td><td class="value">${fmt(t.shipping)}</td></tr>
        ${t.insurance > 0 ? `<tr><td class="label">Insurance (2%):</td><td class="value">${fmt(t.insurance)}</td></tr>` : ''}
        <tr class="grand"><td class="label">Grand Total:</td><td class="value">${fmt(t.grand_total)}</td></tr>
      </table>
    </div>
    ${currentQuote.notes ? `<div class="summary-section"><h3>Notes</h3><p style="font-size:13px;color:#475569">${currentQuote.notes}</p></div>` : ''}
  `;
}

document.getElementById('btn-submit').addEventListener('click', async () => {
  if (!confirm('Submit this quote? This action cannot be undone.')) return;
  try {
    currentQuote = await apiFetch('/quotes/' + currentQuote.id + '/submit', { method: 'POST' });
    document.querySelectorAll('.panel').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.step-indicator').forEach(el => el.classList.add('completed'));
    const el = document.getElementById('step-success');
    el.classList.remove('hidden');
    document.getElementById('success-detail').textContent =
      'Quote ' + currentQuote.id + ' has been submitted. Grand total: ' + fmt(currentQuote.totals.grand_total);
  } catch (e) { alert('Error submitting: ' + e.message); }
});
</script>
</body>
</html>"""
