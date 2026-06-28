from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import asyncio
import json
import os
import uuid
from playwright.async_api import async_playwright

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-32-chars!")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

app = FastAPI(title="Auto Points Platform", version="2.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

DB_FILE = "database/recipes.json"
USERS_FILE = "database/users.json"
HISTORY_FILE = "database/history.json"

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs("database", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

class Step(BaseModel):
    action: str
    selector: Optional[str] = None
    value: Optional[str] = None
    url: Optional[str] = None
    wait_time: Optional[int] = 2

class Recipe(BaseModel):
    id: Optional[str] = None
    user_id: str
    name: str
    url: str
    login_url: Optional[str] = None
    credentials: Optional[Dict[str, str]] = None
    steps: List[Step] = []
    schedule_minutes: int = 60
    is_active: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    total_points: int = 0
    created_at: Optional[datetime] = None

class UserCreate(BaseModel):
    email: str
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

def get_user(username: str):
    users = load_json(USERS_FILE, {"users": []})
    for user in users["users"]:
        if user["username"] == username or user["email"] == username:
            return user
    return None

def authenticate_user(username: str, password: str):
    user = get_user(username)
    if not user:
        return False
    if not verify_password(password, user["hashed_password"]):
        return False
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user(username)
    if user is None:
        raise credentials_exception
    return user

@app.get("/")
def root():
    return {"message": "Auto Points Platform", "version": "2.2"}

@app.post("/auth/register")
def register(user: UserCreate):
    users = load_json(USERS_FILE, {"users": []})
    for u in users["users"]:
        if u["email"] == user.email or u["username"] == user.username:
            raise HTTPException(status_code=400, detail="Email or username already registered")
    
    new_user = {
        "id": str(uuid.uuid4()),
        "email": user.email,
        "username": user.username,
        "hashed_password": get_password_hash(user.password),
        "is_active": True,
        "is_premium": False,
        "subscription_tier": "free",
        "max_recipes": 3,
        "total_points_earned": 0,
        "created_at": datetime.now().isoformat(),
    }
    users["users"].append(new_user)
    save_json(USERS_FILE, users)
    
    access_token = create_access_token(data={"sub": user.username}, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user["username"]}, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "username": current_user["username"],
        "is_premium": current_user["is_premium"],
        "subscription_tier": current_user["subscription_tier"],
        "max_recipes": current_user["max_recipes"],
        "total_points_earned": current_user.get("total_points_earned", 0)
    }

@app.get("/recipes")
def get_recipes(current_user: dict = Depends(get_current_user)):
    db = load_json(DB_FILE, {"recipes": []})
    user_recipes = [r for r in db["recipes"] if r["user_id"] == current_user["id"]]
    return {"my_recipes": user_recipes}

@app.post("/recipes")
def create_recipe(recipe: Recipe, current_user: dict = Depends(get_current_user)):
    db = load_json(DB_FILE, {"recipes": []})
    user_recipe_count = len([r for r in db["recipes"] if r["user_id"] == current_user["id"]])
    if user_recipe_count >= current_user["max_recipes"]:
        raise HTTPException(status_code=403, detail="Recipe limit reached. Upgrade your plan.")
    
    recipe.id = str(uuid.uuid4())
    recipe.user_id = current_user["id"]
    recipe.created_at = datetime.now().isoformat()
    recipe.next_run = (datetime.now() + timedelta(minutes=recipe.schedule_minutes)).isoformat()
    db["recipes"].append(recipe.dict())
    save_json(DB_FILE, db)
    return recipe

@app.delete("/recipes/{recipe_id}")
def delete_recipe(recipe_id: str, current_user: dict = Depends(get_current_user)):
    db = load_json(DB_FILE, {"recipes": []})
    db["recipes"] = [r for r in db["recipes"] if not (r["id"] == recipe_id and r["user_id"] == current_user["id"])]
    save_json(DB_FILE, db)
    return {"message": "Deleted"}

@app.post("/recipes/{recipe_id}/toggle")
def toggle_recipe(recipe_id: str, current_user: dict = Depends(get_current_user)):
    db = load_json(DB_FILE, {"recipes": []})
    for r in db["recipes"]:
        if r["id"] == recipe_id and r["user_id"] == current_user["id"]:
            r["is_active"] = not r["is_active"]
            save_json(DB_FILE, db)
            return r
    raise HTTPException(status_code=404, detail="Recipe not found")

@app.post("/recipes/{recipe_id}/run")
async def run_recipe_now(recipe_id: str, current_user: dict = Depends(get_current_user)):
    db = load_json(DB_FILE, {"recipes": []})
    recipe = None
    for r in db["recipes"]:
        if r["id"] == recipe_id and r["user_id"] == current_user["id"]:
            recipe = r
            break
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    result = {"recipe_id": recipe_id, "success": False, "message": "", "points_earned": 0, "timestamp": datetime.now().isoformat()}
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            if recipe.get("login_url") and recipe.get("credentials"):
                await page.goto(recipe["login_url"])
                await asyncio.sleep(2)
                creds = recipe["credentials"]
                try:
                    await page.fill('input[type="email"], input[name="email"], input[name="username"]', creds.get("username", ""))
                    await page.fill('input[type="password"]', creds.get("password", ""))
                    await page.click('button[type="submit"]')
                    await asyncio.sleep(3)
                except:
                    pass
            
            await page.goto(recipe["url"])
            await asyncio.sleep(3)
            
            for step in recipe.get("steps", []):
                try:
                    if step["action"] == "click":
                        await page.click(step["selector"])
                        await asyncio.sleep(step.get("wait_time", 2))
                    elif step["action"] == "fill":
                        await page.fill(step["selector"], step["value"])
                        await asyncio.sleep(step.get("wait_time", 1))
                    elif step["action"] == "wait":
                        await asyncio.sleep(step.get("wait_time", 2))
                    elif step["action"] == "navigate":
                        await page.goto(step["url"])
                        await asyncio.sleep(3)
                except Exception as e:
                    result["message"] = f"Step failed: {str(e)}"
                    await browser.close()
                    return result
            
            os.makedirs("screenshots", exist_ok=True)
            await page.screenshot(path=f"screenshots/{recipe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
            
            result["points_earned"] = 10
            result["success"] = True
            result["message"] = "Recipe executed successfully"
            await browser.close()
    except Exception as e:
        result["message"] = f"Browser error: {str(e)}"
    
    if result["success"]:
        recipe["total_points"] = recipe.get("total_points", 0) + result["points_earned"]
        recipe["last_run"] = datetime.now().isoformat()
        recipe["next_run"] = (datetime.now() + timedelta(minutes=recipe["schedule_minutes"])).isoformat()
        
        users = load_json(USERS_FILE, {"users": []})
        for user in users["users"]:
            if user["id"] == current_user["id"]:
                user["total_points_earned"] = user.get("total_points_earned", 0) + result["points_earned"]
                save_json(USERS_FILE, users)
                break
    
    save_json(DB_FILE, db)
    history = load_json(HISTORY_FILE, {"runs": []})
    history["runs"].append(result)
    save_json(HISTORY_FILE, history)
    
    return result

@app.get("/history")
def get_history(current_user: dict = Depends(get_current_user)):
    history = load_json(HISTORY_FILE, {"runs": []})
    return [r for r in history["runs"] if r.get("user_id") == current_user["id"]]

async def scheduler_loop():
    while True:
        try:
            db = load_json(DB_FILE, {"recipes": []})
            now = datetime.now()
            for recipe in db["recipes"]:
                if not recipe.get("is_active", True):
                    continue
                next_run = recipe.get("next_run")
                if next_run:
                    next_run_dt = datetime.fromisoformat(next_run) if isinstance(next_run, str) else next_run
                    if now >= next_run_dt:
                        print(f"Running scheduled recipe: {recipe['name']}")
                        recipe["last_run"] = now.isoformat()
                        recipe["next_run"] = (now + timedelta(minutes=recipe["schedule_minutes"])).isoformat()
            save_json(DB_FILE, db)
        except Exception as e:
            print(f"Scheduler error: {e}")
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(scheduler_loop())
app.mount("/app", StaticFiles(directory="../frontend", html=True), name="frontend")
