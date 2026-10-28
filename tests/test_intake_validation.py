"""Intake boundary tests; fixtures are never written into the catalog."""
import copy
import json
import unittest

from api.intake_validation import normalize_payload


def draft():
    return {
        'model': {'brand': ' 示例品牌 ', 'display_name': ' 示例手机 Pro '},
        'variant': {'variant_name': ' 中国大陆版 '},
        'skus': [{'ram_gb': 12, 'storage_gb': 256}],
    }


def submitted():
    body = draft()
    body['model'].update(release_month='2026-10', source_url='https://example.com/specs')
    body['skus'][0]['price'] = {
        'amount': 3999, 'price_kind': 'official_current_price', 'condition': 'new',
        'observed_at': '2026-10-28', 'source_url': 'https://example.com/products/phone',
        'source_note': '官方网站公开价格',
    }
    return body


def with_spec(key='weight_g', value_num=201.5, unit='g', raw_value='201.5 克'):
    body = submitted()
    body['specifications'] = [dict(key=key, value_num=value_num, unit=unit, raw_value=raw_value)]
    return body


class IntakeValidationTests(unittest.TestCase):
    def assert_invalid(self, body, **kwargs):
        normalized, errors, warnings = normalize_payload(body, **kwargs)
        self.assertIsNone(normalized)
        self.assertTrue(errors)
        self.assertTrue(all(type(message) is str for message in errors + warnings))
        # Invalid Unicode and secrets must not be copied into diagnostic output.
        json.dumps({'errors': errors, 'warnings': warnings}, ensure_ascii=False).encode('utf-8')
        return errors

    def test_draft_has_fixed_shape_and_preserves_unknowns_as_null(self):
        original = draft()
        saved = copy.deepcopy(original)
        normalized, errors, warnings = normalize_payload(original)
        self.assertEqual(errors, [])
        self.assertEqual(original, saved)
        self.assertEqual(normalized, {
            'model': {'brand': '示例品牌', 'display_name': '示例手机 Pro',
                      'release_month': None, 'source_url': None},
            'variant': {'variant_name': '中国大陆版', 'region': None,
                        'satellite_support': False, 'is_default': True},
            'skus': [{'ram_gb': 12, 'storage_gb': 256, 'memory_type': None,
                      'price': {'amount': None, 'price_kind': None, 'condition': None,
                                'observed_at': None, 'source_url': None, 'source_note': ''}}],
            'specifications': [], 'source_note': '',
        })
        self.assertTrue(warnings)
        self.assert_invalid(original, complete=True)

    def test_complete_submission_and_zero_price_have_no_fabricated_data(self):
        body = submitted()
        body['skus'][0]['price']['amount'] = 0
        normalized, errors, warnings = normalize_payload(body, complete=True)
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual(normalized['skus'][0]['price']['amount'], 0)
        self.assertEqual(normalized['specifications'], [])
        json.dumps(normalized, allow_nan=False)

    def test_object_and_list_types_are_strict_at_every_level(self):
        for bad in (None, [], '', 3, False):
            with self.subTest(body=repr(bad)):
                self.assert_invalid(bad)
        for path, values in [
            (('model',), [None, [], 'brand']),
            (('variant',), [None, [], False]),
            (('skus',), [None, {}, 'sku', []]),
            (('specifications',), [None, {}, 'spec', False]),
            (('skus', 0), [None, [], True]),
            (('skus', 0, 'price'), [None, [], 'price', 0]),
        ]:
            for bad in values:
                with self.subTest(path=path, bad=repr(bad)):
                    body = draft()
                    target = body
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = bad
                    self.assert_invalid(body)
        for bad in (None, [], '', 0, False):
            body = submitted(); body['specifications'] = [bad]
            self.assert_invalid(body)

    def test_unknown_fields_are_rejected_in_every_object(self):
        for path in [(), ('model',), ('variant',), ('skus', 0),
                     ('skus', 0, 'price'), ('specifications', 0)]:
            with self.subTest(path=path):
                body = with_spec()
                target = body
                for key in path:
                    target = target[key]
                target['unsupported_field'] = None
                self.assert_invalid(body)
        body = draft(); body[None] = 'invalid JSON key'
        self.assert_invalid(body)

    def test_booleans_are_never_numbers_and_boolean_fields_do_not_coerce(self):
        for field in ('satellite_support', 'is_default'):
            for bad in (None, 0, 1, 'true', [], {}):
                body = draft(); body['variant'][field] = bad
                self.assert_invalid(body)
        for path in [('skus', 0, 'ram_gb'), ('skus', 0, 'storage_gb'),
                     ('skus', 0, 'price', 'amount'), ('specifications', 0, 'value_num')]:
            body = with_spec(); target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = True
            self.assert_invalid(body)

    def test_nonfinite_huge_and_wrong_numeric_types_are_rejected_without_exception(self):
        for bad in (float('nan'), float('inf'), -float('inf'), 10 ** 1000, '12', [], {}):
            for path in [('skus', 0, 'ram_gb'), ('skus', 0, 'storage_gb'),
                         ('skus', 0, 'price', 'amount'), ('specifications', 0, 'value_num')]:
                with self.subTest(path=path, bad=type(bad).__name__):
                    body = with_spec(); target = body
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = bad
                    self.assert_invalid(body)
        for field, bad in [('ram_gb', 0), ('ram_gb', 129), ('ram_gb', 12.5),
                           ('storage_gb', -1), ('storage_gb', 16385), ('storage_gb', 256.5)]:
            body = draft(); body['skus'][0][field] = bad
            self.assert_invalid(body)
        for bad in (-1, 1000001):
            body = submitted(); body['skus'][0]['price']['amount'] = bad
            self.assert_invalid(body)

    def test_text_types_invalid_unicode_controls_and_length_cannot_reach_output(self):
        for bad in (True, 12, [], {}, 'a\x00b', 'a\nb', 'a\t b', 'a\x7fb',
                    'a\x85b', 'a\ud800b', 'a\udfffb', '\ufffd', 'x' * 257):
            body = draft(); body['model']['display_name'] = bad
            self.assert_invalid(body)
        for path, length in [(('source_note',), 2001),
                             (('skus', 0, 'price', 'source_note'), 501),
                             (('specifications', 0, 'raw_value'), 501)]:
            body = with_spec(); target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = 'x' * length
            self.assert_invalid(body)
        body = draft(); body['model']['brand'] = ' ' * 300 + '品牌'
        self.assert_invalid(body)

    def test_private_paths_and_secret_patterns_are_rejected_without_echo(self):
        for bad in [r'C:\Users\private\phone.xlsx', r'\\server\private\phone.xlsx',
                    '/Users/private/file', '/home/private/file', 'file:///D:/private/data',
                    'api_key=example-private-value', 'password: example-private-value',
                    'access_token=example-private-value', 'sk-abcdefghijklmnop',
                    'Bearer example-private-token-000000000000']:
            with self.subTest(kind=bad[:8]):
                body = draft(); body['source_note'] = bad
                errors = self.assert_invalid(body)
                self.assertNotIn(bad, ' '.join(errors))

    def test_complete_requires_each_price_field_and_model_month(self):
        for field in ('amount', 'price_kind', 'condition', 'observed_at', 'source_url'):
            for missing in (None, '') if field != 'amount' else (None,):
                body = submitted(); body['skus'][0]['price'][field] = missing
                self.assert_invalid(body, complete=True)
        body = submitted(); del body['model']['release_month']
        self.assert_invalid(body, complete=True)
        body = submitted(); body['skus'].append({'ram_gb': 16, 'storage_gb': 512})
        self.assert_invalid(body, complete=True)

    def test_enum_values_are_checked_even_on_drafts(self):
        for field, bad in [('price_kind', 'made_up'), ('condition', 'refurbished'),
                           ('price_kind', 3), ('condition', True)]:
            body = submitted(); body['skus'][0]['price'][field] = bad
            self.assert_invalid(body)
        body = submitted(); body['skus'][0]['price'].update(
            price_kind='used_market_estimate', condition='unknown')
        self.assertEqual(normalize_payload(body, complete=True)[1], [])

    def test_dates_are_real_strict_ascii_calendar_dates(self):
        for bad in ['2026-2', '2026-13', '2026-00', '0000-01', '２０２６-１０', '2026-10-01', True]:
            body = draft(); body['model']['release_month'] = bad
            self.assert_invalid(body)
        for bad in ['2025-02-29', '2026-04-31', '2026-1-01', '2026-10-28T12:00:00',
                    '2026-00-01', '0000-01-01', True]:
            body = submitted(); body['skus'][0]['price']['observed_at'] = bad
            self.assert_invalid(body)
        body = submitted(); body['skus'][0]['price']['observed_at'] = '2024-02-29'
        self.assertEqual(normalize_payload(body, complete=True)[1], [])

    def test_url_parser_failures_unsafe_credentials_ports_and_hosts_are_errors(self):
        invalid = ['https://', 'https:///path', 'ftp://example.com/x', 'https://:443/x',
                   'https://user:pass@example.com', 'https://@example.com',
                   'https://example.com:bad', 'https://example.com:65536',
                   'https://example.com:-1', 'https://example.com:',
                   'https://[broken', 'https://[::1]trailing', 'https://exam_ple.com',
                   'https://example.com\\private', 'https://example.com/\npath',
                   'https://example.com/%0afoo', 'https://example.com/%00foo',
                   'https://example.com?token=private-token-value',
                   'https://example.com/%ZZ', 'https://example.com/\ud800']
        for url in invalid:
            for path in [('model', 'source_url'), ('skus', 0, 'price', 'source_url')]:
                with self.subTest(url=repr(url), path=path):
                    body = submitted(); target = body
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = url
                    self.assert_invalid(body)

    def test_public_unicode_and_ipv6_urls_are_valid_without_network_requests(self):
        for url in ['https://example.com:443/a?sku=12#specs', 'http://example.com',
                    'https://例子.中国/手机', 'https://[2001:db8::1]:8443/source']:
            body = submitted(); body['model']['source_url'] = url
            body['skus'][0]['price']['source_url'] = url
            result, errors, _ = normalize_payload(body, complete=True)
            self.assertEqual(errors, [])
            self.assertEqual(result['model']['source_url'], url)

    def test_limits_reject_instead_of_silently_truncating(self):
        body = draft(); body['skus'] = [{'ram_gb': 12, 'storage_gb': n} for n in range(1, 21)]
        result, errors, _ = normalize_payload(body)
        self.assertEqual(errors, [])
        self.assertEqual(len(result['skus']), 20)
        body['skus'].append({'ram_gb': 16, 'storage_gb': 256})
        self.assert_invalid(body)
        body = with_spec(); body['specifications'] *= 41
        self.assert_invalid(body)

    def test_duplicate_skus_ignore_memory_type_and_normalize_integral_numbers(self):
        body = draft(); body['skus'].append({'ram_gb': 12.0, 'storage_gb': 256.0, 'memory_type': 'LPDDR5X'})
        self.assert_invalid(body)
        body = draft(); body['skus'][0]['ram_gb'] = 12.0
        normalized, errors, _ = normalize_payload(body)
        self.assertEqual(errors, [])
        self.assertIs(type(normalized['skus'][0]['ram_gb']), int)

    def test_specs_allow_only_technical_keys_and_require_raw_source_text(self):
        for key in ('benchmark', 'camera_score', 'subjective_score', 'made_up'):
            self.assert_invalid(with_spec(key=key))
        body = with_spec(); body['specifications'].append(copy.deepcopy(body['specifications'][0]))
        self.assert_invalid(body)
        for raw in (None, '', ' ', [], 201.5):
            self.assert_invalid(with_spec(raw_value=raw))
        body = with_spec(raw_value='  201.5 克（官网原文）  ')
        normalized, errors, _ = normalize_payload(body, complete=True)
        self.assertEqual(errors, [])
        self.assertEqual(normalized['specifications'][0]['raw_value'], '  201.5 克（官网原文）  ')

    def test_spec_numeric_units_and_ranges_match_the_key(self):
        for key, value, unit in [('weight_g', 201, 'kg'), ('screen_size_inch', 6.8, 'cm'),
                                  ('battery_capacity_mah', 6000, 'Wh'), ('weight_g', 0, 'g'),
                                  ('battery_capacity_mah', 999999, 'mAh'),
                                  ('screen_size_inch', 99, 'inch'), ('has_nfc', 2, None),
                                  ('chipset', 8, None), ('wired_charge_w', 80, 'Hz')]:
            self.assert_invalid(with_spec(key, value, unit))
        for key, value, unit, expected in [
            ('weight_g', 201, '克', 'g'), ('screen_size_inch', 6.8, 'inch', '英寸'),
            ('battery_capacity_mah', 6000, 'mAh', 'mAh'), ('has_nfc', 1, '-', None),
            ('chipset', None, None, None), ('refresh_rate_hz', None, 'Hz', 'Hz'),
        ]:
            normalized, errors, _ = normalize_payload(with_spec(key, value, unit), complete=True)
            self.assertEqual(errors, [])
            self.assertEqual(normalized['specifications'][0]['unit'], expected)

    def test_unknown_core_specs_remain_null_with_warning_even_when_complete(self):
        for key, unit in [('weight_g', 'g'), ('screen_size_inch', '英寸'), ('battery_capacity_mah', 'mAh')]:
            normalized, errors, warnings = normalize_payload(with_spec(key, None, unit, '未知'), complete=True)
            self.assertEqual(errors, [])
            self.assertIsNone(normalized['specifications'][0]['value_num'])
            self.assertTrue(warnings)

    def test_specs_require_model_source_on_complete_but_are_allowed_in_drafts(self):
        body = with_spec(); del body['model']['source_url']
        self.assert_invalid(body, complete=True)
        normalized, errors, warnings = normalize_payload(body)
        self.assertEqual(errors, [])
        self.assertIsNone(normalized['model']['source_url'])
        self.assertTrue(warnings)

    def test_catalog_and_both_pending_shapes_detect_normalized_model_identity(self):
        body = draft(); body['model'] = {'brand': ' ACME ', 'display_name': ' Phone PRO '}
        existing = [{'brand': 'acme', 'display_name': 'phone pro'}]
        self.assert_invalid(body, existing_rows=existing)
        for record in [{'model': {'brand': 'Acme', 'display_name': 'phone pro'}},
                       {'payload': {'model': {'brand': 'acme ', 'display_name': ' Phone Pro'}}}]:
            self.assert_invalid(body, pending_records=[record])
        for records in [None, [], [{'brand': 'Other', 'display_name': 'Phone PRO'}],
                        [{'brand': 'ACME', 'display_name': 'Phone Ultra'}]]:
            self.assertEqual(normalize_payload(body, existing_rows=records)[1], [])
        # Corrupt old records do not take down validation of a new submission.
        self.assertEqual(normalize_payload(body, pending_records=[None, {}, {'payload': None}, {'model': []}])[1], [])


if __name__ == '__main__':
    unittest.main()
