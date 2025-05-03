"""
Run Script for Database Stress Test and Performance Optimization

This script orchestrates the complete stress testing process:
1. Database population with 100,000+ entries
2. Database optimization (indexes, query planning)
3. Performance testing with JMeter for load testing

Note: The server needs to be started manually before running JMeter tests.
"""

import os
import sys
import subprocess
import time
import argparse
import logging

# Configure logging - only to console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def check_dependencies():
    """Check if all required dependencies are installed"""
    required_packages = [
        "faker", "matplotlib", "tqdm", "pandas", "numpy", 
        "aiohttp", "requests"
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        logger.error(f"Missing dependencies: {', '.join(missing)}")
        logger.info("Install dependencies with: pip install faker matplotlib tqdm pandas numpy aiohttp requests")
        return False
    
    return True

def run_database_population():
    """Run the database population and optimization script"""
    logger.info("Running database population and optimization...")
    result = subprocess.run(
        ["python", "stress_test_db.py"],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        logger.error(f"Error running database population: {result.stderr}")
        return False
    
    logger.info("Database population complete")
    return True

def run_jmeter_test(jmeter_path=None):
    """Run the JMeter load test if JMeter is available"""
    # First check if JMeter is available
    jmeter_cmd = jmeter_path if jmeter_path else "jmeter"
    
    try:
        subprocess.run([jmeter_cmd, "--version"], 
                      capture_output=True, 
                      check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("JMeter not found in PATH. Skipping JMeter tests.")
        return False
    
    logger.info("Running JMeter load tests...")
    result = subprocess.run(
        [jmeter_cmd, "-n", "-t", "jmeter_test_plan.jmx", "-l", "jmeter_results.jtl"],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        logger.error(f"Error running JMeter tests: {result.stderr}")
        return False
    
    logger.info("JMeter load tests complete")
    return True

def main():
    """Main function to run the complete stress test"""
    parser = argparse.ArgumentParser(description="Run database stress test and performance optimization")
    parser.add_argument("--skip-population", action="store_true", help="Skip database population")
    parser.add_argument("--skip-jmeter", action="store_true", help="Skip JMeter load tests")
    parser.add_argument("--jmeter-path", help="Path to JMeter executable")
    
    args = parser.parse_args()
    
    # Step 1: Check dependencies
    if not check_dependencies():
        return
    
    try:
        # Step 2: Run database population and optimization
        if not args.skip_population:
            if not run_database_population():
                return
        
        # Step 3: Remind about server for JMeter tests
        if not args.skip_jmeter:
            logger.info("Before running JMeter tests, make sure the FastAPI server is running.")
            logger.info("You can start it with: uvicorn main:app --reload")
            user_input = input("Is the server running? (y/n): ")
            if user_input.lower() != 'y':
                logger.info("Please start the server before continuing.")
                return
            
            # Step 4: Run JMeter load tests
            run_jmeter_test(args.jmeter_path)
        
        logger.info("All stress tests completed successfully!")
        logger.info("Results and visualizations are available in the current directory.")
    
    except Exception as e:
        logger.error(f"Error during stress test: {str(e)}")

if __name__ == "__main__":
    main() 

#python run_stress_test.py --skip-population --jmeter-path="C:\Jmeter\apache-jmeter-5.6.3\bin\jmeter.bat"