import os
import unittest
from unittest.mock import patch, MagicMock
from db.client import get_db

class TestDbClient(unittest.TestCase):
    def setUp(self):
        # Clear the lru_cache for get_db before each test
        get_db.cache_clear()

    @patch("db.client.create_client")
    def test_get_db_singleton(self, mock_create_client):
        # Arrange
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        
        # Act
        db1 = get_db()
        db2 = get_db()
        
        # Assert
        self.assertIs(db1, db2)
        mock_create_client.assert_called_once()

    @patch("db.client.create_client")
    @patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "test-key"})
    def test_get_db_reads_env(self, mock_create_client):
        # Act
        get_db()
        
        # Assert
        mock_create_client.assert_called_with("https://test.supabase.co", "test-key")

    @patch("db.client.create_client")
    @patch.dict(os.environ, {}, clear=True)
    def test_get_db_fallback(self, mock_create_client):
        # Act
        get_db()
        
        # Assert
        args, _ = mock_create_client.call_args
        self.assertEqual(args[0], "https://placeholder.supabase.co")
        self.assertEqual(args[1], "placeholder-key")
