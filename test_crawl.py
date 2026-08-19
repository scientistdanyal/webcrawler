import unittest
from crawl import (
    normalize_url,
    get_heading_from_html,
    get_first_paragraph_from_html,
    get_urls_from_html,
    get_images_from_html,
    extract_page_data,
)
from modules.email_extractor import extract_emails_from_html
from modules.email_store import EmailStore
from modules.tracker import CrawlTracker
from modules.captcha import detect_captcha_type, wait_seconds_for_captcha


class TestCrawl(unittest.TestCase):
    def test_normalize_url_protocol(self) -> None:
        input_url = "https://crawler-test.com/path"
        actual = normalize_url(input_url)
        expected = "crawler-test.com/path"
        self.assertEqual(actual, expected)

    def test_normalize_url_slash(self) -> None:
        input_url = "https://crawler-test.com/path/"
        actual = normalize_url(input_url)
        expected = "crawler-test.com/path"
        self.assertEqual(actual, expected)

    def test_normalize_url_capitals(self) -> None:
        input_url = "https://CRAWLER-TEST.com/path"
        actual = normalize_url(input_url)
        expected = "crawler-test.com/path"
        self.assertEqual(actual, expected)

    def test_normalize_url_http(self) -> None:
        input_url = "http://CRAWLER-TEST.com/path"
        actual = normalize_url(input_url)
        expected = "crawler-test.com/path"
        self.assertEqual(actual, expected)

    def test_normalize_url_strips_www_and_fragment(self) -> None:
        actual = normalize_url("https://www.Crawler-Test.com/path/#section")
        self.assertEqual(actual, "crawler-test.com/path")

    def test_get_heading_from_html_basic(self) -> None:
        input_body = "<html><body><h1>Test Title</h1></body></html>"
        actual = get_heading_from_html(input_body)
        expected = "Test Title"
        self.assertEqual(actual, expected)

    def test_get_heading_from_html_h2_fallback(self) -> None:
        input_body = "<html><body><h2>Fallback Title</h2></body></html>"
        actual = get_heading_from_html(input_body)
        expected = "Fallback Title"
        self.assertEqual(actual, expected)

    def test_get_heading_from_html_with_whitespace(self) -> None:
        input_body = "<html><body><h1>   Whitespace Title   </h1></body></html>"
        actual = get_heading_from_html(input_body)
        expected = "Whitespace Title"
        self.assertEqual(actual, expected)

    def test_get_first_paragraph_from_html_basic(self) -> None:
        input_body = "<html><body><p>This is the first paragraph.</p></body></html>"
        actual = get_first_paragraph_from_html(input_body)
        expected = "This is the first paragraph."
        self.assertEqual(actual, expected)

    def test_get_first_paragraph_from_html_main_priority(self) -> None:
        input_body = """<html><body>
            <p>Outside paragraph.</p>
            <main>
                <p>Main paragraph.</p>
            </main>
        </body></html>"""
        actual = get_first_paragraph_from_html(input_body)
        expected = "Main paragraph."
        self.assertEqual(actual, expected)

    def test_get_first_paragraph_from_html_no_paragraph(self) -> None:
        input_body = "<html><body><h1>No paragraphs here</h1></body></html>"
        actual = get_first_paragraph_from_html(input_body)
        expected = ""
        self.assertEqual(actual, expected)

    def test_get_urls_from_html_absolute(self) -> None:
        input_url = "https://crawler-test.com"
        input_body = '<html><body><a href="https://crawler-test.com"><span>Boot.dev</span></a></body></html>'
        actual = get_urls_from_html(input_body, input_url)
        expected = ["https://crawler-test.com"]
        self.assertEqual(actual, expected)

    def test_get_urls_from_html_relative(self) -> None:
        input_url = "https://crawler-test.com"
        input_body = (
            '<html><body><a href="/path/one"><span>Boot.dev</span></a></body></html>'
        )
        actual = get_urls_from_html(input_body, input_url)
        expected = ["https://crawler-test.com/path/one"]
        self.assertEqual(actual, expected)

    def test_get_urls_from_html_both(self) -> None:
        input_url = "https://crawler-test.com"
        input_body = '<html><body><a href="/path/one"><span>Boot.dev</span></a><a href="https://other.com/path/one"><span>Boot.dev</span></a></body></html>'
        actual = get_urls_from_html(input_body, input_url)
        expected = ["https://crawler-test.com/path/one", "https://other.com/path/one"]
        self.assertEqual(actual, expected)

    def test_get_urls_from_html_dedupes_and_finds_embedded(self) -> None:
        input_url = "https://crawler-test.com/home"
        input_body = """
        <html><body>
          <a href="/contact">Contact</a>
          <a href="/contact/">Contact again</a>
          <a href="https://www.crawler-test.com/contact#form">Contact www</a>
          <p>Also see https://crawler-test.com/about-us for more</p>
        </body></html>
        """
        actual = get_urls_from_html(input_body, input_url)
        norms = {normalize_url(u) for u in actual}
        self.assertIn("crawler-test.com/contact", norms)
        self.assertIn("crawler-test.com/about-us", norms)
        self.assertEqual(
            len([n for n in norms if n == "crawler-test.com/contact"]), 1
        )

    def test_get_images_from_html_absolute(self) -> None:
        input_url = "https://crawler-test.com"
        input_body = '<html><body><img src="https://crawler-test.com/logo.png" alt="Logo"></body></html>'
        actual = get_images_from_html(input_body, input_url)
        expected = ["https://crawler-test.com/logo.png"]
        self.assertEqual(actual, expected)

    def test_get_images_from_html_relative(self) -> None:
        input_url = "https://crawler-test.com"
        input_body = '<html><body><img src="/logo.png" alt="Logo"></body></html>'
        actual = get_images_from_html(input_body, input_url)
        expected = ["https://crawler-test.com/logo.png"]
        self.assertEqual(actual, expected)

    def test_get_images_from_html_multiple(self) -> None:
        input_url = "https://crawler-test.com"
        input_body = '<html><body><img src="/logo.png" alt="Logo"><img src="https://cdn.boot.dev/banner.jpg"></body></html>'
        actual = get_images_from_html(input_body, input_url)
        expected = [
            "https://crawler-test.com/logo.png",
            "https://cdn.boot.dev/banner.jpg",
        ]
        self.assertEqual(actual, expected)

    def test_extract_page_data_basic(self):
        input_url = "https://crawler-test.com"
        input_body = """<html><body>
            <h1>Test Title</h1>
            <p>This is the first paragraph. Contact us at hello@crawler-test.com</p>
            <a href="/link1">Link 1</a>
            <a href="mailto:support@crawler-test.com">Email us</a>
            <img src="/image1.jpg" alt="Image 1">
        </body></html>"""
        actual = extract_page_data(input_body, input_url)
        expected = {
            "url": "https://crawler-test.com",
            "emails": ["hello@crawler-test.com", "support@crawler-test.com"],
        }
        self.assertEqual(actual, expected)

    def test_extract_emails_from_html_dedupes_and_finds_mailto(self) -> None:
        html = """<html><body>
            <p>Reach Hello@Example.com or hello@example.com</p>
            <a href="mailto:Sales@Example.com?subject=Hi">Sales</a>
        </body></html>"""
        actual = extract_emails_from_html(html)
        expected = ["hello@example.com", "sales@example.com"]
        self.assertEqual(actual, expected)

    def test_extract_emails_from_html_none(self) -> None:
        html = "<html><body><p>No contact info here.</p></body></html>"
        self.assertEqual(extract_emails_from_html(html), [])

    def test_email_store_appends_and_dedupes(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emails.json"
            store = EmailStore(path)
            store.append("Branch A", ["a@example.com"])
            store.append("Branch A", ["A@example.com", "b@example.com"])
            self.assertEqual(
                store._data["Branch A"], ["a@example.com", "b@example.com"]
            )

    def test_tracker_done_and_clear(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracking.json"
            tracker = CrawlTracker(path)
            tracker.mark(
                "http://example.com",
                name="Example",
                status="done",
                emails_found=1,
            )
            self.assertTrue(tracker.is_done("http://example.com"))
            tracker.clear("http://example.com")
            self.assertFalse(tracker.is_done("http://example.com"))

    def test_detect_captcha_cloudflare(self) -> None:
        html = "<html><title>Just a moment...</title><div id='cf-challenge-running'></div><script src='cdn-cgi/challenge-platform/x.js'></script></html>"
        self.assertEqual(detect_captcha_type(html), "cloudflare")
        self.assertEqual(wait_seconds_for_captcha("cloudflare"), 20)

    def test_detect_captcha_ignores_normal_cloudflare_cdn(self) -> None:
        # Normal sites often include data-cfasync / cloudflare CDN without a challenge
        html = """
        <html><head><title>Brewton Area YMCA</title></head>
        <body data-cfasync="false">
          <h1>Home</h1>
          <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js"></script>
        </body></html>
        """
        self.assertIsNone(detect_captcha_type(html))

    def test_detect_captcha_recaptcha(self) -> None:
        html = '<html><script src="https://www.google.com/recaptcha/api.js"></script><div class="g-recaptcha"></div></html>'
        self.assertEqual(detect_captcha_type(html), "recaptcha")
        self.assertEqual(wait_seconds_for_captcha("recaptcha"), 10)

    def test_detect_captcha_none(self) -> None:
        html = "<html><body><h1>Brewton Area YMCA</h1><p>Contact us</p></body></html>"
        self.assertIsNone(detect_captcha_type(html))


if __name__ == "__main__":
    unittest.main()
