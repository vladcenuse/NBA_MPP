import sqlite3
import json
import os
from datetime import datetime

def check_database():
    """Simple script to check database contents directly with Python"""
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_file = os.path.join(script_dir, "nba_app.db")
    
    # Get absolute path
    abs_path = os.path.abspath(db_file)
    print(f"Checking database at: {abs_path}")
    
    # Check if file exists
    if not os.path.exists(db_file):
        print(f"ERROR: Database file {db_file} does not exist")
        return
        
    # Check file size
    size = os.path.getsize(db_file)
    print(f"Database size: {size} bytes")
    
    try:
        # Connect to database
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"Tables in database: {tables}")
        
        # Check player count
        cursor.execute("SELECT COUNT(*) FROM players")
        count = cursor.fetchone()[0]
        print(f"Player count: {count}")
        
        # Sample players
        cursor.execute("SELECT id, full_name FROM players LIMIT 5")
        players = cursor.fetchall()
        print(f"Sample players: {players}")
        
        # Check court players
        cursor.execute("SELECT * FROM court_players")
        court = cursor.fetchall()
        print(f"Court players: {court}")
        
        conn.close()
        
        # Write results to file for easier inspection
        results = {
            "timestamp": datetime.now().isoformat(),
            "db_path": abs_path,
            "db_size": size,
            "tables": [t[0] for t in tables],
            "player_count": count,
            "sample_players": players,
            "court_data": court
        }
        
        with open(os.path.join(script_dir, "db_check_results.json"), "w") as f:
            json.dump(results, f, indent=2)
            
        print(f"Results saved to db_check_results.json")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")

if __name__ == "__main__":
    check_database() 