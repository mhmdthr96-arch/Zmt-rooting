"""
ZMT SOFTWARE Store — Standalone FastAPI Backend
Deploy to Render.com: https://render.com
Requires: Redis URL (use Upstash free: https://upstash.com)
"""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import redis.asyncio as aioredis
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zmt_store")

# ── Configuration ──────────────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("JWT_SECRET", "zmt-change-this-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ZMT@Admin2024")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30
STORE_NS = "zmt_store"

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="ZMT SOFTWARE Store API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Redis helpers ──────────────────────────────────────────────────────────────
_redis = None

async def get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis

async def rget(ns: str, key: str) -> list:
    r = await get_redis()
    raw = await r.get(f"{ns}:{key}")
    return json.loads(raw) if raw else []

async def rset(ns: str, key: str, data: list) -> None:
    r = await get_redis()
    await r.set(f"{ns}:{key}", json.dumps(data, default=str))

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def safe_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def compute_end_date(start: str, dtype: str, dval: Optional[int]) -> Optional[str]:
    if dtype == "lifetime":
        return None
    sd = safe_iso(start)
    if dtype == "months" and dval:
        return (sd + timedelta(days=30 * dval)).isoformat()
    if dtype == "years" and dval:
        return (sd + timedelta(days=365 * dval)).isoformat()
    return None

def final_price(p: dict) -> float:
    price = p.get("price", 0.0)
    if p.get("discount_active") and p.get("discount_percentage", 0) > 0:
        return round(price * (1 - p["discount_percentage"] / 100), 2)
    return price

# ── Auth helpers ───────────────────────────────────────────────────────────────
def hash_pw(pw: str) -> str: return pwd_ctx.hash(pw)
def verify_pw(plain: str, hashed: str) -> bool: return pwd_ctx.verify(plain, hashed)

def make_token(data: dict, expire_days: int = TOKEN_EXPIRE_DAYS) -> str:
    payload = {**data, "exp": datetime.now(timezone.utc) + timedelta(days=expire_days)}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)

def read_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

