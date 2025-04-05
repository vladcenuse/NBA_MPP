from fastapi import FastAPI, Query, HTTPException, Body, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# Store active WebSocket connections
active_connections: List[WebSocket] = []

# Track the new players added by the generator thread
new_players = []
player_generation_lock = threading.Lock()

def calculate_win_shares(pts, ast, reb, stl, games_played):
    """
    Simplified Win Shares formula based on basic stats, always positive
    """
    if games_played == 0:
        return 0
        
    # Points efficiency (pts per game * 0.03)
    pts_contribution = (pts / games_played) * 0.03
    
    # Playmaking (assists per game * 0.02)
    ast_contribution = (ast / games_played) * 0.02
    
    # Defense and rebounds (rebounds + steals per game * 0.01)
    def_contribution = ((reb + stl) / games_played) * 0.01
    
    # Combine and scale to realistic WS range (0-15)
    win_shares = (pts_contribution + ast_contribution + def_contribution) * 2
    return min(max(win_shares, 0), 15)  # Ensure positive and capped at 15

def calculate_bpm(pts, ast, reb, stl, games_played):
    """
    Simplified Box Plus/Minus formula based on basic stats, always positive
    """
    if games_played == 0:
        return 0
        
    # Per game stats
    ppg = pts / games_played
    apg = ast / games_played
    rpg = reb / games_played
    spg = stl / games_played
    
    # Base impact (points per game as baseline)
    base_impact = (ppg) * 0.25
    
    # Playmaking impact
    playmaking = (apg) * 0.5
    
    # Defensive impact
    defense = (rpg + spg) * 0.3
    
    # Combine into final BPM (0-12 range)
    bpm = base_impact + playmaking + defense
    return min(max(bpm, 0), 12)  # Ensure positive and capped at 12

def initialize_players():
    active_players = players.get_active_players()[:30]
    players_with_stats = []
    
    logger.info("Initializing players and fetching their stats...")
    for player in active_players:
        try:
            time.sleep(0.6)
            # Get basic stats
            career = playercareerstats.PlayerCareerStats(player_id=player['id'])
            stats_df = career.get_data_frames()[0]
            
            # Get position
            player_info = commonplayerinfo.CommonPlayerInfo(player_id=player['id'])
            player_info_df = player_info.get_data_frames()[0]
            position = player_info_df['POSITION'].iloc[0] if not player_info_df.empty else 'N/A'
            
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
            
            mapped_position = position_mapping.get(position, position)
            
            if not stats_df.empty:
                latest_stats = stats_df.iloc[-1]
                games_played = float(latest_stats["GP"])
                pts = float(latest_stats["PTS"])
                ast = float(latest_stats["AST"])
                reb = float(latest_stats["REB"])
                stl = float(latest_stats["STL"])
                
                # Calculate per game stats
                ppg = pts / games_played
                apg = ast / games_played
                rpg = reb / games_played
                
                # Calculate EFF (Efficiency) stat
                eff = ppg + apg + rpg
                
                win_shares = calculate_win_shares(pts, ast, reb, stl, games_played)
                box_plus_minus = calculate_bpm(pts, ast, reb, stl, games_played)
                
                player_stats = {
                    "ppg": round(ppg, 1),
                    "apg": round(apg, 1),
                    "rpg": round(rpg, 1),
                    "spg": round(stl / games_played, 1),
                    "win_shares": round(win_shares, 1),
                    "box_plus_minus": round(box_plus_minus, 1),
                    "eff": round(eff, 1),
                    "position": mapped_position
                }
            else:
                player_stats = {
                    "ppg": 0, "apg": 0, "rpg": 0, "spg": 0,
                    "win_shares": 0, "box_plus_minus": 0,
                    "eff": 0,
                    "position": mapped_position
                }
                
            players_with_stats.append({
                **player,
                **player_stats
            })
            logger.info(f"Fetched stats for {player['full_name']} (Position: {mapped_position})")
            time.sleep(0.6)
            
        except Exception as e:
            logger.error(f"Error fetching stats for {player['full_name']}: {str(e)}")
            players_with_stats.append({
                **player,
                "ppg": 0, "apg": 0, "rpg": 0, "spg": 0,
                "win_shares": 0, "box_plus_minus": 0,
                "eff": 0,
                "position": 'N/A'
            })
    
    logger.info("Finished initializing players")
    return players_with_stats

