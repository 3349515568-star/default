import unittest
from unittest.mock import Mock, patch

import requests

import europepmc


class EuropePmcTests(unittest.TestCase):
    def test_parse_cleans_escaped_title_markup(self):
        parsed = europepmc._parse({
            "title": "&lt;i&gt;Chelidonium majus&lt;/i&gt; L..",
        })

        self.assertEqual(parsed["title"], "Chelidonium majus L")

    @patch("europepmc.requests.post")
    @patch("europepmc.requests.get")
    def test_query_falls_back_to_official_post_on_get_404(self, get, post):
        get_response = Mock(status_code=404)
        get_response.raise_for_status.side_effect = requests.HTTPError(
            response=get_response)
        get.return_value = get_response
        post_response = Mock(status_code=200)
        post_response.json.return_value = {"resultList": {"result": []}}
        post.return_value = post_response

        self.assertEqual(europepmc._query("berberine", 1, None), [])

        post.assert_called_once_with(
            europepmc.SEARCH_POST_URL,
            data={
                "query": "berberine",
                "format": "json",
                "pageSize": 1,
                "resultType": "core",
            },
            headers={
                **europepmc.HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=40,
        )
        post_response.raise_for_status.assert_called_once_with()

    @patch("europepmc.requests.post")
    @patch("europepmc.requests.get")
    def test_query_does_not_hide_non_routing_http_errors(self, get, post):
        get_response = Mock(status_code=500)
        get_response.raise_for_status.side_effect = requests.HTTPError(
            response=get_response)
        get.return_value = get_response

        with self.assertRaises(requests.HTTPError):
            europepmc._query("berberine", 1, None)

        post.assert_not_called()

    @patch("europepmc.time.sleep")
    @patch("europepmc.requests.get")
    def test_query_retries_get_on_429_then_200(self, get, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "1"})
        ok = Mock(status_code=200, headers={})
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"resultList": {"result": []}}
        get.side_effect = [limited, ok]

        self.assertEqual(europepmc._query("berberine", 1, None), [])
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(1.0)

    @patch("europepmc.time.sleep")
    @patch("europepmc.requests.get")
    def test_query_retries_get_on_5xx_then_200(self, get, sleep):
        err = Mock(status_code=500, headers={})
        ok = Mock(status_code=200, headers={})
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"resultList": {"result": []}}
        get.side_effect = [err, ok]

        self.assertEqual(europepmc._query("berberine", 1, None), [])
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
