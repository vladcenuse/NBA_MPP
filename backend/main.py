from fastapi import FastAPI, Query, HTTPException, Body, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from nba_api.stats.static import players
from nba_api.stats.endpoints import playercareerstats, commonplayerinfo
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
import logging
import time
import asyncio
import threading
import random
import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import secrets
from jose import JWTError, jwt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Database file - use absolute path
DB_FILE = os.path.join(SCRIPT_DIR, "nba_app.db")
logger.info(f"Using database at: {DB_FILE}")

# Create uploads directory
UPLOAD_DIR = os.path.join(SCRIPT_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Security constants
# Use environment variable for SECRET_KEY if available, otherwise generate a new one
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Initialize OAuth2 password bearer for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Authentication models
class User(BaseModel):
    username: str
    role: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str

class Token(BaseModel):
    access_token: str
    token_type: str
    role: str

class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None

# 2FA Models and Storage
class TwoFactorRequest(BaseModel):
    username: str

class TwoFactorVerify(BaseModel):
    username: str
    code: str

# In-memory storage for 2FA codes (replace with database in production)
two_factor_codes = {}

def generate_2fa_code():
    """Generate a 6-digit 2FA code"""
    return ''.join(random.choices('0123456789', k=6))

def store_2fa_code(username: str, code: str):
    """Store 2FA code with expiration"""
    two_factor_codes[username] = {
        'code': code,
        'expires_at': datetime.utcnow() + timedelta(minutes=10),
        'attempts': 0
    }

def verify_2fa_code(username: str, code: str) -> bool:
    """Verify 2FA code"""
    if username not in two_factor_codes:
        return False
    
    stored = two_factor_codes[username]
    
    # Check expiration
    if datetime.utcnow() > stored['expires_at']:
        del two_factor_codes[username]
        return False
    
    # Check attempts
    if stored['attempts'] >= 3:
        del two_factor_codes[username]
        return False
    
    # Verify code
    if stored['code'] != code:
        stored['attempts'] += 1
        return False
    
    # Code verified, clean up
    del two_factor_codes[username]
    return True

# Password hashing and verification
def get_password_hash(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password, hashed_password):
    return get_password_hash(plain_password) == hashed_password

# User database operations
def create_user(user: UserCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # Check if user already exists
        cursor.execute("SELECT username FROM users WHERE username = ?", (user.username,))
        if cursor.fetchone():
            conn.close()
            return False
        
        # Hash password and save user
        hashed_password = get_password_hash(user.password)
        cursor.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (user.username, hashed_password, user.role, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        conn.close()
        return False

def authenticate_user(username: str, password: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT username, password_hash, role FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return False
    
    username, password_hash, role = user
    if not verify_password(password, password_hash):
        return False
    
    return {"username": username, "role": role}

# Token creation and validation
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        
        if username is None:
            raise credentials_exception
            
        token_data = TokenData(username=username, role=role)
    except JWTError:
        raise credentials_exception
        
    return {"username": token_data.username, "role": token_data.role}

# Function to log user actions
def log_action(username, action_type, entity_type, entity_id=None, details=None):
    """
    Log a user action to the actions table
    
    Parameters:
    - username: Username of the user performing the action
    - action_type: Type of action (CREATE, READ, UPDATE, DELETE)
    - entity_type: Type of entity being acted upon (player, court_player, etc.)
    - entity_id: Optional ID of the entity
    - details: Optional JSON string with additional details
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        
        cursor.execute(
            """
            INSERT INTO actions (username, action_type, entity_type, entity_id, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, action_type, entity_type, entity_id, details, timestamp)
        )
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error logging action: {e}")
        # Don't raise the exception so the main functionality continues
        pass

# Initialize database
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create players table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        full_name TEXT NOT NULL,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        is_active INTEGER NOT NULL,
        position TEXT,
        ppg REAL,
        apg REAL,
        rpg REAL,
        spg REAL,
        win_shares REAL,
        box_plus_minus REAL,
        eff REAL,
        data TEXT,
        last_updated TEXT
    )
    ''')
    
    # Create court_players table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS court_players (
        position TEXT PRIMARY KEY,
        player_id INTEGER,
        FOREIGN KEY (player_id) REFERENCES players (id)
    )
    ''')
    
    # Create users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    ''')
    
    # Create actions logging table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        action_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT,
        details TEXT,
        timestamp TEXT NOT NULL
    )
    ''')
    
    # Create monitored users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS monitored_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        reason TEXT NOT NULL,
        action_count INTEGER NOT NULL,
        first_detected TEXT NOT NULL,
        last_updated TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    ''')
    
    # Initialize court positions if they don't exist
    positions = ["PG", "SG", "SF", "PF", "C"]
    for position in positions:
        cursor.execute("INSERT OR IGNORE INTO court_players (position, player_id) VALUES (?, NULL)", (position,))
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

# Helper to convert player data to JSON and back
def player_to_db(player):
    """Convert player object to database format"""
    return {
        "id": player["id"],
        "full_name": player["full_name"],
        "first_name": player["first_name"],
        "last_name": player["last_name"],
        "is_active": int(player.get("is_active", True)),
        "position": player.get("position", ""),
        "ppg": player.get("ppg", 0),
        "apg": player.get("apg", 0),
        "rpg": player.get("rpg", 0),
        "spg": player.get("spg", 0),
        "win_shares": player.get("win_shares", 0),
        "box_plus_minus": player.get("box_plus_minus", 0),
        "eff": player.get("eff", 0),
        "data": json.dumps(player),
        "last_updated": datetime.now().isoformat()
    }

def db_to_player(row):
    """Convert database row to player object"""
    if not row:
        return None
        
    if isinstance(row, dict):
        # Already a dict
        return json.loads(row["data"]) if "data" in row else row
    
    # Convert from tuple
    if len(row) >= 14:  # All columns including data
        return json.loads(row[13])  # data column
    return None

