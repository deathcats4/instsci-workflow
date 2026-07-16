from unittest import TestCase
from unittest.mock import Mock, patch

from instsci.http_utils import request_with_retry


class HttpUtilsTests(TestCase):
    def test_request_with_retry_does_not_retry_exhausted_quota(self) -> None:
        response = Mock(status_code=429)
        response.headers = {"X-RateLimit-Remaining": "0"}
        with patch("instsci.http_utils.requests.request", return_value=response) as request, patch(
            "instsci.http_utils.time.sleep"
        ) as sleep:
            result = request_with_retry("GET", "https://api.openalex.org/works")

        self.assertIs(result, response)
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_request_with_retry_honors_retry_after_header(self) -> None:
        first = Mock(status_code=429)
        first.headers = {"Retry-After": "3"}
        second = Mock(status_code=200)
        second.headers = {}
        with patch("instsci.http_utils.requests.request", side_effect=[first, second]), patch(
            "instsci.http_utils.time.sleep"
        ) as sleep:
            result = request_with_retry("GET", "https://api.openalex.org/works", max_retries=1)

        self.assertIs(result, second)
        sleep.assert_called_once_with(3.0)
