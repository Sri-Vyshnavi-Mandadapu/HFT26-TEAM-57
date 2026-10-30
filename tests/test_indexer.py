
import unittest
import tempfile
import zipfile

from pathlib import Path

from src.backend.indexer import index_repository


class TestRepositoryIndexer(unittest.TestCase):

    def setUp(self):

        self.temp_dir = tempfile.TemporaryDirectory()

        self.zip_path = Path(
            self.temp_dir.name
        ) / "sample_repository.zip"

        with zipfile.ZipFile(
            self.zip_path,
            "w",
            zipfile.ZIP_DEFLATED
        ) as archive:

            # Normal Python source file
            archive.writestr(
                "project/backend/auth.py",
                "def login_user(username, password):\n"
                "    return username == 'admin'\n"
                "\n"
                "class AuthService:\n"
                "    def logout(self):\n"
                "        return True\n"
            )

            # Python file containing a decorator
            archive.writestr(
                "project/backend/helpers.py",
                "@staticmethod\n"
                "def normalize_name(name):\n"
                "    return name.strip().lower()\n"
            )

            # File that should be ignored
            archive.writestr(
                "project/node_modules/library/index.py",
                "def unwanted_function():\n"
                "    pass\n"
            )

            # Non-Python file
            archive.writestr(
                "project/frontend/app.js",
                "function login() {}"
            )

            # Module without functions or classes
            archive.writestr(
                "project/backend/constants.py",
                "APP_NAME = 'Repository Explorer'\n"
                "DEBUG = True\n"
            )

            # Invalid Python file
            archive.writestr(
                "project/backend/broken.py",
                "def invalid(:\n"
            )

            # Unsafe path
            archive.writestr(
                "../outside.py",
                "def dangerous():\n"
                "    pass\n"
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_index_returns_records(self):

        records = index_repository(
            str(self.zip_path)
        )

        self.assertGreater(len(records), 0)

    def test_record_schema(self):

        records = index_repository(
            str(self.zip_path)
        )

        expected_keys = {
            "file_path",
            "symbol",
            "type",
            "start_line",
            "end_line",
            "code",
            "language"
        }

        for record in records:

            self.assertEqual(
                set(record.keys()),
                expected_keys
            )

    def test_function_extraction(self):

        records = index_repository(
            str(self.zip_path)
        )

        symbols = [
            record["symbol"]
            for record in records
        ]

        self.assertIn("login_user", symbols)

    def test_class_extraction(self):

        records = index_repository(
            str(self.zip_path)
        )

        symbols = [
            record["symbol"]
            for record in records
        ]

        self.assertIn("AuthService", symbols)

    def test_method_extraction(self):

        records = index_repository(
            str(self.zip_path)
        )

        symbols = [
            record["symbol"]
            for record in records
        ]

        self.assertIn(
            "AuthService.logout",
            symbols
        )

    def test_decorator_line_numbers(self):

        records = index_repository(
            str(self.zip_path)
        )

        record = next(
            r for r in records
            if r["symbol"] == "normalize_name"
        )

        self.assertEqual(record["start_line"], 1)

    def test_ignored_directories(self):

        records = index_repository(
            str(self.zip_path)
        )

        paths = [
            record["file_path"]
            for record in records
        ]

        self.assertFalse(
            any("node_modules" in path for path in paths)
        )

    def test_non_python_files_ignored(self):

        records = index_repository(
            str(self.zip_path)
        )

        paths = [
            record["file_path"]
            for record in records
        ]

        self.assertFalse(
            any(path.endswith(".js") for path in paths)
        )

    def test_unsafe_paths_ignored(self):

        records = index_repository(
            str(self.zip_path)
        )

        paths = [
            record["file_path"]
            for record in records
        ]

        self.assertFalse(
            any("outside.py" in path for path in paths)
        )

    def test_invalid_python_skipped(self):

        records = index_repository(
            str(self.zip_path)
        )

        paths = [
            record["file_path"]
            for record in records
        ]

        self.assertFalse(
            any("broken.py" in path for path in paths)
        )

    def test_line_numbers_and_code(self):

        records = index_repository(
            str(self.zip_path)
        )

        record = next(
            r for r in records
            if r["symbol"] == "login_user"
        )

        self.assertEqual(record["start_line"], 1)
        self.assertEqual(record["end_line"], 2)

        self.assertIn(
            "return username == 'admin'",
            record["code"]
        )

    def test_invalid_zip(self):

        invalid_path = Path(
            self.temp_dir.name
        ) / "invalid.zip"

        invalid_path.write_text(
            "This is not a ZIP file"
        )

        with self.assertRaises(ValueError):

            index_repository(
                str(invalid_path)
            )


if __name__ == "__main__":

    unittest.main()
