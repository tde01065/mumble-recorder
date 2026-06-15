"""Smoke test to ensure all modules can be imported without errors."""
import unittest


class TestImports(unittest.TestCase):
    """Test that core modules can be imported without AttributeError."""

    def test_mumble_client_import(self):
        """Test mumble_client can be imported."""
        import mumble_recorder.mumble_client  # noqa: F401

    def test_recording_import(self):
        """Test recording can be imported."""
        import mumble_recorder.recording  # noqa: F401

    def test_recording_cli_import(self):
        """Test recording_cli can be imported."""
        import mumble_recorder.recording_cli  # noqa: F401

    def test_main_import(self):
        """Test __main__ can be imported."""
        import mumble_recorder.__main__  # noqa: F401


if __name__ == "__main__":
    unittest.main()