async def require_customer(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = read_token(authorization.split(" ", 1)[1])
    if payload.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer access only")
    return payload

async def require_admin(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authentication required")
    payload = read_token(authorization.split(" ", 1)[1])
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access only")
    return payload

# ── Pydantic Models ────────────────────────────────────────────────────────────
class I18n(BaseModel):
    ar: str = ""; en: str = ""; fr: str = ""

class ProductCreate(BaseModel):
    name: I18n; description: I18n
    price: float = Field(..., ge=0)
    discount_percentage: float = Field(default=0.0, ge=0, le=100)
    discount_active: bool = False
    category_id: Optional[str] = None
    images: list[str] = Field(default_factory=list)
    has_activation_key: bool = False
    product_type: str = "software"

class ProductUpdate(BaseModel):
    name: Optional[I18n] = None; description: Optional[I18n] = None
    price: Optional[float] = None; discount_percentage: Optional[float] = None
    discount_active: Optional[bool] = None; category_id: Optional[str] = None
    images: Optional[list[str]] = None; has_activation_key: Optional[bool] = None
    product_type: Optional[str] = None

class CategoryCreate(BaseModel):
    name: I18n; icon: Optional[str] = "Code"

class CategoryUpdate(BaseModel):
    name: Optional[I18n] = None; icon: Optional[str] = None

class CustomerRegister(BaseModel):
    name: str; email: str; phone: Optional[str] = None; password: str

class CustomerLogin(BaseModel):
    email: str; password: str

class AdminLogin(BaseModel):
    password: str

class CustomerAdminCreate(BaseModel):
    name: str; email: str; phone: Optional[str] = None
    address: Optional[str] = None; password: Optional[str] = None

class CustomerUpdate(BaseModel):
    name: Optional[str] = None; email: Optional[str] = None
    phone: Optional[str] = None; address: Optional[str] = None

class NoteCreate(BaseModel):
    text: str

class OrderItemIn(BaseModel):
    product_id: str; quantity: int = 1

class OrderCreate(BaseModel):
    items: list[OrderItemIn]
    customer_name: Optional[str] = None; customer_email: Optional[str] = None
    customer_phone: Optional[str] = None; notes: Optional[str] = None

class OrderStatusUpdate(BaseModel):
    status: str; admin_notes: Optional[str] = None

class InvoiceCreate(BaseModel):
    customer_id: Optional[str] = None; customer_name: str
    customer_email: Optional[str] = None; customer_phone: Optional[str] = None
    items: list[dict[str, Any]]; discount: float = 0.0; notes: Optional[str] = None

class ActivationKeyCreate(BaseModel):
    customer_id: str; product_id: str; activation_key: str
    duration_type: str; duration_value: Optional[int] = None
    start_date: Optional[str] = None

class ActivationKeyUpdate(BaseModel):
    activation_key: Optional[str] = None; duration_type: Optional[str] = None
    duration_value: Optional[int] = None; start_date: Optional[str] = None

# ── Frontend ───────────────────────────────────────────────────────────────────
STORE_HTML_URL = "https://codewords-uploads.s3.amazonaws.com/runtime_v2/918f16cfbf1e46dca35d140db5ab2d0235edf6e52cad4b4183be532fdc4769de/zmt_store.html"
ADMIN_HTML_URL = "https://codewords-uploads.s3.amazonaws.com/runtime_v2/326777fec4dd4c1fb1b56b5a94fe3c145b5e8aabb62e4efab5a8c2548472e278/zmt_admin.html"
_store_html: str = ""
_admin_html: str = ""

@app.get("/")
async def serve_store():
    global _store_html
    if not _store_html:
        import httpx
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(STORE_HTML_URL)
            _store_html = r.text
    return HTMLResponse(_store_html)

@app.get("/admin")
async def serve_admin():
    global _admin_html
    if not _admin_html:
        import httpx
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(ADMIN_HTML_URL)
            _admin_html = r.text
    return HTMLResponse(_admin_html)

@app.get("/manifest.json")
async def store_manifest():
    return Response(content=json.dumps({"name":"ZMT SOFTWARE Store","short_name":"ZMT Store","start_url":"/","display":"standalone","background_color":"#0a0d1a","theme_color":"#1e3a8a","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}]}), media_type="application/manifest+json")

@app.get("/manifest-admin.json")
async def admin_manifest():
    return Response(content=json.dumps({"name":"ZMT Admin Panel","short_name":"ZMT Admin","start_url":"/admin","display":"standalone","background_color":"#0a0d1a","theme_color":"#0f172a","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml"}]}), media_type="application/manifest+json")

@app.get("/icon.svg")
async def serve_icon():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"><defs><radialGradient id="bg" cx="38%" cy="32%" r="65%"><stop offset="0%" stop-color="#5b9bd5"/><stop offset="45%" stop-color="#1a56b0"/><stop offset="100%" stop-color="#08205a"/></radialGradient><linearGradient id="rim" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#e8e8f0"/><stop offset="40%" stop-color="#b8b8cc"/><stop offset="70%" stop-color="#d8d8e8"/><stop offset="100%" stop-color="#909098"/></linearGradient><linearGradient id="zmt" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#d8efff"/><stop offset="35%" stop-color="#9ecbf0"/><stop offset="65%" stop-color="#5899d0"/><stop offset="100%" stop-color="#2060a0"/></linearGradient><filter id="glow"><feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><circle cx="102" cy="104" r="96" fill="rgba(0,0,20,0.4)"/><circle cx="100" cy="100" r="97" fill="url(#rim)"/><circle cx="100" cy="100" r="92" fill="none" stroke="#3399ff" stroke-width="2.5" filter="url(#glow)" opacity="0.9"/><circle cx="100" cy="100" r="90" fill="url(#bg)"/><path d="M 58 55 L 82 55 L 58 80 L 82 80" fill="none" stroke="url(#zmt)" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M 72 80 L 72 55 L 88 70 L 104 55 L 104 80" fill="none" stroke="url(#zmt)" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M 96 55 L 120 55 M 108 55 L 108 80" fill="none" stroke="url(#zmt)" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/><text x="100" y="108" text-anchor="middle" font-family="Arial Black,sans-serif" font-size="9" font-weight="900" fill="white" letter-spacing="1.2">ZERIBIT MOHAMMED TAHER</text><text x="100" y="121" text-anchor="middle" font-family="Arial,sans-serif" font-size="7" fill="#90c8f0" letter-spacing="0.8">PROGRAMMER &amp; WEB DEVELOPER</text></svg>'''
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/sw.js")
async def service_worker():
    sw = "const CACHE='zmt-v1';self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/ ','/admin'])));self.skipWaiting();});self.addEventListener('activate',e=>{self.clients.claim();});self.addEventListener('fetch',e=>{if(e.request.url.includes('/api/'))return;e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));});"
    return Response(content=sw, media_type="application/javascript")

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "ZMT SOFTWARE Store", "version": "1.0.0"}

# ── Auth Endpoints ─────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(data: CustomerRegister):
    customers = await rget(STORE_NS, "customers")
    if any(c["email"] == data.email for c in customers):
        raise HTTPException(400, "Email already registered")
    c = {"id": str(uuid.uuid4()), "name": data.name, "email": data.email,
         "phone": data.phone or "", "address": "", "password_hash": hash_pw(data.password),
         "notes": [], "created_at": now_iso()}
    customers.append(c)
    await rset(STORE_NS, "customers", customers)
    token = make_token({"sub": c["id"], "role": "customer", "email": c["email"]})
    return {"token": token, "customer": {k: v for k, v in c.items() if k != "password_hash"}}

@app.post("/api/auth/login")
async def login(data: CustomerLogin):
    customers = await rget(STORE_NS, "customers")
    c = next((x for x in customers if x["email"] == data.email), None)
    if not c or not verify_pw(data.password, c.get("password_hash", "")):
        raise HTTPException(401, "Invalid email or password")
    token = make_token({"sub": c["id"], "role": "customer", "email": c["email"]})
    return {"token": token, "customer": {k: v for k, v in c.items() if k != "password_hash"}}

@app.post("/api/auth/admin/login")
async def admin_login(data: AdminLogin):
    if data.password != ADMIN_PASSWORD:
        raise HTTPException(401, "Invalid admin password")
    return {"token": make_token({"sub": "admin", "role": "admin"}, expire_days=90), "role": "admin"}

# ── Categories ────────────────────────────────────────────────────────────────
@app.get("/api/categories")
async def list_categories():
    return await rget(STORE_NS, "categories")

@app.post("/api/categories")
async def create_category(data: CategoryCreate, _=Depends(require_admin)):
    cats = await rget(STORE_NS, "categories")
    cat = {"id": str(uuid.uuid4()), "name": data.name.dict(), "icon": data.icon or "Code", "created_at": now_iso()}
    cats.append(cat); await rset(STORE_NS, "categories", cats); return cat

@app.put("/api/categories/{cat_id}")
async def update_category(cat_id: str, data: CategoryUpdate, _=Depends(require_admin)):
    cats = await rget(STORE_NS, "categories")
    idx = next((i for i, c in enumerate(cats) if c["id"] == cat_id), None)
    if idx is None: raise HTTPException(404, "Category not found")
    if data.name: cats[idx]["name"] = data.name.dict()
    if data.icon is not None: cats[idx]["icon"] = data.icon
    await rset(STORE_NS, "categories", cats); return cats[idx]

@app.delete("/api/categories/{cat_id}")
async def delete_category(cat_id: str, _=Depends(require_admin)):
    cats = await rget(STORE_NS, "categories")
    await rset(STORE_NS, "categories", [c for c in cats if c["id"] != cat_id])
    return {"deleted": True}

# ── Products ──────────────────────────────────────────────────────────────────
@app.get("/api/products")
async def list_products(search: Optional[str] = Query(None), category_id: Optional[str] = Query(None)):
    prods = await rget(STORE_NS, "products")
    if category_id: prods = [p for p in prods if p.get("category_id") == category_id]
    if search:
        s = search.lower()
        prods = [p for p in prods if s in p["name"].get("ar","").lower() or s in p["name"].get("en","").lower() or s in p["name"].get("fr","").lower()]
    for p in prods: p["final_price"] = final_price(p)
    return prods

@app.post("/api/products")
async def create_product(data: ProductCreate, _=Depends(require_admin)):
    prods = await rget(STORE_NS, "products")
    p = {"id": str(uuid.uuid4()), "name": data.name.dict(), "description": data.description.dict(),
         "price": data.price, "discount_percentage": data.discount_percentage, "discount_active": data.discount_active,
         "category_id": data.category_id, "images": data.images, "has_activation_key": data.has_activation_key,
         "product_type": data.product_type, "created_at": now_iso(), "updated_at": now_iso()}
    p["final_price"] = final_price(p); prods.append(p); await rset(STORE_NS, "products", prods); return p

@app.get("/api/products/{product_id}")
async def get_product(product_id: str):
    prods = await rget(STORE_NS, "products")
    p = next((x for x in prods if x["id"] == product_id), None)
    if not p: raise HTTPException(404, "Product not found")
    p["final_price"] = final_price(p); return p

@app.put("/api/products/{product_id}")
async def update_product(product_id: str, data: ProductUpdate, _=Depends(require_admin)):
    prods = await rget(STORE_NS, "products")
    idx = next((i for i, x in enumerate(prods) if x["id"] == product_id), None)
    if idx is None: raise HTTPException(404, "Product not found")
    p = prods[idx]
    if data.name is not None: p["name"] = data.name.dict()
    if data.description is not None: p["description"] = data.description.dict()
    if data.price is not None: p["price"] = data.price
    if data.discount_percentage is not None: p["discount_percentage"] = data.discount_percentage
    if data.discount_active is not None: p["discount_active"] = data.discount_active
    if data.category_id is not None: p["category_id"] = data.category_id
    if data.images is not None: p["images"] = data.images
    if data.has_activation_key is not None: p["has_activation_key"] = data.has_activation_key
    if data.product_type is not None: p["product_type"] = data.product_type
    p["updated_at"] = now_iso(); p["final_price"] = final_price(p)
    await rset(STORE_NS, "products", prods); return p

@app.delete("/api/products/{product_id}")
async def delete_product(product_id: str, _=Depends(require_admin)):
    prods = await rget(STORE_NS, "products")
    await rset(STORE_NS, "products", [p for p in prods if p["id"] != product_id])
    return {"deleted": True}

# ── Customers ─────────────────────────────────────────────────────────────────
@app.get("/api/customers")
async def list_customers(_=Depends(require_admin)):
    customers = await rget(STORE_NS, "customers")
    return [{k: v for k, v in c.items() if k != "password_hash"} for c in customers]

@app.post("/api/customers")
async def create_customer_admin(data: CustomerAdminCreate, _=Depends(require_admin)):
    customers = await rget(STORE_NS, "customers")
    if any(c["email"] == data.email for c in customers): raise HTTPException(400, "Email already exists")
    c = {"id": str(uuid.uuid4()), "name": data.name, "email": data.email,
         "phone": data.phone or "", "address": data.address or "",
         "password_hash": hash_pw(data.password or str(uuid.uuid4())),
         "notes": [], "created_at": now_iso()}
    customers.append(c); await rset(STORE_NS, "customers", customers)
    return {k: v for k, v in c.items() if k != "password_hash"}

@app.put("/api/customers/{cid}")
async def update_customer(cid: str, data: CustomerUpdate, _=Depends(require_admin)):
    customers = await rget(STORE_NS, "customers")
    idx = next((i for i, x in enumerate(customers) if x["id"] == cid), None)
    if idx is None: raise HTTPException(404, "Not found")
    c = customers[idx]
    if data.name: c["name"] = data.name
    if data.email: c["email"] = data.email
    if data.phone is not None: c["phone"] = data.phone
    await rset(STORE_NS, "customers", customers)
    return {k: v for k, v in c.items() if k != "password_hash"}

@app.post("/api/customers/{cid}/notes")
async def add_note(cid: str, data: NoteCreate, _=Depends(require_admin)):
    customers = await rget(STORE_NS, "customers")
    idx = next((i for i, x in enumerate(customers) if x["id"] == cid), None)
    if idx is None: raise HTTPException(404, "Not found")
    note = {"id": str(uuid.uuid4()), "text": data.text, "created_at": now_iso()}
    customers[idx]["notes"].append(note); await rset(STORE_NS, "customers", customers); return note

@app.delete("/api/customers/{cid}/notes/{note_id}")
async def delete_note(cid: str, note_id: str, _=Depends(require_admin)):
    customers = await rget(STORE_NS, "customers")
    idx = next((i for i, x in enumerate(customers) if x["id"] == cid), None)
    if idx is None: raise HTTPException(404, "Not found")
    customers[idx]["notes"] = [n for n in customers[idx]["notes"] if n["id"] != note_id]
    await rset(STORE_NS, "customers", customers); return {"deleted": True}

# ── Orders ────────────────────────────────────────────────────────────────────
@app.get("/api/orders/my")
async def get_my_orders(payload: dict = Depends(require_customer)):
    orders = await rget(STORE_NS, "orders")
    return sorted([o for o in orders if o.get("customer_id") == payload["sub"]], key=lambda x: x["created_at"], reverse=True)

@app.get("/api/orders")
async def list_orders(status: Optional[str] = Query(None), _=Depends(require_admin)):
    orders = await rget(STORE_NS, "orders")
    if status: orders = [o for o in orders if o["status"] == status]
    return sorted(orders, key=lambda x: x["created_at"], reverse=True)

@app.post("/api/orders")
async def create_order(data: OrderCreate, authorization: Optional[str] = Header(None)):
    customer_id = cname = cemail = cphone = None
    if authorization and authorization.startswith("Bearer "):
        try:
            p = read_token(authorization.split(" ", 1)[1])
            if p.get("role") == "customer": customer_id = p["sub"]
        except: pass
    prods = await rget(STORE_NS, "products")
    if customer_id:
        custs = await rget(STORE_NS, "customers")
        cc = next((x for x in custs if x["id"] == customer_id), None)
        if cc: cname = cc["name"]; cemail = cc["email"]; cphone = cc.get("phone","")
    cname = cname or data.customer_name or ""; cemail = cemail or data.customer_email or ""; cphone = cphone or data.customer_phone or ""
    items_out = []; total = 0.0
    for item in data.items:
        prod = next((p for p in prods if p["id"] == item.product_id), None)
        if not prod: raise HTTPException(404, f"Product {item.product_id} not found")
        fp = final_price(prod)
        items_out.append({"product_id": item.product_id, "product_name": prod["name"], "quantity": item.quantity,
                          "unit_price": prod["price"], "final_price": fp, "subtotal": round(fp * item.quantity, 2),
                          "has_activation_key": prod.get("has_activation_key", False)})
        total += fp * item.quantity
    order = {"id": str(uuid.uuid4()), "customer_id": customer_id, "customer_name": cname,
             "customer_email": cemail, "customer_phone": cphone, "items": items_out,
             "total": round(total, 2), "status": "pending", "notes": data.notes or "",
             "created_at": now_iso(), "updated_at": now_iso()}
    orders = await rget(STORE_NS, "orders"); orders.append(order); await rset(STORE_NS, "orders", orders); return order

@app.put("/api/orders/{order_id}/status")
async def update_order_status(order_id: str, data: OrderStatusUpdate, _=Depends(require_admin)):
    orders = await rget(STORE_NS, "orders")
    idx = next((i for i, x in enumerate(orders) if x["id"] == order_id), None)
    if idx is None: raise HTTPException(404, "Not found")
    orders[idx]["status"] = data.status; orders[idx]["updated_at"] = now_iso()
    await rset(STORE_NS, "orders", orders); return orders[idx]

# ── Invoices ──────────────────────────────────────────────────────────────────
@app.get("/api/invoices")
async def list_invoices(_=Depends(require_admin)):
    invs = await rget(STORE_NS, "invoices")
    return sorted(invs, key=lambda x: x["created_at"], reverse=True)

@app.post("/api/invoices")
async def create_invoice(data: InvoiceCreate, _=Depends(require_admin)):
    invs = await rget(STORE_NS, "invoices")
    subtotal = sum(item.get("total", item.get("price", 0) * item.get("qty", item.get("quantity", 1))) for item in data.items)
    total = round(subtotal - data.discount, 2)
    inv = {"id": str(uuid.uuid4()), "invoice_number": f"ZMT-{datetime.now().strftime('%Y%m')}-{str(len(invs)+1).zfill(4)}",
           "customer_id": data.customer_id, "customer_name": data.customer_name,
           "customer_email": data.customer_email or "", "customer_phone": data.customer_phone or "",
           "items": data.items, "subtotal": subtotal, "discount": data.discount, "total": total,
           "notes": data.notes or "", "status": "issued", "created_at": now_iso()}
    invs.append(inv); await rset(STORE_NS, "invoices", invs); return inv

# ── Activation Keys ───────────────────────────────────────────────────────────
@app.get("/api/activation-keys/expiring")
async def get_expiring(days: int = Query(14), _=Depends(require_admin)):
    keys = await rget(STORE_NS, "activation_keys")
    now = datetime.now(timezone.utc); threshold = now + timedelta(days=days)
    result = []
    for k in keys:
        if k.get("is_lifetime") or not k.get("end_date"): continue
        end_dt = safe_iso(k["end_date"])
        if now <= end_dt <= threshold: result.append({**k, "days_remaining": (end_dt - now).days})
    return sorted(result, key=lambda x: x["days_remaining"])

@app.get("/api/activation-keys")
async def list_activation_keys(_=Depends(require_admin)):
    keys = await rget(STORE_NS, "activation_keys")
    return sorted(keys, key=lambda x: x["created_at"], reverse=True)

@app.post("/api/activation-keys")
async def create_activation_key(data: ActivationKeyCreate, _=Depends(require_admin)):
    keys = await rget(STORE_NS, "activation_keys")
    custs = await rget(STORE_NS, "customers"); prods = await rget(STORE_NS, "products")
    cust = next((c for c in custs if c["id"] == data.customer_id), None)
    prod = next((p for p in prods if p["id"] == data.product_id), None)
    start = data.start_date or now_iso(); end = compute_end_date(start, data.duration_type, data.duration_value)
    entry = {"id": str(uuid.uuid4()), "customer_id": data.customer_id,
             "customer_name": cust["name"] if cust else "", "customer_email": cust.get("email","") if cust else "",
             "customer_phone": cust.get("phone","") if cust else "", "product_id": data.product_id,
             "product_name": prod["name"] if prod else {"ar":"","en":"","fr":""}, "activation_key": data.activation_key,
             "duration_type": data.duration_type, "duration_value": data.duration_value,
             "is_lifetime": data.duration_type == "lifetime", "start_date": start, "end_date": end, "created_at": now_iso()}
    keys.append(entry); await rset(STORE_NS, "activation_keys", keys); return entry

@app.put("/api/activation-keys/{key_id}")
async def update_activation_key(key_id: str, data: ActivationKeyUpdate, _=Depends(require_admin)):
    keys = await rget(STORE_NS, "activation_keys")
    idx = next((i for i, k in enumerate(keys) if k["id"] == key_id), None)
    if idx is None: raise HTTPException(404, "Not found")
    k = keys[idx]
    if data.activation_key: k["activation_key"] = data.activation_key
    if data.duration_type: k["duration_type"] = data.duration_type
    if data.duration_value is not None: k["duration_value"] = data.duration_value
    if data.start_date: k["start_date"] = data.start_date
    k["is_lifetime"] = k["duration_type"] == "lifetime"
    k["end_date"] = compute_end_date(k["start_date"], k["duration_type"], k.get("duration_value"))
    await rset(STORE_NS, "activation_keys", keys); return k

@app.delete("/api/activation-keys/{key_id}")
async def delete_activation_key(key_id: str, _=Depends(require_admin)):
    keys = await rget(STORE_NS, "activation_keys")
    await rset(STORE_NS, "activation_keys", [k for k in keys if k["id"] != key_id])
    return {"deleted": True}

# ── Reports ───────────────────────────────────────────────────────────────────
@app.get("/api/reports/dashboard")
async def dashboard(_=Depends(require_admin)):
    prods = await rget(STORE_NS, "products"); custs = await rget(STORE_NS, "customers")
    orders = await rget(STORE_NS, "orders"); invs = await rget(STORE_NS, "invoices")
    keys = await rget(STORE_NS, "activation_keys"); cats = await rget(STORE_NS, "categories")
    total_rev = sum(inv["total"] for inv in invs)
    now = datetime.now(timezone.utc); threshold = now + timedelta(days=14)
    expiring = sum(1 for k in keys if not k.get("is_lifetime") and k.get("end_date") and now <= safe_iso(k["end_date"]) <= threshold)
    recent = sorted(orders, key=lambda x: x["created_at"], reverse=True)[:5]
    return {"totals": {"products": len(prods), "categories": len(cats), "customers": len(custs),
                       "orders": len(orders), "invoices": len(invs), "activation_keys": len(keys)},
            "revenue": {"total": round(total_rev, 2)}, "expiring_soon_count": expiring, "recent_orders": recent}

@app.get("/api/reports/financial")
async def financial_report(year: Optional[int] = Query(None), month: Optional[int] = Query(None), _=Depends(require_admin)):
    invs = await rget(STORE_NS, "invoices"); orders = await rget(STORE_NS, "orders")
    def matches(d):
        dt = safe_iso(d)
        return (not year or dt.year == year) and (not month or dt.month == month)
    f_invs = [i for i in invs if matches(i["created_at"])]; f_orders = [o for o in orders if matches(o["created_at"])]
    monthly: dict[str, float] = {}
    for inv in invs:
        try: dt = safe_iso(inv["created_at"]); k = f"{dt.year}-{dt.month:02d}"; monthly[k] = round(monthly.get(k, 0) + inv["total"], 2)
        except: pass
    return {"invoices": {"count": len(f_invs), "total": round(sum(x["total"] for x in f_invs), 2)},
            "orders": {"count": len(f_orders), "total": round(sum(x["total"] for x in f_orders), 2)},
            "monthly_revenue": dict(sorted(monthly.items()))}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)