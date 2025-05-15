import requests
import time
import json
import random
from datetime import datetime

# Configuration
BASE_URL = "http://127.0.0.1:8000"
USERNAME = "suspicious_user2"
PASSWORD = "password123"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
MONITORING_INTERVAL_SECONDS = 5  # Added for the new monitoring logic

# Operations to perform
NUM_OPERATIONS = 15  # This should be higher than the threshold (10 by default after our changes)
OPERATION_DELAY = 0.1  # seconds between operations

def login(username, password):
    """Login and get authentication token"""
    try:
        response = requests.post(
            f"{BASE_URL}/token",
            data={"username": username, "password": password}
        )
        response.raise_for_status()
        token_data = response.json()
        return token_data["access_token"]
    except Exception as e:
        print(f"Login failed: {e}")
        if hasattr(response, 'text'):
            print(f"Response: {response.text}")
        return None

def register_user(username, password, role="user"):
    """Register a new user"""
    try:
        response = requests.post(
            f"{BASE_URL}/register",
            json={"username": username, "password": password, "role": role}
        )
        if response.status_code == 400 and "already registered" in response.text:
            print(f"User {username} already exists, proceeding with login")
            return login(username, password)
            
        response.raise_for_status()
        token_data = response.json()
        return token_data["access_token"]
    except Exception as e:
        print(f"Registration failed: {e}")
        if hasattr(response, 'text'):
            print(f"Response: {response.text}")
        return None

def get_players(token, page=1):
    """Get a list of players"""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{BASE_URL}/players?page={page}", headers=headers)
    response.raise_for_status()
    return response.json()

def add_player_to_court(token, position, player_id):
    """Add a player to the court"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    response = requests.post(
        f"{BASE_URL}/court/add",
        headers=headers,
        json={"position": position, "player_id": player_id}
    )
    # Don't raise for status as this might fail if position is occupied
    return response.status_code < 300

def remove_player_from_court(token, position):
    """Remove a player from the court"""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.delete(f"{BASE_URL}/court/{position}", headers=headers)
    # Don't raise for status as this might fail if position is empty
    return response.status_code < 300

def update_player_on_court(token, position, player_id):
    """Update a player on the court"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    response = requests.put(
        f"{BASE_URL}/court/{position}",
        headers=headers,
        json={"position": position, "player_id": player_id}
    )
    # Don't raise for status as this might fail
    return response.status_code < 300

def check_monitored_users(admin_token):
    """Check the monitored users list"""
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = requests.get(f"{BASE_URL}/security/monitored-users", headers=headers)
    if response.status_code >= 300:
        print(f"Failed to get monitored users: {response.text}")
        return None
    return response.json()

def main():
    # Ensure the suspicious user exists
    print(f"Creating/logging in user: {USERNAME}")
    token = register_user(USERNAME, PASSWORD)
    if not token:
        print("Failed to register/login user")
        return
    
    # Also ensure we have an admin account to check the monitored users
    print(f"Creating/logging in admin: {ADMIN_USERNAME}")
    admin_token = register_user(ADMIN_USERNAME, ADMIN_PASSWORD, "admin")
    if not admin_token:
        print("Failed to register/login admin")
        return
    
    # Get available players
    print("Fetching players...")
    players_data = get_players(token)
    players = players_data.get("players", [])
    if not players:
        print("No players found")
        return
    
    # Available positions
    positions = ["PG", "SG", "SF", "PF", "C"]
    
    # Perform a series of operations quickly
    print(f"Performing {NUM_OPERATIONS} operations...")
    operation_count = {"add": 0, "remove": 0, "update": 0}
    
    for i in range(NUM_OPERATIONS):
        start_time = datetime.now()
        
        # Pick a random operation: add, remove, or update
        operation = random.choice(["add", "remove", "update"])
        position = random.choice(positions)
        player = random.choice(players)
        
        if operation == "add":
            print(f"Adding player {player['full_name']} to position {position}")
            success = add_player_to_court(token, position, player["id"])
            if success:
                operation_count["add"] += 1
                
        elif operation == "remove":
            print(f"Removing player from position {position}")
            success = remove_player_from_court(token, position)
            if success:
                operation_count["remove"] += 1
                
        elif operation == "update":
            print(f"Updating player at position {position} to {player['full_name']}")
            success = update_player_on_court(token, position, player["id"])
            if success:
                operation_count["update"] += 1
        
        # Sleep a short time between operations
        elapsed = (datetime.now() - start_time).total_seconds()
        sleep_time = max(0, OPERATION_DELAY - elapsed)
        time.sleep(sleep_time)
    
    print("\nOperation summary:")
    print(f"Add operations: {operation_count['add']}")
    print(f"Remove operations: {operation_count['remove']}")
    print(f"Update operations: {operation_count['update']}")
    print(f"Total operations: {sum(operation_count.values())}")
    
    # Wait a short time for the monitoring thread to detect the activity
    print("\nWaiting for monitoring system to detect suspicious activity...")
    print("(The monitoring thread runs every 5 seconds according to our configuration)")
    
    # Wait for 2 monitoring cycles
    wait_time = MONITORING_INTERVAL_SECONDS * 2
    print(f"Waiting {wait_time} seconds...")
    
    for i in range(wait_time):
        if i % 5 == 0:
            print(f"{wait_time - i} seconds remaining...")
        time.sleep(1)
    
    # Check if the user was added to the monitored users list
    print("\nChecking monitored users list...")
    monitored_data = check_monitored_users(admin_token)
    
    if monitored_data:
        monitored_users = monitored_data.get("monitored_users", [])
        print(f"Found {len(monitored_users)} monitored users:")
        
        for user in monitored_users:
            print(f"\nUsername: {user['username']}")
            print(f"Action count: {user['action_count']}")
            print(f"Reason: {user['reason']}")
            print(f"First detected: {user['first_detected']}")
            print(f"Last updated: {user['last_updated']}")
            
        # Check if our suspicious user is in the list
        suspicious_user = next((user for user in monitored_users if user['username'] == USERNAME), None)
        if suspicious_user:
            print(f"\n✅ SUCCESS: User '{USERNAME}' was detected as suspicious!")
        else:
            print(f"\n❌ User '{USERNAME}' was NOT detected as suspicious.")
    else:
        print("Failed to retrieve monitored users list.")

if __name__ == "__main__":
    main() 

#python simulate_suspicious_activity.py