import base64
import datetime
from pathlib import Path
import unittest
from unittest import mock

import broker
from site_config import load


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ARCHIVE_NAME = "root.pxar.didx"
LEGACY_CATALOG_NAME = "catalog.pcat1.didx"
SPLIT_ARCHIVE_NAME = "root.mpxar.didx"
SPLIT_PAYLOAD_NAME = "root.ppxar.didx"


def snapshot(layout):
    archive_name = (
        LEGACY_ARCHIVE_NAME
        if layout == "legacy"
        else SPLIT_ARCHIVE_NAME
    )
    return {
        "epoch": 1789595082,
        "timestamp": "2026-09-16T21:44:42Z",
        "date": "2026-09-16",
        "protected": False,
        "archive_format": layout,
        "archive_name": archive_name,
        "backup_id": "storage-server",
    }


class ArchiveLayoutTests(unittest.TestCase):
    def setUp(self):
        broker._CONFIG = load(str(ROOT / "config/site.example.json"))

    def test_detects_legacy_layout(self):
        layout = broker.archive_layout(
            {LEGACY_ARCHIVE_NAME, LEGACY_CATALOG_NAME}
        )
        self.assertEqual(layout["format"], "legacy")
        self.assertEqual(layout["archive"], LEGACY_ARCHIVE_NAME)

    def test_detects_split_layout(self):
        layout = broker.archive_layout(
            {SPLIT_ARCHIVE_NAME, SPLIT_PAYLOAD_NAME}
        )
        self.assertEqual(layout["format"], "split")
        self.assertEqual(layout["archive"], SPLIT_ARCHIVE_NAME)

    def test_rejects_incomplete_split_layout(self):
        self.assertIsNone(broker.archive_layout({SPLIT_ARCHIVE_NAME}))

    @mock.patch.object(broker, "api_call")
    def test_snapshot_listing_accepts_legacy_and_split(self, api_call):
        now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        api_call.return_value = [
            {
                "backup-time": now - 2,
                "files": [
                    {"filename": LEGACY_ARCHIVE_NAME},
                    {"filename": LEGACY_CATALOG_NAME},
                ],
            },
            {
                "backup-time": now - 1,
                "files": [
                    {"filename": SPLIT_ARCHIVE_NAME},
                    {"filename": SPLIT_PAYLOAD_NAME},
                ],
            },
            {
                "backup-time": now,
                "files": [{"filename": SPLIT_ARCHIVE_NAME}],
            },
        ]

        snapshots = broker.available_snapshots({"PBS_BACKUP_ID": "storage-server"})

        self.assertEqual(
            [item["archive_format"] for item in snapshots], ["split", "legacy"]
        )
        self.assertTrue(
            all(item["backup_id"] == "storage-server" for item in snapshots)
        )

    @mock.patch.object(broker, "available_snapshots")
    @mock.patch.object(broker, "load_environment")
    @mock.patch.object(broker, "identity")
    def test_snapshot_action_returns_only_public_fields(
        self, identity, load_environment, available_snapshots
    ):
        load_environment.return_value = {"PBS_BACKUP_ID": "storage-server"}
        available_snapshots.return_value = [snapshot("split")]

        response = broker.dispatch({"action": "snapshots", "user": "alice"})

        self.assertEqual(
            response,
            {
                "snapshots": [
                    {
                        "epoch": 1789595082,
                        "timestamp": "2026-09-16T21:44:42Z",
                        "date": "2026-09-16",
                        "protected": False,
                    }
                ]
            },
        )
        self.assertNotIn("backup_id", response["snapshots"][0])
        self.assertNotIn("archive_name", response["snapshots"][0])

    @mock.patch.object(broker, "api_call")
    @mock.patch.object(broker, "require_snapshot")
    def test_split_catalog_uses_archive_parameter(self, require_snapshot, api_call):
        require_snapshot.return_value = snapshot("split")
        api_call.return_value = [
            {
                "filepath": base64.b64encode(b"/alice/notes.txt").decode("ascii"),
                "text": "notes.txt",
                "type": "f",
                "leaf": True,
                "size": 12,
                "mtime": 1,
            }
        ]

        listing = broker.list_directory("alice", 1789595082, "", {})

        params = api_call.call_args[0][1]
        self.assertEqual(params["archive-name"], SPLIT_ARCHIVE_NAME)
        self.assertEqual(base64.b64decode(params["filepath"]), b"/alice")
        self.assertEqual(
            base64.b64decode(listing["entries"][0]["token"]), b"notes.txt"
        )

    @mock.patch.object(broker, "api_call")
    @mock.patch.object(broker, "require_snapshot")
    def test_legacy_catalog_keeps_archive_in_path(self, require_snapshot, api_call):
        require_snapshot.return_value = snapshot("legacy")
        api_call.return_value = [
            {
                "filepath": base64.b64encode(
                    b"root.pxar.didx/alice/notes.txt"
                ).decode("ascii"),
                "text": "notes.txt",
                "type": "f",
                "leaf": True,
            }
        ]

        broker.list_directory("alice", 1789595082, "", {})

        params = api_call.call_args[0][1]
        self.assertNotIn("archive-name", params)
        self.assertEqual(
            base64.b64decode(params["filepath"]), b"/root.pxar.didx/alice"
        )

    @mock.patch.object(broker, "api_call")
    @mock.patch.object(broker, "require_snapshot")
    def test_split_catalog_rejects_another_user(self, require_snapshot, api_call):
        require_snapshot.return_value = snapshot("split")
        api_call.return_value = [
            {
                "filepath": base64.b64encode(b"/bob/secret.txt").decode("ascii"),
                "text": "secret.txt",
                "type": "f",
                "leaf": True,
            }
        ]

        with self.assertRaisesRegex(
            broker.BrokerError, "outside the user boundary"
        ):
            broker.list_directory("alice", 1789595082, "", {})


if __name__ == "__main__":
    unittest.main()
