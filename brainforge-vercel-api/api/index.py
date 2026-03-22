from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import json
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timezone, timedelta, time
import bcrypt
import jwt
import stripe

# Vercel uses environment variables directly — no .env file needed
mongo_url = os.environ.get('MONGO_URL', '')
db_name = os.environ.get('DB_NAME', 'forge-drills')

client = AsyncIOMotorClient(mongo_url) if mongo_url else None
db = client[db_name] if client else None

JWT_SECRET = os.environ.get('JWT_SECRET', '')
JWT_ALGORITHM = "HS256"
STRIPE_API_KEY = os.environ.get('STRIPE_API_KEY', '')
stripe.api_key = STRIPE_API_KEY
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

# Subscription tiers - FIXED PRICES (never from frontend)
SUBSCRIPTION_TIERS = {
    "trial": {"name": "7-Day Trial", "price": 0.00, "interval": "week", "trial_days": 7},
    "beginner": {"name": "Beginner", "price": 14.00, "interval": "month", "trial_days": 0},
    "intermediate": {"name": "Intermediate", "price": 18.00, "interval": "month", "trial_days": 0},
    "levelup": {"name": "Level Up", "price": 25.00, "interval": "month", "trial_days": 0},
}

app = FastAPI()
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── REGION DEFINITIONS ──
REGIONS = {
    "command": {
        "id": "command",
        "name": "COMMAND",
        "subtitle": "Prefrontal Cortex",
        "color": "#3B82F6",
        "description": "Executive function. Working memory. Decision speed under cognitive load.",
        "window_offset_start": 0,
        "window_offset_end": 4,
        "window_type": "wake",
        "optimal_window": "06:00-10:00",
        "metric": "Working memory capacity + decision latency"
    },
    "archive": {
        "id": "archive",
        "name": "ARCHIVE",
        "subtitle": "Hippocampus",
        "color": "#D97706",
        "description": "Encoding. Spatial memory. Pattern recognition across modalities.",
        "window_offset_start": 3,
        "window_offset_end": 5,
        "window_type": "wake",
        "optimal_window": "10:00-12:00",
        "metric": "Encoding fidelity + spatial accuracy"
    },
    "sentinel": {
        "id": "sentinel",
        "name": "SENTINEL",
        "subtitle": "Anterior Cingulate",
        "color": "#94A3B8",
        "description": "Sustained attention. Error monitoring. Inhibitory control under fatigue.",
        "window_offset_start": 2,
        "window_offset_end": 4,
        "window_type": "wake",
        "optimal_window": "09:00-11:00",
        "metric": "Sustained accuracy + false-positive rate"
    },
    "furnace": {
        "id": "furnace",
        "name": "FURNACE",
        "subtitle": "Amygdala + Insula",
        "color": "#DC2626",
        "description": "Emotional regulation. Stress inoculation. Autonomic control.",
        "window_offset_start": 7,
        "window_offset_end": 9,
        "window_type": "wake",
        "optimal_window": "14:00-16:00",
        "metric": "HRV coherence + regulation speed"
    },
    "forge_core": {
        "id": "forge_core",
        "name": "FORGE CORE",
        "subtitle": "Parietal + Temporal",
        "color": "#10B981",
        "description": "Creative problem-solving. Lateral thinking. Abstract pattern reasoning.",
        "window_offset_start": 10,
        "window_offset_end": 12,
        "window_type": "wake",
        "optimal_window": "17:00-19:00",
        "metric": "Insight latency + abstract accuracy"
    },
    "vault": {
        "id": "vault",
        "name": "VAULT",
        "subtitle": "Consolidation Network",
        "color": "#4F46E5",
        "description": "Memory consolidation. Spaced retrieval. Day-close integration.",
        "window_offset_start": -2,
        "window_offset_end": 0,
        "window_type": "sleep",
        "optimal_window": "21:00-23:00",
        "metric": "Recall accuracy + consolidation rate"
    }
}