def fetch_new_player():
    """
    Fetches a new player from the NBA API, not already in our list
    """
    try:
        # Get all active players to select from
        all_active_players = players.get_active_players()
        
        # Filter out players already in our list
        existing_ids = [p['id'] for p in PLAYERS_WITH_STATS]
        new_player_candidates = [p for p in all_active_players if p['id'] not in existing_ids]
        
        if not new_player_candidates:
            logger.error("No more players to add!")
            return None
            
        # Select a random player from candidates
        player = random.choice(new_player_candidates)
        
        # Wait to avoid API rate limits
        time.sleep(0.6)
        
        # Get basic stats
        career = playercareerstats.PlayerCareerStats(player_id=player['id'])
        stats_df = career.get_data_frames()[0]
        
        # Get position
        player_info = commonplayerinfo.CommonPlayerInfo(player_id=player['id'])
        player_info_df = player_info.get_data_frames()[0]
        position = player_info_df['POSITION'].iloc[0] if not player_info_df.empty else 'N/A'
        
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
        
        mapped_position = position_mapping.get(position, position)
        
        if not stats_df.empty:
            latest_stats = stats_df.iloc[-1]
            games_played = float(latest_stats["GP"])
            pts = float(latest_stats["PTS"])
            ast = float(latest_stats["AST"])
            reb = float(latest_stats["REB"])
            stl = float(latest_stats["STL"])
            
            # Calculate per game stats
            ppg = pts / games_played if games_played > 0 else 0
            apg = ast / games_played if games_played > 0 else 0
            rpg = reb / games_played if games_played > 0 else 0
            
            # Calculate EFF (Efficiency) stat
            eff = ppg + apg + rpg
            
            win_shares = calculate_win_shares(pts, ast, reb, stl, games_played)
            box_plus_minus = calculate_bpm(pts, ast, reb, stl, games_played)
            
            player_stats = {
                "ppg": round(ppg, 1),
                "apg": round(apg, 1),
                "rpg": round(rpg, 1),
                "spg": round(stl / games_played, 1) if games_played > 0 else 0,
                "win_shares": round(win_shares, 1),
                "box_plus_minus": round(box_plus_minus, 1),
                "eff": round(eff, 1),
                "position": mapped_position
            }
        else:
            player_stats = {
                "ppg": 0, "apg": 0, "rpg": 0, "spg": 0,
                "win_shares": 0, "box_plus_minus": 0,
                "eff": 0,
                "position": mapped_position
            }
            
        new_player = {
            **player,
            **player_stats,
            "added_at": datetime.now().isoformat()
        }
        
        logger.info(f"Generated new player: {new_player['full_name']} (Position: {mapped_position})")
        return new_player
        
    except Exception as e:
        logger.error(f"Error generating new player: {str(e)}")
        return None

def player_generator_thread():
    """
    Background thread that generates a new player every 10 seconds
    """
    logger.info("Starting player generator thread")
    
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    while True:
        try:
            # Generate a new player
            new_player = fetch_new_player()
            
            if new_player:
                with player_generation_lock:
                    # Add to global players list
                    PLAYERS_WITH_STATS.append(new_player)
                
                # Log the new player
                logger.info(f"Generated new player: {new_player['full_name']}")
                
                # Construct the message
                message = {
                    "event": "new_player",
                    "data": new_player
                }
                
                # Send to all clients
                if active_connections:
                    logger.info(f"Broadcasting to {len(active_connections)} clients")
                    loop.run_until_complete(broadcast_message(message))
                else:
                    logger.info("No clients connected to broadcast to")
            
            # Wait for next generation cycle
            time.sleep(10)
            
        except Exception as e:
            logger.error(f"Error in player generator thread: {str(e)}")
            time.sleep(10)  # Still wait before retrying

