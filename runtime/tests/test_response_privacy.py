import copy
import unittest

from serving_runtime.mvp.response_privacy import sanitize_response_metadata, ResponsePrivacyError, MAX_METADATA_DEPTH


class ResponsePrivacyTest(unittest.TestCase):
    def test_unrequested_private_metadata_removed_without_mutation_or_content_filtering(self):
        body = {"id": "r", "object": "chat.completion", "model": "m", "system_fingerprint": "vendor-fp",
                "prompt_token_ids": [1, 2], "prompt_logprobs": [0.1], "private_debug": {"path": "/private/model"},
                "kv_transfer_params": {"remote_host": "private"}, "vendor_extension": {"prompt_token_ids": [1], "cache_hit": True, "latency_ms": 5},
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "private_debug prompt_token_ids /private/model",
                              "reasoning": "normal generated reasoning", "tool_calls": [{"function": {"arguments": '{"prompt":"normal tool argument"}'}}],
                              "prompt_token_ids": [1]}, "finish_reason": "stop", "logprobs": {"content": []}, "token_ids": [3]}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "private_debug": "hidden"}}
        original = copy.deepcopy(body)
        result = sanitize_response_metadata(body)
        self.assertEqual(body, original)
        for name in ("prompt_token_ids", "prompt_logprobs", "private_debug", "kv_transfer_params"):
            self.assertNotIn(name, result)
        self.assertNotIn("logprobs", result["choices"][0])
        self.assertNotIn("token_ids", result["choices"][0])
        self.assertNotIn("prompt_token_ids", result["choices"][0]["message"])
        self.assertEqual(result["choices"][0]["message"]["content"], original["choices"][0]["message"]["content"])
        self.assertEqual(result["choices"][0]["message"]["tool_calls"], original["choices"][0]["message"]["tool_calls"])
        self.assertEqual(result["vendor_extension"], {"cache_hit": True, "latency_ms": 5})
        self.assertEqual(result["system_fingerprint"], "vendor-fp")
        self.assertEqual(result["usage"], {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})

    def test_explicit_validated_return_options_preserve_requested_fields_only(self):
        body = {"prompt": "input", "prompt_text": "input", "prompt_token_ids": [1], "prompt_logprobs": [0.2],
                "private_debug": "internal", "choices": [{"token_ids": [2], "logprobs": {"content": []}, "text": "generated"}]}
        result = sanitize_response_metadata(body, requested_fields=frozenset({"return_prompt_text", "return_token_ids", "logprobs", "prompt_logprobs", "private_debug"}))
        self.assertEqual(result["prompt_text"], "input")
        self.assertEqual(result["prompt_token_ids"], [1])
        self.assertEqual(result["prompt_logprobs"], [0.2])
        self.assertEqual(result["choices"][0]["logprobs"], {"content": []})
        self.assertNotIn("private_debug", result)

    def test_sse_usage_delta_and_completion_standard_values_preserved(self):
        body = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "normal", "private_debug": "secret"},
                             "finish_reason": None}], "usage": {"total_tokens": 1}, "service_tier": "default"}
        result = sanitize_response_metadata(body)
        self.assertEqual(result["choices"][0]["delta"], {"role": "assistant", "content": "normal"})
        self.assertIsNone(result["choices"][0]["finish_reason"])
        self.assertEqual(result["usage"], body["usage"])
        completion = {"choices": [{"index": 0, "text": "prompt_logprobs generated text", "finish_reason": "length"}], "vendor_field": True}
        self.assertEqual(sanitize_response_metadata(completion), completion)

    def test_nested_metadata_arrays_are_sanitized_and_generated_objects_are_opaque(self):
        generated = [{"type": "text", "text": "private_debug is normal text", "prompt_token_ids": "generated-key"}]
        arguments = {"prompt": "user tool argument", "prompt_token_ids": [123], "private_debug": "user content"}
        body = {"vendor_metadata": [{"level": {"private_debug": "hidden", "prompt_token_ids": [1], "ok": True}},
                                     {"content": {"private_debug": "metadata pretending to be content", "ok": 1}}],
                "choices": [{"message": {"content": generated, "reasoning_content": "normal reasoning",
                                "tool_calls": [{"function": {"name": "tool", "arguments": arguments}, "private_debug": "internal"}]},
                             "finish_reason": "tool_calls"}]}
        original = copy.deepcopy(body)
        result = sanitize_response_metadata(body)
        self.assertEqual(result["vendor_metadata"], [{"level": {"ok": True}}, {"content": {"ok": 1}}])
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], generated)
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], arguments)
        self.assertNotIn("private_debug", message["tool_calls"][0])
        self.assertEqual(body, original)

    def test_metadata_depth_limit_rejects_projection_without_partial_output(self):
        nested = {"value": "safe"}
        for _ in range(MAX_METADATA_DEPTH + 1):
            nested = {"nested": nested}
        with self.assertRaises(ResponsePrivacyError):
            sanitize_response_metadata({"vendor_metadata": nested, "private_debug": "hidden"})
        # Deep generated content is opaque; the relay owns overall JSON/bytes limits.
        self.assertEqual(sanitize_response_metadata({"choices": [{"message": {"content": nested}}]})["choices"][0]["message"]["content"], nested)

    def test_content_named_root_metadata_is_not_a_generated_content_boundary(self):
        body = {"content": {"private_debug": "internal", "safe": 1},
                "message": {"content": {"prompt_token_ids": [1], "safe": 2}},
                "choices": [{"message": {"content": {"private_debug": "generated structured content"}}}]}
        result = sanitize_response_metadata(body)
        self.assertEqual(result["content"], {"safe": 1})
        self.assertEqual(result["message"], {"content": {"safe": 2}})
        self.assertEqual(result["choices"][0]["message"], body["choices"][0]["message"])