# Database operations for players
def save_player_to_db(player, username=None):
    """Save or update a player in the database"""
    player_data = player_to_db(player)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # Check if player exists
        cursor.execute("SELECT id FROM players WHERE id = ?", (player_data["id"],))
        exists = cursor.fetchone()
        
        if exists:
            # Update existing player
            cursor.execute("""
            UPDATE players 
            SET full_name = ?, first_name = ?, last_name = ?, is_active = ?,
                position = ?, ppg = ?, apg = ?, rpg = ?, spg = ?,
                win_shares = ?, box_plus_minus = ?, eff = ?, data = ?, last_updated = ?
            WHERE id = ?
            """, (
                player_data["full_name"], player_data["first_name"], player_data["last_name"],
                player_data["is_active"], player_data["position"],
                player_data["ppg"], player_data["apg"], player_data["rpg"], player_data["spg"],
                player_data["win_shares"], player_data["box_plus_minus"], player_data["eff"],
                player_data["data"], player_data["last_updated"], player_data["id"]
            ))
            logger.info(f"Updated player in database: {player_data['full_name']}")
            
            # Log the update action
            if username:
                log_action(
                    username=username,
                    action_type="UPDATE",
                    entity_type="player",
                    entity_id=str(player_data["id"]),
                    details=json.dumps({"name": player_data["full_name"]})
                )
        else:
            # Insert new player
            cursor.execute("""
            INSERT INTO players 
            (id, full_name, first_name, last_name, is_active, position,
             ppg, apg, rpg, spg, win_shares, box_plus_minus, eff, data, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                player_data["id"], player_data["full_name"], player_data["first_name"], 
                player_data["last_name"], player_data["is_active"], player_data["position"],
                player_data["ppg"], player_data["apg"], player_data["rpg"], player_data["spg"],
                player_data["win_shares"], player_data["box_plus_minus"], player_data["eff"],
                player_data["data"], player_data["last_updated"]
            ))
            logger.info(f"Added new player to database: {player_data['full_name']}")
            
            # Log the create action
            if username:
                log_action(
                    username=username,
                    action_type="CREATE",
                    entity_type="player",
                    entity_id=str(player_data["id"]),
                    details=json.dumps({"name": player_data["full_name"]})
                )
        
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving player: {e}")
        conn.rollback()
    finally:
        conn.close()
    return player

def get_player_from_db(player_id):
    """Get a player from the database by ID"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM players WHERE id = ?", (player_id,))
    row = cursor.fetchone()
    
    conn.close()
    return db_to_player(row) if row else None

def get_all_players_from_db():
    """Get all players from the database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT data FROM players")
    rows = cursor.fetchall()
    
    conn.close()
    players = []
    for row in rows:
        if row and row[0]:
            try:
                player = json.loads(row[0])
                players.append(player)
            except Exception as e:
                logger.error(f"Error parsing player data: {e}")
    
    return players

# Database operations for court players
def add_player_to_court_db(position, player_id):
    """Add a player to the court in the database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Update the court position
    cursor.execute(
        "UPDATE court_players SET player_id = ? WHERE position = ?", 
        (player_id, position)
    )
    
    conn.commit()
    conn.close()
    logger.info(f"Added player {player_id} to position {position} in database")

