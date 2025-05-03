"""
Database Stress Test and Optimization Script
- Populates the 'players' table with 100,000+ entries using Faker
- Implements database optimizations (indexes, query optimization)
- Tests performance under load
"""

import sqlite3
import time
import json
import os
import random
import statistics
from datetime import datetime
from pathlib import Path
from faker import Faker
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import logging
import requests
import asyncio
import aiohttp
from tqdm import tqdm

# Setup logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Database file - use absolute path
DB_FILE = os.path.join(SCRIPT_DIR, "nba_app.db")
logger.info(f"Using database at: {DB_FILE}")

# Initialize Faker
fake = Faker()

# Constants
BATCH_SIZE = 1000  # Insert in batches for better performance
TOTAL_PLAYERS = 100000
POSITIONS = ["PG", "SG", "SF", "PF", "C"]

# Initialize database connection
def get_db_connection():
    """Create a database connection with optimized settings"""
    conn = sqlite3.connect(DB_FILE, timeout=30)
    # Performance optimizations
    conn.execute('PRAGMA journal_mode = WAL')  # Write-Ahead Logging for better concurrency
    conn.execute('PRAGMA synchronous = NORMAL')  # Reduce synchronous writes for better performance
    conn.execute('PRAGMA cache_size = -64000')  # 64MB cache
    conn.execute('PRAGMA temp_store = MEMORY')  # Store temp tables in memory
    
    return conn

def create_indices():
    """Create indices on frequently queried columns"""
    logger.info("Creating database indices for performance optimization...")
    conn = get_db_connection()
    try:
        # Index for player lookups by ID (Primary Key is already indexed)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_full_name ON players(full_name)')
        
        # Index for filtering by position
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_position ON players(position)')
        
        # Composite index for stats sorting (covers most common query patterns)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_ppg ON players(ppg DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_apg ON players(apg DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_rpg ON players(rpg DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_win_shares ON players(win_shares DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_eff ON players(eff DESC)')
        
        # Index for filtering by active status
        conn.execute('CREATE INDEX IF NOT EXISTS idx_players_is_active ON players(is_active)')
        
        conn.commit()
        logger.info("Indices created successfully")
    except Exception as e:
        logger.error(f"Error creating indices: {e}")
        conn.rollback()
    finally:
        conn.close()

def generate_fake_player(id):
    """Generate a fake player record with realistic basketball stats"""
    first_name = fake.first_name_male()
    last_name = fake.last_name()
    full_name = f"{first_name} {last_name}"
    
    # Generate random stats (with realistic distributions)
    position = random.choice(POSITIONS)
    is_active = random.random() < 0.7  # 70% active players
    
    # Generate stats with realistic distributions
    ppg = max(0, min(35, random.normalvariate(12, 6)))  # Points per game
    apg = max(0, min(12, random.normalvariate(3, 2)))   # Assists per game
    rpg = max(0, min(15, random.normalvariate(5, 3)))   # Rebounds per game
    spg = max(0, min(4, random.normalvariate(1, 0.7)))  # Steals per game
    
    # Advanced stats
    win_shares = max(-5, min(15, random.normalvariate(4, 3)))
    box_plus_minus = max(-10, min(15, random.normalvariate(0, 4)))
    eff = max(0, min(35, random.normalvariate(15, 7)))
    
    # Create player dictionary
    player = {
        "id": id,
        "full_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "is_active": is_active,
        "position": position,
        "ppg": round(ppg, 1),
        "apg": round(apg, 1),
        "rpg": round(rpg, 1),
        "spg": round(spg, 1),
        "win_shares": round(win_shares, 1),
        "box_plus_minus": round(box_plus_minus, 1),
        "eff": round(eff, 1),
    }
    
    # Add additional details that would be in the data field
    player_data = player.copy()
    player_data.update({
        "team": fake.city() + " " + fake.word(ext_word_list=['Kings', 'Warriors', 'Lakers', 'Nets', 'Heat', 'Bulls', 'Celtics', 'Raptors', 'Bucks', 'Suns']),
        "height": f"{random.randint(68, 87)} inches",
        "weight": f"{random.randint(160, 290)} lbs",
        "draft_year": random.randint(1995, 2023),
        "draft_round": random.randint(1, 2),
        "draft_number": random.randint(1, 60)
    })
    
    # Store the data field as JSON
    player["data"] = json.dumps(player_data)
    player["last_updated"] = datetime.now().isoformat()
    
    return player

