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


if __name__ == "__main__":
    unittest.main()