# ── DRILL CATALOG ──
DRILL_CATALOG = {
    "n_back_2": {"id": "n_back_2", "region": "command", "name": "N-Back 2", "type": "Working Memory", "description": "2-back sequence. Remember stimulus from 2 positions back.", "timer": 90, "tier": "iron"},
    "decision_tree": {"id": "decision_tree", "region": "command", "name": "Decision Tree", "type": "Decision Speed", "description": "Branching scenario. Make the optimal decision under time pressure.", "timer": 120, "tier": "steel"},
    "digit_span": {"id": "digit_span", "region": "command", "name": "Digit Span", "type": "Working Memory", "description": "Forward and backward digit span. Starts at 5 digits.", "timer": 60, "tier": "iron"},
    "word_pair": {"id": "word_pair", "region": "archive", "name": "Word Pair", "type": "Encoding", "description": "8 word pairs for 20s, then recall the partner word.", "timer": 90, "tier": "iron"},
    "location_memory": {"id": "location_memory", "region": "archive", "name": "Location Memory", "type": "Spatial Memory", "description": "Objects on a 4x4 grid. Study 15s, place from memory.", "timer": 90, "tier": "steel"},
    "sequence_recall": {"id": "sequence_recall", "region": "archive", "name": "Sequence Recall", "type": "Pattern Memory", "description": "Watch a sequence of highlighted cells, recreate in order.", "timer": 60, "tier": "iron"},
    "stroop_task": {"id": "stroop_task", "region": "sentinel", "name": "Stroop Task", "type": "Inhibitory Control", "description": "Color-word Stroop. Identify the ink color, not the word.", "timer": 60, "tier": "iron"},
    "go_nogo": {"id": "go_nogo", "region": "sentinel", "name": "Go/No-Go", "type": "Response Inhibition", "description": "Press on GO stimuli (80%), withhold on NOGO (20%).", "timer": 90, "tier": "steel"},
    "sustained_tap": {"id": "sustained_tap", "region": "sentinel", "name": "Sustained Tap", "type": "Sustained Attention", "description": "Tap every time you see the target letter. Miss 3 = session ends.", "timer": 120, "tier": "iron"},
    "breath_pace": {"id": "breath_pace", "region": "furnace", "name": "Breath Pace", "type": "Regulation", "description": "Box breathing (4-4-4-4) with cognitive task during holds.", "timer": 90, "tier": "iron"},
    "lateral_puzzle": {"id": "lateral_puzzle", "region": "forge_core", "name": "Lateral Puzzle", "type": "Lateral Thinking", "description": "Lateral thinking riddle. Timed. Genuine insight problems.", "timer": 120, "tier": "iron"},
    "matrix_reason": {"id": "matrix_reason", "region": "forge_core", "name": "Matrix Reasoning", "type": "Abstract Reasoning", "description": "3x3 matrix with pattern. Select the missing piece.", "timer": 90, "tier": "steel"},
    "spaced_recall": {"id": "spaced_recall", "region": "vault", "name": "Spaced Recall", "type": "Consolidation", "description": "Recall 5 items from today's earlier sessions.", "timer": 90, "tier": "iron"},
    "free_recall": {"id": "free_recall", "region": "vault", "name": "Free Recall", "type": "Memory Retrieval", "description": "List as many items as possible from a category studied today.", "timer": 60, "tier": "iron"},
}

# ── MODELS ──
class UserRegister(BaseModel):
    email: str
    password: str
    display_name: str

class UserLogin(BaseModel):
    email: str
    password: str

class CalibrateInput(BaseModel):
    wake_time: str  # "07:00"
    sleep_time: str  # "23:00"
    timezone: str = "America/New_York"

class DrillStartInput(BaseModel):
    region_id: str
    drill_type: str
    difficulty: int = 1

class DrillCompleteInput(BaseModel):
    session_id: str
    score: int
    accuracy_pct: float
    reaction_time_ms: int

# ── AUTH HELPERS ──
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(user_id: str) -> str:
    payload = {"user_id": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=30)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ── SCHEDULE HELPERS ──
def parse_time(t_str: str) -> time:
    parts = t_str.split(":")
    return time(int(parts[0]), int(parts[1]))

