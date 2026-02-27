"""Tests for speechify_add.api — URL body replacement and header building."""

import tests.conftest  # noqa: F401 — mock third-party deps

import json
import unittest

from speechify_add.api import _build_body, _build_headers, _replace_url_value


class TestReplaceUrlValue(unittest.TestCase):
    """_replace_url_value recursively replaces URL-like strings in JSON."""

    def test_replaces_url_key(self):
        result = _replace_url_value({"url": "http://old.com"}, "http://new.com")
        self.assertEqual(result, {"url": "http://new.com"})

    def test_replaces_various_url_keys(self):
        for key in ("url", "link", "href", "uri", "source"):
            with self.subTest(key=key):
                result = _replace_url_value({key: "http://old.com"}, "http://new.com")
                self.assertEqual(result[key], "http://new.com")

    def test_case_insensitive_key_matching(self):
        result = _replace_url_value({"URL": "http://old.com"}, "http://new.com")
        self.assertEqual(result["URL"], "http://new.com")

    def test_nested_dict(self):
        data = {"outer": {"url": "http://old.com", "name": "test"}}
        result = _replace_url_value(data, "http://new.com")
        self.assertEqual(result["outer"]["url"], "http://new.com")
        self.assertEqual(result["outer"]["name"], "test")

    def test_list_of_dicts(self):
        data = [{"url": "http://old.com"}, {"url": "http://old2.com"}]
        result = _replace_url_value(data, "http://new.com")
        self.assertEqual(result[0]["url"], "http://new.com")
        self.assertEqual(result[1]["url"], "http://new.com")

    def test_replaces_bare_http_string(self):
        result = _replace_url_value("http://old.com", "http://new.com")
        self.assertEqual(result, "http://new.com")

    def test_leaves_non_url_strings_alone(self):
        result = _replace_url_value("not a url", "http://new.com")
        self.assertEqual(result, "not a url")

    def test_leaves_non_url_keys_alone(self):
        result = _replace_url_value({"name": "foo", "count": 5}, "http://new.com")
        self.assertEqual(result, {"name": "foo", "count": 5})

    def test_preserves_non_http_url_key_value(self):
        """URL key with non-http value should not be replaced."""
        result = _replace_url_value({"url": "/relative/path"}, "http://new.com")
        self.assertEqual(result["url"], "/relative/path")

    def test_numbers_and_booleans_pass_through(self):
        result = _replace_url_value(
            {"url": "http://x.com", "n": 42, "ok": True}, "http://y.com"
        )
        self.assertEqual(result, {"url": "http://y.com", "n": 42, "ok": True})

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"url": "http://deep.com"}}}}
        result = _replace_url_value(data, "http://new.com")
        self.assertEqual(result["a"]["b"]["c"]["url"], "http://new.com")

    def test_mixed_list_and_dict(self):
        data = {"items": [{"link": "http://a.com"}, {"link": "http://b.com"}]}
        result = _replace_url_value(data, "http://new.com")
        self.assertEqual(result["items"][0]["link"], "http://new.com")
        self.assertEqual(result["items"][1]["link"], "http://new.com")


class TestBuildBody(unittest.TestCase):
    """_build_body produces the correct request body."""

    def test_empty_body_example_returns_default(self):
        result = json.loads(_build_body("", "http://example.com"))
        self.assertEqual(result["url"], "http://example.com")
        self.assertEqual(result["type"], "url")

    def test_json_body_replacement(self):
        example = json.dumps({"url": "http://old.com", "type": "url"})
        result = json.loads(_build_body(example, "http://new.com"))
        self.assertEqual(result["url"], "http://new.com")

    def test_preserves_non_url_fields(self):
        example = json.dumps({"url": "http://old.com", "type": "url", "tag": "x"})
        result = json.loads(_build_body(example, "http://new.com"))
        self.assertEqual(result["tag"], "x")

    def test_non_json_body_falls_back_to_regex(self):
        example = "url=http://old.com&type=url"
        result = _build_body(example, "http://new.com")
        self.assertIn("http://new.com", result)
        self.assertNotIn("http://old.com", result)


class TestBuildHeaders(unittest.TestCase):
    """_build_headers sets auth and content-type."""

    def test_sets_bearer_token(self):
        headers = _build_headers({"add_headers": {}}, "tok123")
        self.assertEqual(headers["authorization"], "Bearer tok123")

    def test_preserves_captured_headers(self):
        cfg = {"add_headers": {"x-client-version": "1.0"}}
        headers = _build_headers(cfg, "tok")
        self.assertEqual(headers["x-client-version"], "1.0")

    def test_adds_content_type_if_missing(self):
        headers = _build_headers({"add_headers": {}}, "tok")
        self.assertEqual(headers["content-type"], "application/json")

    def test_does_not_override_existing_content_type(self):
        cfg = {"add_headers": {"Content-Type": "text/plain"}}
        headers = _build_headers(cfg, "tok")
        self.assertEqual(headers["Content-Type"], "text/plain")

    def test_empty_cfg(self):
        headers = _build_headers({}, "tok")
        self.assertEqual(headers["authorization"], "Bearer tok")


if __name__ == "__main__":
    unittest.main()