def remove_player_from_court_db(position):
    """Remove a player from the court in the database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Set player_id to NULL for the given position
    cursor.execute(
        "UPDATE court_players SET player_id = NULL WHERE position = ?", 
        (position,)
    )
    
    conn.commit()
    conn.close()
    logger.info(f"Removed player from position {position} in database")

def get_court_players_from_db():
    """Get all players on the court from the database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT cp.position, p.data 
    FROM court_players cp
    LEFT JOIN players p ON cp.player_id = p.id
    """)
    rows = cursor.fetchall()
    
    conn.close()
    
    court_players = {}
    for row in rows:
        position, player_data = row
        if player_data:
            try:
                player = json.loads(player_data)
                court_players[position] = player
            except Exception as e:
                logger.error(f"Error parsing player data for {position}: {e}")
                court_players[position] = None
        else:
            court_players[position] = None
    
    return court_players

def calculate_win_shares(points, assists, rebounds, steals, games):
    """Calculate a simplified Win Shares value"""
    if games == 0:
        return 0
        
    # Simple calculation based on key stats
    raw_ws = (points * 0.5 + assists * 1.2 + rebounds * 0.8 + steals * 2.0) / games * 0.2
    
    # Ensure it's positive and cap at a reasonable value
    return min(max(0, raw_ws), 15)

def calculate_bpm(ppg, apg, rpg, spg):
    """Calculate a simplified Box Plus/Minus value"""
    # This allows negative values for below-average players
    # But scales more reasonably
    raw_bpm = (ppg - 8) * 0.3 + (apg - 2) * 0.7 + (rpg - 3) * 0.5 + (spg - 0.5) * 2.0
    
    # Bound it between reasonable values
    return max(-10, min(15, raw_bpm))

def initialize_players():
    """Initialize players from database or fetch from API if database is empty"""
    try:
        # Try to get players from database first
        db_players = get_all_players_from_db()
        
        if db_players:
            logger.info(f"Loaded {len(db_players)} players from database")
            return db_players
        
        logger.info("No players found in database, fetching from API")
        
        # Fetch active players from the API
        all_players = players.get_active_players()
        players_with_stats = []
        
        # Position mapping
        position_mapping = {
                'G': 'PG',      
                'G-F': 'SG',    
                'F-G': 'SG',    
                'F': 'SF',      
                'F-C': 'PF',    
                'C-F': 'PF',    
                'C': 'C',       
                'Point Guard': 'PG',
                'Shooting Guard': 'SG',
                'Small Forward': 'SF',
                'Power Forward': 'PF',
                'Center': 'C',
                'PG': 'PG',
                'SG': 'SG',
                'SF': 'SF',
                'PF': 'PF',
                'Guard': 'PG',          
                'Forward': 'SF',
                'Guard-Forward': 'SG',
                'Forward-Guard': 'SG',
                'Forward-Center': 'PF',
                'Center-Forward': 'PF'
            }
        
        # Process each player
        for i, player in enumerate(all_players[:25]):  # Limit to 25 for initial load
            try:
                player_id = player["id"]
                
                # Get career stats for this player
                career = playercareerstats.PlayerCareerStats(player_id=player_id)
                career_df = career.get_data_frames()[0]
                
                # Calculate career averages
                if not career_df.empty:
                    # Get the most recent season or career totals
                    if len(career_df) > 0:
                        recent_season = career_df.iloc[-1]
                        
                        # Get common player info to get position
                        player_info = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
                        player_info_df = player_info.get_data_frames()[0]
                        
                        # Extract position
                        position = "Unknown"
                        if not player_info_df.empty:
                            position_raw = player_info_df.iloc[0]["POSITION"] if "POSITION" in player_info_df.columns else ""
                            position = position_mapping.get(position_raw, position_raw)
                        
                        # Get games played
                        games = recent_season["GP"] if "GP" in recent_season else 0
                        
                        # Calculate per-game stats
                        ppg = recent_season["PTS"] / games if games > 0 and "PTS" in recent_season else 0
                        apg = recent_season["AST"] / games if games > 0 and "AST" in recent_season else 0
                        rpg = recent_season["REB"] / games if games > 0 and "REB" in recent_season else 0
                        spg = recent_season["STL"] / games if games > 0 and "STL" in recent_season else 0
                        
                        # Calculate advanced stats
                        win_shares = calculate_win_shares(
                            recent_season["PTS"] if "PTS" in recent_season else 0,
                            recent_season["AST"] if "AST" in recent_season else 0, 
                            recent_season["REB"] if "REB" in recent_season else 0,
                            recent_season["STL"] if "STL" in recent_season else 0,
                            games
                        )
                        
                        box_plus_minus = calculate_bpm(ppg, apg, rpg, spg)
                        
                        # Calculate efficiency (PER-like metric)
                        eff = (
                            recent_season["PTS"] if "PTS" in recent_season else 0
                            + recent_season["REB"] if "REB" in recent_season else 0
                            + recent_season["AST"] if "AST" in recent_season else 0
                            + recent_season["STL"] if "STL" in recent_season else 0
                            + recent_season["BLK"] if "BLK" in recent_season else 0
                            - (recent_season["FGA"] - recent_season["FGM"]) if "FGA" in recent_season and "FGM" in recent_season else 0
                            - (recent_season["FTA"] - recent_season["FTM"]) if "FTA" in recent_season and "FTM" in recent_season else 0
                            - recent_season["TOV"] if "TOV" in recent_season else 0
                        ) / games if games > 0 else 0
                        
                        # Add the stats to the player dictionary
                        player_with_stats = player.copy()
                        player_with_stats.update({
                            "position": position,
                            "ppg": round(ppg, 1),
                            "apg": round(apg, 1),
                            "rpg": round(rpg, 1),
                            "spg": round(spg, 1),
                            "win_shares": round(win_shares, 1),
                            "box_plus_minus": round(box_plus_minus, 1),
                            "eff": round(eff, 1)
                        })
                        
                        # Save player to database
                        save_player_to_db(player_with_stats)
                        
                        players_with_stats.append(player_with_stats)
                        logger.info(f"Processed player {i+1}/25: {player['full_name']}")
            except Exception as e:
                logger.error(f"Error processing player {player['full_name']}: {str(e)}")
        
        return players_with_stats
        
    except Exception as e:
        logger.error(f"Error initializing players: {str(e)}")
        return []

def fetch_new_player():
    """Fetch a new random player from the API"""
    try:
        # Fetch active players
        active_players = players.get_active_players()
        
        # Filter out players we already have
        existing_ids = [p["id"] for p in PLAYERS_WITH_STATS]
        new_players = [p for p in active_players if p["id"] not in existing_ids]
        
        if not new_players:
            logger.warning("No new players available to fetch")
            return None
        
        # Pick a random player
        player = random.choice(new_players)
        player_id = player["id"]
        
        # Get career stats for this player
        career = playercareerstats.PlayerCareerStats(player_id=player_id)
        career_df = career.get_data_frames()[0]
        
        # Get player info for position
        player_info = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
        player_info_df = player_info.get_data_frames()[0]
        
        # Position mapping
        position_mapping = {
                'G': 'PG',      
                'G-F': 'SG',    
                'F-G': 'SG',    
                'F': 'SF',      
                'F-C': 'PF',    
                'C-F': 'PF',    
                'C': 'C',       
                'Point Guard': 'PG',
                'Shooting Guard': 'SG',
                'Small Forward': 'SF',
                'Power Forward': 'PF',
                'Center': 'C',
                'PG': 'PG',
                'SG': 'SG',
                'SF': 'SF',
                'PF': 'PF',
                'Guard': 'PG',          
                'Forward': 'SF',
                'Guard-Forward': 'SG',
                'Forward-Guard': 'SG',
                'Forward-Center': 'PF',
                'Center-Forward': 'PF'
            }
        
        # Extract position
        position = "Unknown"
        if not player_info_df.empty:
            position_raw = player_info_df.iloc[0]["POSITION"] if "POSITION" in player_info_df.columns else ""
            position = position_mapping.get(position_raw, position_raw)
        
        # Calculate career averages if there are stats
        if not career_df.empty and len(career_df) > 0:
            recent_season = career_df.iloc[-1]
            
            # Get games played
            games = recent_season["GP"] if "GP" in recent_season else 0
            
            # Calculate per-game stats
            ppg = recent_season["PTS"] / games if games > 0 and "PTS" in recent_season else 0
            apg = recent_season["AST"] / games if games > 0 and "AST" in recent_season else 0
            rpg = recent_season["REB"] / games if games > 0 and "REB" in recent_season else 0
            spg = recent_season["STL"] / games if games > 0 and "STL" in recent_season else 0
            
            # Calculate advanced stats
            win_shares = calculate_win_shares(
                recent_season["PTS"] if "PTS" in recent_season else 0,
                recent_season["AST"] if "AST" in recent_season else 0, 
                recent_season["REB"] if "REB" in recent_season else 0,
                recent_season["STL"] if "STL" in recent_season else 0,
                games
            )
            
            box_plus_minus = calculate_bpm(ppg, apg, rpg, spg)
            
            # Calculate efficiency (PER-like metric)
            eff = (
                recent_season["PTS"] if "PTS" in recent_season else 0
                + recent_season["REB"] if "REB" in recent_season else 0
                + recent_season["AST"] if "AST" in recent_season else 0
                + recent_season["STL"] if "STL" in recent_season else 0
                + recent_season["BLK"] if "BLK" in recent_season else 0
                - (recent_season["FGA"] - recent_season["FGM"]) if "FGA" in recent_season and "FGM" in recent_season else 0
                - (recent_season["FTA"] - recent_season["FTM"]) if "FTA" in recent_season and "FTM" in recent_season else 0
                - recent_season["TOV"] if "TOV" in recent_season else 0
            ) / games if games > 0 else 0
            
            # Add the stats to the player dictionary
            player_with_stats = player.copy()
            player_with_stats.update({
                "position": position,
                "ppg": round(ppg, 1),
                "apg": round(apg, 1),
                "rpg": round(rpg, 1),
                "spg": round(spg, 1),
                "win_shares": round(win_shares, 1),
                "box_plus_minus": round(box_plus_minus, 1),
                "eff": round(eff, 1)
            })
            
            # Save to database
            save_player_to_db(player_with_stats)
            
            return player_with_stats
        else:
            logger.warning(f"No stats available for player {player['full_name']}")
            return None
    except Exception as e:
        logger.error(f"Error fetching new player: {str(e)}")
        return None

def player_generator_thread():
    """Thread that generates a new player every 10 seconds"""
    logger.info("Starting player generator thread")
    
    while True:
        try:
            # Use asyncio to manage the event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Generate a new player
            new_player = fetch_new_player()
            
            if new_player:
                with player_generation_lock:
                    PLAYERS_WITH_STATS.append(new_player)
                    new_players.append(new_player)
                
                # Broadcast to connected clients
                if active_connections:
                    logger.info(f"Broadcasting new player to {len(active_connections)} client(s)")
                    for connection in active_connections:
                        try:
                            loop.run_until_complete(connection.send_text(json.dumps({
                                "event": "new_player",
                                "data": new_player
                            })))
                        except Exception as e:
                            logger.error(f"Error broadcasting to client: {str(e)}")
                else:
                    logger.info("No connected clients to broadcast to")
            
            # Wait 10 seconds before generating another player
            time.sleep(10)
        except Exception as e:
            logger.error(f"Error in player generator thread: {str(e)}")
            time.sleep(5)  # Wait a bit before retrying if there's an error

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://51.21.190.192", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# Store active WebSocket connections
active_connections: List[WebSocket] = []

# Track the new players added by the generator thread
new_players = []
player_generation_lock = threading.Lock()

# Initialize database
init_db()

# Initialize players with stats at startup
PLAYERS_WITH_STATS = initialize_players()
ITEMS_PER_PAGE = 10

# Start the player generator thread
player_generator = threading.Thread(target=player_generator_thread, daemon=True)
player_generator.start()

# Define a global variable to hold the security monitor thread
security_monitor = None

# Constants for security monitoring
SUSPICIOUS_THRESHOLD_COUNT = 10  # Number of actions in time window to consider suspicious (reduced for testing)
SUSPICIOUS_TIME_WINDOW_MINUTES = 1  # Time window to analyze for suspicious activity (reduced for testing)
MONITORING_INTERVAL_SECONDS = 5  # How often to run the monitoring thread (reduced for testing)
ADMIN_THRESHOLD_MULTIPLIER = 2.0  # Admins have higher threshold (2x normal users)

def analyze_logs_for_suspicious_activity():
    """Analyze the action logs to detect potentially suspicious activity"""
    try:
        logger.info("=== Starting suspicious activity analysis ===")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Calculate time window for analysis
        time_window = datetime.now() - timedelta(minutes=SUSPICIOUS_TIME_WINDOW_MINUTES)
        time_window_iso = time_window.isoformat()
        
        logger.info(f"Looking for users with > {SUSPICIOUS_THRESHOLD_COUNT} actions in the last {SUSPICIOUS_TIME_WINDOW_MINUTES} minutes")
        
        # Query to count actions per user in the time window
        cursor.execute("""
        SELECT username, COUNT(*) as action_count 
        FROM actions 
        WHERE timestamp > ? 
        GROUP BY username
        """, (time_window_iso,))
        
        user_action_counts = cursor.fetchall()
        
        if not user_action_counts:
            logger.info("No recent user actions found in the time window")
            conn.close()
            return
            
        logger.info(f"Found {len(user_action_counts)} users with recent activity:")
        for username, count in user_action_counts:
            logger.info(f"  - {username}: {count} actions")
        
        # Get user roles
        cursor.execute("SELECT username, role FROM users")
        user_roles = {username: role for username, role in cursor.fetchall()}
        
        # Check each user against thresholds
        suspicious_users_found = False
        
        for username, action_count in user_action_counts:
            # Different threshold for admins
            threshold = SUSPICIOUS_THRESHOLD_COUNT
            if username in user_roles and user_roles[username] == "admin":
                threshold *= ADMIN_THRESHOLD_MULTIPLIER
                logger.info(f"User {username} is an admin, threshold is {threshold}")
                
            if action_count > threshold:
                suspicious_users_found = True
                logger.info(f"⚠️ Suspicious activity detected for user {username}: {action_count} actions > threshold {threshold}")
                
                # Get specific action types to provide more detail
                cursor.execute("""
                SELECT action_type, COUNT(*) as type_count 
                FROM actions 
                WHERE username = ? AND timestamp > ? 
                GROUP BY action_type
                """, (username, time_window_iso))
                
                action_types = cursor.fetchall()
                action_type_summary = ", ".join([f"{count} {action_type}" for action_type, count in action_types])
                
                reason = f"Performed {action_count} actions in {SUSPICIOUS_TIME_WINDOW_MINUTES} minutes ({action_type_summary})"
                
                # Check if user is already monitored
                cursor.execute("SELECT id, action_count FROM monitored_users WHERE username = ? AND is_active = 1", (username,))
                existing = cursor.fetchone()
                
                now_iso = datetime.now().isoformat()
                
                if existing:
                    # Update existing monitored user
                    monitored_id, prev_count = existing
                    updated_count = prev_count + action_count
                    cursor.execute("""
                    UPDATE monitored_users 
                    SET action_count = ?, reason = ?, last_updated = ?
                    WHERE id = ?
                    """, (updated_count, reason, now_iso, monitored_id))
                    logger.info(f"📝 Updated monitored user {username}: {reason}")
                else:
                    # Add new monitored user
                    cursor.execute("""
                    INSERT INTO monitored_users (username, reason, action_count, first_detected, last_updated, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """, (username, reason, action_count, now_iso, now_iso))
                    logger.info(f"🚨 Added new monitored user {username}: {reason}")
            else:
                logger.info(f"User {username} activity ({action_count}) is below threshold ({threshold})")
        
        if not suspicious_users_found:
            logger.info("No suspicious activity detected")
            
        conn.commit()
        conn.close()
        logger.info("=== Completed suspicious activity analysis ===")
    except Exception as e:
        logger.error(f"Error analyzing logs for suspicious activity: {e}")
        logger.exception(e)

def security_monitoring_thread():
    """Thread that periodically checks for suspicious activities"""
    logger.info("Starting security monitoring thread")
    
    while True:
        try:
            # Use asyncio to manage the event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Analyze logs
            analyze_logs_for_suspicious_activity()
            
            # Wait before the next check
            time.sleep(MONITORING_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error in security monitoring thread: {str(e)}")
            time.sleep(MONITORING_INTERVAL_SECONDS)  # Wait before retrying

def start_security_monitor():
    """Function to start the security monitoring thread"""
    global security_monitor
    security_monitor = threading.Thread(target=security_monitoring_thread, daemon=True)
    security_monitor.start()
    logger.info("Security monitoring thread started")

# Start the security monitoring thread
start_security_monitor()

@app.get("/")
async def root():
    return {"message": "NBA Stats API is running"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected to WebSocket")
    active_connections.append(websocket)
    
    try:
        # Send a welcome message
        await websocket.send_text(json.dumps({
            "event": "connection_status",
            "data": "connected"
        }))
        
        # Keep the connection alive
        while True:
            # This will wait for any message from the client
            # and prevent the connection from closing
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket")
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
    finally:
        # Remove from active connections
        if websocket in active_connections:
            active_connections.remove(websocket)
            logger.info("Client removed from active connections")

@app.get("/players")
async def get_players(
    page: int = Query(1, ge=1),
    search: str = Query(default=""),
    filter_by: str = Query(default="name"),
    sort_order: str = Query(default="desc"),
    position: str = Query(default="ALL"),
    selected_ids: str = Query(default="")
):
    try:
        # Convert selected_ids string to list of integers
        selected_id_list = [int(id) for id in selected_ids.split(',')] if selected_ids else []
        
        # Get all players from database
        all_players = get_all_players_from_db()
        
        # Start with all players
        available_players = [p for p in all_players if p['id'] not in selected_id_list]
        
        # Apply search filter
        if search:
            search_lower = search.lower()
            available_players = [
                p for p in available_players
                if search_lower in p.get('full_name', '').lower()
            ]
        
        # Apply position filter
        if position and position != "ALL":
            available_players = [
                p for p in available_players 
                if p.get('position', '').upper() == position.upper()
            ]

        # Sort players
        valid_sort_fields = {
            "name": "full_name",
            "ppg": "ppg",
            "apg": "apg",
            "rpg": "rpg",
            "spg": "spg",
            "win_shares": "win_shares",
            "box_plus_minus": "box_plus_minus",
            "eff": "eff"
        }

        sort_field = valid_sort_fields.get(filter_by, "full_name")
        reverse_sort = sort_order.lower() == "desc"
        
        available_players.sort(
            key=lambda x: (
                float(x.get(sort_field, 0)) 
                if sort_field != "full_name" 
                else x.get(sort_field, "")
            ),
            reverse=reverse_sort
        )

        # Pagination
        total_players = len(available_players)
        total_pages = max(1, (total_players + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        
        start_idx = (page - 1) * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, total_players)
        
        return {
            "players": available_players[start_idx:end_idx],
            "total_pages": total_pages,
            "current_page": page,
            "total_players": total_players
        }

    except Exception as e:
        logger.error(f"Error in get_players: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/player/{player_id}/stats")
async def get_player_stats_endpoint(player_id: int):
    player = get_player_from_db(player_id)
    if player:
            return player
    raise HTTPException(status_code=404, detail="Player not found")

# Local dev: Run uvicorn backend.main:app --reload (from project root)
# Deployment: gunicorn main:app -k uvicorn.workers.UvicornWorker (from backend directory)
#Frontend: npm run serve

@app.get("/test")
async def test():
    return {"message": "Backend is working"}

class CourtPosition(BaseModel):
    position: str
    player_id: int

class CourtUpdate(BaseModel):
    position: str
    player: Optional[Dict] = None

COURT_PLAYERS = {
    "PG": None,
    "SG": None,
    "SF": None,
    "PF": None,
    "C": None
}

@app.get("/court")
async def get_court_players():
    """Get all players currently on the court"""
    return get_court_players_from_db()

@app.post("/court/add")
async def add_player_to_court(data: CourtPosition, current_user: dict = Depends(get_current_user)):
    """Add a player to a position on the court"""
    position = data.position
    player_id = data.player_id
    
    if position not in ["PG", "SG", "SF", "PF", "C"]:
        raise HTTPException(status_code=400, detail=f"Invalid position: {position}")
    
    # Find player in our data
    player = get_player_from_db(player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player with ID {player_id} not found")
    
    # Update player on the court
    add_player_to_court_db(position, player_id)
    
    # Log the create action
    username = current_user["username"]
    player_details = {"name": player.get("full_name", "Unknown"), "id": player.get("id", "Unknown")}
    log_action(
        username=username,
        action_type="CREATE",
        entity_type="court_player",
        entity_id=position,
        details=json.dumps(player_details)
    )
    
    logger.info(f"Added player {player['full_name']} to position {position}")
    return {"position": position, "player": player}

@app.delete("/court/{position}", response_model=dict)
async def remove_player_from_court(position: str, current_user: dict = Depends(get_current_user)):
    """Remove a player from a position on the court"""
    if position not in ["PG", "SG", "SF", "PF", "C"]:
        raise HTTPException(status_code=400, detail=f"Invalid position: {position}")
    
    # Get player details before removing
    court_players = get_court_players_from_db()
    player = court_players.get(position)
    
    if player is None:
        return {"message": f"No player at position {position}"}
    
    # Remove player from the court
    remove_player_from_court_db(position)
    
    # Log the delete action
    username = current_user["username"]
    player_details = {"name": player.get("full_name", "Unknown"), "id": player.get("id", "Unknown")}
    log_action(
        username=username,
        action_type="DELETE",
        entity_type="court_player",
        entity_id=position,
        details=json.dumps(player_details)
    )
    
    logger.info(f"Removed player from position {position}")
    return {"position": position, "player": player}

@app.put("/court/{position}")
async def update_player_on_court(position: str, data: CourtPosition, current_user: dict = Depends(get_current_user)):
    """Update/replace a player at a position on the court"""
    if position not in ["PG", "SG", "SF", "PF", "C"]:
        raise HTTPException(status_code=400, detail=f"Invalid position: {position}")
    
    player_id = data.player_id
    
    # Find player in our data
    player = get_player_from_db(player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player with ID {player_id} not found")
    
    # Update player on the court
    add_player_to_court_db(position, player_id)
    
    # Log the update action
    username = current_user["username"]
    player_details = {"name": player.get("full_name", "Unknown"), "id": player.get("id", "Unknown")}
    log_action(
        username=username,
        action_type="UPDATE",
        entity_type="court_player",
        entity_id=position,
        details=json.dumps(player_details)
    )
    
    logger.info(f"Updated position {position} with player {player['full_name']}")
    return {"position": position, "player": player}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), description: str = Form(""), current_user: dict = Depends(get_current_user)):
    """
    Upload a file to the server with an optional description
    """
    try:
        # Create a unique filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # Create file metadata
        file_info = {
            "filename": filename,
            "original_name": file.filename,
            "content_type": file.content_type,
            "size": file_size,
            "description": description,
            "uploaded_at": datetime.now().isoformat(),
            "path": f"/files/{filename}"
        }
        
        # Create metadata file
        metadata_path = os.path.join(UPLOAD_DIR, f"{filename}.meta.json")
        with open(metadata_path, "w") as meta_file:
            json.dump(file_info, meta_file)
            
        # Log the upload action
        username = current_user["username"]
        file_details = {
            "filename": filename,
            "original_name": file.filename,
            "size": file_size,
            "content_type": file.content_type
        }
        log_action(
            username=username,
            action_type="CREATE",
            entity_type="file",
            entity_id=filename,
            details=json.dumps(file_details)
        )
            
        logger.info(f"File uploaded: {filename}, size: {file_size} bytes")
        
        return {
            "status": "success",
            "message": "File uploaded successfully",
            "file_info": file_info
        }
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

@app.get("/files")
async def list_files():
    """
    List all uploaded files
    """
    try:
        files = []
        for filename in os.listdir(UPLOAD_DIR):
            # Skip metadata files
            if filename.endswith(".meta.json"):
                continue
                
            # Get metadata if available
            metadata_path = os.path.join(UPLOAD_DIR, f"{filename}.meta.json")
            if os.path.exists(metadata_path):
                with open(metadata_path, "r") as meta_file:
                    file_info = json.load(meta_file)
                    files.append(file_info)
            else:
                # Create basic info for files without metadata
                file_path = os.path.join(UPLOAD_DIR, filename)
                file_size = os.path.getsize(file_path)
                file_info = {
                    "filename": filename,
                    "original_name": filename,
                    "size": file_size,
                    "description": "",
                    "uploaded_at": datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(),
                    "path": f"/files/{filename}"
                }
                files.append(file_info)
                
        # Sort by upload date (newest first)
        files.sort(key=lambda x: x["uploaded_at"], reverse=True)
        
        return {
            "status": "success",
            "files": files
        }
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")

@app.get("/download/{filename}")
async def download_file(filename: str):
    """
    Download a file from the server
    """
    try:
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")
            
        # Get metadata if available
        content_type = "application/octet-stream"  # Default content type
        original_name = filename
        
        metadata_path = os.path.join(UPLOAD_DIR, f"{filename}.meta.json")
        if os.path.exists(metadata_path):
            with open(metadata_path, "r") as meta_file:
                metadata = json.load(meta_file)
                content_type = metadata.get("content_type", content_type)
                original_name = metadata.get("original_name", original_name)
        
        # Read file content
        with open(file_path, "rb") as file:
            content = file.read()
            
        # Return file as response with appropriate headers
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename=\"{original_name}\""
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)}")

@app.delete("/file-delete/{filename}")
async def delete_file_endpoint(filename: str, current_user: dict = Depends(get_current_user)):
    """
    Delete a file from the server
    """
    try:
        # Sanitize filename to prevent directory traversal
        if os.path.sep in filename or filename.startswith('.'):
            raise HTTPException(status_code=400, detail="Invalid filename")
            
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        # Get file metadata if available
        file_metadata = {}
        metadata_path = os.path.join(UPLOAD_DIR, f"{filename}.meta.json")
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as meta_file:
                    file_metadata = json.load(meta_file)
            except Exception as e:
                logger.error(f"Error reading file metadata: {e}")
            
        try:
            # Delete the file
            os.remove(file_path)
            
            # Delete metadata if exists
            if os.path.exists(metadata_path):
                os.remove(metadata_path)
                
            # Log the delete action
            username = current_user["username"]
            file_details = {
                "filename": filename,
                "original_name": file_metadata.get("original_name", filename),
                "size": file_metadata.get("size", 0)
            }
            log_action(
                username=username,
                action_type="DELETE",
                entity_type="file",
                entity_id=filename,
                details=json.dumps(file_details)
            )
                
            logger.info(f"File deleted: {filename}")
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied to delete file")
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"OS error when deleting file: {str(e)}")
        
        return {
            "status": "success",
            "message": f"File {filename} deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")

# Add an options endpoint for preflight requests
@app.options("/file-delete/{filename}")
async def options_file_delete(filename: str):
    return {}

@app.get("/ping")
async def ping():
    """
    Simple ping endpoint for checking server availability
    """
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/debug/database")
async def debug_database():
    """
    Debug endpoint to check database contents
    """
    try:
        # Check database file existence
        if not os.path.exists(DB_FILE):
            return {"error": f"Database file {DB_FILE} does not exist"}
            
        # Check database size
        db_size = os.path.getsize(DB_FILE)
        
        # Connect to database and check tables
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Check if tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        table_names = [t[0] for t in tables]
        
        # Get player count
        player_count = 0
        if "players" in table_names:
            cursor.execute("SELECT COUNT(*) FROM players")
            player_count = cursor.fetchone()[0]
            
            # Get a sample of players
            cursor.execute("SELECT id, full_name FROM players LIMIT 5")
            sample_players = cursor.fetchall()
        else:
            sample_players = []
            
        # Check court_players
        court_data = []
        if "court_players" in table_names:
            cursor.execute("SELECT * FROM court_players")
            court_data = cursor.fetchall()
            
        conn.close()
        
        return {
            "db_file": DB_FILE,
            "db_exists": True,
            "db_size": db_size,
            "tables": table_names,
            "player_count": player_count,
            "sample_players": sample_players,
            "court_data": court_data
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/export-db")
async def export_database():
    """
    Export database contents to a JSON file
    """
    try:
        export_file = "db_export.json"
        
        # Connect to database
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row  # This enables column access by name
        cursor = conn.cursor()
        
        # Get all players
        cursor.execute("SELECT * FROM players")
        player_rows = cursor.fetchall()
        players_data = []
        for row in player_rows:
            player_dict = {key: row[key] for key in row.keys()}
            players_data.append(player_dict)
            
        # Get court players
        cursor.execute("SELECT * FROM court_players")
        court_rows = cursor.fetchall()
        court_data = []
        for row in court_rows:
            court_dict = {key: row[key] for key in row.keys()}
            court_data.append(court_dict)
            
        conn.close()
        
        # Create export data
        export_data = {
            "db_file": os.path.abspath(DB_FILE),
            "export_time": datetime.now().isoformat(),
            "player_count": len(players_data),
            "players": players_data[:10],  # Only export first 10 for brevity
            "court_players": court_data
        }
        
        # Write to file
        with open(export_file, "w") as f:
            json.dump(export_data, f, indent=2)
            
        return {
            "message": f"Database exported to {export_file}",
            "filepath": os.path.abspath(export_file),
            "player_count": len(players_data)
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/reset-database")
async def reset_database():
    """Reset the database and rebuild from scratch"""
    try:
        # Close any open connections
        conn = sqlite3.connect(DB_FILE)
        conn.close()
        
        # Delete the database file
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
            logger.info(f"Deleted database file: {DB_FILE}")
        
        # Reinitialize the database
        init_db()
        logger.info("Database reinitialized")
        
        # Reload players
        players_count = len(initialize_players())
        logger.info(f"Reloaded {players_count} players")
        
        return {
            "status": "success",
            "message": "Database reset and rebuilt successfully",
            "players_count": players_count
        }
    except Exception as e:
        logger.error(f"Error resetting database: {str(e)}")
        return {"error": str(e)}

@app.get("/stats/advanced")
async def advanced_player_stats(
    min_ppg: float = Query(0.0, ge=0.0, description="Minimum points per game"),
    min_win_shares: float = Query(-5.0, ge=-5.0, description="Minimum win shares"),
    position: str = Query("ALL", description="Filter by position"),
    sort_by: str = Query("eff", description="Sort by statistical category"),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
    combine_stats: bool = Query(False, description="Whether to use combined statistical ranking"),
    include_inactive: bool = Query(False, description="Whether to include inactive players"),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    limit: int = Query(100, ge=1, le=500, description="Number of players per page")
):
    """
    Advanced statistical query endpoint optimized for performance with large datasets.
    This endpoint demonstrates various SQL optimization techniques.
    """
    try:
        # Create connection with performance optimizations
        conn = sqlite3.connect(DB_FILE, timeout=30)
        conn.execute('PRAGMA journal_mode = WAL')  # Write-Ahead Logging for better concurrency
        conn.execute('PRAGMA synchronous = NORMAL')  # Reduce synchronous writes
        conn.execute('PRAGMA cache_size = -64000')  # 64MB cache
        conn.execute('PRAGMA temp_store = MEMORY')  # Store temp tables in memory
        cursor = conn.cursor()
        
        # Start building the query
        query_parts = ["SELECT * FROM players WHERE 1=1"]
        params = []
        
        # Apply filters
        if min_ppg > 0:
            query_parts.append("AND ppg >= ?")
            params.append(min_ppg)
            
        if min_win_shares > -5:
            query_parts.append("AND win_shares >= ?")
            params.append(min_win_shares)
            
        if position != "ALL":
            query_parts.append("AND position = ?")
            params.append(position)
            
        if not include_inactive:
            query_parts.append("AND is_active = 1")
        
        # Determine sort field
        valid_sort_fields = {
            "name": "full_name",
            "ppg": "ppg",
            "apg": "apg",
            "rpg": "rpg",
            "spg": "spg",
            "win_shares": "win_shares",
            "box_plus_minus": "box_plus_minus",
            "eff": "eff"
        }
        
        sort_field = valid_sort_fields.get(sort_by, "eff")
        
        # Apply sorting
        if combine_stats:
            # Use a complex statistical formula for ranking
            # This demonstrates a more complex query that can benefit from optimization
            query_parts.append("""
                ORDER BY (
                    CASE WHEN ppg IS NULL THEN 0 ELSE ppg * 1.0 END +
                    CASE WHEN apg IS NULL THEN 0 ELSE apg * 2.0 END +
                    CASE WHEN rpg IS NULL THEN 0 ELSE rpg * 1.5 END +
                    CASE WHEN spg IS NULL THEN 0 ELSE spg * 3.0 END +
                    CASE WHEN win_shares IS NULL THEN 0 ELSE win_shares * 2.5 END +
                    CASE WHEN box_plus_minus IS NULL THEN 0 ELSE box_plus_minus * 2.0 END +
                    CASE WHEN eff IS NULL THEN 0 ELSE eff * 1.8 END
                ) 
            """)
        else:
            # Standard sorting
            query_parts.append(f"ORDER BY {sort_field}")
        
        # Apply sort direction
        query_parts.append("DESC" if sort_order.lower() == "desc" else "ASC")
        
        # Apply pagination
        offset = (page - 1) * limit
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        # Construct the final query
        final_query = " ".join(query_parts)
        
        # Add query execution timing
        start_time = time.time()
        cursor.execute(final_query, params)
        rows = cursor.fetchall()
        query_time = time.time() - start_time
        
        # Get the count of total matches (for pagination info)
        # Extract just the WHERE clause for the count query
        where_clause = " ".join(query_parts[0:len(query_parts)-3])  # Remove ORDER BY, LIMIT, OFFSET
        count_query = f"SELECT COUNT(*) FROM ({where_clause})"
        cursor.execute(count_query, params[:-2])  # Remove limit and offset params
        total_count = cursor.fetchone()[0]
        
        # Convert rows to player objects
        players = []
        columns = [column[0] for column in cursor.description]
        for row in rows:
            player_dict = dict(zip(columns, row))
            if 'data' in player_dict and player_dict['data']:
                try:
                    # Parse the stored JSON data
                    player_data = json.loads(player_dict['data'])
                    players.append(player_data)
                except json.JSONDecodeError:
                    # Fallback to raw dictionary if JSON parsing fails
                    players.append(player_dict)
            else:
                players.append(player_dict)
        
        conn.close()
        
        # Calculate pagination details
        total_pages = max(1, (total_count + limit - 1) // limit)
        
        return {
            "players": players,
            "total_players": total_count,
            "page": page,
            "total_pages": total_pages,
            "limit": limit,
            "query_time_ms": round(query_time * 1000, 2)
        }
        
    except Exception as e:
        logger.error(f"Error in advanced player stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Authentication endpoints
@app.post("/register", response_model=Token)
async def register_user(user: UserCreate):
    if user.role not in ["admin", "user"]:
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
    
    if not create_user(user):
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer", "role": user["role"]}

@app.get("/users/me", response_model=User)
async def read_users_me(current_user: dict = Depends(get_current_user)):
    return {"username": current_user["username"], "role": current_user["role"]}

@app.get("/actions/log")
async def get_action_log(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of log entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    username: Optional[str] = Query(None, description="Filter by username"),
    action_type: Optional[str] = Query(None, description="Filter by action type (CREATE, READ, UPDATE, DELETE)"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (player, court_player, file)"),
    from_date: Optional[str] = Query(None, description="Filter by date from (ISO format)"),
    to_date: Optional[str] = Query(None, description="Filter by date to (ISO format)"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get action log history with filtering options.
    Only admin users can access this endpoint.
    """
    # Check if user has admin role
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admin users can access the action log"
        )
        
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Start building the query
        query = "SELECT * FROM actions WHERE 1=1"
        params = []
        
        # Apply filters
        if username:
            query += " AND username = ?"
            params.append(username)
            
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
            
        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
            
        if from_date:
            query += " AND timestamp >= ?"
            params.append(from_date)
            
        if to_date:
            query += " AND timestamp <= ?"
            params.append(to_date)
            
        # Add ordering and pagination
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        # Execute query
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Get column names
        column_names = [description[0] for description in cursor.description]
        
        # Get total count for pagination
        count_query = f"SELECT COUNT(*) FROM actions WHERE 1=1"
        count_params = []
        
        if username:
            count_query += " AND username = ?"
            count_params.append(username)
            
        if action_type:
            count_query += " AND action_type = ?"
            count_params.append(action_type)
            
        if entity_type:
            count_query += " AND entity_type = ?"
            count_params.append(entity_type)
            
        if from_date:
            count_query += " AND timestamp >= ?"
            count_params.append(from_date)
            
        if to_date:
            count_query += " AND timestamp <= ?"
            count_params.append(to_date)
            
        cursor.execute(count_query, count_params)
        total_count = cursor.fetchone()[0]
        
        # Convert rows to dictionaries
        results = []
        for row in rows:
            result = dict(zip(column_names, row))
            
            # Parse details JSON if present
            if result.get("details"):
                try:
                    result["details"] = json.loads(result["details"])
                except json.JSONDecodeError:
                    # Keep as string if not valid JSON
                    pass
                    
            results.append(result)
            
        conn.close()
        
        return {
            "logs": results,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Error retrieving action log: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving action log: {str(e)}")

@app.get("/security/monitored-users")
async def get_monitored_users(current_user: dict = Depends(get_current_user)):
    """
    Get a list of monitored users (suspicious activity)
    Only admin users can access this endpoint
    """
    # Check if user has admin role
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admin users can view monitored users"
        )
        
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row  # This enables column access by name
        cursor = conn.cursor()
        
        # Get monitored users
        cursor.execute("""
        SELECT id, username, reason, action_count, first_detected, last_updated
        FROM monitored_users
        WHERE is_active = 1
        ORDER BY last_updated DESC
        """)
        
        rows = cursor.fetchall()
        
        # Convert to list of dictionaries
        monitored_users = []
        for row in rows:
            monitored_users.append({
                "id": row["id"],
                "username": row["username"],
                "reason": row["reason"],
                "action_count": row["action_count"],
                "first_detected": row["first_detected"],
                "last_updated": row["last_updated"]
            })
            
        conn.close()
        
        return {
            "monitored_users": monitored_users
        }
    except Exception as e:
        logger.error(f"Error retrieving monitored users: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving monitored users: {str(e)}")