def calculate_windows(wake_str: str, sleep_str: str):
    wake_h, wake_m = map(int, wake_str.split(":"))
    sleep_h, sleep_m = map(int, sleep_str.split(":"))
    windows = {}
    for rid, region in REGIONS.items():
        if region["window_type"] == "wake":
            start_h = (wake_h + region["window_offset_start"]) % 24
            end_h = (wake_h + region["window_offset_end"]) % 24
        else:
            start_h = (sleep_h + region["window_offset_start"]) % 24
            end_h = (sleep_h + region["window_offset_end"]) % 24
        windows[rid] = {
            "start": f"{start_h:02d}:{wake_m:02d}",
            "end": f"{end_h:02d}:{wake_m:02d}",
            "region_id": rid
        }
    return windows

async def generate_daily_schedule(user):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = await db.daily_schedules.find_one({"user_id": user["id"], "schedule_date": today}, {"_id": 0})
    if existing:
        return existing
    windows = calculate_windows(user.get("wake_time", "07:00"), user.get("sleep_time", "23:00"))
    schedule = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "schedule_date": today,
        "windows": windows,
        "regions_completed": [],
        "total_score_delta": 0,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.daily_schedules.insert_one(schedule)
    doc = await db.daily_schedules.find_one({"id": schedule["id"]}, {"_id": 0})
    return doc

# ── AUTH ROUTES ──
@api_router.post("/auth/register")
async def register(input: UserRegister):
    # Validate email format
    if not input.email or "@" not in input.email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address")
    # Validate password length
    if len(input.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    # Validate display name
    if not input.display_name or len(input.display_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Display name must be at least 2 characters")
    
    email_lower = input.email.lower().strip()
    existing = await db.users.find_one({"email": email_lower})
    if existing:
        raise HTTPException(status_code=400, detail="This email is already registered. Try logging in instead.")
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "email": email_lower,
        "password_hash": hash_password(input.password),
        "display_name": input.display_name.strip(),
        "avatar_url": None,
        "wake_time": "07:00",
        "sleep_time": "23:00",
        "timezone": "America/New_York",
        "subscription_tier": "iron",
        "onboarding_complete": False,
        "streak_days": 0,
        "streak_last_date": None,
        "forge_score": 0,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(user)
    for rid in REGIONS:
        perf = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "region_id": rid,
            "current_level": "iron",
            "current_score": 0,
            "personal_best": 0,
            "sessions_completed": 0,
            "last_drilled_at": None,
            "level_progress_pct": 0
        }
        await db.region_performance.insert_one(perf)
    token = create_token(user_id)
    return {"token": token, "user": {k: v for k, v in user.items() if k != "password_hash" and k != "_id"}}

@api_router.post("/auth/login")
async def login(input: UserLogin):
    email_lower = input.email.lower().strip() if input.email else ""
    user = await db.users.find_one({"email": email_lower}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="No account found with this email. Please register first.")
    if not verify_password(input.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")
    token = create_token(user["id"])
    return {"token": token, "user": {k: v for k, v in user.items() if k != "password_hash"}}

# ── USER ROUTES ──
@api_router.get("/user/me")
async def get_me(user=Depends(get_current_user)):
    return {k: v for k, v in user.items() if k != "password_hash"}

@api_router.put("/user/calibrate")
async def calibrate(input: CalibrateInput, user=Depends(get_current_user)):
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"wake_time": input.wake_time, "sleep_time": input.sleep_time, "timezone": input.timezone, "onboarding_complete": True}}
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.daily_schedules.delete_one({"user_id": user["id"], "schedule_date": today})
    updated_user = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    schedule = await generate_daily_schedule(updated_user)
    return {"user": {k: v for k, v in updated_user.items() if k != "password_hash"}, "schedule": schedule}

# ── SCHEDULE ROUTES ──
@api_router.get("/schedule/today")
async def get_today_schedule(user=Depends(get_current_user)):
    schedule = await generate_daily_schedule(user)
    return schedule

@api_router.get("/schedule/week")
async def get_week_schedule(user=Depends(get_current_user)):
    today = datetime.now(timezone.utc)
    week_schedules = []
    for i in range(7):
        date = (today - timedelta(days=6-i)).strftime("%Y-%m-%d")
        sched = await db.daily_schedules.find_one({"user_id": user["id"], "schedule_date": date}, {"_id": 0})
        if sched:
            week_schedules.append(sched)
        else:
            week_schedules.append({"schedule_date": date, "regions_completed": [], "windows": {}})
    return {"schedules": week_schedules}

