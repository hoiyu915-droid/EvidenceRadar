from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import package_work_delivery as delivery


class WorkDeliveryNamingTests(unittest.TestCase):
    def test_delivery_alias_names_use_jst_to_second_precision(self) -> None:
        aliases = delivery.delivery_alias_names(
            {"finished_at": "2026-08-10T22:13:08+00:00"}
        )
        self.assertEqual(
            aliases["EvidenceRadar_Report.html"],
            "20260811_071308__EvidenceRadar_Report.html",
        )

    def test_naive_delivery_timestamp_fails_closed(self) -> None:
        with self.assertRaises(delivery.WorkDeliveryError):
            delivery.delivery_alias_names({"finished_at": "2026-08-11T07:13:08"})

    def test_package_writes_byte_identical_prefixed_siblings_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "delivery"
            source.mkdir()
            run = {
                "run_id": "test-run",
                "protocol_commit": "a" * 40,
                "execution_lane": "chatgpt_work",
                "finished_at": "2026-08-11T07:13:08+09:00",
            }
            (source / "EvidenceRadar_Report.html").write_bytes(b"<html>test</html>")
            (source / "EvidenceRadar_State.json").write_text("{}\n", encoding="utf-8")
            (source / "EvidenceRadar_Evidence.json").write_text("{}\n", encoding="utf-8")
            (source / "EvidenceRadar_Run.json").write_text(
                json.dumps(run) + "\n", encoding="utf-8"
            )
            with mock.patch.object(delivery, "validate_files", return_value=[]), mock.patch.object(
                delivery, "validate_delivery_bundle", return_value=([], run)
            ):
                result = delivery.package_work_delivery(
                    source,
                    output,
                    source_date_epoch=0,
                    validation_root=root,
                )
                self.assertEqual(len(result.delivery_files), 4)
                for canonical, alias in zip(delivery.CANONICAL_FILES, result.delivery_files):
                    self.assertEqual((source / canonical).read_bytes(), alias.read_bytes())
                    self.assertTrue(alias.name.startswith("20260811_071308__"))
                with self.assertRaises(delivery.WorkDeliveryError):
                    delivery.package_work_delivery(
                        source,
                        output,
                        source_date_epoch=0,
                        validation_root=root,
                    )


if __name__ == "__main__":
    unittest.main()
