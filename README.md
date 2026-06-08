# ZMT SOFTWARE Store — Deployment Guide

## Quick Deploy to Render.com (FREE)

### Step 1: Get Free Redis
1. Go to https://upstash.com and sign up (FREE)
2. Create a new Redis database
3. Copy the "Redis URL" (starts with redis://)

### Step 2: Deploy to Render
1. Go to https://render.com and sign up (FREE)
2. New → Web Service → "Deploy from existing code"
3. Upload these files OR connect GitHub repo
4. Set environment variables:
   - REDIS_URL: (paste your Upstash URL)
   - ADMIN_PASSWORD: admin taher123
   - JWT_SECRET: any_random_string_here

### Step 3: Access Your Store
After deploy (~3 minutes):
- Customer Store: https://your-app.onrender.com/
- Admin Panel: https://your-app.onrender.com/admin
- Admin Password: admin taher123

## Local Testing
```bash
pip install -r requirements.txt
export REDIS_URL="redis://localhost:6379"
export ADMIN_PASSWORD="admin taher123"
export JWT_SECRET="test-secret"
python main.py
```
Then open: http://localhost:8000