from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from services.assistant_api import container_entrypoint as entry


class ContainerEntrypointTests(unittest.TestCase):
    def test_path_escape_and_arbitrary_directory_are_rejected(self):
        for state, database in ((Path("/tmp"), Path("/tmp/db.sqlite3")), (entry.VOLUME, Path("/tmp/db.sqlite3")),
                                (entry.VOLUME, entry.VOLUME / "password.txt")):
            with self.assertRaisesRegex(ValueError, "invalid_container_volume_path"):
                entry.prepare_volume(state, database, 123, 123)

    def test_only_exact_directories_and_sqlite_files_are_prepared_no_recursion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            historical = root / "historical"; historical.mkdir()
            database = historical / "historical.sqlite3"; database.write_bytes(b"frozen-database-content")
            unrelated = root / "evidence.json"; unrelated.write_bytes(b"frozen-evidence")
            with patch.object(entry, "VOLUME", root), patch.object(os, "chown", create=True) as chown:
                entry.prepare_volume(root, database, 123, 456)
            self.assertEqual([call.args[0] for call in chown.call_args_list], [root, historical, database])
            self.assertEqual(database.read_bytes(), b"frozen-database-content")
            self.assertEqual(unrelated.read_bytes(), b"frozen-evidence")

    @unittest.skipUnless(os.name == "posix", "POSIX symlink ownership safety")
    def test_symlink_outside_mount_cannot_be_prepared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = root / "mount"; state.mkdir()
            outside = root / "outside"; outside.mkdir(); (state / "historical").symlink_to(outside)
            with patch.object(entry, "VOLUME", state), self.assertRaisesRegex(ValueError, "invalid_container_volume_path"):
                entry.prepare_volume(state, state / "historical/db.sqlite3", 123, 456)