# ── DRILL ROUTES ──
@api_router.get("/drills/{region_id}")
async def get_drills(region_id: str, user=Depends(get_current_user)):
    tier_order = ["iron", "steel", "tempered"]
    user_tier_idx = tier_order.index(user.get("subscription_tier", "iron"))
    drills = []
    for d in DRILL_CATALOG.values():
        if d["region"] == region_id:
            drill_tier_idx = tier_order.index(d["tier"])
            drills.append({**d, "locked": drill_tier_idx > user_tier_idx})
    return {"drills": drills, "region": REGIONS.get(region_id)}

@api_router.post("/drill/start")
async def start_drill(input: DrillStartInput, user=Depends(get_current_user)):
    session_id = str(uuid.uuid4())
    session = {
        "id": session_id,
        "user_id": user["id"],
        "region_id": input.region_id,
        "drill_type": input.drill_type,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "duration_seconds": None,
        "score": None,
        "accuracy_pct": None,
        "reaction_time_ms": None,
        "difficulty_level": input.difficulty,
        "completed": False
    }
    await db.drill_sessions.insert_one(session)
    drill_info = DRILL_CATALOG.get(input.drill_type)
    return {"session_id": session_id, "drill": drill_info}

@api_router.post("/drill/complete")
async def complete_drill(input: DrillCompleteInput, user=Depends(get_current_user)):
    session = await db.drill_sessions.find_one({"id": input.session_id, "user_id": user["id"]}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    started = datetime.fromisoformat(session["started_at"])
    duration = int((datetime.now(timezone.utc) - started).total_seconds())
    await db.drill_sessions.update_one(
        {"id": input.session_id},
        {"$set": {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration,
            "score": input.score,
            "accuracy_pct": input.accuracy_pct,
            "reaction_time_ms": input.reaction_time_ms,
            "completed": True
        }}
    )
    region_id = session["region_id"]
    perf = await db.region_performance.find_one({"user_id": user["id"], "region_id": region_id}, {"_id": 0})
    if perf:
        new_score = int(perf["current_score"] * 0.7 + input.score * 0.3)
        new_best = max(perf["personal_best"], input.score)
        sessions = perf["sessions_completed"] + 1
        level_thresholds = {"iron": 200, "steel": 400, "tempered": 600, "hardened": 800, "forge_master": 1000}
        levels = list(level_thresholds.keys())
        current_level = perf["current_level"]
        for lvl in levels:
            if new_score >= level_thresholds[lvl]:
                current_level = lvl
        current_idx = levels.index(current_level)
        if current_idx < len(levels) - 1:
            next_threshold = level_thresholds[levels[current_idx + 1]]
            current_threshold = level_thresholds[current_level]
            progress = min(100, int((new_score - current_threshold) / (next_threshold - current_threshold) * 100))
        else:
            progress = 100
        await db.region_performance.update_one(
            {"user_id": user["id"], "region_id": region_id},
            {"$set": {
                "current_score": new_score,
                "personal_best": new_best,
                "sessions_completed": sessions,
                "last_drilled_at": datetime.now(timezone.utc).isoformat(),
                "current_level": current_level,
                "level_progress_pct": max(0, progress)
            }}
        )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    schedule = await db.daily_schedules.find_one({"user_id": user["id"], "schedule_date": today}, {"_id": 0})
    if schedule and region_id not in schedule.get("regions_completed", []):
        await db.daily_schedules.update_one(
            {"user_id": user["id"], "schedule_date": today},
            {"$push": {"regions_completed": region_id}, "$inc": {"total_score_delta": input.score}}
        )
    all_perfs = await db.region_performance.find({"user_id": user["id"]}, {"_id": 0}).to_list(10)
    scores = [p["current_score"] for p in all_perfs if p["current_score"] > 0]
    base_score = int(sum(scores) / max(len(scores), 1)) if scores else 0
    streak = user.get("streak_days", 0)
    last_date = user.get("streak_last_date")
    if last_date != today:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        if last_date == yesterday:
            streak += 1
        else:
            streak = 1
    consistency_bonus = min(100, streak * 2)
    forge_score = min(1000, base_score + consistency_bonus)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"forge_score": forge_score, "streak_days": streak, "streak_last_date": today}}
    )
    updated_perf = await db.region_performance.find_one({"user_id": user["id"], "region_id": region_id}, {"_id": 0})
    return {
        "score": input.score,
        "accuracy_pct": input.accuracy_pct,
        "reaction_time_ms": input.reaction_time_ms,
        "region_performance": updated_perf,
        "forge_score": forge_score,
        "streak_days": streak,
        "score_delta": input.score
    }

