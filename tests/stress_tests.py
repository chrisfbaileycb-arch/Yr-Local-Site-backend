import os
import sys
import time
import threading
import concurrent.futures
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Add parent directory to path to allow importing db and main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.client import get_db
from main import app

# Global flag to control database health mock
DB_HEALTHY = True

def create_mock_db_client():
    """Creates a mock Supabase client that respects the DB_HEALTHY flag."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    
    # Simulate table().select().limit().execute()
    def mock_table(table_name):
        if not DB_HEALTHY:
            raise Exception("Database connection timed out (Simulated Failure)")
        return mock_query
        
    mock_client.table = mock_table
    mock_query.select.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[{"id": 1}])
    
    # Mock postgrest for thread safety test
    class MockPostgrest:
        def __init__(self):
            self.headers = {"Authorization": "Bearer placeholder"}
        def auth(self, token):
            self.headers["Authorization"] = f"Bearer {token}"
            
    mock_client.postgrest = MockPostgrest()
    return mock_client

def test_get_db_singleton_concurrency(mock_create_client):
    """Verify that get_db() returns the same instance across threads, but is not thread-safe for headers."""
    print("\n=== Test 1: get_db() Singleton Concurrency & Thread-Safety ===")
    get_db.cache_clear()
    
    # Setup mock client
    mock_client = create_mock_db_client()
    mock_create_client.return_value = mock_client
    
    # 1. Verify singleton instance under concurrency
    instances = []
    def worker():
        db = get_db()
        instances.append(db)
        
    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    first = instances[0]
    all_same = all(inst is first for inst in instances)
    print(f"Singleton Integrity Check: All {len(instances)} concurrent threads received same instance? {all_same}")
    assert all_same, "get_db() failed to return a singleton under concurrency!"
    
    # 2. Verify RLS/Auth header clobbering vulnerability
    db = get_db()
    
    def client_mutator(thread_id, token):
        db.postgrest.auth(token)
        # Sleep slightly to allow other threads to execute and potentially clobber
        time.sleep(0.05)
        return db.postgrest.headers.get("Authorization")
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(client_mutator, 1, "token-A")
        time.sleep(0.01)  # Stagger slightly
        f2 = executor.submit(client_mutator, 2, "token-B")
        
        res1 = f1.result()
        res2 = f2.result()
        
    print(f"Thread 1 auth token: Expected 'Bearer token-A', Actual: '{res1}'")
    print(f"Thread 2 auth token: Expected 'Bearer token-B', Actual: '{res2}'")
    
    clobbered = (res1 == "Bearer token-B")
    print(f"Vulnerability Check: Did Thread B clobber Thread A's auth headers? {clobbered}")
    if clobbered:
        print("[WARNING/VULNERABILITY] Confirmed auth header leakage across concurrent requests!")

def test_health_check_stress_and_recovery(mock_create_client):
    global DB_HEALTHY
    
    print("\n=== Test 2: FastAPI /health Endpoint Stress & DB State Recovery ===")
    
    # Setup mock client
    mock_client = create_mock_db_client()
    mock_create_client.return_value = mock_client
    
    client = TestClient(app)
    
    def send_request():
        start = time.time()
        try:
            response = client.get("/health")
            duration = time.time() - start
            return response.status_code, response.json(), duration
        except Exception as e:
            return 500, {"error": str(e)}, time.time() - start

    # Phase 1: DB Healthy
    DB_HEALTHY = True
    print("Phase 1: DB is Healthy. Sending 100 concurrent requests...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(send_request) for _ in range(100)]
        results = [f.result() for f in futures]
        
    statuses = [r[0] for r in results]
    latencies = [r[2] for r in results]
    avg_latency = sum(latencies) / len(latencies)
    success_rate = (statuses.count(200) / len(statuses)) * 100
    print(f"Phase 1 Results: Success Rate = {success_rate}%, Avg Latency = {avg_latency:.4f}s")
    assert success_rate == 100, f"Expected 100% success rate, got {success_rate}%"
    
    # Phase 2: DB Failure
    DB_HEALTHY = False
    print("Phase 2: DB goes Offline. Sending 100 concurrent requests...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(send_request) for _ in range(100)]
        results = [f.result() for f in futures]
        
    statuses = [r[0] for r in results]
    latencies = [r[2] for r in results]
    avg_latency = sum(latencies) / len(latencies)
    failure_rate = (statuses.count(503) / len(statuses)) * 100
    print(f"Phase 2 Results: 503 Unhealthy Rate = {failure_rate}%, Avg Latency = {avg_latency:.4f}s")
    assert failure_rate == 100, f"Expected 100% failure rate, got {failure_rate}%"
    
    # Phase 3: DB Recovers
    DB_HEALTHY = True
    print("Phase 3: DB recovers Online. Sending 100 concurrent requests...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(send_request) for _ in range(100)]
        results = [f.result() for f in futures]
        
    statuses = [r[0] for r in results]
    latencies = [r[2] for r in results]
    avg_latency = sum(latencies) / len(latencies)
    recovery_rate = (statuses.count(200) / len(statuses)) * 100
    print(f"Phase 3 Results: Success Rate = {recovery_rate}%, Avg Latency = {avg_latency:.4f}s")
    assert recovery_rate == 100, f"Expected 100% recovery rate, got {recovery_rate}%"

if __name__ == "__main__":
    # We patch create_client globally for both test suites
    with patch("db.client.create_client") as mock_create:
        try:
            test_get_db_singleton_concurrency(mock_create)
            test_health_check_stress_and_recovery(mock_create)
            print("\nAll stress tests completed successfully.")
        except AssertionError as e:
            print(f"\n[FAIL] Assertion failed: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] Test execution failed: {e}")
            sys.exit(1)
