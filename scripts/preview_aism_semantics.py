"""Local offline AISM + extraction UI preview; no paid calls and no injected mistakes.

    .venv/Scripts/python.exe scripts/preview_aism_semantics.py --port 8617
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8617)
    args = parser.parse_args()
    os.environ['PHONE_ENABLE_LLM'] = 'false'
    from api.app import create_app
    from api.needs import NeedsService
    from providers.jev import MockClassifier
    import uvicorn

    with tempfile.TemporaryDirectory(prefix='zhixuan-aism-preview-') as folder:
        service = NeedsService(enabled=True, key='OFFLINE-NOT-A-REAL-KEY', classifier=MockClassifier(),
                               quota_path=Path(folder)/'quota.sqlite')
        app = create_app(needs_service=service)
        original_meta = app.state.service.meta

        def offline_meta():
            meta = original_meta()
            meta['features']['offline_fixture'] = True
            return meta

        app.state.service.meta = offline_meta
        print(f'Offline AISM structure preview: http://127.0.0.1:{args.port}/', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