# ── PERFORMANCE ROUTES ──
@api_router.get("/performance/overview")
async def get_performance(user=Depends(get_current_user)):
    perfs = await db.region_performance.find({"user_id": user["id"]}, {"_id": 0}).to_list(10)
    recent_sessions = await db.drill_sessions.find(
        {"user_id": user["id"], "completed": True}, {"_id": 0}
    ).sort("completed_at", -1).to_list(20)
    return {
        "forge_score": user.get("forge_score", 0),
        "streak_days": user.get("streak_days", 0),
        "regions": perfs,
        "recent_sessions": recent_sessions
    }

@api_router.get("/performance/region/{region_id}")
async def get_region_performance(region_id: str, user=Depends(get_current_user)):
    perf = await db.region_performance.find_one({"user_id": user["id"], "region_id": region_id}, {"_id": 0})
    sessions = await db.drill_sessions.find(
        {"user_id": user["id"], "region_id": region_id, "completed": True}, {"_id": 0}
    ).sort("completed_at", -1).to_list(20)
    return {"performance": perf, "sessions": sessions, "region": REGIONS.get(region_id)}

# ── REGION ROUTES ──
@api_router.get("/regions")
async def get_regions():
    return {"regions": list(REGIONS.values())}

@api_router.get("/regions/{region_id}")
async def get_region(region_id: str):
    region = REGIONS.get(region_id)
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    drills = [d for d in DRILL_CATALOG.values() if d["region"] == region_id]
    return {"region": region, "drills": drills}

# ── PUBLIC SHARE ROUTES ──
@api_router.get("/share/{user_id}")
async def get_public_profile(user_id: str):
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0, "email": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    perfs = await db.region_performance.find({"user_id": user_id}, {"_id": 0}).to_list(10)
    total_sessions = sum(p.get("sessions_completed", 0) for p in perfs)
    return {
        "display_name": user.get("display_name", "Forger"),
        "forge_score": user.get("forge_score", 0),
        "streak_days": user.get("streak_days", 0),
        "regions": perfs,
        "total_sessions": total_sessions,
        "member_since": user.get("created_at", ""),
    }

# ── HEALTH ──
@api_router.get("/")
async def root():
    return {"message": "Brain Forge API", "status": "operational"}

# ── STRIPE PAYMENT ROUTES ──
class CreateCheckoutRequest(BaseModel):
    tier_id: str = Field(..., description="Subscription tier: trial, beginner, intermediate, levelup")
    origin_url: str = Field(..., description="Frontend origin URL for redirects")

class CheckoutResponse(BaseModel):
    url: str
    session_id: str

@api_router.get("/subscriptions/tiers")
async def get_subscription_tiers():
    """Get available subscription tiers"""
    return {"tiers": SUBSCRIPTION_TIERS}

