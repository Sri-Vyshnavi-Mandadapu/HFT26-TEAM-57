
import os
import ntpath
import stat
import zipfile

from typing import Any

from .parser import parse_python


# Only Python files are supported in this MVP.
SUPPORTED_EXTENSIONS = {".py"}


# Directories that should not be indexed.
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "vendor",
    "site-packages",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "coverage",
    ".idea",
    ".vscode"
}


IGNORED_FILES = {
    ".ds_store",
    "thumbs.db",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml"
}


# Safety limits
MAX_FILE_SIZE = 1 * 1024 * 1024

MAX_TOTAL_SIZE = 50 * 1024 * 1024

MAX_FILES = 2000

MAX_COMPRESSION_RATIO = 100


def safe_member_path(filename: str):
    """
    Validate ZIP file paths.

    Prevents directory traversal and absolute paths.
    """

    if not filename or "\x00" in filename:
        return None

    filename = filename.replace("\\", "/")

    # Reject absolute paths.
    if filename.startswith("/"):
        return None

    # Reject Windows drive paths.
    if ntpath.splitdrive(filename)[0]:
        return None

    parts = filename.split("/")

    # Reject parent directory traversal.
    if any(part in ("..", ".") for part in parts):
        return None

    parts = [part for part in parts if part]

    if not parts:
        return None

    return "/".join(parts)


def should_ignore(file_path: str):
    """
    Check whether a repository file should be excluded.
    """

    parts = file_path.lower().split("/")

    filename = parts[-1]

    # Ignore generated and dependency directories.
    if any(part in IGNORED_DIRS for part in parts[:-1]):
        return True

    # Ignore unwanted files.
    if filename in IGNORED_FILES:
        return True

    if filename.startswith("._"):
        return True

    # Check extension.
    extension = os.path.splitext(filename)[1].lower()

    if extension not in SUPPORTED_EXTENSIONS:
        return True

    return False


def is_symlink(info: zipfile.ZipInfo):
    """
    Detect symbolic links in ZIP metadata.
    """

    unix_mode = info.external_attr >> 16

    return stat.S_ISLNK(unix_mode)


def index_repository(zip_path: str) -> list[dict[str, Any]]:
    """
    Index Python source code from an uploaded ZIP archive.

    Steps:
        1. Validate ZIP file.
        2. Inspect archive entries.
        3. Filter unwanted files.
        4. Check file sizes and ZIP paths.
        5. Read Python source code.
        6. Parse source using AST.
        7. Return indexed records.

    Uploaded code is never executed.
    """

    if not isinstance(zip_path, str) or not zip_path.strip():
        raise ValueError("ZIP path cannot be empty")

    if not os.path.isfile(zip_path):
        raise FileNotFoundError(
            f"ZIP file not found: {zip_path}"
        )

    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Invalid ZIP archive")

    indexed_records = []

    total_size = 0

    scanned_files = 0

    seen_paths = set()

    try:

        with zipfile.ZipFile(zip_path, "r") as archive:

            for info in archive.infolist():

                # Ignore directories.
                if info.is_dir():
                    continue

                # Check file path safety.
                file_path = safe_member_path(info.filename)

                if file_path is None:
                    continue

                # Ignore generated files and unsupported extensions.
                if should_ignore(file_path):
                    continue

                # Avoid symbolic links.
                if is_symlink(info):
                    continue

                # Avoid encrypted files.
                if info.flag_bits & 0x1:
                    continue

                # Avoid duplicate ZIP paths.
                if file_path in seen_paths:
                    continue

                seen_paths.add(file_path)

                # Check individual file size.
                if info.file_size > MAX_FILE_SIZE:
                    continue

                if info.file_size < 0:
                    continue

                # Check suspicious compression ratios.
                compressed_size = max(info.compress_size, 1)

                if (
                    info.file_size / compressed_size
                    > MAX_COMPRESSION_RATIO
                ):
                    continue

                # Limit the number of files.
                if scanned_files >= MAX_FILES:
                    break

                if total_size + info.file_size > MAX_TOTAL_SIZE:
                    continue

                # Read the file without extracting it.
                try:

                    with archive.open(info, "r") as source:

                        raw_content = source.read(
                            MAX_FILE_SIZE + 1
                        )

                except (
                    OSError,
                    RuntimeError,
                    zipfile.BadZipFile
                ):
                    continue

                # Verify actual size, not just ZIP metadata.
                if len(raw_content) > MAX_FILE_SIZE:
                    continue

                total_size += len(raw_content)

                scanned_files += 1

                # Decode source code.
                try:

                    content = raw_content.decode(
                        "utf-8-sig"
                    )

                except UnicodeDecodeError:
                    continue

                if not content.strip():
                    continue

                # Parse Python code.
                records = parse_python(
                    content,
                    file_path
                )

                indexed_records.extend(records)

    except zipfile.BadZipFile as error:

        raise ValueError(
            "The ZIP archive is corrupted"
        ) from error

    return indexed_records
