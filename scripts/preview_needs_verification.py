"""Local HTML/API demo with offline provider fixtures; never calls a paid model.

    .venv/Scripts/python.exe scripts/preview_needs_verification.py --inject-errors

Try: 最多3000元，至少256GB，不要苹果，拍照不重要，游戏最重要
The optional injection deliberately corrupts budget/brand/photo replies, then uses
the same production verifier, API, browser form and ranking as the normal service.
"""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8617)
    parser.add_argument('--inject-errors', action='store_true')
    args = parser.parse_args()
    os.environ['PHONE_ENABLE_LLM'] = 'false'
    from api.app import create_app
    from api.needs import NeedsService
    from providers.jev import MockClassifier, normalise_response
    import uvicorn

    class OfflineClassifier:
        def classify(self, text):
            reply = deepcopy(MockClassifier().classify(text).raw)
            if args.inject_errors:
                cap = reply['hard_constraints'].get('budget_max')
                if cap is not None:
                    reply['hard_constraints']['budget_max'] = cap + 1000
                if reply['excluded_brands']:
                    reply['hard_constraints']['brands'] = list(reply['excluded_brands'])
                    reply['excluded_brands'] = []
                for judgement in reply['judgements']:
                    if judgement['criterion'] == 'photo' and judgement['choice'] == '不在意':
                        judgement['choice'] = '重要'
            return normalise_response(reply, provider='offline-error-injection' if args.inject_errors else 'offline-fixture',
                                      model_version='demo-only-not-real-model-output', source_text=text)

    with tempfile.TemporaryDirectory(prefix='zhixuan-needs-preview-') as folder:
        service = NeedsService(enabled=True, key='OFFLINE-NOT-A-REAL-KEY', classifier=OfflineClassifier(),
                               quota_path=Path(folder)/'quota.sqlite')
        app = create_app(needs_service=service)
        original_meta = app.state.service.meta

        def offline_meta():
            meta = original_meta()
            meta['features']['offline_fixture'] = True
            meta['features']['injected_errors'] = args.inject_errors
            return meta

        app.state.service.meta = offline_meta
        print(f'Offline verification preview: http://127.0.0.1:{args.port}/; injected errors={args.inject_errors}', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