# 2FA Endpoints
@app.post("/auth/2fa/request")
async def request_2fa_code(request: TwoFactorRequest):
    """Request a new 2FA code"""
    # In a real implementation, you would:
    # 1. Verify the user exists
    # 2. Check if 2FA is enabled for the user
    # 3. Send the code via email
    
    code = generate_2fa_code()
    store_2fa_code(request.username, code)
    
    # For development/testing, return the code
    # In production, send via email and return success message
    return {"message": "2FA code sent", "code": code}  # Remove code in production!

@app.post("/auth/2fa/verify")
async def verify_2fa_code_endpoint(verify: TwoFactorVerify):
    """Verify 2FA code and complete login"""
    if not verify_2fa_code(verify.username, verify.code):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired 2FA code"
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Get user role (in production, fetch from database)
    role = "user"  # Placeholder
    
    access_token = create_access_token(
        data={"sub": verify.username, "role": role},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": role
    }

@app.post("/auth/2fa/enable")
async def enable_2fa(current_user: dict = Depends(get_current_user)):
    """Enable 2FA for user"""
    # In a real implementation, you would:
    # 1. Update user's 2FA status in database
    # 2. Possibly generate and return TOTP secret
    
    return {"message": "2FA enabled successfully"}

@app.post("/auth/2fa/disable")
async def disable_2fa(current_user: dict = Depends(get_current_user)):
    """Disable 2FA for user"""
    # In a real implementation, you would:
    # 1. Update user's 2FA status in database
    # 2. Clean up any 2FA-related data
    
    return {"message": "2FA disabled successfully"}