def populate_players_table():
    """Populate the players table with fake player data"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check current count
        cursor.execute("SELECT COUNT(*) FROM players")
        current_count = cursor.fetchone()[0]
        logger.info(f"Current player count: {current_count}")
        
        if current_count >= TOTAL_PLAYERS:
            logger.info(f"Already have {current_count} players, skipping population")
            return
        
        # How many more to add
        to_add = TOTAL_PLAYERS - current_count
        logger.info(f"Adding {to_add} more players to reach {TOTAL_PLAYERS}")
        
        # Determine starting ID (need to be unique)
        cursor.execute("SELECT MAX(id) FROM players")
        max_id = cursor.fetchone()[0]
        start_id = (max_id or 0) + 1
        
        # Use batch inserts for better performance
        start_time = time.time()
        batch = []
        
        for i in tqdm(range(to_add), desc="Generating players"):
            player_id = start_id + i
            player = generate_fake_player(player_id)
            
            # Add to batch
            batch.append((
                player["id"], player["full_name"], player["first_name"], 
                player["last_name"], int(player["is_active"]), player["position"],
                player["ppg"], player["apg"], player["rpg"], player["spg"],
                player["win_shares"], player["box_plus_minus"], player["eff"],
                player["data"], player["last_updated"]
            ))
            
            # Insert batch when it reaches the batch size
            if len(batch) >= BATCH_SIZE:
                cursor.executemany("""
                    INSERT INTO players 
                    (id, full_name, first_name, last_name, is_active, position,
                     ppg, apg, rpg, spg, win_shares, box_plus_minus, eff, data, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                conn.commit()
                batch = []
        
        # Insert any remaining records
        if batch:
            cursor.executemany("""
                INSERT INTO players 
                (id, full_name, first_name, last_name, is_active, position,
                 ppg, apg, rpg, spg, win_shares, box_plus_minus, eff, data, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
        
        end_time = time.time()
        logger.info(f"Added {to_add} players in {end_time - start_time:.2f} seconds")
        
        # Verify final count
        cursor.execute("SELECT COUNT(*) FROM players")
        final_count = cursor.fetchone()[0]
        logger.info(f"Final player count: {final_count}")
        
    except Exception as e:
        logger.error(f"Error populating database: {e}")
        conn.rollback()
    finally:
        conn.close()

def optimize_table_structure():
    """Optimize table structure and storage"""
    conn = get_db_connection()
    try:
        logger.info("Optimizing database structure...")
        # Run ANALYZE to update statistics used by the query planner
        conn.execute("ANALYZE")
        
        # Vacuum database to recover free space
        conn.execute("VACUUM")
        
        # Optimize database
        conn.execute("PRAGMA optimize")
        
        logger.info("Database structure optimization completed")
    except Exception as e:
        logger.error(f"Error optimizing database: {e}")
    finally:
        conn.close()

def test_query_performance(query, params=(), iterations=10, description="Query"):
    """Test the performance of a SQL query"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    execution_times = []
    
    logger.info(f"Testing performance of: {description}")
    for i in range(iterations):
        start_time = time.time()
        cursor.execute(query, params)
        results = cursor.fetchall()
        end_time = time.time()
        
        execution_time = (end_time - start_time) * 1000  # Convert to milliseconds
        execution_times.append(execution_time)
        
        # Log only the first and last iteration details
        if i == 0 or i == iterations - 1:
            logger.info(f"  Iteration {i+1}: {execution_time:.2f}ms, returned {len(results)} rows")
    
    avg_time = statistics.mean(execution_times)
    median_time = statistics.median(execution_times)
    min_time = min(execution_times)
    max_time = max(execution_times)
    
    logger.info(f"Performance results for {description}:")
    logger.info(f"  Average: {avg_time:.2f}ms")
    logger.info(f"  Median: {median_time:.2f}ms")
    logger.info(f"  Min: {min_time:.2f}ms")
    logger.info(f"  Max: {max_time:.2f}ms")
    
    conn.close()
    
    return {
        "description": description,
        "avg_time": avg_time,
        "median_time": median_time,
        "min_time": min_time,
        "max_time": max_time,
        "times": execution_times
    }

def run_performance_tests():
    """Run a series of performance tests on different query patterns"""
    results = []
    
    # Test 1: Simple query by ID (should be fast as it uses primary key)
    random_id = random.randint(1, TOTAL_PLAYERS)
    results.append(test_query_performance(
        "SELECT * FROM players WHERE id = ?", 
        (random_id,),
        description="Lookup by ID (Primary Key)"
    ))
    
    # Test 2: Get players by position (using index)
    results.append(test_query_performance(
        "SELECT * FROM players WHERE position = ? LIMIT 100", 
        (random.choice(POSITIONS),),
        description="Filter by position"
    ))
    
    # Test 3: Complex query - Top players sorted by points
    results.append(test_query_performance(
        "SELECT * FROM players WHERE is_active = 1 ORDER BY ppg DESC LIMIT 100",
        description="Top active players by points"
    ))
    
    # Test a complex statistical query - Active players with highest efficiency and win shares
    results.append(test_query_performance(
        """
        SELECT * FROM players 
        WHERE is_active = 1 
        ORDER BY (win_shares + eff) DESC 
        LIMIT 100
        """,
        description="Complex stat query - Combined win_shares and efficiency"
    ))
    
    # Test 5: Search by name
    results.append(test_query_performance(
        "SELECT * FROM players WHERE full_name LIKE ? LIMIT 100",
        (f"%{chr(ord('A') + random.randint(0, 25))}%",),  # Random letter search
        description="Name search"
    ))
    
    # Test 6: Advanced filter - Players with more assists than points (requires calculation)
    results.append(test_query_performance(
        """
        SELECT * FROM players 
        WHERE apg > ppg AND is_active = 1 
        ORDER BY apg DESC 
        LIMIT 100
        """,
        description="Complex filter - Assists > Points"
    ))
    
    # Display comparative results
    descriptions = [r["description"] for r in results]
    avg_times = [r["avg_time"] for r in results]
    
    logger.info("\nPerformance Test Summary:")
    for desc, avg in zip(descriptions, avg_times):
        logger.info(f"{desc}: {avg:.2f}ms")
    
    return results

def explain_query(query, params=()):
    """Analyze query execution plan with EXPLAIN QUERY PLAN"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    logger.info(f"Analyzing execution plan for: {query}")
    cursor.execute(f"EXPLAIN QUERY PLAN {query}", params)
    plan = cursor.fetchall()
    
    for row in plan:
        logger.info(f"  {row}")
    
    conn.close()

def plot_performance_results(results):
    """Generate a performance report with visualizations"""
    # Create a performance comparison bar chart
    descriptions = [r["description"] for r in results]
    avg_times = [r["avg_time"] for r in results]
    
    # Abbreviate long descriptions for the chart
    short_descs = [d[:20] + "..." if len(d) > 20 else d for d in descriptions]
    
    plt.figure(figsize=(10, 6))
    bars = plt.bar(short_descs, avg_times, color='skyblue')
    
    # Add values on top of bars
    for bar, val in zip(bars, avg_times):
        plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                 f"{val:.1f}ms", ha='center', va='bottom', rotation=0)
    
    plt.ylabel('Average Execution Time (ms)')
    plt.title('Database Query Performance')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    
    # Save the figure
    plt.savefig(os.path.join(SCRIPT_DIR, 'query_performance.png'))
    logger.info(f"Performance chart saved to {os.path.join(SCRIPT_DIR, 'query_performance.png')}")

def simulate_concurrent_requests(endpoint="http://localhost:8000/players", num_requests=100, concurrency=10):
    """
    Simulate concurrent API requests to test server performance under load
    (Intended to be used with JMeter for more comprehensive load testing)
    """
    async def fetch_players(session, params, request_id):
        try:
            async with session.get(endpoint, params=params) as response:
                start_time = time.time()
                data = await response.json()
                end_time = time.time()
                return {
                    "request_id": request_id,
                    "status": response.status,
                    "time": (end_time - start_time) * 1000,  # ms
                    "players_count": len(data.get("players", [])) if "players" in data else 0
                }
        except Exception as e:
            return {
                "request_id": request_id,
                "status": "error",
                "time": 0,
                "error": str(e)
            }
    
    async def run_test():
        async with aiohttp.ClientSession() as session:
            tasks = []
            for i in range(num_requests):
                # Vary parameters to test different query patterns
                params = {
                    "page": random.randint(1, 10),
                    "search": "",
                    "filter_by": random.choice(["name", "ppg", "apg", "rpg", "win_shares", "eff"]),
                    "sort_order": random.choice(["asc", "desc"]),
                    "position": random.choice(["ALL", "PG", "SG", "SF", "PF", "C"])
                }
                
                # Occasionally add a search term
                if random.random() < 0.2:
                    params["search"] = chr(ord('A') + random.randint(0, 25))
                
                tasks.append(fetch_players(session, params, i))
                
                # Control concurrency
                if len(tasks) >= concurrency:
                    results = await asyncio.gather(*tasks)
                    tasks = []
                    
                    # Print some progress
                    if i % 10 == 0:
                        logger.info(f"Processed {i}/{num_requests} requests")
            
            # Process any remaining tasks
            if tasks:
                remaining_results = await asyncio.gather(*tasks)
                results.extend(remaining_results)
            
            return results
    
    logger.info(f"Simulating {num_requests} requests with concurrency {concurrency}...")
    
    # Run the async test
    results = asyncio.run(run_test())
    
    # Analyze results
    times = [r["time"] for r in results if r["status"] == 200]
    success_count = len(times)
    error_count = num_requests - success_count
    
    logger.info(f"Test complete: {success_count} successful requests, {error_count} errors")
    
    if times:
        avg_time = statistics.mean(times)
        median_time = statistics.median(times)
        p95_time = sorted(times)[int(len(times) * 0.95)]
        
        logger.info(f"Response time statistics:")
        logger.info(f"  Average: {avg_time:.2f}ms")
        logger.info(f"  Median: {median_time:.2f}ms")
        logger.info(f"  95th percentile: {p95_time:.2f}ms")
    
    return results

def main():
    """Main function to run the stress test and optimization"""
    logger.info("Starting database stress test and optimization")
    
    # Step 1: Populate the database
    populate_players_table()
    
    # Step 2: Create indices for optimization
    create_indices()
    
    # Step 3: Optimize table structure
    optimize_table_structure()
    
    # Step 4: Test query performance
    results = run_performance_tests()
    
    # Step 5: Analyze execution plans for key queries
    explain_query("SELECT * FROM players WHERE is_active = 1 ORDER BY ppg DESC LIMIT 100")
    explain_query("SELECT * FROM players WHERE position = 'PG' AND is_active = 1 ORDER BY win_shares DESC LIMIT 100")
    
    # Step 6: Plot performance results
    plot_performance_results(results)
    
    logger.info("Database stress test and optimization completed")

if __name__ == "__main__":
    main() 