"""
Complex Query Optimization Script

This script demonstrates how to optimize a complex statistical query
against a large dataset of NBA players. It:

1. Creates the necessary indices for query optimization
2. Compares performance before and after optimization
3. Explains the query execution plan
4. Implements advanced SQLite optimizations 
"""

import sqlite3
import time
import json
import os
import logging
import statistics
import matplotlib.pyplot as plt

# Setup logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Database file - use absolute path
DB_FILE = os.path.join(SCRIPT_DIR, "nba_app.db")
logger.info(f"Using database at: {DB_FILE}")

# The complex query we want to optimize
COMPLEX_QUERY = """
SELECT *
FROM players
WHERE is_active = 1
AND ppg > 10
AND position = 'PG'
ORDER BY (
    ppg * 1.0 +
    apg * 2.0 +
    rpg * 1.5 +
    spg * 3.0 +
    win_shares * 2.5 +
    box_plus_minus * 2.0 +
    eff * 1.8
) DESC
LIMIT 100
"""

# Another complex query that's more challenging
VERY_COMPLEX_QUERY = """
SELECT *
FROM players
WHERE is_active = 1
AND (
    (ppg > 15 AND position = 'PG') OR
    (rpg > 8 AND position = 'C') OR
    (apg > 5 AND win_shares > 3)
)
ORDER BY (
    CASE
        WHEN position = 'PG' THEN ppg * 2.0 + apg * 3.0
        WHEN position = 'SG' THEN ppg * 2.5 + spg * 2.0
        WHEN position = 'SF' THEN ppg * 1.8 + rpg * 1.5
        WHEN position = 'PF' THEN ppg * 1.5 + rpg * 2.0
        WHEN position = 'C' THEN rpg * 2.5 + ppg * 1.0
        ELSE ppg + rpg + apg
    END
) DESC
LIMIT 100
"""

def get_unoptimized_connection():
    """Create a database connection without optimizations"""
    return sqlite3.connect(DB_FILE)

def get_optimized_connection():
    """Create a database connection with optimizations"""
    conn = sqlite3.connect(DB_FILE)
    
    # Apply SQLite optimizations
    conn.execute('PRAGMA journal_mode = WAL')  # Write-Ahead Logging
    conn.execute('PRAGMA synchronous = NORMAL')  # Less synchronous
    conn.execute('PRAGMA cache_size = -64000')  # 64MB cache
    conn.execute('PRAGMA temp_store = MEMORY')  # Store temp tables in memory
    conn.execute('PRAGMA mmap_size = 2000000000')  # Memory mapping (2GB)
    
    return conn

def remove_indices():
    """Remove all indices except primary key to test unoptimized performance"""
    conn = get_unoptimized_connection()
    cursor = conn.cursor()
    
    try:
        # Get all indices
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        indices = cursor.fetchall()
        
        # Drop each index
        for index in indices:
            index_name = index[0]
            logger.info(f"Dropping index: {index_name}")
            cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
        
        conn.commit()
        logger.info("All indices removed")
    except Exception as e:
        logger.error(f"Error removing indices: {e}")
        conn.rollback()
    finally:
        conn.close()

