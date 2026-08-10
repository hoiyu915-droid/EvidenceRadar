from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from publisher_feed import fetch_feed_records, parse_feed  # noqa: E402


NATURE_LIKE_RDF = '''<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
 <item rdf:about="https://www.nature.com/articles/example">
  <title>Quantum widgets improve measurement</title>
  <link>https://www.nature.com/articles/example</link>
  <dc:date>2026-08-10</dc:date>
  <dc:creator>A. Example</dc:creator>
  <description>Recent physics result.</description>
  <prism:doi>10.1038/example</prism:doi>
  <prism:publicationName>Nature Physics</prism:publicationName>
 </item>
</rdf:RDF>'''

ATOM = '''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <title>Social science result</title>
  <link rel="alternate" href="https://example.org/work" />
  <published>2026-08-09T10:00:00Z</published>
  <summary>Governance and public policy.</summary>
  <author><name>B. Example</name></author>
 </entry>
</feed>'''


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return _Response(self.text)


class PublisherFeedTests(unittest.TestCase):
    def test_parse_nature_style_rdf(self) -> None:
        rows = parse_feed(
            NATURE_LIKE_RDF,
            feed_url="https://www.nature.com/subjects/physics.rss",
            source_id="nature_physics",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["publication_date"], "2026-08-10")
        self.assertEqual(rows[0]["doi"], "10.1038/example")
        self.assertEqual(rows[0]["venue"], "Nature Physics")
        self.assertEqual(rows[0]["title"], "Quantum widgets improve measurement")

    def test_parse_atom(self) -> None:
        rows = parse_feed(ATOM, feed_url="https://example.org/feed.atom", source_id="example")
        self.assertEqual(rows[0]["publication_date"], "2026-08-09")
        self.assertEqual(rows[0]["authors"], ["B. Example"])

    def test_window_query_and_cache(self) -> None:
        session = _Session(NATURE_LIKE_RDF)
        cache = {}
        cfg = {"feeds": ["https://www.nature.com/subjects/physics.rss"]}
        rows = fetch_feed_records(
            session,
            source_id="nature_physics",
            source_config=cfg,
            query="quantum measurement",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=10,
            cache=cache,
        )
        self.assertEqual(len(rows), 1)
        rows2 = fetch_feed_records(
            session,
            source_id="nature_physics",
            source_config=cfg,
            query="*",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=10,
            cache=cache,
        )
        self.assertEqual(len(rows2), 1)
        self.assertEqual(session.calls, 1)


if __name__ == "__main__":
    unittest.main()
