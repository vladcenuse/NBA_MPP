from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from nba_api.stats.static import players
from nba_api.stats.endpoints import playercareerstats, commonplayerinfo
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
            
            # Map NBA API position codes to our format
            position_mapping = {
                # Common NBA API position formats
                'G': 'PG',      # Changed from SG to PG
                'G-F': 'SG',    # Guard-Forward is typically a shooting guard
                'F-G': 'SG',    # Forward-Guard is typically a shooting guard
                'F': 'SF',      # Forward defaults to Small Forward
                'F-C': 'PF',    # Forward-Center is typically Power Forward
                'C-F': 'PF',    # Center-Forward is typically Power Forward
                'C': 'C',       # Center stays Center
                # Explicit positions
                'Point Guard': 'PG',
                'Shooting Guard': 'SG',
                'Small Forward': 'SF',
                'Power Forward': 'PF',
                'Center': 'C',
                # Abbreviations
                'PG': 'PG',
                'SG': 'SG',
                'SF': 'SF',
                'PF': 'PF',
                # Additional combinations
                'Guard': 'PG',          # Changed from SG to PG
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
                
                # Calculate advanced stats using our formulas
                win_shares = calculate_win_shares(pts, ast, reb, stl, games_played)
                box_plus_minus = calculate_bpm(pts, ast, reb, stl, games_played)
                
                player_stats = {
                    "ppg": round(pts / games_played, 1),
                    "apg": round(ast / games_played, 1),
                    "rpg": round(reb / games_played, 1),
                    "spg": round(stl / games_played, 1),
                    "win_shares": round(win_shares, 1),
                    "box_plus_minus": round(box_plus_minus, 1),
                    "position": mapped_position
                }
            else:
                player_stats = {
                    "ppg": 0, "apg": 0, "rpg": 0, "spg": 0,
                    "win_shares": 0, "box_plus_minus": 0,
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
                "position": 'N/A'
            })
    
    logger.info("Finished initializing players")
    return players_with_stats

# Initialize players with stats at startup
PLAYERS_WITH_STATS = initialize_players()
ITEMS_PER_PAGE = 10

@app.get("/")
async def root():
    return {"message": "NBA Stats API is running"}

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
            "box_plus_minus": "box_plus_minus"
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