def create_indices():
    """Create indices for query optimization"""
    conn = get_unoptimized_connection()
    cursor = conn.cursor()
    
    try:
        # Create indices for the fields used in WHERE clauses and ORDER BY
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_players_is_active ON players(is_active)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_players_position ON players(position)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_players_ppg ON players(ppg)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_players_rpg ON players(rpg)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_players_apg ON players(apg)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_players_win_shares ON players(win_shares)')
        
        # Composite index for the combined filtering conditions in the complex query
        # This can help with the complex WHERE clause in VERY_COMPLEX_QUERY
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_players_combined ON players(
            is_active, position, ppg, rpg, apg, win_shares
        )
        ''')
        
        conn.commit()
        logger.info("Indices created successfully")
    except Exception as e:
        logger.error(f"Error creating indices: {e}")
        conn.rollback()
    finally:
        conn.close()

def explain_query(query, optimized=True):
    """Print the execution plan for a query"""
    conn = get_optimized_connection() if optimized else get_unoptimized_connection()
    cursor = conn.cursor()
    
    logger.info(f"Execution plan ({'optimized' if optimized else 'unoptimized'}):")
    cursor.execute(f"EXPLAIN QUERY PLAN {query}")
    plan = cursor.fetchall()
    
    for row in plan:
        logger.info(f"  {row}")
    
    conn.close()

def run_query_performance_test(query, iterations=5, optimized=True):
    """Test the performance of a query"""
    conn = get_optimized_connection() if optimized else get_unoptimized_connection()
    cursor = conn.cursor()
    
    times = []
    row_counts = []
    
    for i in range(iterations):
        # Clear caches between runs for more realistic results
        if i > 0:
            cursor.execute("PRAGMA optimize")
        
        start_time = time.time()
        cursor.execute(query)
        rows = cursor.fetchall()
        end_time = time.time()
        
        execution_time = (end_time - start_time) * 1000  # Convert to ms
        times.append(execution_time)
        row_counts.append(len(rows))
    
    conn.close()
    
    avg_time = statistics.mean(times)
    median_time = statistics.median(times)
    min_time = min(times)
    max_time = max(times)
    
    logger.info(f"Query performance ({'optimized' if optimized else 'unoptimized'}):")
    logger.info(f"  Average: {avg_time:.2f}ms")
    logger.info(f"  Median: {median_time:.2f}ms")
    logger.info(f"  Min: {min_time:.2f}ms")
    logger.info(f"  Max: {max_time:.2f}ms")
    logger.info(f"  Result rows: {row_counts[0]}")
    
    return {
        "avg_time": avg_time,
        "median_time": median_time,
        "min_time": min_time,
        "max_time": max_time,
        "rows": row_counts[0]
    }

def analyze_database():
    """Run ANALYZE to update SQLite statistics"""
    conn = get_optimized_connection()
    cursor = conn.cursor()
    
    logger.info("Running ANALYZE to update query statistics...")
    cursor.execute("ANALYZE")
    
    conn.close()
    logger.info("Analysis complete")

def compare_and_visualize(query_name, unopt_results, opt_results):
    """Compare and visualize the performance difference"""
    # Calculate improvement
    avg_improvement = ((unopt_results["avg_time"] - opt_results["avg_time"]) / 
                      unopt_results["avg_time"]) * 100
    
    logger.info(f"\nPerformance comparison for {query_name}:")
    logger.info(f"  Unoptimized: {unopt_results['avg_time']:.2f}ms")
    logger.info(f"  Optimized: {opt_results['avg_time']:.2f}ms")
    logger.info(f"  Improvement: {avg_improvement:.1f}%")
    
    # Create visualization
    plt.figure(figsize=(10, 6))
    
    # Bar chart for average times
    plt.subplot(1, 2, 1)
    bars = plt.bar(['Unoptimized', 'Optimized'], 
                  [unopt_results['avg_time'], opt_results['avg_time']], 
                  color=['lightcoral', 'mediumseagreen'])
    
    # Add values on top of bars
    for bar, val in zip(bars, [unopt_results['avg_time'], opt_results['avg_time']]):
        plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"{val:.1f}ms", ha='center', va='bottom')
    
    plt.title(f'Average Query Time - {query_name}')
    plt.ylabel('Execution Time (ms)')
    
    # Box plot for time distributions
    plt.subplot(1, 2, 2)
    
    # Create data for box plots (min, median, max)
    unopt_data = [unopt_results['min_time'], unopt_results['median_time'], unopt_results['max_time']]
    opt_data = [opt_results['min_time'], opt_results['median_time'], opt_results['max_time']]
    
    plt.boxplot([unopt_data, opt_data], labels=['Unoptimized', 'Optimized'])
    plt.title('Query Time Distribution')
    plt.ylabel('Execution Time (ms)')
    
    plt.tight_layout()
    plt.savefig(os.path.join(SCRIPT_DIR, f'{query_name.replace(" ", "_").lower()}_optimization.png'))
    logger.info(f"Performance chart saved to {os.path.join(SCRIPT_DIR, f'{query_name.replace(' ', '_').lower()}_optimization.png')}")

def main():
    """Main function to run the optimization process"""
    logger.info("Starting complex query optimization process")
    
    # Step 1: Analyze the database for statistics gathering
    analyze_database()
    
    # Step 2: Test the complex query without optimization
    logger.info("\nTesting complex query without optimization...")
    remove_indices()
    unopt_results_1 = run_query_performance_test(COMPLEX_QUERY, optimized=False)
    explain_query(COMPLEX_QUERY, optimized=False)
    
    unopt_results_2 = run_query_performance_test(VERY_COMPLEX_QUERY, optimized=False)
    explain_query(VERY_COMPLEX_QUERY, optimized=False)
    
    # Step 3: Create indices and optimize
    logger.info("\nCreating indices for optimization...")
    create_indices()
    
    # Step 4: Test the query with optimization
    logger.info("\nTesting complex query with optimization...")
    opt_results_1 = run_query_performance_test(COMPLEX_QUERY, optimized=True)
    explain_query(COMPLEX_QUERY, optimized=True)
    
    opt_results_2 = run_query_performance_test(VERY_COMPLEX_QUERY, optimized=True)
    explain_query(VERY_COMPLEX_QUERY, optimized=True)
    
    # Step 5: Compare and visualize results
    compare_and_visualize("Complex Query", unopt_results_1, opt_results_1)
    compare_and_visualize("Very Complex Query", unopt_results_2, opt_results_2)
    
    logger.info("\nOptimization process completed")

if __name__ == "__main__":
    main() 