"""Check the actual add-on HTTP handler, including cache-busted asset URLs."""
from html.parser import HTMLParser
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        url = attrs.get("src") if tag == "script" else attrs.get("href") if tag == "link" and attrs.get("rel") == "stylesheet" else None
        if url and not urlsplit(url).netloc:
            self.urls.append(url)


class StaticAssetsTest(unittest.TestCase):
    def test_html_assets_are_served_with_executable_types(self):
        class Handler(server.Handler):
            def log_message(self, *_): pass
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{http.server_port}/"
        try:
            with urlopen(base) as response:
                parser = Assets()
                parser.feed(response.read().decode("utf-8"))
            self.assertTrue(any(url.startswith("basemaps.js?") for url in parser.urls))
            for url in parser.urls:
                with self.subTest(url=url), urlopen(base+url) as response:
                    kind = response.headers.get_content_type()
                    if urlsplit(url).path.endswith(".js"):
                        self.assertIn(kind, ("text/javascript", "application/javascript"))
                    else:
                        self.assertEqual(kind, "text/css")
                    self.assertGreater(len(response.read()), 0)
            # Expanding the UI allowlist must not expose Python or cached data.
            with self.assertRaises(HTTPError) as error:
                urlopen(base+"archive_import.py")
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
