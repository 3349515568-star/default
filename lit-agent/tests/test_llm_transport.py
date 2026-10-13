import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import llm


class LLMTransportTests(unittest.TestCase):
    def setUp(self):
        self.saved = (
            llm._clients,
            llm._task_model,
            llm._providers,
            llm._defaults,
            llm._usage,
        )

    def tearDown(self):
        (
            llm._clients,
            llm._task_model,
            llm._providers,
            llm._defaults,
            llm._usage,
        ) = self.saved

    def test_streaming_provider_collects_text_and_usage_in_one_request(self):
        usage = SimpleNamespace(
            prompt_tokens=11, completion_tokens=7, total_tokens=18)
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Terra "))],
                usage=None,
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"))],
                usage=None,
            ),
            SimpleNamespace(choices=[], usage=usage),
        ]
        create = Mock(return_value=iter(chunks))
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm._clients = {"ofox": client}
        llm._task_model = {"summary": ("ofox", "openai/gpt-5.6-terra")}
        llm._providers = {"ofox": {"stream": True, "call_attempts": 1}}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        result = llm._call_model(
            [{"role": "user", "content": "Reply OK"}],
            task="summary",
            usage_tag="transport_probe",
        )

        self.assertEqual(result, "Terra OK")
        self.assertEqual(create.call_count, 1)
        self.assertTrue(create.call_args.kwargs["stream"])
        self.assertEqual(
            create.call_args.kwargs["stream_options"], {"include_usage": True})
        self.assertEqual(llm._usage, [{
            "tag": "transport_probe",
            "model": "openai/gpt-5.6-terra",
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }])

    def test_chat_records_usage_for_empty_attempt_before_retry(self):
        empty_usage = SimpleNamespace(
            prompt_tokens=10, completion_tokens=0, total_tokens=10)
        ok_usage = SimpleNamespace(
            prompt_tokens=10, completion_tokens=4, total_tokens=14)
        create = Mock(side_effect=[
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=""))],
                usage=empty_usage),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
                usage=ok_usage),
        ])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm._clients = {"deepseek": client}
        llm._task_model = {"score": ("deepseek", "deepseek-v4-flash")}
        llm._providers = {"deepseek": {
            "call_attempts": 2, "thinking": "disabled"}}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        result = llm._call_model(
            [{"role": "user", "content": "score"}], task="score",
            json_response=True, usage_tag="score")

        self.assertEqual(result, "OK")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(
            create.call_args.kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual([row["total_tokens"] for row in llm._usage], [10, 14])

    @patch("llm._log_transport")
    @patch("llm._log_raw")
    def test_responses_stream_collects_structured_events(self, log_raw, log_transport):
        usage = SimpleNamespace(input_tokens=13, output_tokens=8, total_tokens=21)
        events = [
            SimpleNamespace(
                type="response.created", sequence_number=0,
                response=SimpleNamespace(
                    id="resp_test", status="in_progress", usage=None),
            ),
            SimpleNamespace(
                type="response.reasoning_summary_text.delta", sequence_number=1,
                delta="brief", response=None,
            ),
            SimpleNamespace(
                type="response.output_text.delta", sequence_number=2,
                delta="Sol ", response=None,
            ),
            SimpleNamespace(
                type="response.output_text.delta", sequence_number=3,
                delta="OK", response=None,
            ),
            SimpleNamespace(
                type="response.completed", sequence_number=4,
                response=SimpleNamespace(
                    id="resp_test", status="completed", usage=usage),
            ),
        ]
        create = Mock(return_value=iter(events))
        client = SimpleNamespace(responses=SimpleNamespace(
            create=create, retrieve=Mock()))
        llm._clients = {"ofox": client}
        llm._task_model = {"summary": ("ofox", "openai/gpt-5.6-sol")}
        llm._providers = {"ofox": {
            "api_protocol": "responses", "stream": True, "call_attempts": 1,
            "responses_store": True, "recover_responses": True,
        }}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        result = llm._call_model([
            {"role": "system", "content": "Be precise"},
            {"role": "user", "content": "Reply OK"},
        ], task="summary", usage_tag="transport_probe")

        self.assertEqual(result, "Sol OK")
        self.assertEqual(create.call_count, 1)
        kwargs = create.call_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertTrue(kwargs["store"])
        self.assertEqual(kwargs["instructions"], "Be precise")
        self.assertEqual(kwargs["input"][0]["content"][0]["text"], "Reply OK")
        self.assertEqual(kwargs["metadata"], {"lit_agent_tag": "transport_probe"})
        client.responses.retrieve.assert_not_called()
        self.assertEqual(llm._usage, [{
            "tag": "transport_probe",
            "model": "openai/gpt-5.6-sol",
            "prompt_tokens": 13,
            "completion_tokens": 8,
            "total_tokens": 21,
        }])
        transport = log_transport.call_args.args[0]
        self.assertEqual(transport["response_id"], "resp_test")
        self.assertEqual(transport["reasoning_chars"], 5)
        self.assertTrue(transport["completed_event"])
        log_raw.assert_called_once()

    @patch("llm._log_transport")
    @patch("llm._log_raw")
    def test_responses_disconnect_recovers_by_id_without_resend(
            self, log_raw, log_transport):
        def broken_stream():
            yield SimpleNamespace(
                type="response.created", sequence_number=0,
                response=SimpleNamespace(
                    id="resp_recover", status="in_progress", usage=None),
            )
            yield SimpleNamespace(
                type="response.output_text.delta", sequence_number=1,
                delta="partial", response=None,
            )
            raise RuntimeError("incomplete chunked read")

        usage = SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30)
        completed = SimpleNamespace(
            id="resp_recover", status="completed", output_text="complete report",
            usage=usage, _request_id="req_retrieve")
        create = Mock(return_value=broken_stream())
        retrieve = Mock(return_value=completed)
        client = SimpleNamespace(
            responses=SimpleNamespace(create=create, retrieve=retrieve))
        llm._clients = {"ofox": client}
        llm._task_model = {"summary": ("ofox", "openai/gpt-5.6-sol")}
        llm._providers = {"ofox": {
            "api_protocol": "responses", "stream": True, "call_attempts": 3,
            "responses_store": True, "recover_responses": True,
        }}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        result = llm._call_model(
            [{"role": "user", "content": "Report"}], task="summary")

        self.assertEqual(result, "complete report")
        self.assertEqual(create.call_count, 1)
        retrieve.assert_called_once_with("resp_recover")
        self.assertTrue(log_transport.call_args.args[0]["recovered"])
        self.assertEqual(llm._usage[0]["total_tokens"], 30)
        log_raw.assert_called_once()

    @patch("llm._log_transport")
    @patch("llm._log_raw")
    @patch("llm.time.sleep")
    def test_responses_billed_request_is_not_resent_when_recovery_fails(
            self, sleep, log_raw, log_transport):
        def broken_stream():
            yield SimpleNamespace(
                type="response.created", sequence_number=0,
                response=SimpleNamespace(
                    id="resp_billed", status="in_progress", usage=None),
            )
            raise RuntimeError("stream lost")

        create = Mock(return_value=broken_stream())
        retrieve = Mock(side_effect=RuntimeError("retrieve unsupported"))
        client = SimpleNamespace(
            responses=SimpleNamespace(create=create, retrieve=retrieve))
        llm._clients = {"ofox": client}
        llm._task_model = {"summary": ("ofox", "openai/gpt-5.6-sol")}
        llm._providers = {"ofox": {
            "api_protocol": "responses", "stream": True, "call_attempts": 3,
            "recover_responses": True,
        }}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        with self.assertRaisesRegex(RuntimeError, "stream lost") as raised:
            llm._call_model(
                [{"role": "user", "content": "Report"}], task="summary")

        self.assertEqual(getattr(raised.exception, "response_id"), "resp_billed")
        self.assertEqual(create.call_count, 1)
        retrieve.assert_called_once_with("resp_billed")
        sleep.assert_not_called()
        self.assertEqual(log_transport.call_args.args[0]["response_id"], "resp_billed")
        log_raw.assert_called_once()

    @patch("llm._save_partial", return_value="logs/partials/report.partial.md")
    @patch("llm._log_transport")
    @patch("llm._log_raw")
    @patch("llm.time.sleep")
    def test_ofox_disconnect_saves_partial_and_never_resends(
            self, sleep, log_raw, log_transport, save_partial):
        def broken_stream():
            yield SimpleNamespace(
                type="response.created", sequence_number=0,
                response=SimpleNamespace(
                    id="resp_partial", status="in_progress", usage=None),
            )
            yield SimpleNamespace(
                type="response.output_text.delta", sequence_number=1,
                delta="received text", response=None,
            )
            raise RuntimeError("connection reset")

        create = Mock(return_value=broken_stream())
        retrieve = Mock()
        client = SimpleNamespace(
            responses=SimpleNamespace(create=create, retrieve=retrieve))
        llm._clients = {"ofox": client}
        llm._task_model = {"summary": ("ofox", "openai/gpt-5.6-sol")}
        llm._providers = {"ofox": {
            "api_protocol": "responses", "stream": True, "call_attempts": 3,
            "recover_responses": False,
        }}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        with self.assertRaisesRegex(RuntimeError, "connection reset") as raised:
            llm._call_model(
                [{"role": "user", "content": "Report"}], task="summary")

        self.assertEqual(getattr(raised.exception, "response_id"), "resp_partial")
        self.assertEqual(create.call_count, 1)
        retrieve.assert_not_called()
        sleep.assert_not_called()
        save_partial.assert_called_once_with(
            "chat", "openai/gpt-5.6-sol", "received text")
        transport = log_transport.call_args.args[0]
        self.assertEqual(transport["partial_path"], "logs/partials/report.partial.md")
        self.assertEqual(transport["output_chars"], len("received text"))
        log_raw.assert_called_once()

    @patch("llm.OpenAI")
    def test_init_disables_sdk_retries_and_applies_provider_timeout(self, client):
        llm.init(
            {"ofox": {
                "base_url": "https://api.example/v1",
                "key_env": "OFOX_API_KEY",
                "timeout_seconds": 180,
            }},
            {"summary": "ofox:openai/gpt-5.6-terra"},
            {"ofox": "test-key"},
            20000,
            0.15,
        )

        client.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.example/v1",
            timeout=180.0,
            max_retries=0,
        )

    def test_single_attempt_provider_does_not_resend_failed_request(self):
        create = Mock(side_effect=RuntimeError("transport failed"))
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm._clients = {"ofox": client}
        llm._task_model = {"summary": ("ofox", "openai/gpt-5.6-terra")}
        llm._providers = {"ofox": {"stream": True, "call_attempts": 1}}
        llm._defaults = {"max_tokens": 128, "temperature": 0.1}
        llm._usage = []

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            llm._call_model(
                [{"role": "user", "content": "Reply OK"}],
                task="summary",
                usage_tag="transport_probe",
            )

        self.assertEqual(create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