@api_router.post("/subscriptions/checkout", response_model=CheckoutResponse)
async def create_checkout_session(request: CreateCheckoutRequest, http_request: Request, user: dict = Depends(get_current_user)):
    """Create a Stripe checkout session for subscription"""
    user_id = user["id"]
    
    # Validate tier
    if request.tier_id not in SUBSCRIPTION_TIERS:
        raise HTTPException(status_code=400, detail="Invalid subscription tier")
    
    tier = SUBSCRIPTION_TIERS[request.tier_id]
    
    # Handle free trial differently
    if request.tier_id == "trial":
        # Activate trial directly without payment
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "subscription_tier": "trial",
                "trial_started_at": datetime.now(timezone.utc).isoformat(),
                "trial_ends_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            }}
        )
        return CheckoutResponse(url=f"{request.origin_url}/dashboard?trial=activated", session_id="trial_free")
    
    # Create Stripe checkout for paid tiers
    success_url = f"{request.origin_url}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{request.origin_url}/subscription/cancel"
    
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": int(tier["price"] * 100),  # Stripe uses cents
                    "recurring": {"interval": "month"},
                    "product_data": {"name": tier["name"]},
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=user.get("email", ""),
            metadata={
                "user_id": user_id,
                "user_email": user.get("email", ""),
                "tier_id": request.tier_id,
                "tier_name": tier["name"]
            }
        )
        
        # Create payment transaction record
        transaction = {
            "id": str(uuid.uuid4()),
            "session_id": session.id,
            "user_id": user_id,
            "user_email": user.get("email", ""),
            "tier_id": request.tier_id,
            "tier_name": tier["name"],
            "amount": tier["price"],
            "currency": "usd",
            "payment_status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.payment_transactions.insert_one(transaction)
        
        return CheckoutResponse(url=session.url, session_id=session.id)
    except Exception as e:
        logging.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")

@api_router.get("/subscriptions/status/{session_id}")
async def get_checkout_status(session_id: str, user: dict = Depends(get_current_user)):
    """Get the status of a checkout session and update user subscription if paid"""
    user_id = user["id"]
    
    # Check if already processed
    transaction = await db.payment_transactions.find_one({"session_id": session_id, "user_id": user_id}, {"_id": 0})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction.get("payment_status") == "paid":
        return {"status": "complete", "payment_status": "paid", "message": "Payment already processed"}
    
    # Check with Stripe
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        payment_status = "paid" if session.payment_status == "paid" else session.payment_status
        
        # Update transaction
        await db.payment_transactions.update_one(
            {"session_id": session_id},
            {"$set": {"payment_status": payment_status, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
        
        # If paid, update user subscription
        if payment_status == "paid":
            tier_id = transaction.get("tier_id", "beginner")
            await db.users.update_one(
                {"id": user_id},
                {"$set": {
                    "subscription_tier": tier_id,
                    "subscription_started_at": datetime.now(timezone.utc).isoformat(),
                    "subscription_status": "active"
                }}
            )
        
        return {
            "status": session.status,
            "payment_status": payment_status,
            "amount_total": session.amount_total,
            "currency": session.currency
        }
    except Exception as e:
        logging.error(f"Stripe status check error: {e}")
        raise HTTPException(status_code=500, detail="Failed to check payment status")

@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(body, signature, STRIPE_WEBHOOK_SECRET)
        else:
            import json
            event = json.loads(body)
        
        if event.get("type") == "checkout.session.completed":
            session_data = event["data"]["object"]
            session_id = session_data["id"]
            metadata = session_data.get("metadata", {})
            payment_status = session_data.get("payment_status", "")
            
            if payment_status == "paid":
                # Update transaction
                await db.payment_transactions.update_one(
                    {"session_id": session_id},
                    {"$set": {"payment_status": "paid", "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
                
                # Update user subscription
                user_id = metadata.get("user_id")
                tier_id = metadata.get("tier_id", "beginner")
                if user_id:
                    await db.users.update_one(
                        {"id": user_id},
                        {"$set": {
                            "subscription_tier": tier_id,
                            "subscription_started_at": datetime.now(timezone.utc).isoformat(),
                            "subscription_status": "active"
                        }}
                    )
        
        return {"status": "received"}
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@api_router.get("/user/subscription")
async def get_user_subscription(user: dict = Depends(get_current_user)):
    """Get current user's subscription status"""
    tier_id = user.get("subscription_tier", "iron")
    tier_info = SUBSCRIPTION_TIERS.get(tier_id, {"name": "Free", "price": 0})
    
    # Check if trial expired
    trial_ends = user.get("trial_ends_at")
    if tier_id == "trial" and trial_ends:
        trial_end_dt = datetime.fromisoformat(trial_ends.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > trial_end_dt:
            tier_id = "iron"
            tier_info = {"name": "Free (Trial Expired)", "price": 0}
    
    return {
        "tier_id": tier_id,
        "tier_name": tier_info.get("name"),
        "tier_price": tier_info.get("price"),
        "subscription_status": user.get("subscription_status", "inactive"),
        "trial_ends_at": user.get("trial_ends_at"),
        "subscription_started_at": user.get("subscription_started_at")
    }

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
