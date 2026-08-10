from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import materialize_delivery_aliases as delivery


class WorkDeliveryNamingTests(unittest.TestCase):
    def test_alias_names_use_jst_to_second_precision(self) -> None:
        aliases = delivery.alias_names({"finished_at": "2026-08-10T22:13:08+00:00"})
        self.assertEqual(
            aliases["EvidenceRadar_Report.html"],
            "20260811_071308__EvidenceRadar_Report.html",
        )

    def test_naive_delivery_timestamp_fails_closed(self) -> None:
        with self.assertRaises(delivery.DeliveryAliasError):
            delivery.alias_names({"finished_at": "2026-08-11T07:13:08"})

    def test_materializer_writes_byte_identical_siblings_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "delivery"
            source.mkdir()
            run = {"finished_at": "2026-08-11T07:13:08+09:00"}
            payloads = {
                "EvidenceRadar_Report.html": b"<html>test</html>",
                "EvidenceRadar_State.json": b"{}\n",
                "EvidenceRadar_Evidence.json": b"{}\n",
                "EvidenceRadar_Run.json": (json.dumps(run) + "\n").encode(),
            }
            for name, payload in payloads.items():
                (source / name).write_bytes(payload)
            paths = delivery.materialize_aliases(source, output)
            self.assertEqual(len(paths), 4)
            for canonical, alias in zip(delivery.CANONICAL_FILES, paths):
                self.assertEqual((source / canonical).read_bytes(), alias.read_bytes())
                self.assertTrue(alias.name.startswith("20260811_071308__"))
            with self.assertRaises(delivery.DeliveryAliasError):
                delivery.materialize_aliases(source, output)


if __name__ == "__main__":
    unittest.main()
