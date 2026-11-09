"""Driver registration and actual client use, without contacting an engine."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.mvp.backend import BackendConfig, load_backend_config
from serving_runtime.mvp.auth import CHAT_ROUTE
from serving_runtime.mvp.backend_drivers import BackendDriver, DRIVERS, register_driver
from serving_runtime.mvp.engine_client import EngineClient
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.policy import load_policy


class BackendDriverExtensionTests(unittest.TestCase):
    def test_actual_gateway_uses_driver_client_for_different_tokenizer_semantics(self):
        class ExampleClient(EngineClient):
            def count_input_tokens(self, request_id, input_value, backend_model_id, **kwargs):
                _, _, body = self.request('POST', '/example-non-openai-tokenizer')
                return len(json.loads(body)['example_token_ids'])
        class ExampleDriver(BackendDriver):
            def make_client(self, backend, timeout_s): return ExampleClient(backend, timeout_s)
        with patch.dict(DRIVERS), tempfile.TemporaryDirectory() as directory:
            contribution = ExampleDriver('example-semantic-driver', 'EXAMPLE')
            register_driver(contribution)
            root = Path(directory); path = root / 'backend.json'
            path.write_text(json.dumps({'schema_version': 'runtime-backend/1.0', 'kind': contribution.id,
                'base_url': 'http://127.0.0.1:9', 'model_map': {'test-model': 'EXAMPLE-model'}}))
            config = MVPConfig(backend=contribution.id, backend_config_path=str(path), data_dir=str(root / 'state'))
            policy_path = Path(__file__).parents[1] / 'examples/mvp/policy.json'
            gateway = GatewayApp(config, load_policy(policy_path))
            self.assertIsInstance(gateway._engine_client, ExampleClient)
            self.assertIsInstance(gateway._status_engine_client, ExampleClient)
            with patch.object(gateway._engine_client, 'request', return_value=(200, {}, b'{"example_token_ids":[1,2,3,4]}')):
                self.assertEqual(gateway._engine_client.count_input_tokens('EXAMPLE-request', [], 'EXAMPLE-model'), 4)

    def test_registered_extension_is_used_by_config_and_tokenizer(self):
        contribution = BackendDriver('example-extension', 'EXAMPLE', health_path='/ready-example',
            tokenize_path='/count-example', chat_output_field='max_completion_tokens')
        with patch.dict(DRIVERS):
            register_driver(contribution)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'backend.json'
                path.write_text(json.dumps({'schema_version': 'runtime-backend/1.0', 'kind': contribution.id,
                    'base_url': 'http://127.0.0.1:9', 'model_map': {'example': 'EXAMPLE-model'}}))
                backend = load_backend_config(path, environ={})
            self.assertEqual(backend.health_path, '/ready-example')
            self.assertEqual(backend.output_tokens_field(CHAT_ROUTE), 'max_completion_tokens')
            client = EngineClient(backend, 1)
            with patch.object(client, 'request', return_value=(200, {}, b'{"count":3}')) as request:
                self.assertEqual(client.count_input_tokens('EXAMPLE-request', [], 'EXAMPLE-model'), 3)
                self.assertEqual(request.call_args.args[:2], ('POST', '/count-example'))

    def test_metadata_only_driver_cannot_guess_token_count(self):
        backend = replace(BackendConfig.mock(('127.0.0.1', 9)), kind='tensorrt-llm')
        client = EngineClient(backend, 1)
        with patch.object(client, 'request') as request:
            with self.assertRaisesRegex(ValueError, 'TOKENIZER_PROFILE_UNAVAILABLE'):
                client.count_input_tokens('EXAMPLE-request', [], 'EXAMPLE-model')
            request.assert_not_called()

    def test_driver_implementation_can_replace_metadata_without_editing_client(self):
        with patch.dict(DRIVERS):
            register_driver(BackendDriver('tensorrt-llm', 'EXAMPLE implementation', tokenize_path='/example-count'),
                replace_metadata_only=True)
            self.assertEqual(DRIVERS['tensorrt-llm'].tokenize_path, '/example-count')
            with self.assertRaisesRegex(ValueError, 'DUPLICATE_OR_INVALID'):
                register_driver(BackendDriver('tensorrt-llm', 'duplicate'))