async def broadcast_message(message):
    """
    Broadcasts a message to all active WebSocket connections
    """
    # Convert the message to JSON
    message_text = json.dumps(message)
    
    # Make a copy of connections to avoid issues during iteration
    connections = active_connections.copy()
    
    # Send to all connections
    for connection in connections:
        try:
            await connection.send_text(message_text)
        except Exception as e:
            logger.error(f"Error sending message to client: {str(e)}")
            # Remove failed connection
            if connection in active_connections:
                active_connections.remove(connection)

# Initialize players with stats at startup
PLAYERS_WITH_STATS = initialize_players()
ITEMS_PER_PAGE = 10

# Start the player generator thread
player_generator = threading.Thread(target=player_generator_thread, daemon=True)
player_generator.start()

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
        
        # Start with all players
        available_players = [p for p in PLAYERS_WITH_STATS if p['id'] not in selected_id_list]
        
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
    for player in PLAYERS_WITH_STATS:
        if player['id'] == player_id:
            return player
    raise HTTPException(status_code=404, detail="Player not found")

#Backend: Run uvicorn backend.main:app --reload
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
    court_with_details = {}
    
    for position, player_id in COURT_PLAYERS.items():
        if player_id is not None:
            # Find player details
            player = next((p for p in PLAYERS_WITH_STATS if p['id'] == player_id), None)
            court_with_details[position] = player
        else:
            court_with_details[position] = None
            
    return court_with_details

@app.post("/court/add")
async def add_player_to_court(data: CourtPosition):
    """Add a player to a position on the court"""
    position = data.position
    player_id = data.player_id
    
    if position not in COURT_PLAYERS:
        raise HTTPException(status_code=400, detail=f"Invalid position: {position}")
    
    # Find player in our data
    player = next((p for p in PLAYERS_WITH_STATS if p['id'] == player_id), None)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player with ID {player_id} not found")
    
    # Add player to the court
    COURT_PLAYERS[position] = player_id
    
    logger.info(f"Added player {player['full_name']} to position {position}")
    return {"position": position, "player": player}

@app.delete("/court/{position}")
async def remove_player_from_court(position: str):
    """Remove a player from a position on the court"""
    if position not in COURT_PLAYERS:
        raise HTTPException(status_code=400, detail=f"Invalid position: {position}")
    
    if COURT_PLAYERS[position] is None:
        return {"message": f"No player at position {position}"}
    
    # Get player details before removing
    player_id = COURT_PLAYERS[position]
    player = next((p for p in PLAYERS_WITH_STATS if p['id'] == player_id), None)
    
    # Remove player from the court
    COURT_PLAYERS[position] = None
    
    logger.info(f"Removed player from position {position}")
    return {"position": position, "player": player}

@app.put("/court/{position}")
async def update_player_on_court(position: str, data: CourtPosition):
    """Update/replace a player at a position on the court"""
    if position not in COURT_PLAYERS:
        raise HTTPException(status_code=400, detail=f"Invalid position: {position}")
    
    player_id = data.player_id
    
    # Find player in our data
    player = next((p for p in PLAYERS_WITH_STATS if p['id'] == player_id), None)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player with ID {player_id} not found")
    
    # Update player on the court
    COURT_PLAYERS[position] = player_id
    
    logger.info(f"Updated position {position} with player {player['full_name']}")
    return {"position": position, "player": player}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), description: str = Form("")):
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
async def delete_file_endpoint(filename: str):
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
            
        try:
            # Delete the file
            os.remove(file_path)
            
            # Delete metadata if exists
            metadata_path = os.path.join(UPLOAD_DIR, f"{filename}.meta.json")
            if os.path.exists(metadata_path):
                os.remove(metadata_path)
                
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

