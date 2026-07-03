import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health_check_healthy():
    # Mock get_db and its execution to simulate a successful query
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.limit.return_value = mock_query
    
    with patch("main.get_db", return_value=mock_db):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "database": "connected"}
        
        # Verify lightweight database query was executed correctly
        mock_db.table.assert_called_once_with("profiles")
        mock_query.select.assert_called_once_with("id")
        mock_query.limit.assert_called_once_with(1)
        mock_query.execute.assert_called_once()

def test_health_check_unhealthy():
    # Mock get_db to raise an exception, simulating database failure
    with patch("main.get_db", side_effect=Exception("Database connection timed out")):
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "unhealthy", "error": "Database connection timed out"}
